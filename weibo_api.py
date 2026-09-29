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
import os
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


# ---------------------------------------------------------------------------
# The browserless CLI
# ---------------------------------------------------------------------------
# The README's central claim is that none of the paid products is needed for
# these three modes, and until this existed no COMMAND stood behind it:
# `WeiboClient` was library-only, and every runnable entry point launched
# Chromium. A claim a user cannot execute is a claim they have to take on
# trust (CLAUDE.md §13).
#
# Deliberately a fourth CLI rather than an `--engine http` flag on the three
# browser engines. Those three declare one identical flag set, asserted in
# both directions by the suite, and a flag that only one of them can honour
# would either break that or force two engines to accept an option they
# cannot implement. Transport and engine are different things.
#
# It offers a SUBSET of the family flag list, and the omissions are the
# point: `--cdp-endpoint`, `--fingerprint`, `--fp-country`, `--fp-tags` and
# `--headless/--headful` all describe a browser, and there is none here.
# Offering them would be the kind of setting that looks configurable and is
# not (§3).

import argparse
import sys

CLI_MODES = ("hot", "user", "post")


def _recover_long_text(client: "WeiboClient", rows) -> tuple:
    """Fetch the untruncated body for every row the site flagged.

    The same three outcomes the engines distinguish: recovered, the
    endpoint answering that there is no more text (so the post was whole
    and `isLongText` over-reported), and the endpoint not reachable at all
    — only the last is a failure. See product_parser.apply_longtext.
    """
    flagged = [r for r in rows if r.text_truncated]
    if not flagged:
        return 0, 0, 0
    recovered = false_flags = 0
    for row in flagged:
        if not row.sku:
            continue
        try:
            state, payload = client.fetch(P.longtext_url(row.sku))
        except (TransportError, VisitorError) as exc:
            log.warning("[!] long-text recovery failed for %s: %s",
                        row.sku, mask_secrets(str(exc))[:120])
            P.apply_longtext(row, None, reached=False)
            continue
        if state in ("content", "empty"):
            P.apply_longtext(row, P.parse_longtext(payload), reached=True)
            if row.text_source == "longtext":
                recovered += 1
            elif row.text_source == "inline":
                false_flags += 1
        else:
            P.apply_longtext(row, None, reached=False)
    if false_flags:
        log.info("[i] %d flagged post(s) had nothing to recover — the site's "
                 "isLongText flag was a false positive, and those rows are "
                 "marked whole rather than truncated", false_flags)
    log.info("[+] long text recovered for %d of %d flagged post(s)",
             recovered, len(flagged))
    return recovered, len(flagged), false_flags


