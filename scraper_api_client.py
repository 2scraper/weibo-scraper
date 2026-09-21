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
import sys
import time
from typing import Optional

import requests

import product_parser as P
from product_parser import (BOT_CHALLENGE_MARKERS,  # noqa: F401
                            detect_bot_challenge, detect_page_state)
from output_writer import save
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


def _build_wait_for(args) -> Optional[str]:
    """`waitFor` must be a JSON STRING (double-encoded), per the API docs.
    Passing a nested object is silently wrong.

    Default (no flag): wait for the DOM. On a challenge-protected page
    that resolves instantly against the challenge page itself — which is
    exactly the trap documented in this module's docstring, so
    --wait-text/--wait-element exist to wait on something only the real
    page can contain."""
    if args.wait_text:
        return json.dumps({"text": args.wait_text})
    if args.wait_element:
        return json.dumps({"element": args.wait_element, "checkVisible": True})
    if args.wait_state:
        return json.dumps({"state": args.wait_state})
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
        logger.info("waitFor: %s", wait_for)

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
        logger.info("x-debug: %s", debug)

    if resp.status_code != 200:
        # 422 = task ran but errored (this is what a bad/unreachable
        # cdpurl produces: "CDP connect failed (user cdpurl) after N
        # attempts"); 402 = out of balance; 408 = sync wait exceeded.
        raise RuntimeError(
            f"Scraper API returned HTTP {resp.status_code}: {resp.text[:500]}"
        )

    body = resp.json()
    html = body.get("body") or ""
    upstream_status = body.get("status")
    logger.info("Upstream page status %s, %d bytes of HTML.", upstream_status, len(html))
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

    # A challenge page is not necessarily final (see _run_once), so a
    # single attempt is not evidence. Each retry is a fresh billable task —
    # $0.0005 at the observed rate — so the default is deliberately low.
    attempts = max(1, args.retries + 1)
    for attempt in range(1, attempts + 1):
        rc = _run_once(args, attempt, attempts)
        if rc != 3 or attempt == attempts:
            return rc
        logger.info("Challenge page on attempt %d/%d — retrying in %ds.",
                    attempt, attempts, args.retry_delay)
        time.sleep(args.retry_delay)
    return rc


def _run_once(args, attempt: int = 1, attempts: int = 1) -> int:
    if attempts > 1:
        logger.info("Attempt %d/%d", attempt, attempts)

    try:
        html, upstream_status = fetch_html(args)
    except requests.RequestException as e:
        logger.error("Network error talking to the Scraper API: %s", e)
        return EXIT_API_ERROR
    except RuntimeError as e:
        # HTTP 4xx/5xx from the API, including the 422 that a busy or
        # unreachable cdpurl produces.
        logger.error("%s", e)
        return EXIT_API_ERROR

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
        return EXIT_API_ERROR

    if state == "blocked":
        dump = f"{args.out}_scraperapi_debug.html"
        with open(dump, "w", encoding="utf-8") as f:
            f.write(html)
        logger.error(
            "BBB did not serve the Scraper API's request (upstream HTTP %s, "
            "%d bytes) — saved to %s. Measured 2026-09-16 on this exact URL: "
            "the Scraper API's own exits are datacenter addresses and BBB "
            "refuses them (403, 12,889 bytes), while the SAME task routed "
            "through a Scraping Browser session returned 200 and 113,389 "
            "bytes with the profile parsing in full. Pass --cdp-url. This is "
            "exit 3, distinct from an empty result (exit 4).",
            upstream_status, len(html), dump)
        return 3

    vendor = detect_bot_challenge(html)
    if vendor:
        logger.error(
            "The Scraper API returned a %s bot-challenge page (%d bytes), not real content.",
            vendor, len(html),
        )
        logger.error("A challenge page is not a final answer — retry before "
                     "concluding anything (--retries). Note that no challenge "
                     "was observed on any Weibo route this repo reads, so "
                     "reaching this line is itself news worth an issue.")
        return 3

    products, _cursor = P.rows_for_mode(args.mode, html, page=1)
    logger.info("Parsed %d post(s).", len(products))

    if not products:
        dump = f"{args.out}_scraperapi_debug.html"
        with open(dump, "w", encoding="utf-8") as f:
            f.write(html)
        logger.warning("0 posts parsed — saved the raw response to %s so "
                       "you can see what actually came back. If it holds "
                       "`{\"error\":\"Forbidden\"}` the fetch arrived as a "
                       "NAVIGATION rather than an XHR, which is the case "
                       "this engine's docstring says is unverified.", dump)
        return 4

    return save(products, args.out, args.format, allow_empty=args.allow_empty)


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
                   default="profile",
                   help="Default profile, unlike the browser engines, "
                        "because a profile is the only page kind this path "
                        "reads that the free endpoint cannot.")
    p.add_argument("--category", default=None, help="Label to tag output rows with. Defaults to the category segment of the URL, so the column is never empty just because the flag was omitted.")
    p.add_argument("--format", choices=["json", "csv", "both"], default="both")
    p.add_argument("--out", default="weibo_posts_scraperapi", help="Output file prefix")
    p.add_argument("--timeout", type=int, default=60,
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
                   help="Write output files even when 0 products were parsed. Off by "
                        "default so a failed fetch can't overwrite a good result.")
    p.add_argument("--retries", type=int, default=1,
                   help="Extra attempts if a bot-challenge page comes back. One retry is "
                        "usually worth it. Each attempt is a separate billable task, so "
                        "this defaults to 1.")
    p.add_argument("--retry-delay", type=int, default=10,
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
