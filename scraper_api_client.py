#!/usr/bin/env python3
"""
weibo-scraper — 2captcha Scraper API edition (fourth engine)
============================================================

A fourth way to run this scraper, which on Weibo **does not reach the data**
— measured, not assumed. Read this before spending anything on it.

What was measured, 2026-09-21, with a live key
-----------------------------------------------
The Scraper API works perfectly well from here, and the bill proves the
call happened:

    https://weibo.com/                 upstream 200, 3,444 bytes, $0.0005
    an /ajax/ feed URL                 upstream 403,    21 bytes, $0.0005
    the same, + X-Requested-With and
      a Referer in `headers`           upstream 403,    21 bytes, $0.0005
    the same, via `requestHeaders`     upstream 403,    21 bytes, $0.0005

The 21 bytes are `{"error":"Forbidden"}` — the identical signature a browser
`page.goto()` gets. Weibo declines a top-level NAVIGATION to its own API and
serves the identical request made as an XHR, and this product fetches by
navigation. Custom headers did not change it.

That is a statement about how this fetch arrives, not about the product:
2captcha's Scraper API fetches the page it was asked for, and other repos in
this family use it successfully. On THESE URLs, on THIS site, it arrives in
the shape Weibo refuses.

What to use instead
-------------------
Nothing, in the sense that costs money. The same payloads come back over
plain HTTPS from an ordinary datacenter address, with a free visitor cookie
and no browser at all — 100 requests in 83 seconds, zero refusals. That is
`weibo_api.WeiboClient`, and `playwright_scraper.py` drives the same route
through a browser when you want one.

This file is kept rather than deleted so the measurement above has somewhere
to live, and so that a future change on either side (Weibo relaxing the
check, or the API growing an XHR-shaped fetch mode) has a client ready to
test it with. If you find a payload that works, please open an issue and
this docstring gets a better measurement.

Usage
-----
    python3 scraper_api_client.py --mode hot --out weibo_posts

    # the key comes from $TWOCAPTCHA_KEY and the endpoint from
    # $WEIBO_CDP_ENDPOINT, so neither needs to be typed — a secret in argv
    # is readable by anything that can run `ps`

Requires: pip install -r requirements.txt
          (no playwright/selenium/pyppeteer needed for this engine)
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from typing import Optional

import requests

import page_flow
import product_parser as P
from product_parser import (BOT_CHALLENGE_MARKERS,  # noqa: F401
                            detect_bot_challenge, detect_page_state)
from output_writer import finish_run
import env_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("scraper_api_client")

API_BASE = "https://scraper.2captcha.com"
SYNC_ENDPOINT = f"{API_BASE}/tasks/sync"

# The API caps `timeout` at 120s and rejects bodies over 10,000 bytes.
MAX_API_TIMEOUT = 120

# Exit codes. Kept distinct from 2 (bad usage) on purpose: a remote API
# failing is not the operator passing wrong arguments, and a harness that
# lumps them together sends you looking in the wrong place. An early run
# reported `exit=2` for an HTTP 422 from the API — which reads as "you called
# it wrong".
#
# Imported rather than redefined: the browser engines return the same code for
# a Scraping Browser that will not accept a connection, and two definitions
# of one exit code is how a family's contract drifts.
from output_writer import EXIT_API_ERROR  # noqa: E402

def _mask_credentials(url: str) -> str:
    """Never print a username:password embedded in a ws://... or http://... URL."""
    if "@" not in url:
        return url
    scheme_sep = url.find("://")
    if scheme_sep == -1:
        return url
    scheme, rest = url[:scheme_sep + 3], url[scheme_sep + 3:]
    _, _, host_part = rest.partition("@")
    return f"{scheme}***:***@{host_part}"


