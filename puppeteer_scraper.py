#!/usr/bin/env python3
"""
puppeteer_scraper.py — the pyppeteer engine.

A parity engine. It must behave identically to playwright_scraper.py: same
flags, same exit codes, same run status, same rows. Where it cannot, the
difference is stated here and asserted by the suite rather than left to be
discovered.

    python3 puppeteer_scraper.py --mode hot --pages 3 --out weibo_posts
    python3 puppeteer_scraper.py --mode user --url https://weibo.com/u/2803301701
    python3 puppeteer_scraper.py --mode post --url https://weibo.com/2803301701/RiPCAfklU

pyppeteer is effectively unmaintained and its own README points at
Playwright. It is here for parity, not because it is recommended.

The 403 that is not a refusal
------------------------------
Navigating a browser straight to one of Weibo's API URLs answers
`403 {"error":"Forbidden"}`, while the same URL with the same cookies
returns the payload when it is fetched the way the site's own front end
fetches it. Measured 2026-09-21; see playwright_scraper.py's module
docstring for the three-way count.

Playwright can issue that XHR with no JavaScript at all, through its
context request API. pyppeteer has no equivalent, so this engine does it
the other legal way: it opens `https://weibo.com/` and calls `fetch`
inside the page.

That JavaScript is handed to `page.evaluate` as a real function, which
goes through `Runtime.callFunctionOn` — NOT an evaluated string, which is
the form a Content-Security-Policy without `unsafe-eval` refuses and which
killed a run in a sibling repo. And it lives HERE, in the
engine that speaks this dialect, never in a shared module: Selenium's
`execute_script` takes a function body with an explicit `return` while
this takes an arrow function, so a shared snippet would quietly acquire
one driver's grammar.
"""

import argparse
import asyncio
import json
import logging
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Imported at MODULE level on purpose. A sibling repo
# imported its driver inside the launch path, so the module imported
# cleanly with no pyppeteer installed, the suite's "engine absent" skip
# never fired, and CI ran green against a stub version for a while. The
# suite asserts this import is at module level with an `ast` walk.
from pyppeteer import connect as pyppeteer_connect
from pyppeteer import launch as pyppeteer_launch
from pyppeteer.errors import NetworkError as PuppeteerError
from pyppeteer.errors import TimeoutError as PuppeteerTimeout

import env_config
import page_flow
import product_parser as P
import weibo_api as W
from output_writer import (EXIT_API_ERROR, Post,
                           dedupe_by_key, finish_run)
import proxy_pool
from proxy_pool import ProxyPool

log = logging.getLogger("weibo.puppeteer")

# --------------------------------------------------------------------------
# Site constants — byte-identical to the other two engines
# --------------------------------------------------------------------------

