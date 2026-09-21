#!/usr/bin/env python3
"""
selenium_scraper.py — the Selenium engine.

A parity engine. It must behave identically to playwright_scraper.py: same
flags, same exit codes, same run status, same rows.

    python3 selenium_scraper.py --mode hot --pages 3 --out weibo_posts
    python3 selenium_scraper.py --mode user --url https://weibo.com/u/2803301701
    python3 selenium_scraper.py --mode post --url https://weibo.com/2803301701/RiPCAfklU

Two things this engine genuinely cannot do
-------------------------------------------
Stated here rather than left to be discovered, because both look like bugs
and neither is one (CLAUDE.md §6):

  * **`--cdp-endpoint` with credentials is refused.** Playwright's
    `connect_over_cdp` and pyppeteer's `browserWSEndpoint` both take a full
    `ws://user:pass@host:port` and authenticate on the WebSocket upgrade.
    chromedriver takes a bare `host:port` in `debuggerAddress` with nowhere
    to put a password. This is not a generic "cannot connect to CDP" — it
    is specifically that an AUTHENTICATED endpoint has no field. The
    Scraping Browser endpoint is authenticated, so this engine refuses it
    with that reason instead of failing obscurely later.

  * **`--proxy` credentials are stripped, with a warning.** Chromium's
    `--proxy-server` cannot carry them and Selenium has no equivalent of
    `page.authenticate`. Letting a user believe a `user:pass` URL is doing
    something is worse than saying it is not.

The 403 that is not a refusal
------------------------------
Navigating straight to one of Weibo's API URLs answers
`403 {"error":"Forbidden"}` while the same URL fetched as an XHR from a
loaded weibo.com page returns the payload. See playwright_scraper.py's
module docstring for the measurement.

So this engine lands on `https://weibo.com/` and fetches through
`execute_async_script`. Note the dialect, which is exactly why CLAUDE.md
§1 forbids sharing JavaScript between engines: Selenium takes a function
BODY with an explicit `return` and an injected callback, while pyppeteer
takes an arrow function. The same snippet in a shared module would work in
one and be a syntax error in the other.
"""

import argparse
import json
import logging
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Imported at MODULE level on purpose — see playwright_scraper.py (§10).
from selenium import webdriver
from selenium.common.exceptions import TimeoutException as SeleniumTimeout
from selenium.common.exceptions import WebDriverException as SeleniumError
from selenium.webdriver.chrome.options import Options as ChromeOptions

import env_config
import page_flow
import product_parser as P
import weibo_api as W
from output_writer import (EXIT_API_ERROR, EXIT_NO_PRODUCTS, Post,
                           dedupe_by_key, finish_run)
from proxy_pool import ProxyPool

log = logging.getLogger("weibo.selenium")

# --------------------------------------------------------------------------
# Site constants — byte-identical to the other two engines (§5)
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

# Selenium's dialect: a function BODY, with the driver's callback appended
# as the last argument. `return` here would return from the body, not from
# the fetch, which is why the result travels through `done(...)` instead.
_FETCH_JS = """
var url = arguments[0], headers = arguments[1], done = arguments[2];
fetch(url, {credentials: 'include', headers: headers})
  .then(function (r) {
      return r.text().then(function (b) { done({status: r.status, body: b}); });
  })
  .catch(function (e) { done({status: null, body: '' + e}); });
"""


def _chrome_ua(caps: Dict[str, Any]) -> str:
    """Build a UA from the Chrome that chromedriver actually started (§8)."""
    version = ""
    try:
        version = str((caps or {}).get("browserVersion")
                      or (caps or {}).get("version") or "")
    except Exception:  # noqa: BLE001
        pass
    major = version.split(".")[0] or "140"
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
    """Name the proxy error in an exception, or "" (§8)."""
    text = str(exc or "")
    for marker in _PROXY_ERRORS:
        if marker in text:
            return marker
    return ""