# Credentials embedded ANYWHERE in a blob of text, not just in a string that
# is entirely a URL — and every occurrence, not the first. A masker that
# handles one occurrence prints the password the other four times and looks
# like it is working.
_CREDS_IN_TEXT_RE = re.compile(r"([a-z][a-z0-9+.-]*://)[^/\s'\"@]+@", re.IGNORECASE)
# Same shape as captcha_solver's and fingerprint_client's. A third copy is
# one too many and they should be unified in a family pass; reaching into
# another module's private name to avoid it would be worse.
_KEY_IN_TEXT_RE = re.compile(
    r"((?:client)?key|token|api[_-]?key)=([^&\s'\"]{6,})", re.IGNORECASE)


def _redact_debug_header(value: str) -> str:
    """The x-debug header, safe to log.

    SECURITY.md names this header as one of three places credentials reach a
    log unmasked, and it was logged verbatim: the API echoes back the task it
    ran, so a run driven through a credentialed CDP endpoint put that
    endpoint's username and password into the log, and a key passed as a
    query parameter would go the same way.

    Redaction rather than an allowlist of fields, deliberately: the header is
    the API's own metadata and its shape is not ours to pin, so an allowlist
    would silently drop the cost and timing figures this is logged FOR the
    first time the API adds a field.
    """
    return _KEY_IN_TEXT_RE.sub(r"\1=***",
                               _CREDS_IN_TEXT_RE.sub(r"\1***:***@", value))


def _build_wait_for(args) -> Optional[dict]:
    """`waitFor` is sent as a JSON OBJECT.

    Measured 2026-09-23 against /tasks/sync: the JSON-encoded STRING form
    this client used to send (on the strength of the API docs of the time)
    is now answered HTTP 422, "params.waitFor must be an object" -- and the
    task is still billed ($0.0005). The same request with an object is
    answered HTTP 200.

    Default (no flag): wait for the DOM. On a challenge-protected page
    that resolves instantly against the challenge page itself — which is
    exactly the trap documented in this module's docstring, so
    --wait-text/--wait-element exist to wait on something only the real
    page can contain."""
    if args.wait_text:
        return {"text": args.wait_text}
    if args.wait_element:
        return {"element": args.wait_element, "checkVisible": True}
    if args.wait_state:
        return {"state": args.wait_state}
    return None


def fetch_html(args) -> str:
    payload = {
        "task_type": "scrape",
        "url": args.url,
        "data_format": "raw",   # we want HTML; product_parser does the rest
        "format": "json",       # so we get {"status", "headers", "body"}
        "timeout": min(args.timeout, MAX_API_TIMEOUT),
    }

    wait_for = _build_wait_for(args)
    if wait_for:
        payload["waitFor"] = wait_for
        logger.info("waitFor: %s", json.dumps(wait_for))

    if args.cdp_url:
        payload["cdpurl"] = args.cdp_url
        logger.info("Routing through an existing browser session: %s",
                    _mask_credentials(args.cdp_url))

    logger.info("POST %s (url=%s)", SYNC_ENDPOINT, args.url)
    resp = requests.post(
        SYNC_ENDPOINT,
        headers={"Authorization": f"Bearer {args.key}", "Content-Type": "application/json"},
        json=payload,
        # Give the HTTP call more headroom than the API-side task timeout,
        # otherwise a task that legitimately runs the full 120s looks like
        # a client-side network failure.
        timeout=min(args.timeout, MAX_API_TIMEOUT) + 30,
    )

    # The API returns its own per-task metadata (price, timings, status)
    # in an x-debug header — worth logging, it's the only place the real
    # cost of the call shows up.
    debug = resp.headers.get("x-debug")
    if debug:
        logger.info("x-debug: %s", _redact_debug_header(debug))

    if resp.status_code != 200:
        # 422 = task ran but errored (this is what a bad/unreachable
        # cdpurl produces: "CDP connect failed (user cdpurl) after N
        # attempts"); 402 = out of balance; 408 = sync wait exceeded.
        raise RuntimeError(
            f"Scraper API returned HTTP {resp.status_code}: {resp.text[:500]}"
        )

    body = resp.json()
    html = body.get("body") or ""
    # The TARGET's HTTP status is `http_code` (an int). `status` is the
    # API's own verdict STRING ("success"), which this line used to read --
    # so a target 403 or 503 reached the classifier as "success" and was
    # never seen (measured 2026-09-23). `status` is kept as a fallback only
    # when it really is an int.
    upstream_status = body.get("http_code")
    if not isinstance(upstream_status, int):
        raw = body.get("status")
        upstream_status = raw if isinstance(raw, int) and not isinstance(raw, bool) else None
    logger.info("Upstream page status (http_code) %s, %d bytes of HTML.", upstream_status, len(html))
    # The STATUS is returned alongside the HTML, not thrown away. It used to
    # be, and that cost this engine the family's central distinction. On this
    # site a refusal carries no markup at all — nothing a challenge check
    # on it, so the challenge check below finds nothing and the run fell
    # through to "0 products" and exit 4. A pipeline branching on the exit
    # code then reads a block as an empty category. See detect_page_state,
    # which the three browser engines already reach through page_flow.
    return html, upstream_status