MIN_CARD_MATCHES = page_flow.MIN_CARD_MATCHES
NEXT_PAGE_SELECTOR = None
DEFAULT_FEED_GROUP = "102803"
LANDING_URL = "https://weibo.com/"
API_HEADERS = {
    "Referer": LANDING_URL,
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/plain, */*",
}
MODES = ("hot", "user", "post")
NAV_TIMEOUT_MS = page_flow.CONTENT_TIMEOUT_MS

# The fetch, as a real function object. `page.evaluate` compiles this and
# calls it through the protocol; it is never `eval`'d as a string.
_FETCH_JS = """
async (url, headers) => {
    const r = await fetch(url, {credentials: 'include', headers: headers});
    return {status: r.status, body: await r.text()};
}
"""


def _chrome_ua(chromium_version: str) -> str:
    """Build a UA from the Chromium that is actually installed."""
    major = (chromium_version or "").split(".")[0] or "140"
    if major.lower().startswith("headless"):
        major = major.split("/")[-1].split(".")[0] or "140"
    return ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{major}.0.0.0 Safari/537.36")


@dataclass
class PageOutcome:
    page_num: int
    url: str
    rows: List[Post] = field(default_factory=list)
    state: str = "unknown"
    next_cursor: Any = None
    error: Optional[str] = None
    attempted: bool = True

    @property
    def ok(self) -> bool:
        return self.error is None and page_flow.should_parse(self.state)


def _mask(text: Any) -> str:
    return W.mask_secrets(str(text))


_PROXY_ERRORS = (
    "ERR_PROXY_CONNECTION_FAILED", "ERR_TUNNEL_CONNECTION_FAILED",
    "ERR_PROXY_AUTH_UNSUPPORTED", "ERR_PROXY_AUTH_REQUESTED",
    "ERR_UNEXPECTED_PROXY_AUTH", "ERR_SOCKS_CONNECTION_FAILED",
)


def _proxy_failure(exc: Exception) -> str:
    """Name the proxy error in an exception, or "".

    A dead proxy is NOT a timeout — Chromium reports it as a generic error
    and the two want opposite responses.
    """
    text = str(exc or "")
    for marker in _PROXY_ERRORS:
        if marker in text:
            return marker
    return ""


class _BrowserSession:
    """One browser, one page, one exit — async, because pyppeteer is.

    A rotation is a fresh browser: cookies issued against exit A and
    replayed from exit B are a stronger signal than either address alone.
    """

    def __init__(self, args, proxy_url: Optional[str]):
        self.args = args
        self.proxy_url = proxy_url
        self.browser = None
        self.page = None
        self.user_agent = W.DEFAULT_UA
        self.remote = bool(args.cdp_endpoint)

    async def open(self) -> None:
        args = self.args
        if args.cdp_endpoint:
            # Never set a UA, a fingerprint or a proxy over a remote
            # endpoint: the remote browser brings its own.
            try:
                self.browser = await pyppeteer_connect(
                    browserWSEndpoint=args.cdp_endpoint)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    "could not connect to the Scraping Browser endpoint: "
                    f"{_mask(exc)}") from None
            self.page = await self.browser.newPage()
        else:
            # The shared module already splits this correctly,
            # keeping credentials out of the launch args.
            server, creds = proxy_pool.split_credentials(self.proxy_url)
            user, pw = creds if creds else (None, None)
            launch_args = ["--no-sandbox", "--disable-dev-shm-usage"]
            if server:
                launch_args.append(f"--proxy-server={server}")
            if args.locale:
                # The same Chromium switch the Selenium engine passes; this
                # engine used to accept --locale and ignore it.
                launch_args.append(f"--lang={args.locale}")
            self.browser = await pyppeteer_launch(
                headless=args.headless, args=launch_args,
                handleSIGINT=False, handleSIGTERM=False, handleSIGHUP=False)
            self.page = await self.browser.newPage()
            if user:
                await self._authenticate_proxy(user, pw or "")
            try:
                self.user_agent = _chrome_ua(await self.browser.version())
                await self.page.setUserAgent(self.user_agent)
            except Exception as exc:  # noqa: BLE001
                log.warning("[!] could not set the user agent: %s",
                            _mask(exc)[:100])

        self.page.setDefaultNavigationTimeout(NAV_TIMEOUT_MS)
        await self._install_visitor_cookies()
        await self._land()


    async def _authenticate_proxy(self, username: str, password: str) -> None:
        """Answer the proxy's auth challenge for this page.

        `page.authenticate` relies on `Network.setRequestInterception`, which
        current Chromium builds no longer provide; on those it raises
        "'Network.setRequestInterception' wasn't found" before the first
        navigation. Only that error falls back to CDP's Fetch domain with
        `handleAuthRequests`, which is what Playwright uses; anything else
        propagates. The fallback pauses every request and continues it
        unchanged, and answers only a challenge whose source is the PROXY,
        with credentials that stay in this process's memory.
        """
        try:
            await self.page.authenticate({"username": username,
                                          "password": password})
            return
        except Exception as exc:  # noqa: BLE001 - pyppeteer raises several types
            if "setRequestInterception" not in str(exc):
                raise
            log.info("[i] page.authenticate is not available on this "
                     "Chromium; answering the proxy through CDP Fetch")
        cdp = await self.page.target.createCDPSession()

        def _paused(event):
            asyncio.ensure_future(cdp.send(
                "Fetch.continueRequest", {"requestId": event["requestId"]}))

        def _auth(event):
            source = (event.get("authChallenge") or {}).get("source")
            response = ({"response": "ProvideCredentials",
                         "username": username, "password": password}
                        if source == "Proxy" else {"response": "Default"})
            asyncio.ensure_future(cdp.send(
                "Fetch.continueWithAuth",
                {"requestId": event["requestId"],
                 "authChallengeResponse": response}))

        cdp.on("Fetch.requestPaused", _paused)
        cdp.on("Fetch.authRequired", _auth)
        await cdp.send("Fetch.enable", {"handleAuthRequests": True,
                                        "patterns": [{"urlPattern": "*"}]})
        self._auth_session = cdp

    async def rotate_to(self, proxy_url):
        """Tear this session down and build another on a different exit.

        A rotation is a FRESH BROWSER, not a swapped proxy:
        cookies a bot manager issued against exit A and replayed from exit B
        are a stronger signal than either address alone. So the visitor
        cookie is re-minted through the new exit too, rather than carried
        across.
        """
        log.info("[i] rotating to a new exit and rebuilding the browser")
        await self.close()
        self.proxy_url = proxy_url
        await self.open()

    async def _install_visitor_cookies(self) -> None:
        """Give the session a visitor cookie.

        Two routes, because the right one depends on WHERE the browser is.

        LOCAL browser: mint over HTTP through the same proxy the browser
        uses, and install the jar.

        REMOTE browser (--cdp-endpoint): navigate it to weibo.com and let
        the SITE run its own handshake, so the cookie is issued to that
        browser, from that browser's address.

        The original code minted over HTTP for the remote case too, with no
        proxy, so the cookie was issued to this machine and replayed from
        the Scraping Browser's exit. Measured 2026-09-21 in one session:
        the remote browser left from a residential US address (AS11776,
        California) while the HTTP handshake left from a Finnish datacenter
        (AS24940). Cookies issued against one exit and replayed from another
        are a stronger signal than either address alone — and it also wasted what the Scraping
        Browser is bought for, since the cookie IS the session identity.
        """
        if self.remote:
            await self._mint_in_browser()
            return
        try:
            jar = W.mint_visitor_cookies(
                user_agent=self.user_agent,
                proxy=(None if self.remote else self.proxy_url))
        except W.TransportError as exc:
            raise RuntimeError("visitor handshake could not reach passport: "
                               f"{_mask(exc)}") from None
        except W.VisitorError as exc:
            log.warning("[!] visitor handshake refused (%s) — continuing "
                        "without a cookie; the feed will answer ok:-100 and "
                        "the run will report that honestly", _mask(exc))
            return
        for cookie in W.cookies_for_browser(jar):
            try:
                await self.page.setCookie(cookie)
            except Exception as exc:  # noqa: BLE001
                log.warning("[!] could not set cookie %s: %s",
                            cookie.get("name"), _mask(exc)[:80])
        log.info("[+] visitor cookies installed in the browser")


    async def _mint_in_browser(self) -> None:
        """Let the site hand the REMOTE browser its own visitor cookie."""
        try:
            await self.page.goto(LANDING_URL, waitUntil="domcontentloaded",
                                 timeout=NAV_TIMEOUT_MS)
            await asyncio.sleep(4)
        except Exception as exc:  # noqa: BLE001
            log.warning("[!] could not run the visitor handshake in the "
                        "remote browser: %s", _mask(exc)[:140])
            return
        try:
            names = {c.get("name") for c in await self.page.cookies()}
        except Exception:  # noqa: BLE001
            names = set()
        if "SUB" in names:
            log.info("[+] visitor cookie minted BY the remote browser, from "
                     "its own exit (%d cookies)", len(names))
        else:
            log.warning("[!] the remote browser finished the handshake with "
                        "no SUB cookie — cookies present: %s", sorted(names))

    async def _land(self) -> None:
        """Open the site once, so every payload read is an XHR from it."""
        try:
            await self.page.goto(LANDING_URL, waitUntil="domcontentloaded",
                                 timeout=NAV_TIMEOUT_MS)
        except (PuppeteerError, PuppeteerTimeout, Exception) as exc:  # noqa: BLE001
            log.warning("[!] could not open %s (%s) — continuing",
                        LANDING_URL, _mask(exc)[:120])

    async def remint_visitor(self) -> None:
        await self._install_visitor_cookies()

    async def close(self) -> None:
        try:
            if self.browser:
                await self.browser.close()
        except Exception:  # noqa: BLE001 — teardown must not mask a result
            pass


async def _api_get(session: _BrowserSession, url: str) -> Tuple[Optional[int], str]:
    """Fetch one API URL as an XHR from the loaded page.

    The status is RETURNED rather than discarded and passed positionally
    into `classify`: a discarded status turns a 403 into a retryable parse
    error, a failure mode this family has hit before.
    """
    got = await session.page.evaluate(_FETCH_JS, url, API_HEADERS)
    if not isinstance(got, dict):
        return None, ""
    return got.get("status"), got.get("body") or ""


async def handle_captcha_if_present(page, args,
                                    budget: page_flow.SolveBudget) -> bool:
    """Detect a challenge, solve it once, and inject the token.

    Written against captcha_solver's REAL API. The first version called a
    `solve_on_page` helper that the module has never defined — in all three
    engines, invisible to import, `--help`, `compileall` and 4,900 green
    offline checks, and it would have raised AttributeError at the exact
    moment a challenge first appeared. It surfaced only once a real API key
    arrived and the suite's signature-binding check was widened to cover
    this module (the credential-gated paths are the ones
    nobody ran).

    `budget` is NOT optional and is shared with the other call site in the
    same attempt. Every engine in this family calls this twice per
    attempt and only the second call was ever counted, so
    `SOLVES_PER_PAGE = 1` enforced nothing.

    Expected never to fire on Weibo: no challenge was rendered on any route
    this repo reads. It is here because Weibo has two captcha vendors wired
    into its own page chrome, so one appearing later is a change in the
    site rather than an impossibility.
    """
    if args.solve_captcha == "never":
        return False
    if not budget.may_solve():
        log.info("[i] solve budget for this page is spent (%r) — not "
                 "paying twice for the same page", budget)
        return False

    page_url = ""
    try:
        page_url = page.url or ""
    except Exception:  # noqa: BLE001
        pass
    try:
        html = await page.content()
    except Exception:  # noqa: BLE001
        return False

    marker = P.detect_bot_challenge(html)
    if marker and marker not in P.CHALLENGE_MARKERS:
        # A login wall is not a challenge. Nothing is being tested, access
        # is being declined, and paying for a request the API will reject
        # is worse than reporting the page unsolved.
        log.info("[i] %r is a login wall, not a challenge — no solve "
                 "attempted (this repo does not implement account login)",
                 marker)
        return False

    import captcha_solver as CS

    # BOTH detectors, then reconcile — never short-circuit on the first.
    # They can disagree about the same page, and the live
    # one sees a widget the markup only hints at.
    static_hit = CS.detect_recaptcha_v3(html, page_url)
    runtime_hit = None
    try:
        discovered = await page.evaluate(CS.RECAPTCHA_DISCOVERY_JS)
        runtime_hit = CS.detect_recaptcha_in_page(lambda _js: discovered,
                                                  page_url)
    except Exception as exc:  # noqa: BLE001
        log.debug("live captcha discovery failed: %s", _mask(exc)[:120])
    challenge = CS.reconcile_detections(static_hit, runtime_hit)
    if challenge is None:
        return False

    log.info("[i] challenge detected: kind=%s size=%s source=%s",
             challenge.kind, challenge.size, challenge.source)
    if not args.twocaptcha_key:
        log.warning("[!] a challenge is on the page and no --twocaptcha-key "
                    "was given — continuing unsolved")
        return False

    try:
        budget.charge()
        token = CS.solve_recaptcha(challenge, args.twocaptcha_key,
                                   api_version=args.captcha_api,
                                   min_score=args.min_score)
    except CS.CaptchaUnsolvable as exc:
        log.warning("[!] 2captcha could not solve it: %s", _mask(exc))
        return False
    except Exception as exc:  # noqa: BLE001
        log.warning("[!] captcha solve failed: %s — continuing", _mask(exc))
        return False

    if not token:
        return False
    try:
        await page.evaluate(CS.INJECT_TOKEN_JS, token)
    except Exception as exc:  # noqa: BLE001
        log.warning("[!] the token was bought but could not be injected: %s",
                    _mask(exc))
        return False
    log.info("[+] challenge solved and the token injected (%d chars)",
             len(token))
    return True

def _snapshot(body: str, url: str, args, page_num: int, state: str) -> None:
    """Write the exact bytes this run saw, on success too."""
    if not args.dump_html:
        return
    path = f"{args.out}_debug.page{page_num}.{state}.json"
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body or "")
        log.info("[+] wrote %s (%d bytes)", path, len(body or ""))
    except OSError as exc:
        log.warning("[!] could not write %s: %s", path, exc)


async def _fetch_one(session: _BrowserSession, args, pool: Optional[ProxyPool],
                     page_num: int, url: str,
                     parent_sku: Optional[str] = None,
                     author_followers: Optional[int] = None) -> PageOutcome:
    """Fetch one API URL, with retries, and parse it."""
    attempts = max(1, int(args.retries) + 1)
    last_error: Optional[str] = None
    blocked_tries = 0

    # ONE allowance for this page, created before the retry loop: a retry
    # or an exit rotation does not hand the page a fresh one.
    budget = page_flow.SolveBudget()
    for attempt in range(1, attempts + 1):
        try:
            if args.solve_captcha == "always":
                # Pre-classification call site, only on request. Under
                # `when-blocked` nothing is bought until the response has
                # been read and classified as a challenge.
                await handle_captcha_if_present(session.page, args, budget)
            status, body = await _api_get(session, url)
            state = page_flow.classify(body, status, url)

            if page_flow.needs_visitor_cookie(state):
                log.info("[i] page %d: %s — re-minting the visitor cookie",
                         page_num, state)
                await session.remint_visitor()
                status, body = await _api_get(session, url)
                state = page_flow.classify(body, status, url)

            if page_flow.should_solve(state):
                if await handle_captcha_if_present(session.page, args, budget):
                    status, body = await _api_get(session, url)
                    state = page_flow.classify(body, status, url)

            _snapshot(body, url, args, page_num, state)

            if page_flow.should_parse(state):
                rows, cursor = P.rows_for_mode(
                    args.mode, body, page=page_num, parent_sku=parent_sku,
                    author_followers=author_followers)
                return PageOutcome(page_num, url, rows=rows, state=state,
                                   next_cursor=cursor)

            if page_flow.counts_as_blocked(state):
                # Two questions, and they are different. The POLICY says
                # whether this particular state is worth another attempt;
                # RETRY_ON_BLOCKED says whether a blocked page is worth
                # re-fetching at all on this site. Both are consulted, so
                # neither is prose pretending to be enforcement.
                if not (page_flow.RETRY_ON_BLOCKED
                        and page_flow.should_retry(state)):
                    log.error("[x] page %d: %s — %s Not retrying.",
                              page_num, state, page_flow.blocked_hint(state))
                    return PageOutcome(page_num, url, state=state, error=state)
                budgeted = (len(pool) - 1 if pool
                            else page_flow.BLOCK_RETRIES_WITHOUT_POOL)
                if blocked_tries >= budgeted:
                    log.error("[x] page %d: %s and the block-retry budget "
                              "(%d) is spent", page_num, state, budgeted)
                    return PageOutcome(page_num, url, state=state, error=state)
                blocked_tries += 1

            last_error = state
            if not page_flow.should_retry(state):
                return PageOutcome(page_num, url, state=state, error=state)

        except PuppeteerTimeout as exc:
            last_error = f"timeout: {_mask(exc)[:160]}"
            log.warning("[!] page %d attempt %d: %s", page_num, attempt, last_error)
        except PuppeteerError as exc:
            which = _proxy_failure(exc)
            if which:
                last_error = f"proxy: {which}"
                log.warning("[!] page %d attempt %d: the exit failed (%s)",
                            page_num, attempt, which)
                if pool and len(pool) > 1:
                    # A rotation is a FRESH BROWSER: cookies
                    # a bot manager issued against exit A and replayed from
                    # exit B are a stronger signal than either address
                    # alone, so the session is torn down and rebuilt rather
                    # than having the proxy swapped under it.
                    await session.rotate_to(pool.advance("proxy failure"))
            else:
                last_error = f"browser: {_mask(exc)[:160]}"
                log.warning("[!] page %d attempt %d: %s", page_num, attempt,
                            last_error)
        except RuntimeError as exc:
            # The visitor handshake (or the rebuilt browser after a rotation)
            # could not reach passport. That is this page failing, and it
            # goes through the retry budget like any transport fault; it
            # used to escape to main() as exit 1 and lose earlier pages.
            last_error = f"session: {_mask(exc)[:160]}"
            log.warning("[!] page %d attempt %d: %s", page_num, attempt,
                        last_error)

        if attempt < attempts:
            await asyncio.sleep(max(0.0, float(args.retry_delay)))

    return PageOutcome(page_num, url, state="unknown", error=last_error)


async def _recover_long_text(session: _BrowserSession, args,
                             rows: List[Post]) -> Tuple[int, int]:
    """Fetch the untruncated body for every row the site flagged.

    Not optional: `text_raw` on a flagged post is cut off at roughly 150
    characters with nothing a consumer would notice, and recovered bodies
    measured on 2026-09-21 ran to 1,034 and 1,235 characters against inline
    text of 149 and 146.
    """
    flagged = [r for r in rows if r.text_truncated]
    if not flagged:
        return 0, 0
    recovered = 0
    false_flags = 0
    for row in flagged:
        if not row.sku:
            continue
        try:
            status, body = await _api_get(session, P.longtext_url(row.sku))
            state = page_flow.classify(body, status)
            if state in ("content", "empty"):
                # "empty" is the endpoint ANSWERING: {"ok": 1, "data": {}}
                # means there is no more text and the post was whole, which
                # is a fact about the post rather than a failed fetch. Only
                # a state meaning we could not ask counts as a failure.
                P.apply_longtext(row, P.parse_longtext(body), reached=True)
                if row.text_source == "longtext":
                    recovered += 1
                elif row.text_source == "inline":
                    false_flags += 1
            else:
                P.apply_longtext(row, None, reached=False)
        except (PuppeteerError, PuppeteerTimeout) as exc:
            log.warning("[!] long-text recovery failed for %s: %s",
                        row.sku, _mask(exc)[:120])
            P.apply_longtext(row, None, reached=False)
        if args.delay:
            await asyncio.sleep(float(args.delay) * 0.5)
    if false_flags:
        log.info("[i] %d flagged post(s) had nothing to recover — the site's "
                 "isLongText flag was a false positive, and those rows are "
                 "marked whole rather than truncated", false_flags)
    log.info("[+] long text recovered for %d of %d flagged post(s)",
             recovered, len(flagged))
    return recovered, len(flagged)


def _resolve_target(args) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """`(uid, mblogid, mid)` for the mode, or a usage error."""
    if args.mode == "hot":
        return None, None, None
    if not args.url:
        log.error(
            f"--mode {args.mode} needs --url. For `user`, an account: "
            "https://weibo.com/u/2803301701 (or the bare id). For `post`, a "
            "post: https://weibo.com/2803301701/RiPCAfklU")
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
        return uid, None, None
    mblogid, mid = P.post_id_from_url(args.url)
    if not (mblogid or mid):
        log.error(
            f"could not find a post id in {args.url!r}. Expected "
            "https://weibo.com/<uid>/<mblogid>, or /detail/<mid>.")
        raise SystemExit(2)
    return P.uid_from_url(args.url), mblogid, mid


async def _resolve_mid(session: _BrowserSession, mblogid: str
                       ) -> Tuple[Optional[str], str]:
    """`(mid, state)` for a base-62 post id, via /statuses/show.

    The state is returned with it so the caller can tell a refused
    or failed lookup from a post that does not exist.
    """
    try:
        status, body = await _api_get(session, P.SHOW_URL.format(mblogid=mblogid))
    except (PuppeteerError, PuppeteerTimeout, RuntimeError) as exc:
        log.warning("[!] /statuses/show failed: %s", _mask(exc)[:160])
        return None, "transport_error"
    state = page_flow.classify(body, status)
    if state != "content":
        return None, state
    payload = P._payload_dict(body)
    mid = (str(payload.get("mid")) if payload.get("mid")
           else str(payload.get("idstr")) if payload.get("idstr") else None)
    return mid, state


async def _scrape_async(args) -> int:
    # Checked FIRST, before a pool is built. It used to sit after
    # proxy_pool.from_args, so a run that passed both flags crashed
    # on the way to this message instead of reading it — a refusal
    # that exists in the source and cannot be reached is not a
    # refusal.
    if args.cdp_endpoint and (args.proxy or args.proxy_file):
        # Exit 2, the family's code for bad usage. A bare
        # `raise SystemExit("...")` exits 1, which is "crash" — and a caller
        # branching on the exit code would read a typo as a bug in here.
        log.error(
            "--proxy cannot be combined with --cdp-endpoint: the Scraping "
            "Browser already exits through its own network, and stacking a "
            "second proxy on it is a contradiction rather than better "
            "cover. Pick the exit with the endpoint's own `country-` "
            "segment.")
        raise SystemExit(2)

    pool = None
    if args.proxy_file or args.proxy:
        pool = proxy_pool.from_args(args)
        if pool and not len(pool):
            pool = None

    if args.concurrency > 1:
        log.warning("[!] --concurrency %d refused: neither the hot feed nor a "
                    "user's cursor has independent page addresses, so a "
                    "worker cannot be handed a page. Running with 1.",
                    args.concurrency)
        args.concurrency = 1
    limit = page_flow.concurrency_limit(args.cdp_endpoint)
    if limit:
        args.concurrency = min(args.concurrency, limit)

    rows: List[Post] = []
    seen: set = set()
    pages_completed = 0
    pages_failed: List[int] = []
    stop_reason = "completed"
    blocked = False
    extra: Dict[str, Any] = {}
    start_url = ""
    final_url = ""
    dedupe_key = "comment_id" if args.mode == "post" else "sku"

    pages = page_flow.pages_to_plan(args.pages, None)
    proxy_url = pool.current if pool else args.proxy

    session = _BrowserSession(args, proxy_url)
    try:
        await session.open()
    except RuntimeError as exc:
        log.error("[x] %s", _mask(exc))
        return EXIT_API_ERROR

    try:
        uid, mblogid, mid = _resolve_target(args)
        author_followers = None

        if args.mode == "user":
            try:
                status, body = await _api_get(session, P.profile_info_url(uid))
            except (PuppeteerError, PuppeteerTimeout, RuntimeError) as exc:
                # The profile only adds context columns. Its failure
                # must not end the run as a crash (exit 1) before the
                # feed itself has been asked for.
                log.warning("[!] profile request failed: %s",
                            _mask(exc)[:160])
                status, body = None, ""
            if page_flow.classify(body, status) == "content":
                prof = P.parse_profile(body)
                author_followers = prof.get("followers_count")
                extra["statuses_claimed"] = prof.get("statuses_count")
                extra["account"] = prof.get("screen_name")
                extra["followers_count"] = author_followers
            else:
                log.warning("[!] could not read the profile for %s — follower "
                            "counts will be null on every row", uid)

        unresolved = False
        if args.mode == "post" and not mid and mblogid:
            mid, show_state = await _resolve_mid(session, mblogid)
            if not mid:
                # Decided by what /statuses/show answered, through
                # finish_run: a wall is blocked (3), "no such post" is
                # a real empty answer (4), anything else means the
                # post was never reached (5). A bare exit 4 here used
                # to claim "no comments" for a lookup that failed.
                unresolved = True
                blocked, stop_reason = page_flow.unresolved_post_outcome(
                    show_state)
                start_url = final_url = P.SHOW_URL.format(mblogid=mblogid)
                log.error("[x] could not resolve %s to a numeric mid, "
                          "which is the id the comments endpoint takes "
                          "(/statuses/show answered %s)", mblogid,
                          show_state)

        cursor: Any = 0
        for page_num in range(1, (0 if unresolved else pages) + 1):
            if args.mode == "hot":
                url = P.hot_feed_url(count=25, group=args.category)
            elif args.mode == "user":
                url = P.user_feed_url(uid, cursor)
            else:
                url = P.comments_url(mid, count=20, max_id=cursor or 0)

            if page_num == 1:
                start_url = url
            final_url = url

            outcome = await _fetch_one(session, args, pool, page_num, url,
                                       parent_sku=mblogid,
                                       author_followers=author_followers)

            if outcome.error and not outcome.rows:
                pages_failed.append(page_num)
                if page_flow.counts_as_blocked(outcome.state):
                    blocked = True
                    stop_reason = outcome.state
                    break
                stop_reason = f"error_on_page_{page_num}"
                break

            if page_flow.served_rows_we_could_not_read(outcome.state,
                                                       outcome.rows):
                # The site served rows and the parser read none: the
                # response shape changed. Not an empty feed (exit 4),
                # and not a complete run either.
                pages_failed.append(page_num)
                stop_reason = page_flow.PARSER_FAILURE_REASON
                log.error("[x] page %d was served with rows in it and the "
                          "parser read none of them — %s", page_num,
                          stop_reason)
                break

            fresh = dedupe_by_key(outcome.rows, seen, key=dedupe_key)
            rows.extend(fresh)
            pages_completed += 1
            unit = "fetch" if args.mode == "hot" else "page"
            log.info("[+] %s %d: %d row(s), %d new (total %d)",
                     unit, page_num, len(outcome.rows), len(fresh), len(rows))

            if not outcome.rows:
                stop_reason = "no_new_products"
                break

            if args.mode == "hot":
                if page_num >= pages:
                    stop_reason = "feed_not_addressable"
                extra["feed_rerolled"] = True
            elif args.mode == "user":
                cursor = outcome.next_cursor
                if cursor in (None, -1, 0, "-1", "0"):
                    stop_reason = "cursor_exhausted"
                    break
            else:
                cursor = outcome.next_cursor
                if cursor in (None, 0, "0"):
                    stop_reason = ("single_page_mode" if pages == 1
                                   else "cursor_exhausted")
                    break

            if not fresh:
                stop_reason = "no_new_products"
                break

            if args.delay and page_num < pages:
                await asyncio.sleep(float(args.delay) +
                                    random.uniform(0, float(args.delay) * 0.25))
        else:
            if args.mode == "hot":
                stop_reason = "feed_not_addressable"

        if args.mode in ("hot", "user") and rows:
            recovered, attempted = await _recover_long_text(session, args, rows)
            extra["long_text_flagged"] = attempted
            extra["long_text_recovered"] = recovered

    finally:
        await session.close()

    if rows:
        still = sum(1 for r in rows if r.text_truncated)
        if still:
            log.warning("[!] %d of %d row(s) still hold TRUNCATED text — see "
                        "the text_truncated column before using `title`",
                        still, len(rows))
        extra["text_truncated_rows"] = still

    return finish_run(
        rows, args.out, args.format, args.allow_empty, blocked=blocked,
        stop_reason=stop_reason, pages_requested=pages,
        pages_completed=pages_completed, pages_failed=pages_failed,
        start_url=start_url, final_url=final_url, mode=args.mode,
        source=P.SOURCE, extra=extra)


def scrape(args) -> int:
    """Sync entry point, so all three engines present one shape."""
    return asyncio.get_event_loop().run_until_complete(_scrape_async(args))


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Scrape Weibo's hot feed, one account's posts, or one "
                    "post's comments, through pyppeteer.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)

    p.add_argument("--mode", choices=MODES, default="hot",
                   help="hot: the public hot feed. user: one account's posts "
                        "(--url an account). post: one post's comments "
                        "(--url a post).")
    p.add_argument("--url", default=None,
                   help="An account or post URL, or a bare id. Required for "
                        "--mode user and --mode post.")
    p.add_argument("--category", default=DEFAULT_FEED_GROUP,
                   help="The hot feed's group id. Default 102803 (推荐), the "
                        "ONLY value verified.")
    p.add_argument("--pages", type=env_config.positive_int, default=1,
                   help=f"How many fetches to make (max {P.MAX_PAGES}).")
    p.add_argument("--format", choices=["json", "csv", "both"], default="json")
    p.add_argument("--out", default="weibo_posts")
    p.add_argument("--delay", type=env_config.non_negative_float, default=1.0)
    p.add_argument("--retries", type=env_config.non_negative_int, default=2)
    p.add_argument("--retry-delay", type=env_config.non_negative_float, default=3.0)
    p.add_argument("--concurrency", type=env_config.positive_int, default=1,
                   help="Refused above 1: neither feed has independent page "
                        "addresses, so a worker cannot be handed a page.")

    p.add_argument("--proxy", default=None)
    p.add_argument("--proxy-file", default=None)
    # The choices are taken FROM proxy_pool rather than written out here.
    # They were written out, as ["per-page", "per-run", "on-block"] with
    # "on-block" as the default — and proxy_pool.ROTATE_MODES has only
    # ("per-run", "per-page"), so every run that passed a proxy died on
    # `ProxyError: rotate must be one of ...` before fetching anything.
    # When a shared module documents a constraint, grep the
    # callers for values that violate it. Reading the constant instead
    # makes that impossible to get wrong again.
    p.add_argument("--proxy-rotate", choices=list(proxy_pool.ROTATE_MODES),
                   default="per-run",
                   help="When to move to the next exit. Rotation on a proxy "
                        "FAILURE happens under either mode — the engine "
                        "calls advance() when an exit dies.")
    p.add_argument("--proxy-shuffle", action="store_true")
    p.add_argument("--proxy-block-retries", type=env_config.non_negative_int,
                   default=page_flow.BLOCK_RETRIES_WITHOUT_POOL)

    p.add_argument("--twocaptcha-key", default=None)
    p.add_argument("--captcha-api", choices=["v1", "v2"], default="v2")
    p.add_argument("--solve-captcha", choices=["never", "when-blocked", "always"],
                   default="when-blocked",
                   help="No challenge was observed on any route this repo "
                        "reads, so this is expected never to fire.")
    p.add_argument("--min-score", type=float, default=0.3)

    p.add_argument("--cdp-endpoint", default=None,
                   help="A Scraping Browser API WebSocket endpoint. Cannot be "
                        "combined with --proxy.")
    p.add_argument("--fingerprint", action="store_true",
                   help="Not applied by this engine — see the README's engine "
                        "table. pyppeteer has no context-level fingerprint "
                        "surface; use the Playwright engine.")
    p.add_argument("--fp-country", default=None)
    p.add_argument("--fp-tags", default="Windows",
                   help="ONE tag, from a short closed list. Measured "
                        "against the live API 2026-09-21: Windows, "
                        "Microsoft Windows, Linux and Android are accepted "
                        "(case-insensitively); EVERYTHING else tested "
                        "answers HTTP 400 — including macOS, iOS, Mac OS X, "
                        "Chrome OS, Ubuntu, every browser name, every "
                        "form-factor name, and any list such as "
                        "'Windows,Chrome,Desktop'. There is no Apple "
                        "platform in the set.")
    p.add_argument("--locale", default=None)

    p.add_argument("--allow-empty", action="store_true")
    p.add_argument("--dump-html", action="store_true",
                   help="Write the exact response bytes, on success too.")
    headless = p.add_mutually_exclusive_group()
    headless.add_argument("--headless", dest="headless", action="store_true",
                          default=True)
    headless.add_argument("--headful", dest="headless", action="store_false")

    args = p.parse_args(argv)
    env_config.apply(args)
    if args.pages < 1:
        p.error("--pages must be at least 1")
    if args.pages > P.MAX_PAGES:
        p.error(f"--pages above {P.MAX_PAGES} is refused")
    if args.concurrency > 1:
        # Refused, not warned and then run with 1: neither feed this repo
        # reads has independent page addresses (page N's cursor does not
        # exist until page N-1 arrives), so a worker cannot be handed a page.
        p.error("--concurrency above 1 is refused: neither the hot feed nor "
                "a cursor has independent page addresses")
    if not str(args.category or "").isdigit():
        p.error(f"--category must be a numeric feed group id, got "
                f"{args.category!r}")
    if args.fingerprint:
        p.error("--fingerprint is not applied by this engine; use "
                "playwright_scraper.py, which applies it to the browser context")
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
        log.error("[x] %s", _mask(exc))
        if os.environ.get("WEIBO_TRACEBACK"):
            raise
        return 1


if __name__ == "__main__":
    sys.exit(main())
