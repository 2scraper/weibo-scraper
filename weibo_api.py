"""
weibo_api.py
------------
The passport visitor handshake, and the HTTP reader built on top of it.

Why this file exists
---------------------
Every `/ajax/` route on weibo.com answers `{"ok": -100}` to a session that
carries no cookie. Weibo's own front end deals with that before it fetches
anything: it runs the ANONYMOUS VISITOR handshake against passport, which
hands back a `SUB` cookie, and every request afterwards is made as that
visitor. No account is involved and nothing is paid for.

    POST passport.weibo.com/visitor/genvisitor   {cb, fp}   -> a tid
    GET  passport.weibo.com/visitor/visitor      a=incarnate&t={tid}
                                                            -> SUB, SUBP

Measured 2026-09-21 from a Hetzner datacenter address in Finland, with no
proxy: **5 mints out of 5**, `retcode: 20000000` each time, about 1.5
seconds per handshake, and the hot feed opened immediately afterwards. The
handshake is not geo-gated, not rate-limited at the volumes this repo uses,
and not something 2Captcha (or any other vendor) needs to be involved in.

What this is NOT
-----------------
It is not a login, and it does not pretend to be one. A visitor cookie
opens the feed, profiles, single posts, comments and likers; it does NOT
open `/ajax/statuses/mymblog`, Weibo's search, or anything on m.weibo.cn.
Those want an account, and this repo does not implement account login —
which is a TODO rather than a limitation of the site or of any product
(CLAUDE.md §19). If you need them, the honest answer is "not implemented
here", not "impossible".

Credentials
------------
The handshake itself carries no secret of ours. A PROXY URL does, and
`requests` puts the full URL — credentials included — into the text of
`HTTPError` and of every connection error it raises (CLAUDE.md §8). So
every call below is wrapped, and anything that escapes is re-raised with
the credentials masked GLOBALLY rather than once: a masker that handles the
first occurrence prints the password the other four times and looks like it
is working.
"""

import json
import logging
import random
import re
import time
from typing import Any, Dict, Optional, Tuple

import requests

import product_parser as P

log = logging.getLogger("weibo_api")

# Bound every remote call. Neither `requests` nor any browser library in
# this family provides a default, and an unbounded fetch is how a run hangs
# instead of reporting a timeout (CLAUDE.md §8).
HTTP_TIMEOUT = 25

# The handshake's fingerprint blob. Weibo's own page sends a description of
# the browser here; the server does not validate it beyond its shape, and a
# plausible constant mints a cookie every time (5 of 5 measured). It is NOT
# an anti-detect fingerprint and must not be confused with `--fingerprint`,
# which is a separate 2Captcha product applied to the BROWSER.
_VISITOR_FP = {
    "os": "1",
    "browser": "Chrome140,0,0,0",
    "fonts": "undefined",
    "screenInfo": "1920*1080*24",
    "plugins": "",
}

# A real desktop Chrome UA. Overridden by the engines with the actual
# browser's own string — CLAUDE.md §8: the user agent comes from the
# browser, not from a literal, because a hardcoded version drifts from
# whatever Chromium is installed and claiming an older Chrome than the JS
# engine reports is itself a mismatch. This constant is the HTTP path's
# only option, since there is no browser there to ask.
DEFAULT_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/140.0.0.0 Safari/537.36")

_CALLBACK_RE = re.compile(r"\((.*)\)\s*;?\s*$", re.S)
_VISITOR_OK = 20000000

# Patterns whose VALUE is a secret, masked anywhere a message is built.
# `t=` is the visitor tid — not a credential of the user's, but a session
# token that identifies this run, and there is no reason to print it.
_SECRET_RE = re.compile(
    r"((?:client)?key|token|api[_-]?key|password|passwd|pwd)=([^&\s\"']+)",
    re.I)
_PROXY_CRED_RE = re.compile(r"://([^/:@\s]+):([^/@\s]+)@")