def main() -> int:
    args = parse_args()

    if not args.key:
        logger.error("No 2captcha API key. Pass --key, or better, export TWOCAPTCHA_KEY.")
        return 2

    # A challenge page is not necessarily final (see _attempt), so a
    # single attempt is not evidence. Each retry is a fresh billable task —
    # $0.0005 at the observed rate — so the default is deliberately low, and
    # only a challenge is retried.
    attempts = max(1, args.retries + 1)
    for attempt in range(1, attempts + 1):
        outcome = _attempt(args, attempt, attempts)
        if not outcome["retry"] or attempt == attempts:
            break
        logger.info("Challenge page on attempt %d/%d — retrying in %ds.",
                    attempt, attempts, args.retry_delay)
        time.sleep(args.retry_delay)
    return _finish(args, outcome)


def _outcome(*, rows=(), blocked=False, stop_reason, failed=True,
             retry=False) -> dict:
    return {"rows": list(rows), "blocked": blocked, "stop_reason": stop_reason,
            "pages_failed": [1] if failed else [], "retry": retry}


def _finish(args, outcome: dict) -> int:
    """Write through the shared `finish_run`, so this client follows the
    same contract as the other engines: a genuine empty result is exit 4
    (and with --allow-empty an empty file plus `status: empty`), blocked is
    3, never obtained or a parser failure is 5, and an earlier good output
    is kept whenever nothing was obtained."""
    rows = outcome["rows"]
    return finish_run(
        rows, args.out, args.format, args.allow_empty,
        blocked=outcome["blocked"], stop_reason=outcome["stop_reason"],
        pages_requested=1, pages_completed=0 if outcome["pages_failed"] else 1,
        pages_failed=outcome["pages_failed"], start_url=args.url,
        final_url=args.url, mode=args.mode, source=P.SOURCE,
        extra={"transport": "scraper_api"})


def _save_debug(args, html: str) -> str:
    dump = f"{args.out}_scraperapi_debug.html"
    with open(dump, "w", encoding="utf-8") as f:
        f.write(html)
    return dump