def strip_proxy_credentials(proxy_url: Optional[str]) -> Tuple[Optional[str], bool]:
    """`(server_without_credentials, had_credentials)`.

    Selenium cannot authenticate a proxy at all, so credentials are removed
    and the caller warns. Passing them through would put them on the
    browser's command line — readable by anything that can run `ps` (§8) —
    AND they would not work, which is the worst of both.
    """
    if not proxy_url:
        return None, False
    m = re.match(r"^(?P<scheme>\w+)://(?:(?P<user>[^:@/]+):(?P<pw>[^@/]*)@)?"
                 r"(?P<host>[^/@]+)/?$", proxy_url.strip())
    if not m:
        return proxy_url, False
    return f"{m.group('scheme')}://{m.group('host')}", bool(m.group("user"))


class _BrowserSession:
    """One driver, one exit — for this run's lifetime."""

    def __init__(self, args, proxy_url: Optional[str]):
        self.args = args
        self.proxy_url = proxy_url
        self.driver = None
        self.user_agent = W.DEFAULT_UA
        self.remote = bool(args.cdp_endpoint)
        self._open()

    def _open(self) -> None:
        args = self.args
        opts = ChromeOptions()

        if args.cdp_endpoint:
            endpoint = args.cdp_endpoint.strip()
            if "@" in endpoint:
                raise RuntimeError(
                    "Selenium cannot use an AUTHENTICATED CDP endpoint. "
                    "chromedriver's `debuggerAddress` takes a bare "
                    "host:port and has nowhere to put a password, while "
                    "Playwright and pyppeteer authenticate on the "
                    "WebSocket upgrade. Use playwright_scraper.py for the "
                    "Scraping Browser API, or pass an unauthenticated "
                    "host:port here.")
            opts.add_experimental_option(
                "debuggerAddress", endpoint.replace("ws://", "").replace(
                    "http://", ""))
        else:
            if args.headless:
                opts.add_argument("--headless=new")
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            server, had_creds = strip_proxy_credentials(self.proxy_url)
            if had_creds:
                log.warning("[!] --proxy carries credentials and Selenium "
                            "cannot authenticate a proxy. They have been "
                            "STRIPPED: this run exits through %s "
                            "unauthenticated, which most gateways refuse. "
                            "Use the Playwright or pyppeteer engine for an "
                            "authenticated proxy.", _mask(server))
            if server:
                opts.add_argument(f"--proxy-server={server}")
            if args.locale:
                opts.add_argument(f"--lang={args.locale}")

        try:
            self.driver = webdriver.Chrome(options=opts)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"could not start chromedriver: "
                               f"{_mask(exc)}") from None

        if not args.cdp_endpoint:
            self.user_agent = _chrome_ua(getattr(self.driver, "capabilities", {}))
            try:
                self.driver.execute_cdp_cmd(
                    "Network.setUserAgentOverride",
                    {"userAgent": self.user_agent})
            except Exception as exc:  # noqa: BLE001
                log.warning("[!] could not set the user agent: %s",
                            _mask(exc)[:100])

        self.driver.set_page_load_timeout(NAV_TIMEOUT_MS / 1000)
        self.driver.set_script_timeout(NAV_TIMEOUT_MS / 1000)
        self._land()
        self._install_visitor_cookies()

    def _land(self) -> None:
        """Open the site before setting cookies.

        Order matters here and does not in the other two engines: Selenium
        refuses `add_cookie` for a domain the driver is not currently on,
        so the landing navigation has to come FIRST. Playwright and
        pyppeteer accept a cookie with an explicit domain at any time.
        """
        try:
            self.driver.get(LANDING_URL)
        except (SeleniumError, SeleniumTimeout) as exc:
            log.warning("[!] could not open %s (%s) — continuing",
                        LANDING_URL, _mask(exc)[:120])

    def _install_visitor_cookies(self) -> None:
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
                self.driver.add_cookie(cookie)
            except Exception as exc:  # noqa: BLE001
                log.warning("[!] could not set cookie %s: %s",
                            cookie.get("name"), _mask(exc)[:80])
        try:
            self.driver.get(LANDING_URL)
        except (SeleniumError, SeleniumTimeout):
            pass
        log.info("[+] visitor cookies installed in the browser")

    def remint_visitor(self) -> None:
        self._install_visitor_cookies()

    def close(self) -> None:
        try:
            if self.driver:
                self.driver.quit()
        except Exception:  # noqa: BLE001 — teardown must not mask a result
            pass


