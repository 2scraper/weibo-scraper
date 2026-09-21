#!/usr/bin/env python3
"""
playwright_scraper.py — the primary engine.

Reads Weibo's own JSON API through a real browser, in three modes:

    --mode hot                    the public hot feed (推荐)
    --mode user  --url <account>  one account's posts, by cursor
    --mode post  --url <post>     one post's comments

    python3 playwright_scraper.py --mode hot --pages 3 --out weibo_posts
    python3 playwright_scraper.py --mode user --url https://weibo.com/u/2803301701 --pages 5
    python3 playwright_scraper.py --mode post --url https://weibo.com/2803301701/RiPCAfklU

Why a browser at all, when the data is JSON
--------------------------------------------
Honest answer, and it belongs at the top rather than buried in the README:
for these three modes you do NOT need one. `requests` plus the visitor
handshake reads every route this engine reads, from a datacenter address,
with no key and no proxy — measured 2026-09-21, 100 requests in 83 seconds
with zero refusals. `weibo_api.WeiboClient` is that path and the smoke
suite exercises it.

The browser earns its place in three narrower cases, and the README says
so plainly rather than implying the paid path is required:

  * the Scraping Browser API (`--cdp-endpoint`) puts the run on a
    residential exit with a persistent profile, which is what you want at
    volume rather than for a first run;
  * a challenge, if Weibo ever renders one on these routes, needs a real
    browser to solve in (none was observed — see product_parser's marker
    table);
  * a fingerprint (`--fingerprint`) applies to a browser and to nothing
    else.

There is no geographic reason to use one. Weibo did not geo-block this
code at any point during development, and no Chinese exit was used.

The 403 that is not a refusal
------------------------------
Navigating a browser STRAIGHT to one of these API URLs answers
`403 {"error":"Forbidden"}` — 21 bytes, no explanation — while the exact
same URL, with the same cookies, returns 188 KB of posts when it is
fetched the way the site's own front end fetches it. Weibo declines a
TOP-LEVEL NAVIGATION to its own API and serves the same request made as an
XHR.

Measured 2026-09-21, one session, three ways, same URL:

    page.goto(api_url)                       403, 21 bytes
    context.request.get(api_url, headers)    200, 188,547 bytes, 10 posts
    fetch() from a loaded weibo.com page     200, 272,917 bytes, 10 posts

So this engine navigates ONCE to `https://weibo.com/` — which is what the
front end is, and what gives a challenge somewhere to render if one ever
appears — and then reads every payload through the context's own request
API. That path carries the context's cookies and its proxy, and it uses no
JavaScript at all, so it cannot trip over a Content-Security-Policy the
way an evaluated string can (CLAUDE.md §18).

Worth knowing before "fixing" a 403 here: it is not a block, not a rate
limit and not geography. It is the wrong KIND of request.

Client-side redirect note
--------------------------
Every HTML route on weibo.com bounces a cookie-less session to
`passport.weibo.com/visitor/visitor`. That is the anonymous-visitor
handshake, NOT a refusal — this engine performs the handshake up front and
installs the resulting cookies before navigating, so it never meets the
bounce. A run that suddenly starts seeing it has lost its cookies, which
`page_flow` reports as `needs_visitor` and this engine fixes by minting
once more.
"""

import argparse
import json
import logging
import os
import random
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from queue import Queue, Empty
from typing import Any, Dict, List, Optional, Tuple

# Imported at MODULE level on purpose. CLAUDE.md §10: an engine that
# imports its driver inside the launch path imports cleanly with no driver
# installed, so the suite's "skipped, engine absent" group never fires and
# the CI job that exists to catch a broken import cannot. The suite asserts
# this import is at module level with an `ast` walk.
from playwright.sync_api import sync_playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

import page_flow
import product_parser as P
import weibo_api as W
import env_config
from output_writer import (EXIT_API_ERROR, EXIT_BLOCKED, EXIT_NO_PRODUCTS,
                           Post, dedupe_by_key, finish_run)
import proxy_pool
from proxy_pool import ProxyPool

log = logging.getLogger("weibo.playwright")