def mask_secrets(text: str) -> str:
    """Mask credentials in a string, every occurrence of every pattern.

    Globally on purpose. Playwright repeats a CDP endpoint five times in one
    error (the message plus a four-line call log), so a masker that stops
    after the first match prints the password four more times while looking
    like it works (CLAUDE.md §8).

    Host and port are KEPT. Which exit a run used is the point of the log
    and is not the secret.
    """
    if not text:
        return text
    out = _SECRET_RE.sub(lambda m: f"{m.group(1)}=***", str(text))
    out = _PROXY_CRED_RE.sub(lambda m: f"://{m.group(1)}:***@", out)
    return out


class VisitorError(RuntimeError):
    """The handshake did not produce a cookie. Distinct from a transport
    fault so a caller can tell "passport said no" from "nothing answered"."""


class TransportError(RuntimeError):
    """Nothing was reached: a dead proxy, DNS, a refused connection.

    Its own type because CLAUDE.md §8 says a proxy failure is not a timeout
    and the two want opposite responses — a timeout deserves another try at
    the same exit, a dead proxy a different one. Catching only the timeout
    type is what let this escape as a traceback in an older repo.
    """


def _raise_masked(exc: Exception, what: str) -> None:
    """Re-raise a library exception with its message masked.

    `requests` puts the full URL, query string included, into the text of
    `HTTPError` and of every connection error — so any endpoint taking a
    key as a query parameter leaks it the moment anything goes wrong. The
    endpoint and the status are the useful half and are kept.
    """
    msg = mask_secrets(f"{what}: {type(exc).__name__}: {exc}")
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        raise TransportError(msg) from None
    raise VisitorError(msg) from None


def _unwrap_callback(text: str) -> Dict[str, Any]:
    """Pull the JSON out of `window.cb && cb({...});`.

    Passport answers JSONP whichever `cb` is asked for, so the payload has
    to be unwrapped rather than parsed directly. A body that does not match
    is reported as such instead of being read as an empty result — an
    unparseable answer and a refusal are different facts.
    """
    m = _CALLBACK_RE.search((text or "").strip())
    if not m:
        raise VisitorError(
            "passport did not answer in the expected JSONP shape "
            f"(got {len(text or '')} bytes starting {(text or '')[:60]!r})")
    try:
        obj = json.loads(m.group(1))
    except ValueError as exc:
        raise VisitorError(f"passport's JSONP body did not parse: {exc}") from None
    if not isinstance(obj, dict):
        raise VisitorError("passport's JSONP body was not an object")
    return obj


def mint_visitor_cookies(session: Optional[requests.Session] = None,
                         user_agent: str = DEFAULT_UA,
                         proxy: Optional[str] = None,
                         timeout: int = HTTP_TIMEOUT) -> Dict[str, str]:
    """Run the handshake; return the cookies it produced.

    Raises VisitorError when passport refuses and TransportError when
    nothing was reached. Never returns an empty dict silently: a function
    that returns `{}` on error is this codebase's most common historical bug
    class (CLAUDE.md §8), and an empty cookie jar here would send every
    later request into a `-100` that looks like the site's fault.
    """
    s = session or requests.Session()
    s.headers.setdefault("User-Agent", user_agent)
    s.headers.setdefault("Referer", "https://weibo.com/")
    if proxy:
        s.proxies.update({"http": proxy, "https": proxy})

    try:
        r = s.post(P.VISITOR_GEN_URL,
                   data={"cb": "gen_callback", "fp": json.dumps(_VISITOR_FP)},
                   timeout=timeout)
        r.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — re-raised, masked, typed
        _raise_masked(exc, "visitor/genvisitor")

    body = _unwrap_callback(r.text)
    if body.get("retcode") != _VISITOR_OK:
        raise VisitorError(
            f"visitor/genvisitor refused: retcode={body.get('retcode')!r} "
            f"msg={body.get('msg')!r}")
    tid = (body.get("data") or {}).get("tid")
    if not tid:
        raise VisitorError("visitor/genvisitor returned no tid")

    url = P.VISITOR_INCARNATE_URL.format(
        tid=requests.utils.quote(str(tid), safe=""),
        rand=f"{time.time():.4f}")
    try:
        r2 = s.get(url, timeout=timeout)
        r2.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        _raise_masked(exc, "visitor/incarnate")

    body2 = _unwrap_callback(r2.text)
    if body2.get("retcode") != _VISITOR_OK:
        raise VisitorError(
            f"visitor/incarnate refused: retcode={body2.get('retcode')!r} "
            f"msg={body2.get('msg')!r}")

    jar = s.cookies.get_dict()
    if "SUB" not in jar:
        raise VisitorError(
            "the handshake reported success but set no SUB cookie — "
            f"cookies present: {sorted(jar)}")
    log.info("[+] visitor cookie minted (SUB present, %d cookies)", len(jar))
    return jar


