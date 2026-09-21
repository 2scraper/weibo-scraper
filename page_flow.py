"""
page_flow.py
------------
The retry / solve / blocked decision, as DATA rather than as three copies of
an if-chain (CLAUDE.md §1).

Weibo answers a request six ways, and five of them want a different response:

    an /ajax/ payload with posts in it                 -> parse
    the same payload with an empty list                -> parse, it is an answer
    {"ok": -100, "url": ".../sso/signin?…"}            -> re-mint a visitor
                                                          cookie, then retry
    {"ok": 0, "message": "…请登录后使用"}, HTTP 403      -> an ACCOUNT wall;
                                                          nothing retries past it
    the passport visitor HTML gate                     -> mint, then retry
    Chromium's own network-error page                  -> a transport fault,
                                                          not a refusal

Three copies of that triage across three engines would drift, and the drift
would be silent — one engine reporting exit 3 where its twin reports exit 0
on the same response.

The distinction this file exists to keep straight
--------------------------------------------------
Weibo has no captcha on the routes this repo reads and no geographic block
at all. What it has is a LOGIN wall, and the wall comes in two grades that
look almost identical and want opposite responses:

  a MISSING VISITOR COOKIE is free to fix. Every /ajax/ route answers
  `ok: -100` to a cold session, and the passport handshake
  (genvisitor -> incarnate) hands out a SUB cookie that clears it. Measured
  2026-09-21 from a Hetzner datacenter address in Finland: 5 mints out of 5,
  ~1.5 s each, and the feed opened immediately afterwards. So `ok: -100` is
  a RETRYABLE state — the fix costs one request.

  an ACCOUNT WALL is not fixable from here at all. `/ajax/statuses/mymblog`
  answers HTTP 403 with "前方有点拥堵，请登录后使用" to a session holding a
  perfectly good visitor cookie, and `m.weibo.cn`'s container routes answer
  `ok: -100` forever however many times the cookie is re-minted. Retrying
  those spends requests to be told the same thing, so `retry` is False on
  that state even though its name says blocked.

Neither grade is geographic, and that is the measured claim rather than an
assumption: the whole reconnaissance above ran from a Finnish datacenter IP
with no proxy, and the routes this repo uses returned real data — 100
requests in 83 seconds with zero refusals. A Chinese exit is not what stands
between this code and `mymblog`; an account is.

Nothing here imports a browser, and **no JavaScript crosses this boundary**:
Selenium's `execute_script` takes a function BODY with an explicit `return`
while Playwright and pyppeteer take `() => expr`, so a shared snippet would
quietly acquire one driver's dialect. The callbacks below are named for the
OPERATION instead, and each engine spells it in its own dialect (§1).
"""

import json
import logging
import re
from typing import Callable, Optional
from urllib.parse import urlparse

from product_parser import (MAX_PAGES, detect_bot_challenge,  # noqa: F401
                            detect_page_state)

log = logging.getLogger("page_flow")


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------

# How many post records mean "this response is a feed".
#
# `> 1` on purpose, per CLAUDE.md §5: waiting for ONE match resolves on
# something unrelated long before the feed is really there. Measured
# 2026-09-21: /ajax/feed/hottimeline returns 10 posts to `count=25` (the
# parameter is a ceiling the site ignores upward), and a waterfall cursor
# page returned 19 or 20. Four is a floor a real response clears instantly
# and a half-arrived one does not.
MIN_CARD_MATCHES = 4

# A readiness anchor for the browser engines.
#
# These three modes all read JSON rather than markup, so the anchor is the
# payload itself: the engines navigate to the API URL and wait for a body
# that parses as JSON carrying the mode's own list key. There is no CSS
# selector to wait for and inventing one would be the §4 mistake of
# anchoring on markup that no run reads.
READY_KEY_HOT = "statuses"
READY_KEY_USER = "data.list"
READY_KEY_POST = "data"

# Kept under the family's names so the engines bind against one vocabulary.
READY_SELECTOR_LISTING = "pre, body"
READY_SELECTOR_PROFILE = "pre, body"

CONTENT_TIMEOUT_MS = 45_000
CONTENT_TIMEOUT_MS_PROFILE = 30_000