def _attempt(args, attempt: int = 1, attempts: int = 1) -> dict:
    """One billed fetch, classified. Returns what `_finish` needs; writes
    no output itself."""
    if attempts > 1:
        logger.info("Attempt %d/%d", attempt, attempts)

    try:
        html, upstream_status = fetch_html(args)
    except requests.RequestException as e:
        logger.error("Network error talking to the Scraper API: %s", e)
        return _outcome(stop_reason="scraper_api_error")
    except RuntimeError as e:
        # HTTP 4xx/5xx from the API, including the 422 that a busy or
        # unreachable cdpurl produces.
        logger.error("%s", e)
        return _outcome(stop_reason="scraper_api_error")

    if args.dump_html:
        with open(args.dump_html, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info("Raw HTML written to %s", args.dump_html)

    # Same policy as the browser engines: the status decides the blocked
    # case, because this site's refusal has no marker to detect.
    state = detect_page_state(html, status=upstream_status, url=args.url)
    if state == "bad_request":
        logger.error(
            "Weibo answered HTTP %s with %d bytes — this is the measured "
            "outcome for an /ajax/ URL fetched through this product: it "
            "arrives as a page NAVIGATION, which Weibo refuses, while the "
            "identical request made as an XHR is served. It is not a block, "
            "not a rate limit and not geography.",
            upstream_status, len(html))
        logger.error(
            "Use playwright_scraper.py, or weibo_api.WeiboClient for a "
            "browserless read — both fetch these URLs successfully with a "
            "free visitor cookie and no key.")
        return _outcome(stop_reason="bad_request")

    # Any state the shared policy counts as blocked (on Weibo that is the
    # login wall and a rendered challenge), not a literal "blocked" state:
    # page_flow.STATE_POLICY has no state of that name, so a string
    # comparison here could never fire.
    if page_flow.counts_as_blocked(state):
        dump = _save_debug(args, html)
        logger.error(
            "Weibo did not serve the Scraper API's request (upstream HTTP %s, "
            "%d bytes, state %r) — saved to %s. A 'login_wall' wants an "
            "account, which this repo does not implement, and no exit "
            "address changes it; a 'challenge' was never observed on any "
            "route this repo reads, so it is worth an issue. This is exit "
            "3, distinct from an empty "
            "result (exit 4).",
            upstream_status, len(html), state, dump)
        return _outcome(blocked=True, stop_reason=state,
                        retry=state == "challenge")

    # Only a RENDERED challenge's vocabulary, as in the browser engines. The
    # visitor and login prose in BOT_CHALLENGE_MARKERS is already decided by
    # the state above (a login wall is blocked; the visitor gate is not a
    # refusal this client can retry its way past), and matching it here
    # also fired on a served payload whose own text quoted that prose.
    vendor = detect_bot_challenge(html)
    if vendor in P.CHALLENGE_MARKERS:
        logger.error(
            "The Scraper API returned a %s bot-challenge page (%d bytes), not real content.",
            vendor, len(html),
        )
        logger.error("A challenge page is not a final answer — retry before "
                     "concluding anything (--retries). Note that no challenge "
                     "was observed on any Weibo route this repo reads, so "
                     "reaching this line is itself news worth an issue.")
        return _outcome(blocked=True, stop_reason="challenge", retry=True)

    if not page_flow.should_parse(state):
        # Not the site's payload: an unrecognised body, a transport fault,
        # or the visitor gate (this client cannot run the visitor
        # handshake). Nothing here says the feed is empty, so it is never
        # obtained (exit 5), not "0 posts" (exit 4).
        dump = _save_debug(args, html)
        logger.error("Upstream HTTP %s, %d bytes, state %r: not a payload "
                     "this repo reads — saved to %s. Exit 5: the content was "
                     "not obtained, which says nothing about the feed.",
                     upstream_status, len(html), state, dump)
        return _outcome(stop_reason=f"state_{state}")

    products, _cursor = P.rows_for_mode(args.mode, html, page=1)
    logger.info("Parsed %d post(s).", len(products))

    if page_flow.served_rows_we_could_not_read(state, products):
        # The payload holds rows and the parser read none: the shape
        # changed under the parser. A parser failure, not an empty feed.
        dump = _save_debug(args, html)
        logger.error("The response holds rows and the parser read none of "
                     "them — %s. Saved to %s.",
                     page_flow.PARSER_FAILURE_REASON, dump)
        return _outcome(stop_reason=page_flow.PARSER_FAILURE_REASON)

    if not products:
        # `empty` is the site's own answer: `ok: 1` with an empty list, or
        # `ok: 0` with a reason other than the login wall. A genuine empty
        # result (exit 4).
        logger.info("Weibo answered with an empty payload (state %r) — a "
                    "genuine empty result.", state)
        return _outcome(stop_reason="no_new_products", failed=False)

    return _outcome(rows=products, stop_reason="single_page_mode", failed=False)


def parse_args():
    p = argparse.ArgumentParser(
        description="Weibo scraper — 2captcha Scraper API edition (no local "
                    "browser). UNVERIFIED: no key was available while this "
                    "repo was written, and Weibo answers 403 to a top-level "
                    "NAVIGATION to its own API while serving the identical "
                    "request made as an XHR. You almost certainly do not "
                    "need this path — the same data is free over plain "
                    "HTTPS from an ordinary address (weibo_api.py).")
    # NOT required: prefer the TWOCAPTCHA_KEY env var. A key passed on the
    # command line is visible to anyone who can run `ps`, and it lands in
    # shell history and in any log that echoes the command line.
    p.add_argument("--key", default=os.environ.get("TWOCAPTCHA_KEY"),
                   help="2captcha.com API key (sent as a Bearer token). "
                        "Defaults to $TWOCAPTCHA_KEY, which is the safer way to pass it.")
    p.add_argument("--url", default=None,
                   help="A weibo.com account or post URL. Not needed for "
                        "--mode hot. Falls back to $WEIBO_URL or .env.")
    p.add_argument("--mode", choices=["hot", "user", "post"],
                   default="hot",
                   help="Which parser reads the returned payload. Default "
                        "hot, as in the other CLIs.")
    p.add_argument("--category", default=None,
                   help="Accepted for the family's shared flag set and not "
                        "used by this client: the --url decides what is "
                        "fetched.")
    p.add_argument("--format", choices=["json", "csv", "both"], default="both")
    p.add_argument("--out", default="weibo_posts_scraperapi", help="Output file prefix")
    p.add_argument("--timeout", type=env_config.positive_int, default=60,
                   help=f"API-side task timeout in seconds (1-{MAX_API_TIMEOUT}, default 60)")
    p.add_argument("--cdp-url", default=None,
                   help="Route the fetch through an existing browser session over CDP "
                        "(sent as the API's `cdpurl` param), e.g. ws://user:pass@host:port")
    wait = p.add_mutually_exclusive_group()
    wait.add_argument("--wait-text", default=None,
                      help="Wait until this string appears on the page, e.g. '$'. Use this "
                           "on protected sites — a DOM/load wait is satisfied instantly by "
                           "the challenge page itself.")
    wait.add_argument("--wait-element", default=None,
                      help="Wait until this CSS selector is visible, e.g. 'a[href*=\"-item-\"]'")
    wait.add_argument("--wait-state", choices=["load", "domcontentloaded"], default=None,
                      help="Wait for a page load state instead of specific content")
    p.add_argument("--allow-empty", action="store_true",
                   help="Write an empty result ([] and a header-only CSV, sidecar "
                        "status 'empty', exit 4) when Weibo's own payload is empty. "
                        "A blocked or failed run never writes one, and an earlier "
                        "output is kept.")
    p.add_argument("--retries", type=env_config.non_negative_int, default=1,
                   help="Extra attempts if a bot-challenge page comes back. One retry is "
                        "usually worth it. Each attempt is a separate billable task, so "
                        "this defaults to 1.")
    p.add_argument("--retry-delay", type=env_config.non_negative_int, default=10,
                   help="Seconds between retries (default 10)")
    p.add_argument("--dump-html", default=None,
                   help="Also write the raw returned HTML to this path (always, even on success)")
    args = p.parse_args()
    # This client uses --key and --cdp-url rather than --twocaptcha-key and
    # --cdp-endpoint, so the env mapping is spelled out instead of defaulted.
    env_config.apply(args, keys={
        "TWOCAPTCHA_KEY": "key",
        "WEIBO_CDP_ENDPOINT": "cdp_url",
        "WEIBO_URL": "url",
    })
    if not args.url and args.mode == "hot":
        # The hot feed has ONE address and no per-account part, so asking a
        # user to type it is asking them to copy a constant. The three
        # browser engines build it themselves for the same reason; this one
        # differing was a usability gap, not a design choice.
        args.url = P.hot_feed_url(count=25)
    if not args.url:
        p.error(f"--mode {args.mode} needs --url (an account or post URL), "
                "and WEIBO_URL is not set in the environment or in .env. "
                "Only --mode hot has a fixed address.")
    return args


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