def cookie_header(jar: Dict[str, str]) -> str:
    """A `Cookie:` header value, for a caller that is not using a session."""
    return "; ".join(f"{k}={v}" for k, v in (jar or {}).items())


# Cookies the browser engines have to install before navigating. SUB is the
# one that matters; the others ride along because passport sets them and a
# partial jar is a difference from what the site's own front end holds.
VISITOR_COOKIE_NAMES = ("SUB", "SUBP", "SRT", "SRF", "SVB")


def cookies_for_browser(jar: Dict[str, str], domain: str = ".weibo.com"):
    """The jar as a list of browser-shaped cookie dicts.

    Shared by all three engines so they cannot disagree about which cookies
    a visitor session consists of. Each driver spells the INSTALL its own
    way — that part stays in the engines — but the CONTENT is decided once.
    """
    return [{"name": k, "value": v, "domain": domain, "path": "/"}
            for k, v in (jar or {}).items()
            if k in VISITOR_COOKIE_NAMES]


class WeiboClient:
    """A visitor-authenticated reader for the routes this repo uses.

    Used directly by the HTTP path and by the smoke suite; the browser
    engines reuse `mint_visitor_cookies` and `cookies_for_browser` but drive
    their own navigation.
    """

    def __init__(self, user_agent: str = DEFAULT_UA,
                 proxy: Optional[str] = None,
                 timeout: int = HTTP_TIMEOUT,
                 delay: float = 0.0):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Referer": "https://weibo.com/",
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
        })
        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})
        self.timeout = timeout
        self.delay = float(delay or 0)
        self.proxy = proxy
        self._minted = False

    def ensure_visitor(self, force: bool = False) -> None:
        if self._minted and not force:
            return
        mint_visitor_cookies(self.session,
                             user_agent=self.session.headers["User-Agent"],
                             proxy=None,  # already on the session
                             timeout=self.timeout)
        self._minted = True

    def get(self, url: str) -> Tuple[int, str]:
        """`(status, body)` for one URL, with the delay applied first."""
        if self.delay:
            time.sleep(self.delay + random.uniform(0, self.delay * 0.25))
        try:
            r = self.session.get(url, timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001
            _raise_masked(exc, f"GET {mask_secrets(url)}")
        return r.status_code, r.text

    def fetch(self, url: str, allow_mint: bool = True) -> Tuple[str, Any]:
        """`(state, payload)` — fetch, classify, and re-mint once if asked.

        The re-mint is capped at `page_flow.VISITOR_MINTS_PER_PAGE`, which
        is one. A second mint answers the same thing a second time, and the
        one-attempt cap is what keeps a `needs_visitor` loop from becoming
        an unbounded retry against someone else's server.
        """
        from page_flow import classify, needs_visitor_cookie

        self.ensure_visitor()
        status, body = self.get(url)
        state = classify(body, status, url)
        if needs_visitor_cookie(state) and allow_mint:
            log.info("[i] %s -> re-minting the visitor cookie and retrying once",
                     state)
            self.ensure_visitor(force=True)
            status, body = self.get(url)
            state = classify(body, status, url)
        return state, P._payload_dict(body)