def ready_key(mode: str) -> str:
    """Which key in the response body means 'the payload arrived'."""
    return {"hot": READY_KEY_HOT, "user": READY_KEY_USER,
            "post": READY_KEY_POST}.get(mode, READY_KEY_HOT)


def ready_selector(mode: str) -> str:
    return READY_SELECTOR_PROFILE if mode == "post" else READY_SELECTOR_LISTING


def min_matches(mode: str) -> int:
    # One comment is a legitimate answer for a post that has one comment, so
    # `--mode post` floors at 1. A feed does not.
    return 1 if mode == "post" else MIN_CARD_MATCHES


def content_timeout_ms(mode: str) -> int:
    return CONTENT_TIMEOUT_MS_PROFILE if mode == "post" else CONTENT_TIMEOUT_MS


READY_POLL_MS = 500


def wait_for_count(count: Callable[[str], int], selector: str, minimum: int,
                   timeout_ms: int, sleep_ms: Callable[[int], None]) -> int:
    """Poll `count(selector)` until it reaches `minimum` or time runs out.

    The caller supplies the counting primitive, so this never evaluates a
    JavaScript STRING — CLAUDE.md §18's CSP trap, where a site whose policy
    omits `unsafe-eval` killed a run with `EvalError` on its most obvious
    URL. Weibo's own policy was not measured to forbid it and the rule is
    followed anyway: a readiness wait that works under any CSP costs nothing
    extra to write.
    """
    waited = 0
    n = count(selector)
    while n < minimum and waited < timeout_ms:
        sleep_ms(READY_POLL_MS)
        waited += READY_POLL_MS
        n = count(selector)
    return n


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

# The site's own "please log in" prose, and the visitor gate's own title.
#
# The spellings are MEASURED off the pages rather than guessed, because the
# first version guessed wrong: it looked for `passport.weibo.com/visitor`,
# which never appears in the gate's BODY — the host is in the URL. So a
# cold session's very first page classified as `unknown` instead of
# `needs_visitor`, and a run that had lost its cookie would have retried
# blindly rather than minting a new one. Counted on the real gate
# (9,486 bytes, 2026-09-21): `visitor/visitor` x4, `Sina Visitor System`
# x1, `weibo.com/login` x1, `passport.weibo.com/visitor` x0.
#
# `detect_bot_challenge` had the right spellings all along, so the two
# detectors disagreed about the same page — CLAUDE.md §19: when a static
# and a live detector disagree, the one missing a spelling is usually the
# static one.
#
# Matched as a SECONDARY signal only: the `ok` field and the status code are what carry
# the decision, because prose is translated, reworded and — in this case —
# actively misleading. "前方有点拥堵" means "it is a bit congested ahead",
# which describes a capacity problem the site does not have.
_LOGIN_PROSE = re.compile(
    r"请登录|登录后使用|sso/signin|weibo\.com/login"
    r"|visitor/visitor|Sina Visitor System", re.I)

# Chromium's own network-error page. Not a refusal by the site — the site
# was never reached — and it wants a different answer from a block: a dead
# proxy needs a DIFFERENT exit, a timeout needs another try at the same one.
#
# CLAUDE.md §18 records why a title check cannot do this job: Chromium's
# error page carries the requested hostname in its own `<title>`, so a page
# that never left the browser reads as a page the site served.
_CHROME_ERROR = re.compile(
    r"ERR_(PROXY_CONNECTION_FAILED|TUNNEL_CONNECTION_FAILED|CONNECTION_\w+|"
    r"NAME_NOT_RESOLVED|EMPTY_RESPONSE|SSL_\w+|TIMED_OUT|FAILED)")