def _api_get(session: _BrowserSession, url: str) -> Tuple[Optional[int], str]:
    """Fetch one API URL as an XHR from the loaded page.

    The status is RETURNED and passed positionally into `classify`. Note
    what makes that non-trivial here: a Selenium `driver.get` exposes NO
    status code at all, which is why the fetch runs in the page and hands
    the status back explicitly. A discarded status turns a 403 into a
    retryable parse error.
    """
    got = session.driver.execute_async_script(_FETCH_JS, url, API_HEADERS)
    if not isinstance(got, dict):
        return None, ""
    return got.get("status"), got.get("body") or ""


def handle_captcha_if_present(driver, args,
                              budget: page_flow.SolveBudget) -> bool:
    """Solve a challenge if one is present and the budget allows.

    `budget` is NOT optional and is shared with the other call site in the
    same attempt (CLAUDE.md §23 — see playwright_scraper.py).
    """
    if args.solve_captcha == "never":
        return False
    if not budget.may_solve():
        log.info("[i] solve budget for this page is spent (%r)", budget)
        return False
    try:
        html = driver.page_source
    except Exception:  # noqa: BLE001
        return False
    marker = P.detect_bot_challenge(html)
    if not marker:
        return False
    if marker not in P.CHALLENGE_MARKERS:
        log.info("[i] %r is a login wall, not a challenge — no solve "
                 "attempted (this repo does not implement account login)",
                 marker)
        return False
    if not args.twocaptcha_key:
        log.warning("[!] a challenge (%s) is present and no --twocaptcha-key "
                    "was given — continuing unsolved", marker)
        return False
    try:
        import captcha_solver
        budget.charge()
        solved = captcha_solver.solve_on_page(
            driver, api_key=args.twocaptcha_key, api_kind=args.captcha_api,
            min_score=args.min_score)
    except Exception as exc:  # noqa: BLE001
        log.warning("[!] captcha solve failed: %s — continuing", _mask(exc))
        return False
    if solved:
        log.info("[+] challenge solved")
    return bool(solved)


def _snapshot(body: str, url: str, args, page_num: int, state: str) -> None:
    """Write the exact bytes this run saw, on success too (§9)."""
    if not args.dump_html:
        return
    path = f"{args.out}_debug.page{page_num}.{state}.json"
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body or "")
        log.info("[+] wrote %s (%d bytes)", path, len(body or ""))
    except OSError as exc:
        log.warning("[!] could not write %s: %s", path, exc)


def _fetch_one(session: _BrowserSession, args, pool: Optional[ProxyPool],
               page_num: int, url: str,
               parent_sku: Optional[str] = None,
               author_followers: Optional[int] = None) -> PageOutcome:
    attempts = max(1, int(args.retries) + 1)
    last_error: Optional[str] = None
    blocked_tries = 0

    for attempt in range(1, attempts + 1):
        budget = page_flow.SolveBudget()
        try:
            handle_captcha_if_present(session.driver, args, budget)
            status, body = _api_get(session, url)
            state = page_flow.classify(body, status, url)

            if page_flow.needs_visitor_cookie(state):
                log.info("[i] page %d: %s — re-minting the visitor cookie",
                         page_num, state)
                session.remint_visitor()
                status, body = _api_get(session, url)
                state = page_flow.classify(body, status, url)

            if page_flow.should_solve(state):
                if handle_captcha_if_present(session.driver, args, budget):
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

        except SeleniumTimeout as exc:
            last_error = f"timeout: {_mask(exc)[:160]}"
            log.warning("[!] page %d attempt %d: %s", page_num, attempt, last_error)
        except SeleniumError as exc:
            which = _proxy_failure(exc)
            if which:
                last_error = f"proxy: {which}"
                log.warning("[!] page %d attempt %d: the exit failed (%s)",
                            page_num, attempt, which)
                if pool:
                    pool.mark_failed(session.proxy_url)
            else:
                last_error = f"browser: {_mask(exc)[:160]}"
                log.warning("[!] page %d attempt %d: %s", page_num, attempt,
                            last_error)

        if attempt < attempts:
            time.sleep(max(0.0, float(args.retry_delay)))

    return PageOutcome(page_num, url, state="unknown", error=last_error)