# --------------------------------------------------------------------------
# Site constants — kept byte-identical across the three engines (§5)
# --------------------------------------------------------------------------

# How many post records mean "this response is a feed". See page_flow.
MIN_CARD_MATCHES = page_flow.MIN_CARD_MATCHES

# Weibo publishes no "next page" link anywhere this repo reads: the hot
# feed is not addressable at all and the user feed hands back a cursor in
# its payload. So there is no NEXT_PAGE_SELECTOR, and inventing one would
# be the §7 failure — pagination resting on a selector that can quietly die
# and report a complete run holding one page. Pagination here is layer 3
# only: a terminating condition based on DATA.
NEXT_PAGE_SELECTOR = None

# The hot feed's group. 102803 is 推荐 ("recommended"), which is what
# weibo.com's own front page requests. Other group ids exist and NONE of
# them was verified, so --category accepts one and the help says exactly
# that rather than implying a menu that was tested.
DEFAULT_FEED_GROUP = "102803"

# The page a session lands on before it reads anything. Not decoration:
# Weibo answers 403 to a top-level navigation to its own API (see the
# module docstring), so the run holds a real page open and fetches the
# payloads from that context.
LANDING_URL = "https://weibo.com/"

# Headers the site's own XHRs carry. `X-Requested-With` and the referer are
# what separate an accepted request from the 403 above.
API_HEADERS = {
    "Referer": LANDING_URL,
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/plain, */*",
}

MODES = ("hot", "user", "post")

# How long to let a navigation take before calling it a timeout.
NAV_TIMEOUT_MS = page_flow.CONTENT_TIMEOUT_MS


def _chrome_ua(chromium_version: str) -> str:
    """Build a UA from the Chromium that is actually installed.

    CLAUDE.md §8: the user agent comes from the browser, not a literal. A
    hardcoded version drifts from whatever is installed, and claiming an
    older Chrome than the JS engine and TLS handshake report is itself a
    mismatch worth more than the string is.
    """
    major = (chromium_version or "").split(".")[0] or "140"
    return ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{major}.0.0.0 Safari/537.36")


@dataclass
class PageOutcome:
    """What one fetch produced, so a worker can report without ordering."""
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
    """Mask credentials anywhere in a message. See weibo_api.mask_secrets."""
    return W.mask_secrets(str(text))


# --------------------------------------------------------------------------
# Proxy handling
# --------------------------------------------------------------------------

_PROXY_ERRORS = (
    "ERR_PROXY_CONNECTION_FAILED", "ERR_TUNNEL_CONNECTION_FAILED",
    "ERR_PROXY_AUTH_UNSUPPORTED", "ERR_PROXY_AUTH_REQUESTED",
    "ERR_UNEXPECTED_PROXY_AUTH", "ERR_SOCKS_CONNECTION_FAILED",
)


def _proxy_failure(exc: Exception) -> str:
    """Name the proxy error in an exception, or "".

    Its own function because CLAUDE.md §8 records what conflating this with
    a timeout costs: Chromium reports a dead proxy as a GENERIC error, not
    as a timeout, and the two want opposite responses — a timeout deserves
    another try at the same exit, a dead proxy deserves a different one.
    Catching only the timeout type let this escape as a traceback.
    """
    text = str(exc or "")
    for marker in _PROXY_ERRORS:
        if marker in text:
            return marker
    return ""


# --------------------------------------------------------------------------
# Browser session
# --------------------------------------------------------------------------

class _BrowserSession:
    """One browser, one context, one exit — for this worker's lifetime.

    A worker owns its browser because Playwright's sync API ties a browser
    to its creating thread (§7), and it owns ONE exit because a rotation is
    a fresh browser: cookies a bot manager issued against exit A and
    replayed from exit B are a stronger signal than either address alone
    (§8). Rotating means tearing this down and building another.
    """

    def __init__(self, pw, args, proxy_url: Optional[str]):
        self.args = args
        self.proxy_url = proxy_url
        self.browser = None
        self.context = None
        self.page = None
        self.user_agent = W.DEFAULT_UA
        self.remote = bool(args.cdp_endpoint)
        # Kept so a rotation can rebuild: Playwright's sync API
        # ties a browser to its creating thread, so the session
        # must reopen through the same driver object.
        self._pw = pw
        self._open(pw)

    def _open(self, pw):
        args = self.args
        if args.cdp_endpoint:
            # Never set a UA, a fingerprint or a proxy over a remote
            # endpoint: the remote browser brings its own, and stacking a
            # second creates a contradiction rather than better cover (§8).
            try:
                self.browser = pw.chromium.connect_over_cdp(
                    args.cdp_endpoint, timeout=NAV_TIMEOUT_MS)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"could not connect to the Scraping Browser endpoint: "
                    f"{_mask(exc)}") from None
            self.context = (self.browser.contexts[0] if self.browser.contexts
                            else self.browser.new_context())
        else:
            launch: Dict[str, Any] = {"headless": args.headless}
            # proxy_pool.to_playwright puts credentials in
            # Playwright's own fields, never in `server` — which
            # becomes a Chromium command line and is readable by
            # anything that can run `ps` (CLAUDE.md §8). This was
            # hand-rolled here until the shared module turned out
            # to already do it, correctly (§16).
            proxy = proxy_pool.to_playwright(self.proxy_url)
            if proxy:
                launch["proxy"] = proxy
            self.browser = pw.chromium.launch(**launch)
            self.user_agent = _chrome_ua(self.browser.version)
            ctx: Dict[str, Any] = {"user_agent": self.user_agent}
            if args.locale:
                ctx["locale"] = args.locale
            if args.fingerprint:
                ctx.update(self._fingerprint_kwargs())
            self.context = self.browser.new_context(**ctx)

        self.context.set_default_timeout(NAV_TIMEOUT_MS)
        self.page = self.context.new_page()
        self._install_visitor_cookies()
        self._land()

    def _land(self) -> None:
        """Open the site itself once, before any payload is read.

        Gives the run a real page — which is where a challenge would render
        if Weibo ever put one on these routes — and makes every later
        request an XHR from a weibo.com document rather than a navigation
        to an API, which is the difference between 200 and 403 here.
        """
        try:
            self.page.goto(LANDING_URL, wait_until="domcontentloaded",
                           timeout=NAV_TIMEOUT_MS)
        except (PlaywrightError, PlaywrightTimeout) as exc:
            log.warning("[!] could not open %s (%s) — continuing; the API "
                        "requests do not strictly need the page open",
                        LANDING_URL, _mask(exc)[:120])


    def rotate_to(self, proxy_url):
        """Tear this session down and build another on a different exit.

        A rotation is a FRESH BROWSER, not a swapped proxy (CLAUDE.md §8):
        cookies a bot manager issued against exit A and replayed from exit B
        are a stronger signal than either address alone. So the visitor
        cookie is re-minted through the new exit too, rather than carried
        across.
        """
        log.info("[i] rotating to a new exit and rebuilding the browser")
        self.close()
        self.proxy_url = proxy_url
        self._open(self._pw)

    def _fingerprint_kwargs(self) -> Dict[str, Any]:
        """Context kwargs from a 2Captcha fingerprint, or {}.

        Delegates BOTH steps to fingerprint_client rather than reading the
        payload here, and that is not tidiness — it is CLAUDE.md §16 with a
        scar. The first version of this method hand-rolled the translation
        and read `fp["timezone"]` and `fp["locale"]`, neither of which
        exists: the API puts them in `fp["intl"]` as `timeZone` and
        `contentLocale`. So it silently applied NO timezone and NO locale,
        which is two of the exact four defects §16 lists as having lived for
        months in this family — re-introduced in new code, in a repo whose
        shared module had already fixed them.
        `playwright_context_kwargs` is the tested path; this calls it.

        Measured against the live API 2026-09-21 with `country="de"`:
        user_agent, viewport, screen, device_scale_factor, locale `de-DE`
        (not `en-DE`) and timezone_id `Europe/Berlin` all come back
        populated.

        Every key is one `new_context` accepts. An unknown key is a
        TypeError at launch, on the paid path, at runtime (§10).
        """
        try:
            import fingerprint_client as FP
            fp = FP.get_fingerprint(
                self.args.twocaptcha_key,
                tags=self.args.fp_tags,
                country=self.args.fp_country)
        except Exception as exc:  # noqa: BLE001
            # A fingerprint failure is a WARNING, never the end of a run:
            # the run's job is data, and it can still get it (§8).
            log.warning("[!] --fingerprint: %s — continuing with the "
                        "browser's own identity", _mask(exc))
            return {}
        kwargs = FP.playwright_context_kwargs(fp)
        ua = kwargs.get("user_agent")
        if ua:
            self.user_agent = ua
        log.info("[+] fingerprint %s (%s) applied: %s",
                 fp.get("id"), fp.get("country"), ", ".join(sorted(kwargs)))
        return kwargs

    def _install_visitor_cookies(self) -> None:
        """Give the session a visitor cookie.

        Two routes, because the right one depends on WHERE the browser is.

        LOCAL browser: mint over HTTP through the same proxy the browser
        uses, and install the jar. One POST and one GET, cheaper and more
        reliable than driving the handshake through a page.

        REMOTE browser (--cdp-endpoint): navigate it to weibo.com and let
        the SITE run its own handshake. Weibo bounces a cookie-less session
        to `passport.weibo.com/visitor/visitor`, which sets SUB in that
        browser, from that browser's address.

        That distinction is the whole point and it was got wrong first.
        The original code minted over HTTP for the remote case too, with no
        proxy — so the cookie was issued to this machine and replayed from
        the Scraping Browser's exit. Measured 2026-09-21 in one session:

            the remote browser leaves from  23.244.216.86  California, US,
                                            AS11776 Breezeline (residential)
            the HTTP handshake leaves from  65.108.17.126  Helsinki, FI,
                                            AS24940 Hetzner (datacenter)

        CLAUDE.md §8: cookies a bot manager issued against exit A and
        replayed from exit B are a stronger signal than either address
        alone. And it threw away what the Scraping Browser is bought for —
        the cookie IS the session identity, and it was being minted on the
        cheap address while the expensive one carried the requests.
        """
        if self.remote:
            self._mint_in_browser()
            return
        try:
            jar = W.mint_visitor_cookies(
                user_agent=self.user_agent,
                proxy=(None if self.remote else self.proxy_url))
        except W.TransportError as exc:
            raise RuntimeError(f"visitor handshake could not reach "
                               f"passport: {_mask(exc)}") from None
        except W.VisitorError as exc:
            log.warning("[!] visitor handshake refused (%s) — continuing "
                        "without a cookie; the feed will answer ok:-100 and "
                        "the run will report that honestly", _mask(exc))
            return
        self.context.add_cookies(W.cookies_for_browser(jar))
        log.info("[+] visitor cookies installed in the browser")


    def _mint_in_browser(self) -> None:
        """Let the site hand the REMOTE browser its own visitor cookie."""
        try:
            self.page.goto(LANDING_URL, wait_until="domcontentloaded",
                           timeout=NAV_TIMEOUT_MS)
            self.page.wait_for_timeout(4000)
        except Exception as exc:  # noqa: BLE001
            log.warning("[!] could not run the visitor handshake in the "
                        "remote browser: %s", _mask(exc)[:140])
            return
        names = {c.get("name") for c in self.context.cookies()}
        if "SUB" in names:
            log.info("[+] visitor cookie minted BY the remote browser, from "
                     "its own exit (%d cookies)", len(names))
        else:
            log.warning("[!] the remote browser finished the handshake with "
                        "no SUB cookie — cookies present: %s", sorted(names))

    def remint_visitor(self) -> None:
        self._install_visitor_cookies()

    def close(self) -> None:
        for obj in (self.context, self.browser):
            try:
                if obj:
                    obj.close()
            except Exception:  # noqa: BLE001 — teardown must not mask a result
                pass


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

def _body_text(page) -> str:
    """The response body, whatever Chromium wrapped it in.

    Navigating to a JSON endpoint gives Chromium's JSON viewer, so
    `page.content()` is markup with the payload inside a `<pre>`.
    `inner_text` on the body recovers the raw JSON; `content()` is the
    fallback and `page_flow._as_payload` can unwrap either.
    """
    try:
        text = page.inner_text("body")
        if text and text.strip().startswith(("{", "[")):
            return text
    except PlaywrightError:
        pass
    try:
        return page.content()
    except PlaywrightError:
        return ""


def handle_captcha_if_present(page, args, budget: page_flow.SolveBudget) -> bool:
    """Detect a challenge, solve it once, and inject the token.

    Written against captcha_solver's REAL API. The first version called a
    `solve_on_page` helper that the module has never defined — in all three
    engines, invisible to import, `--help`, `compileall` and 4,900 green
    offline checks, and it would have raised AttributeError at the exact
    moment a challenge first appeared. It surfaced only once a real API key
    arrived and the suite's signature-binding check was widened to cover
    this module (CLAUDE.md §16: the credential-gated paths are the ones
    nobody ran).

    `budget` is NOT optional and is shared with the other call site in the
    same attempt. §23: every engine in this family calls this twice per
    attempt and only the second call was ever counted, so
    `SOLVES_PER_PAGE = 1` enforced nothing.

    Expected never to fire on Weibo: no challenge was rendered on any route
    this repo reads. It is here because Weibo has two captcha vendors wired
    into its own page chrome, so one appearing later is a change in the
    site rather than an impossibility (§19).
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
        html = _body_text(page)
    except Exception:  # noqa: BLE001
        return False

    marker = P.detect_bot_challenge(html)
    if marker and marker not in P.CHALLENGE_MARKERS:
        # A login wall is not a challenge. Nothing is being tested, access
        # is being declined, and paying for a request the API will reject
        # is worse than reporting the page unsolved (CLAUDE.md §19).
        log.info("[i] %r is a login wall, not a challenge — no solve "
                 "attempted (this repo does not implement account login)",
                 marker)
        return False

    import captcha_solver as CS

    # BOTH detectors, then reconcile — never short-circuit on the first
    # (CLAUDE.md §8). They can disagree about the same page, and the live
    # one sees a widget the markup only hints at.
    static_hit = CS.detect_recaptcha_v3(html, page_url)
    runtime_hit = None
    try:
        discovered = page.evaluate(CS.RECAPTCHA_DISCOVERY_JS)
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
        page.evaluate(CS.INJECT_TOKEN_JS, token)
    except Exception as exc:  # noqa: BLE001
        log.warning("[!] the token was bought but could not be injected: %s",
                    _mask(exc))
        return False
    log.info("[+] challenge solved and the token injected (%d chars)",
             len(token))
    return True

def _snapshot(body: str, url: str, args, page_num: int, state: str) -> None:
    """Write the exact bytes this run saw, on success too.

    CLAUDE.md §9: `--dump-html` writes on success as well as failure,
    because a run can return the right COUNT with a field silently
    unpopulated, and then the bytes are the only way to tell a parsing bug
    from a too-early snapshot.

    Takes the BODY rather than the page: the payload never reaches a
    document here (see _api_get), so dumping `page.content()` would write
    the landing page on every call — a snapshot that looks like evidence
    and is not.
    """
    if not args.dump_html:
        return
    path = f"{args.out}_debug.page{page_num}.{state}.json"
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        log.info("[+] wrote %s (%d bytes)", path, len(body))
    except OSError as exc:
        log.warning("[!] could not write %s: %s", path, exc)


def _api_get(session: _BrowserSession, url: str) -> Tuple[Optional[int], str]:
    """Fetch one API URL from the browser context; return `(status, body)`.

    Uses the context's own request API rather than a navigation, for the
    reason in the module docstring — a navigation to these URLs is refused
    with a 403 that looks exactly like a block and is not one. It carries
    the context's cookies and proxy, and it runs no JavaScript, so it is
    also immune to the CSP trap that killed a run in a sibling repo (§18).

    The status is RETURNED rather than discarded, and passed positionally
    into `classify`. A discarded status turns a 403 into a retryable parse
    error, which is a failure mode this family has hit before.
    """
    resp = session.context.request.get(url, headers=API_HEADERS,
                                       timeout=NAV_TIMEOUT_MS)
    return resp.status, resp.text()


def _fetch_one(session: _BrowserSession, args, pool: Optional[ProxyPool],
               page_num: int, url: str,
               parent_sku: Optional[str] = None,
               author_followers: Optional[int] = None) -> PageOutcome:
    """Fetch one API URL, with retries, and parse it.

    The retry budget is the USER's, for transient faults. A state the
    policy marks non-retryable (a login wall) does not consume it — the
    engine reports and stops rather than spending it proving the same thing
    twice.
    """
    attempts = max(1, int(args.retries) + 1)
    last_error: Optional[str] = None
    blocked_tries = 0

    for attempt in range(1, attempts + 1):
        budget = page_flow.SolveBudget()
        try:
            # First call site: clear a challenge BEFORE judging the page,
            # so a challenge that gates the content does not get classified
            # as the content's absence.
            handle_captcha_if_present(session.page, args, budget)
            status, body = _api_get(session, url)
            state = page_flow.classify(body, status, url)

            if page_flow.needs_visitor_cookie(state):
                log.info("[i] page %d: %s — re-minting the visitor cookie",
                         page_num, state)
                session.remint_visitor()
                status, body = _api_get(session, url)
                state = page_flow.classify(body, status, url)

            if page_flow.should_solve(state):
                # Second call site: the page really is gated. Same budget.
                if handle_captcha_if_present(session.page, args, budget):
                    status, body = _api_get(session, url)
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
                # neither is prose pretending to be enforcement (§17).
                if not (page_flow.RETRY_ON_BLOCKED
                        and page_flow.should_retry(state)):
                    log.error("[x] page %d: %s — this wants an ACCOUNT, not a "
                              "different address or another attempt. Not "
                              "retrying.", page_num, state)
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

        except PlaywrightTimeout as exc:
            last_error = f"timeout: {_mask(exc)[:160]}"
            log.warning("[!] page %d attempt %d: %s", page_num, attempt,
                        last_error)
        except PlaywrightError as exc:
            which = _proxy_failure(exc)
            if which:
                last_error = f"proxy: {which}"
                log.warning("[!] page %d attempt %d: the exit failed (%s)",
                            page_num, attempt, which)
                if pool and len(pool) > 1:
                    # A rotation is a FRESH BROWSER (CLAUDE.md §8): cookies
                    # a bot manager issued against exit A and replayed from
                    # exit B are a stronger signal than either address
                    # alone, so the session is torn down and rebuilt rather
                    # than having the proxy swapped under it.
                    session.rotate_to(pool.advance("proxy failure"))
            else:
                last_error = f"browser: {_mask(exc)[:160]}"
                log.warning("[!] page %d attempt %d: %s", page_num, attempt,
                            last_error)

        if attempt < attempts:
            time.sleep(max(0.0, float(args.retry_delay)))

    return PageOutcome(page_num, url, state="unknown", error=last_error)


# --------------------------------------------------------------------------
# Long-text recovery
# --------------------------------------------------------------------------

def _recover_long_text(session: _BrowserSession, args,
                       rows: List[Post]) -> Tuple[int, int]:
    """Fetch the untruncated body for every row the site flagged.

    Returns `(recovered, attempted)`. This is not an optimisation and it is
    not optional: `text_raw` on a flagged post is cut off at roughly 150
    characters with no marker a consumer would notice, and the recovered
    bodies measured on 2026-09-21 were 1,034 and 1,235 characters against
    inline text of 149 and 146. Skipping this ships a `title` column that
    is fully populated and mostly missing.

    A failure leaves the row marked `longtext_failed` and `text_truncated`
    True rather than silently passing the short text off as whole.
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
            status, body = _api_get(session, P.longtext_url(row.sku))
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
        except (PlaywrightError, PlaywrightTimeout) as exc:
            log.warning("[!] long-text recovery failed for %s: %s",
                        row.sku, _mask(exc)[:120])
            P.apply_longtext(row, None, reached=False)
        if args.delay:
            time.sleep(float(args.delay) * 0.5)
    if false_flags:
        log.info("[i] %d flagged post(s) had nothing to recover — the site's "
                 "isLongText flag was a false positive, and those rows are "
                 "marked whole rather than truncated", false_flags)
    log.info("[+] long text recovered for %d of %d flagged post(s)",
             recovered, len(flagged))
    return recovered, len(flagged)


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------

def _resolve_target(args) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """`(uid, mblogid, mid)` for the mode, or a usage error.

    Reads the URL the user gave rather than making them know which of
    Weibo's three ids a mode needs. `--mode post` is the awkward one: the
    comments endpoint wants the numeric `mid` while a post URL carries the
    base-62 `mblogid`, so one extra request resolves it.
    """
    if args.mode == "hot":
        return None, None, None
    if not args.url:
        raise SystemExit(
            f"--mode {args.mode} needs --url. For `user`, an account: "
            "https://weibo.com/u/2803301701 (or the bare id). For `post`, a "
            "post: https://weibo.com/2803301701/RiPCAfklU")
    ok, why = P.is_supported_host(args.url) if "//" in args.url else (True, "")
    if not ok:
        raise SystemExit(f"refusing {args.url}: {why}")
    if args.mode == "user":
        uid = P.uid_from_url(args.url)
        if not uid:
            raise SystemExit(
                f"could not find an account id in {args.url!r}. Expected "
                "https://weibo.com/u/<digits>, or the bare id.")
        return uid, None, None
    mblogid, mid = P.post_id_from_url(args.url)
    if not (mblogid or mid):
        raise SystemExit(
            f"could not find a post id in {args.url!r}. Expected "
            "https://weibo.com/<uid>/<mblogid>, or /detail/<mid>.")
    return P.uid_from_url(args.url), mblogid, mid


def _resolve_mid(session: _BrowserSession, mblogid: str) -> Optional[str]:
    """The numeric `mid` for a base-62 post id, via /statuses/show."""
    status, body = _api_get(session, P.SHOW_URL.format(mblogid=mblogid))
    if page_flow.classify(body, status) != "content":
        return None
    payload = P._payload_dict(body)
    return (str(payload.get("mid")) if payload.get("mid")
            else str(payload.get("idstr")) if payload.get("idstr") else None)


def scrape(args) -> int:
    # Checked FIRST, before a pool is built. It used to sit after
    # proxy_pool.from_args, so a run that passed both flags crashed
    # on the way to this message instead of reading it — a refusal
    # that exists in the source and cannot be reached is not a
    # refusal (CLAUDE.md §17).
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
        # Refused rather than warned, and the reason is the mode's: neither
        # feed this repo reads is addressable, so there is no page 5 to
        # hand a second worker — page 5's cursor does not exist until page
        # 4 arrives (§7, §18). Warning and then running would produce N
        # workers all re-fetching page 1.
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

    with sync_playwright() as pw:
        try:
            session = _BrowserSession(pw, args, proxy_url)
        except RuntimeError as exc:
            log.error("[x] %s", _mask(exc))
            return EXIT_API_ERROR

        try:
            uid, mblogid, mid = _resolve_target(args)
            author_followers = None

            if args.mode == "user":
                status, body = _api_get(session, P.profile_info_url(uid))
                if page_flow.classify(body, status) == "content":
                    prof = P.parse_profile(body)
                    author_followers = prof.get("followers_count")
                    # The site's own count for this account, so a file
                    # holding 5 rows against 21,947 posts says so (§21:
                    # "complete" and "exhaustive" are different words).
                    extra["statuses_claimed"] = prof.get("statuses_count")
                    extra["account"] = prof.get("screen_name")
                    extra["followers_count"] = author_followers
                else:
                    log.warning("[!] could not read the profile for %s — "
                                "follower counts will be null on every row",
                                uid)

            if args.mode == "post" and not mid and mblogid:
                mid = _resolve_mid(session, mblogid)
                if not mid:
                    log.error("[x] could not resolve %s to a numeric mid, "
                              "which is the id the comments endpoint takes",
                              mblogid)
                    return EXIT_NO_PRODUCTS

            cursor: Any = 0
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

                outcome = _fetch_one(session, args, pool, page_num, url,
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

                fresh = dedupe_by_key(outcome.rows, seen, key=dedupe_key)
                # Merged in PAGE order, because the loop is sequential and
                # stays that way: dedupe that mutates a running set inside a
                # concurrent loop makes the output depend on which page
                # finished first (§8).
                rows.extend(fresh)
                pages_completed += 1
                log.info("[+] page %d: %d row(s), %d new (total %d)",
                         page_num, len(outcome.rows), len(fresh), len(rows))

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
                    time.sleep(float(args.delay) +
                               random.uniform(0, float(args.delay) * 0.25))
            else:
                if args.mode == "hot":
                    stop_reason = "feed_not_addressable"

            if args.mode in ("hot", "user") and rows:
                recovered, attempted = _recover_long_text(session, args, rows)
                extra["long_text_flagged"] = attempted
                extra["long_text_recovered"] = recovered

        finally:
            session.close()

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


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Scrape Weibo's hot feed, one account's posts, or one "
                    "post's comments, through Playwright.",
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
                   help="The hot feed's group id. Default 102803 (推荐), "
                        "which is what weibo.com's own front page requests "
                        "and the ONLY value verified — others exist and were "
                        "not tested.")
    p.add_argument("--pages", type=int, default=1,
                   help=f"How many fetches to make (max {P.MAX_PAGES}). The "
                        "hot feed has no page 2: N there means N fetches of a "
                        "feed that re-rolls.")
    p.add_argument("--format", choices=["json", "csv", "both"], default="json")
    p.add_argument("--out", default="weibo_posts")
    p.add_argument("--delay", type=float, default=1.0)
    p.add_argument("--retries", type=int, default=2)
    p.add_argument("--retry-delay", type=float, default=3.0)
    p.add_argument("--concurrency", type=int, default=1,
                   help="Refused above 1: neither feed has independent page "
                        "addresses, so a worker cannot be handed a page.")

    p.add_argument("--proxy", default=None)
    p.add_argument("--proxy-file", default=None)
    # The choices are taken FROM proxy_pool rather than written out here.
    # They were written out, as ["per-page", "per-run", "on-block"] with
    # "on-block" as the default — and proxy_pool.ROTATE_MODES has only
    # ("per-run", "per-page"), so every run that passed a proxy died on
    # `ProxyError: rotate must be one of ...` before fetching anything.
    # CLAUDE.md §17: when a shared module documents a constraint, grep the
    # callers for values that violate it. Reading the constant instead
    # makes that impossible to get wrong again.
    p.add_argument("--proxy-rotate", choices=list(proxy_pool.ROTATE_MODES),
                   default="per-run",
                   help="When to move to the next exit. Rotation on a proxy "
                        "FAILURE happens under either mode — the engine "
                        "calls advance() when an exit dies.")
    p.add_argument("--proxy-shuffle", action="store_true")
    p.add_argument("--proxy-block-retries", type=int,
                   default=page_flow.BLOCK_RETRIES_WITHOUT_POOL)

    p.add_argument("--twocaptcha-key", default=None)
    p.add_argument("--captcha-api", choices=["v1", "v2"], default="v2")
    p.add_argument("--solve-captcha", choices=["never", "when-blocked", "always"],
                   default="when-blocked",
                   help="No challenge was observed on any route this repo "
                        "reads (5 captures, 0 widgets), so this is expected "
                        "never to fire. It is wired because Weibo HAS a "
                        "captcha configured on its login page, so one "
                        "appearing here later is a change in the site.")
    p.add_argument("--min-score", type=float, default=0.3)

    p.add_argument("--cdp-endpoint", default=None,
                   help="A Scraping Browser API WebSocket endpoint. Cannot be "
                        "combined with --proxy.")
    p.add_argument("--fingerprint", action="store_true")
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