def classify(body: Optional[str], status: Optional[int] = None,
             url: Optional[str] = None) -> str:
    """Name what came back, from the response body and its status code.

    `status` is positional and second, and every caller passes it that way.
    That is not a stylistic note: CLAUDE.md §17 records a sibling repo where
    two of three engines called this `classify(html, url=…)`, putting the URL
    where the status belongs, and BOTH crashed on their first fetch while
    every offline check stayed green. The suite here binds all three engines'
    calls against this signature for that reason.
    """
    text = body or ""
    if not text.strip():
        return "unknown"

    if _CHROME_ERROR.search(text[:4000]):
        return "transport_error"

    payload = _as_payload(text)
    if payload is not None:
        # `/ajax/statuses/show` is the odd one out: it returns the post
        # object BARE, with no `{"ok": …, "data": …}` wrapper that every
        # other route in this repo carries. A classifier that insists on
        # the wrapper calls a perfectly good 5 KB post "unknown", which is
        # what broke `--mode post` on its first live run — the id lookup
        # it needs runs through this route.
        ok = payload.get("ok")
        # -100 is the site's "sign in" answer and a visitor cookie clears it.
        if ok == -100:
            return "needs_visitor"
        # ok:0 with a login message is the account wall. ok:0 with anything
        # else is the site declining for its own reasons (a deleted post
        # answers `{"ok":0,"errno":"20101"}`) — an answer, not a refusal.
        if ok == 0:
            msg = str(payload.get("message") or payload.get("msg") or "")
            if status == 403 or _LOGIN_PROSE.search(msg):
                return "login_wall"
            return "empty"
        if ok == 1 or "statuses" in payload or "data" in payload:
            return "content" if _has_rows(payload) else "empty"
        # `{"error": "Forbidden"}` under HTTP 403. Weibo's answer to a
        # top-level NAVIGATION to one of its API URLs — the same URL,
        # same cookies, fetched as an XHR, returns the payload. It is
        # neither a block nor a login wall nor geography, and calling it
        # either would send a reader hunting for a proxy problem or an
        # account they do not need (CLAUDE.md §18's whole argument for
        # classifying by what a signal PROVES).
        if status == 403 and payload.get("error"):
            return "bad_request"
        return "unknown"

    # Not JSON. The HTML visitor gate is the expected case: every weibo.com
    # HTML route redirects a cold session to passport's visitor page, which
    # is a handshake rather than a refusal.
    if _LOGIN_PROSE.search(text[:8000]):
        return "needs_visitor"
    if status == 403:
        return "login_wall"
    if status and status >= 500:
        return "transport_error"
    return "unknown"


def _as_payload(text: str) -> Optional[dict]:
    """Parse a response body as the site's JSON, or return None.

    A browser engine hands back `page.content()`, which wraps a JSON
    response in Chromium's JSON-viewer markup, so the raw body has to be
    recovered before anything can read `ok`. Reading the wrapper as HTML and
    the payload as prose is how a served page gets classified as unknown.
    """
    s = text.strip()
    if not s.startswith(("{", "[")):
        m = re.search(r"<pre[^>]*>(.*?)</pre>", text, re.S)
        if not m:
            return None
        s = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        if not s.startswith(("{", "[")):
            return None
    try:
        obj = json.loads(s)
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def _has_rows(payload: dict) -> bool:
    """Whether an `ok: 1` payload actually carries something.

    The key order matters and was got wrong once, live. `data.list` is
    checked FIRST and decides on its own where it exists, so a user feed
    that answers `{"data": {"list": [], "next_cursor": -1}}` is `empty` —
    an end of listing, not content. Only where there is no list at all
    does a non-empty `data` object count on its own, which is what makes
    `/ajax/statuses/longtext` classify correctly: it answers
    `{"ok": 1, "data": {"longTextContent": "…"}}` with no list anywhere,
    and an earlier version of this function looked for three named keys,
    found none of them, and called a 10 KB recovered post "empty". The
    long-text recovery then skipped every row it was written for — 0 of 9
    on a live run — while the run reported success.

    `{"ok": 1, "data": {}}` stays empty, and must: that is the honest
    answer for a post that was never truncated.
    """
    # `/ajax/statuses/show` returns the post object at the TOP LEVEL,
    # alongside `ok: 1` and with no `data` and no `statuses` anywhere. Every
    # other route in this repo wraps its payload, so a check that only knew
    # about wrappers called a perfectly good 7 KB post "empty" — which broke
    # `--mode post` on its first live run, since the id lookup that mode
    # needs goes through exactly this route. Checked FIRST, because it is
    # the unambiguous positive signal and the list checks below are the
    # weaker, shape-based ones (§17's ordering rule).
    if payload.get("mblogid") or payload.get("mid"):
        return True
    if payload.get("statuses"):
        return True
    data = payload.get("data")
    if isinstance(data, list):
        return bool(data)
    if isinstance(data, dict):
        for key in ("list", "statuses"):
            if key in data:
                return bool(data[key])
        return bool(data)
    return False