def _recover_long_text(session: _BrowserSession, args,
                       rows: List[Post]) -> Tuple[int, int]:
    """Fetch the untruncated body for every row the site flagged.

    Not optional — see playwright_scraper.py for the measurement.
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
        except (SeleniumError, SeleniumTimeout) as exc:
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


def _resolve_target(args) -> Tuple[Optional[str], Optional[str], Optional[str]]:
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
    status, body = _api_get(session, P.SHOW_URL.format(mblogid=mblogid))
    if page_flow.classify(body, status) != "content":
        return None
    payload = P._payload_dict(body)
    return (str(payload.get("mid")) if payload.get("mid")
            else str(payload.get("idstr")) if payload.get("idstr") else None)


def scrape(args) -> int:
    pool = None
    if args.proxy_file or args.proxy:
        pool = ProxyPool.from_args(args)
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

    if args.cdp_endpoint and (args.proxy or args.proxy_file):
        raise SystemExit(
            "--proxy cannot be combined with --cdp-endpoint: the Scraping "
            "Browser already exits through its own network, and stacking a "
            "second proxy on it is a contradiction rather than better cover. "
            "Pick the exit with the endpoint's own `country-` segment.")

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
    proxy_url = pool.next() if pool else args.proxy

    try:
        session = _BrowserSession(args, proxy_url)
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
                extra["statuses_claimed"] = prof.get("statuses_count")
                extra["account"] = prof.get("screen_name")
                extra["followers_count"] = author_followers
            else:
                log.warning("[!] could not read the profile for %s — follower "
                            "counts will be null on every row", uid)

        if args.mode == "post" and not mid and mblogid:
            mid = _resolve_mid(session, mblogid)
            if not mid:
                log.error("[x] could not resolve %s to a numeric mid, which "
                          "is the id the comments endpoint takes", mblogid)
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


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Scrape Weibo's hot feed, one account's posts, or one "
                    "post's comments, through Selenium.",
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
    p.add_argument("--pages", type=int, default=1,
                   help=f"How many fetches to make (max {P.MAX_PAGES}).")
    p.add_argument("--format", choices=["json", "csv", "both"], default="json")
    p.add_argument("--out", default="weibo_posts")
    p.add_argument("--delay", type=float, default=1.0)
    p.add_argument("--retries", type=int, default=2)
    p.add_argument("--retry-delay", type=float, default=3.0)
    p.add_argument("--concurrency", type=int, default=1,
                   help="Refused above 1: neither feed has independent page "
                        "addresses, so a worker cannot be handed a page.")

    p.add_argument("--proxy", default=None,
                   help="Credentials are STRIPPED with a warning: Selenium "
                        "cannot authenticate a proxy.")
    p.add_argument("--proxy-file", default=None)
    p.add_argument("--proxy-rotate", choices=["per-page", "per-run", "on-block"],
                   default="on-block")
    p.add_argument("--proxy-shuffle", action="store_true")
    p.add_argument("--proxy-block-retries", type=int,
                   default=page_flow.BLOCK_RETRIES_WITHOUT_POOL)

    p.add_argument("--twocaptcha-key", default=None)
    p.add_argument("--captcha-api", choices=["v1", "v2"], default="v2")
    p.add_argument("--solve-captcha", choices=["never", "when-blocked", "always"],
                   default="when-blocked",
                   help="No challenge was observed on any route this repo "
                        "reads, so this is expected never to fire.")
    p.add_argument("--min-score", type=float, default=0.3)

    p.add_argument("--cdp-endpoint", default=None,
                   help="Refused if it carries credentials: chromedriver's "
                        "debuggerAddress has nowhere to put a password.")
    p.add_argument("--fingerprint", action="store_true",
                   help="Not applied by this engine — see the README's engine "
                        "table.")
    p.add_argument("--fp-country", default=None)
    p.add_argument("--fp-tags", default="Windows",
                   help="ONE OS-family tag. The API rejects a list.")
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