def scrape(args) -> int:
    from output_writer import EXIT_API_ERROR, EXIT_NO_PRODUCTS, dedupe_by_key, finish_run
    import page_flow

    client = WeiboClient(proxy=args.proxy, delay=args.delay)

    uid = mblogid = mid = None
    if args.mode != "hot":
        if not args.url:
            log.error(
                f"--mode {args.mode} needs --url. For `user`, an account: "
                "https://weibo.com/u/2803301701 (or the bare id). For "
                "`post`, a post: https://weibo.com/2803301701/RiPCAfklU")
            raise SystemExit(2)
        ok, why = P.is_supported_host(args.url) if "//" in args.url else (True, "")
        if not ok:
            log.error(f"refusing {args.url}: {why}")
            raise SystemExit(2)
        if args.mode == "user":
            uid = P.uid_from_url(args.url)
            if not uid:
                log.error(
                    f"could not find an account id in {args.url!r}. Expected "
                    "https://weibo.com/u/<digits>, or the bare id.")
                raise SystemExit(2)
        else:
            mblogid, mid = P.post_id_from_url(args.url)
            if not (mblogid or mid):
                log.error(
                    f"could not find a post id in {args.url!r}. Expected "
                    "https://weibo.com/<uid>/<mblogid>, or /detail/<mid>.")
                raise SystemExit(2)

    rows, seen = [], set()
    pages_completed, pages_failed = 0, []
    stop_reason, blocked = "completed", False
    extra, start_url, final_url = {}, "", ""
    author_followers = None
    dedupe_key = "comment_id" if args.mode == "post" else "sku"
    pages = page_flow.pages_to_plan(args.pages, None)

    try:
        if args.mode == "user":
            state, payload = client.fetch(P.profile_info_url(uid))
            if state == "content":
                prof = P.parse_profile(payload)
                author_followers = prof.get("followers_count")
                extra["statuses_claimed"] = prof.get("statuses_count")
                extra["account"] = prof.get("screen_name")
                extra["followers_count"] = author_followers
            else:
                log.warning("[!] could not read the profile for %s — follower "
                            "counts will be null on every row", uid)

        if args.mode == "post" and not mid and mblogid:
            state, payload = client.fetch(P.SHOW_URL.format(mblogid=mblogid))
            if state == "content":
                mid = (str(payload.get("mid")) if payload.get("mid")
                       else str(payload.get("idstr")) if payload.get("idstr")
                       else None)
            if not mid:
                log.error("[x] could not resolve %s to a numeric mid, which "
                          "is the id the comments endpoint takes", mblogid)
                return EXIT_NO_PRODUCTS

        cursor = 0
        for page_num in range(1, pages + 1):
            if args.mode == "hot":
                url = P.hot_feed_url(count=25)
            elif args.mode == "user":
                url = P.user_feed_url(uid, cursor)
            else:
                url = P.comments_url(mid, count=20, max_id=cursor or 0)
            if page_num == 1:
                start_url = url
            final_url = url

            state, payload = client.fetch(url)
            if not page_flow.should_parse(state):
                pages_failed.append(page_num)
                if page_flow.counts_as_blocked(state):
                    blocked = True
                    stop_reason = state
                    log.error("[x] page %d: %s — this wants an ACCOUNT, and "
                              "no exit address supplies one.", page_num, state)
                else:
                    stop_reason = f"error_on_page_{page_num}"
                    log.error("[x] page %d: %s", page_num, state)
                break

            page_rows, cursor_next = P.rows_for_mode(
                args.mode, payload, page=page_num, parent_sku=mblogid,
                author_followers=author_followers)
            fresh = dedupe_by_key(page_rows, seen, key=dedupe_key)
            rows.extend(fresh)
            pages_completed += 1
            unit = "fetch" if args.mode == "hot" else "page"
            log.info("[+] %s %d: %d row(s), %d new (total %d)",
                     unit, page_num, len(page_rows), len(fresh), len(rows))

            if not page_rows:
                stop_reason = "no_new_products"
                break
            if args.mode == "hot":
                extra["feed_rerolled"] = True
                if page_num >= pages:
                    stop_reason = "feed_not_addressable"
            else:
                cursor = cursor_next
                if cursor in (None, -1, 0, "-1", "0"):
                    stop_reason = ("single_page_mode" if pages == 1
                                   else "cursor_exhausted")
                    break
            if not fresh:
                stop_reason = "no_new_products"
                break
        else:
            if args.mode == "hot":
                stop_reason = "feed_not_addressable"

        if args.mode in ("hot", "user") and rows:
            recovered, attempted, _ = _recover_long_text(client, rows)
            extra["long_text_flagged"] = attempted
            extra["long_text_recovered"] = recovered

    except TransportError as exc:
        log.error("[x] nothing was reached: %s", mask_secrets(str(exc)))
        if not rows:
            return EXIT_API_ERROR
        stop_reason = "transport_error"
    except VisitorError as exc:
        log.error("[x] the visitor handshake failed: %s", mask_secrets(str(exc)))
        if not rows:
            return EXIT_API_ERROR
        stop_reason = "visitor_failed"

    if rows:
        still = sum(1 for r in rows if r.text_truncated)
        if still:
            log.warning("[!] %d of %d row(s) still hold TRUNCATED text — see "
                        "the text_truncated column before using `title`",
                        still, len(rows))
        extra["text_truncated_rows"] = still
    extra["transport"] = "http"

    return finish_run(
        rows, args.out, args.format, args.allow_empty, blocked=blocked,
        stop_reason=stop_reason, pages_requested=pages,
        pages_completed=pages_completed, pages_failed=pages_failed,
        start_url=start_url, final_url=final_url, mode=args.mode,
        source=P.SOURCE, extra=extra)


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Scrape Weibo with NO browser at all — the visitor "
                    "handshake and the site's own JSON over plain HTTPS. "
                    "This is the path the README's 'you need no key, no "
                    "proxy and no account' claim describes, now runnable.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    p.add_argument("--mode", choices=CLI_MODES, default="hot")
    p.add_argument("--url", default=None,
                   help="An account or post URL, or a bare id. Required for "
                        "--mode user and --mode post.")
    p.add_argument("--pages", type=int, default=1,
                   help=f"How many fetches to make (max {P.MAX_PAGES}). The "
                        "hot feed has no page 2: N there means N fetches of "
                        "a feed that re-rolls.")
    p.add_argument("--format", choices=["json", "csv", "both"], default="json")
    p.add_argument("--out", default="weibo_posts")
    p.add_argument("--delay", type=float, default=1.0)
    p.add_argument("--proxy", default=None,
                   help="Credentials are read from .env and never from a "
                        "command line, where `ps` can see them.")
    p.add_argument("--allow-empty", action="store_true")
    args = p.parse_args(argv)
    import env_config
    env_config.apply(args)
    if args.pages < 1:
        p.error("--pages must be at least 1")
    if args.pages > P.MAX_PAGES:
        p.error(f"--pages above {P.MAX_PAGES} is refused")
    return args


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args(argv)
    try:
        return scrape(args)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        log.error("[x] interrupted")
        return 1
    except Exception as exc:  # noqa: BLE001
        log.error("[x] %s", mask_secrets(str(exc)))
        if os.environ.get("WEIBO_TRACEBACK"):
            raise
        return 1


if __name__ == "__main__":
    sys.exit(main())