STATE_POLICY = {
    # The payload, with posts in it.
    "content":        {"retry": False, "solve": False, "blocked": False, "parse": True},
    # The payload, with an empty list. Weibo served exactly what was asked
    # for and it holds nothing — a real answer, and EXIT_NO_PRODUCTS rather
    # than EXIT_BLOCKED. Reporting it as blocked sends a user hunting for a
    # proxy problem that is not there.
    "empty":          {"retry": False, "solve": False, "blocked": False, "parse": True},
    # `ok: -100`, or the passport visitor HTML gate. The session has no
    # visitor cookie, or its cookie aged out. Retryable and CHEAP: the
    # handshake is one POST and one GET and it succeeded 5 of 5 from a
    # datacenter address. Not `blocked`, because a run that clears it on the
    # retry was never blocked — reporting exit 3 for a state the next
    # request fixes is the false alarm §17's classification-order trap is
    # about.
    "needs_visitor":  {"retry": True,  "solve": False, "blocked": False, "parse": False},
    # HTTP 403 with the site's login prose, to a session that already holds
    # a visitor cookie. This wants an ACCOUNT. `retry` is False because no
    # number of attempts and no exit IP changes it — the engines report it
    # and stop rather than spending the user's retry budget proving it
    # twice. `solve` is False because there is no challenge here: nothing is
    # being tested, access is being declined.
    "login_wall":     {"retry": False, "solve": False, "blocked": True,  "parse": False},
    # Reached for completeness rather than from a measurement. No captcha
    # was rendered on any route this repo reads — 5 captures, 0 widgets —
    # but Weibo HAS one configured (its login page loads GeeTest), so a
    # challenge appearing on a scraping path later is a change in the site
    # rather than an impossibility. This is the state that would pay for a
    # solver if it ever fires; see product_parser.detect_bot_challenge for
    # why the marker set is narrow.
    "challenge":      {"retry": True,  "solve": True,  "blocked": True,  "parse": False},
    # The request never reached Weibo: a dead proxy, a DNS failure, a
    # refused connection. Retryable, and NOT blocked — calling a broken
    # proxy a site refusal is how a user goes looking for a bot manager
    # that was never involved.
    "transport_error": {"retry": True, "solve": False, "blocked": False, "parse": False},
    # The request was the wrong SHAPE, not the wrong credentials. Not
    # retryable, because the next identical request is refused identically
    # — what has to change is how the request is made, and the engines
    # already make it correctly. Not blocked, because nothing refused this
    # run's access to anything.
    "bad_request":    {"retry": False, "solve": False, "blocked": False, "parse": False},
    # Served by Weibo but not a payload this code recognises. A wait, not a
    # spend.
    "unknown":        {"retry": True,  "solve": False, "blocked": False, "parse": False},
}


def should_retry(state: str) -> bool:
    return STATE_POLICY.get(state, STATE_POLICY["unknown"])["retry"]


def should_solve(state: str) -> bool:
    return STATE_POLICY.get(state, STATE_POLICY["unknown"])["solve"]


def counts_as_blocked(state: str) -> bool:
    return STATE_POLICY.get(state, STATE_POLICY["unknown"])["blocked"]


def should_parse(state: str) -> bool:
    return STATE_POLICY.get(state, STATE_POLICY["unknown"])["parse"]


def needs_visitor_cookie(state: str) -> bool:
    """Whether the fix for this state is the passport handshake.

    Its own function rather than a comparison spelled out in three engines,
    for the same reason the table above exists.
    """
    return state == "needs_visitor"


# Whether a blocked page is worth re-fetching at all.
#
# False here, and CONSULTED rather than merely documented — the engines read
# it, so this really does stop the retry loop. (A sibling repo carried this
# constant with a paragraph of justification and no reader, which is the
# same defect as dead code that looks load-bearing: §17. The suite asserts
# it has a consumer outside this module.)
#
# False because on Weibo the only state that reaches it is `login_wall`, and
# that is a decision about the SESSION's credentials rather than about its
# address. Measured: /ajax/statuses/mymblog refused a visitor-cookie session
# identically on every attempt, and the same address read the hot feed 100
# times in 83 seconds without a single refusal. There is nothing for a retry
# to change. A site that starts scoring addresses would want this True
# again, which is why it is a constant and not an inlined `False`.
RETRY_ON_BLOCKED = False

# How many times to re-fetch a blocked page when there is no proxy pool to
# rotate into. Moot while RETRY_ON_BLOCKED is False, and kept so that
# flipping that one flag restores the family's behaviour rather than
# requiring new code.
BLOCK_RETRIES_WITHOUT_POOL = 1

# How many times the visitor handshake may be re-run for one fetch.
#
# One. The handshake either works or the account behind the exit is being
# refused outright, and a second mint answers the same thing a second time.
VISITOR_MINTS_PER_PAGE = 1

# At most one solve per page. A challenge that survives a solved token is
# not a challenge this run can pass, and a second solve is a second charge
# for the same answer.
#
# This constant has read like an enforced limit across this whole family and
# was NOT one (CLAUDE.md §23): every engine calls the captcha handler TWICE
# per attempt — once before the response is classified, once after — and
# only the second call was ever counted, so one page could buy three solves
# on a site where a challenge renders on every fetch. `SolveBudget` below is
# what makes the number true, and both call sites in all three engines go
# through it. The suite counts the call sites, the guards and the
# increments and asserts the three are equal.
SOLVES_PER_PAGE = 1


class SolveBudget:
    """One page's allowance of paid solves, spent through a single counter.

    Created per page-attempt by each engine and passed to BOTH captcha call
    sites, so the two cannot each believe they are the first.
    """

    def __init__(self, limit: int = SOLVES_PER_PAGE):
        self.limit = int(limit)
        self.spent = 0

    def may_solve(self) -> bool:
        return self.spent < self.limit

    def charge(self) -> None:
        self.spent += 1

    def __repr__(self) -> str:
        return f"SolveBudget(spent={self.spent}/{self.limit})"


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def pagination_is_addressable(url: str) -> bool:
    """Whether page N of this feed can be fetched without walking to it.

    FALSE for the hot feed and TRUE for nothing else this repo reads, which
    is why the question is asked per URL rather than per site (CLAUDE.md
    §18).

    Measured 2026-09-21. `/ajax/feed/hottimeline` answers `max_id: 1` to
    every request, including the request that passes `max_id=1` back to it,
    and four consecutive fetches returned four different sets of ten posts.
    There is no page 2: there is only "fetch it again and see what is there
    now". A `page_url()` that built `max_id=N` would return a full response
    every time and a run would look paginated while re-rolling one feed.

    `--mode user` walks a CURSOR, which is addressable only in the sense
    that the site hands you the next one — page 5's cursor is unknowable
    until page 4 arrives — so it chains link-to-link and refuses
    `--concurrency` above 1 for the reason §7 gives: a worker cannot be
    handed a page whose address does not exist yet.
    """
    parsed = urlparse(url or "")
    if not (parsed.scheme and parsed.netloc):
        return False
    return "/ajax/feed/hottimeline" not in (parsed.path or "")


def pages_to_plan(pages_requested: int, pages_available: Optional[int]) -> int:
    """How many pages a run may ask for, given what page 1 reported.

    Weibo states no total anywhere this repo reads — a feed response carries
    no count of what is behind it, and `next_cursor` says only whether there
    is more, never how much. So `pages_available` is None on every call this
    repo makes today, and the ceiling is the user's request capped at
    MAX_PAGES. The parameter is kept because the family shares this
    signature and because the cursor could start reporting a total.
    """
    ceiling = pages_available if pages_available else MAX_PAGES
    return max(1, min(int(pages_requested), min(ceiling, MAX_PAGES)))


def concurrency_limit(cdp_endpoint: Optional[str]) -> Optional[int]:
    """1 when workers would collide, else None for "no limit imposed here".

    The Scraping Browser API allows ONE live connection per profile, so N
    workers sharing a `pid` collide with `profile_locked`. Several `pid`s,
    one run each, is the way to parallelise that path (§7).

    `--mode hot` and `--mode user` are capped at 1 by the engines for a
    second, independent reason — neither feed is addressable, so there is
    no page to hand a second worker. That cap lives in the engines beside
    the mode, not here, because it is a property of the MODE rather than of
    the endpoint this function is asked about.
    """
    return 1 if cdp_endpoint else None
