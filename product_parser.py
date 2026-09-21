"""
product_parser.py
-----------------
Everything this repo knows about Weibo. If you are adding site knowledge
anywhere else, it belongs here (CLAUDE.md §1).

Where the data comes from
--------------------------
Weibo publishes **zero** JSON-LD. Counted 2026-09-21 across five captures —
the hot feed, the hot band, the hot-search sidebar, a profile and a
waterfall page — `application/ld+json` appears 0 times. So the usual
"structured data first, CSS fallback second" (§4) does not apply: there is
no structured block to read and nothing to fall back to.

What there is instead is better. Weibo's own front end is a React app that
renders nothing server-side and fetches everything from `/ajax/` endpoints,
so the site hands out clean JSON that is RICHER than anything it paints —
CLAUDE.md §21's "ask what the front end calls before assuming a browser",
answered the same way BBB answered it. Every function below parses that
JSON. No HTML is parsed at all, and BeautifulSoup is imported for exactly
one job: stripping the `<a>` wrapper off the posting-client string.

    --mode hot    /ajax/feed/hottimeline               the public hot feed
    --mode user   /ajax/profile/getWaterFallContent    one account's posts
    --mode post   /ajax/statuses/buildComments         one post's comments

The route map, and what it cost to find
----------------------------------------
Gating on Weibo is per-ROUTE, not per-site, and the difference is worth
money. Measured 2026-09-21 from a Hetzner datacenter address in Finland
(AS24940) with no proxy of any kind:

    OPEN to a session holding only a free visitor cookie
      /ajax/feed/hottimeline          10 posts a call
      /ajax/profile/getWaterFallContent   19-20 posts a cursor page
      /ajax/profile/info              the account, with followers_count
      /ajax/statuses/show             one post
      /ajax/statuses/longtext         the untruncated body
      /ajax/statuses/buildComments    211 KB of comments off one post
      /ajax/statuses/likeShow         61 KB of likers
      m.weibo.cn/statuses/show        the same post, mobile shape

    OPEN with no cookie at all
      /ajax/side/hotSearch            22 KB, cold, from a bare curl
      /ajax/statuses/hot_band         34 KB

    CLOSED to everything short of an account
      /ajax/statuses/mymblog          HTTP 403, "请登录后使用"
      m.weibo.cn/api/container/*      {"ok": -100} on every container id
      s.weibo.com/weibo?q=            redirects to the login page

The last group is an ACCOUNT wall and not a geographic one. That matters
because the obvious assumption about a Chinese site is that it needs a
Chinese exit, and on these routes it does not: the whole table above was
measured from Finland, and a burst of 100 requests in 83 seconds drew zero
refusals and zero rate limiting. A Chinese proxy buys nothing here — what
`mymblog` wants is a logged-in SUB cookie, and it will say so just as
politely from Beijing. See README for what that does and does not mean.

The traps, each of which would ship a wrong column
---------------------------------------------------
1. `text_raw` is truncated on long posts, silently, ending in U+200B —
   between 13% and 50% of a feed depending on the feed (4 of 30 across two
   captures, 5 of 10 on a live fetch the same day). Two of those five grew
   from ~148 characters to over a thousand when recovered, so this is most
   of the body rather than a tail. `/ajax/statuses/longtext` recovers it.
   See output_writer.Post.title.
2. `number_display_strategy.display_text` reads "100万+" on a post with 18
   likes. It is the site's display RULE, identical on every post, not a
   figure. The integers beside it are the facts.
3. `region_name` is "发布于 广东" — "posted from Guangdong". The prefix is
   not part of the province.
4. The posting client is user-settable text on some accounts ("名号被窝"),
   so it names a device only by convention.
5. `textLength` does not count the characters of anything this file
   writes: 341 where the recovered text was 183.
"""

import html as _html
import json
import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, quote, urlparse

from bs4 import BeautifulSoup

from output_writer import Post

log = logging.getLogger("product_parser")

# A hard ceiling on pages, so a typo in --pages cannot start a thousand-page
# run against someone else's server.
MAX_PAGES = 200

# The one host this repo reads. Weibo has no country TLDs and no locale
# paths: one site, one catalogue, and the interface language follows the
# account rather than the exit IP.
HOSTS = ("weibo.com", "www.weibo.com", "m.weibo.cn")

SOURCE = "weibo.com"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
# Kept as constants rather than spelled out at each call site: three engines
# read the same routes, and a fourth spelling is how they start disagreeing
# about which URL a mode fetches.

API_BASE = "https://weibo.com/ajax"

# The hot feed. `group_id` and `containerid` are both 102803 — the site's id
# for the public "推荐" (recommended) feed — and `extparam` is what its own
# front end sends. `count` is a ceiling the server ignores upward: it
# answered 10 posts to count=25 on every call measured.
HOT_FEED_URL = (API_BASE + "/feed/hottimeline?since_id=0&refresh=0"
                "&group_id=102803&containerid=102803"
                "&extparam=discover%7Cnew_feed&max_id={max_id}&count={count}")

USER_FEED_URL = API_BASE + "/profile/getWaterFallContent?uid={uid}&cursor={cursor}"
PROFILE_INFO_URL = API_BASE + "/profile/info?uid={uid}"
LONGTEXT_URL = API_BASE + "/statuses/longtext?id={mblogid}"
SHOW_URL = API_BASE + "/statuses/show?id={mblogid}"
COMMENTS_URL = (API_BASE + "/statuses/buildComments?is_reload=1&id={mid}"
                "&is_show_bulletin=2&is_mix=0&count={count}&fetch_level=0"
                "&locale=zh-CN&max_id={max_id}")

# The passport handshake. Not an /ajax/ route and not on weibo.com: this is
# the anonymous-visitor system, and it is what turns `ok: -100` into data.
VISITOR_GEN_URL = "https://passport.weibo.com/visitor/genvisitor"
VISITOR_INCARNATE_URL = (
    "https://passport.weibo.com/visitor/visitor?a=incarnate&t={tid}"
    "&w=2&c=095&gc=&cb=cross_domain&from=weibo&_rand={rand}")


def hot_feed_url(max_id: int = 0, count: int = 25) -> str:
    return HOT_FEED_URL.format(max_id=int(max_id), count=int(count))


def user_feed_url(uid: str, cursor: Any = 0) -> str:
    return USER_FEED_URL.format(uid=quote(str(uid), safe=""), cursor=quote(str(cursor), safe=""))


def profile_info_url(uid: str) -> str:
    return PROFILE_INFO_URL.format(uid=quote(str(uid), safe=""))


def longtext_url(mblogid: str) -> str:
    return LONGTEXT_URL.format(mblogid=quote(str(mblogid), safe=""))


def comments_url(mid: str, count: int = 20, max_id: int = 0) -> str:
    return COMMENTS_URL.format(mid=quote(str(mid), safe=""), count=int(count),
                               max_id=quote(str(max_id), safe=""))


# ---------------------------------------------------------------------------
# Reading a URL the user typed
# ---------------------------------------------------------------------------

# A numeric account id: `weibo.com/u/2803301701`, or the bare number.
_UID_IN_URL_RE = re.compile(r"/u/(\d{4,})")
# A post address: `weibo.com/2803301701/RiPCAfklU`. The second segment is
# base-62 and case-sensitive, which is why it is matched as a character
# class rather than lowercased anywhere.
_POST_IN_URL_RE = re.compile(r"/(\d{4,})/([A-Za-z0-9]{6,12})(?:[/?#]|$)")
# `weibo.com/detail/5344883403653158` and `m.weibo.cn/detail/…` use the mid.
_DETAIL_IN_URL_RE = re.compile(r"/(?:detail|status)/(\d{8,})")


def uid_from_url(url: str) -> Optional[str]:
    """The account id in a profile URL, or None.

    A bare number is accepted because that is what a user copies out of the
    address bar as often as the whole URL.
    """
    if not url:
        return None
    s = url.strip()
    if s.isdigit() and len(s) >= 4:
        return s
    m = _UID_IN_URL_RE.search(s)
    if m:
        return m.group(1)
    m = _POST_IN_URL_RE.search(s)
    return m.group(1) if m else None


def post_id_from_url(url: str) -> Tuple[Optional[str], Optional[str]]:
    """`(mblogid, mid)` from a post URL — either may be None.

    Weibo addresses one post two ways and the two ids are not
    interchangeable: `/ajax/statuses/longtext` wants the base-62 `mblogid`
    while `/ajax/statuses/buildComments` wants the numeric `mid`. A URL
    carries one or the other, never both, so a caller that has only the
    wrong one has to resolve it through /statuses/show.
    """
    if not url:
        return None, None
    s = url.strip()
    m = _DETAIL_IN_URL_RE.search(s)
    if m:
        return None, m.group(1)
    m = _POST_IN_URL_RE.search(s)
    if m:
        return m.group(2), None
    if s.isdigit() and len(s) >= 8:
        return None, s
    if re.fullmatch(r"[A-Za-z0-9]{6,12}", s):
        return s, None
    return None, None


def is_supported_host(url: str) -> Tuple[bool, str]:
    """Whether this repo can read `url`, and why not when it cannot.

    The reason is returned rather than a bare False because CLAUDE.md §5
    records what a bare refusal costs: "is not a Weibo site" is FALSE of
    `m.weibo.cn` — it is very much Weibo — and sends the reader looking for
    a typo. The honest refusal names the route.
    """
    host = (urlparse(url).netloc or "").lower().split(":")[0]
    if not host:
        return False, "no hostname in the URL"
    if host in ("weibo.com", "www.weibo.com"):
        return True, ""
    if host == "m.weibo.cn":
        return False, ("m.weibo.cn is Weibo's mobile site and this repo cannot "
                       "read it: every /api/container/ route on it answered "
                       "{\"ok\": -100} and a login redirect to an anonymous "
                       "session AND to a visitor-cookie session when measured "
                       "on 2026-09-21. Use the weibo.com address for the same "
                       "account or post.")
    if host == "s.weibo.com":
        return False, ("s.weibo.com is Weibo's SEARCH and it requires a "
                       "logged-in account — an anonymous request is "
                       "redirected to the login page. This repo does not "
                       "implement login, so search is not one of its modes.")
    return False, f"{host} is not a weibo.com address"


# ---------------------------------------------------------------------------
# Block detection
# ---------------------------------------------------------------------------

# Markers that mean the SITE refused, counted on pages known to be good
# before any of them was adopted (CLAUDE.md §18).
#
# The count that shaped this set, 2026-09-21, over 6 served responses (hot
# feed, waterfall, profile, comments, hot search, a profile HTML page) and 4
# refusals (the mymblog 403, an m.weibo.cn container, a cold HTML request, a
# cold /ajax/ request):
#
#     marker                      served   refused
#     "ok":-100                     0        2
#     请登录 / 登录后使用             0        1
#     weibo.com/login               0        2
#     Sina Visitor System           0        1
#     geetest                       1        0   <-- INVERTED
#     captcha                       1        0   <-- INVERTED
#     cf-turnstile                  0        0
#     challenges.cloudflare.com     0        0
#     turnstile / _cf_chl           0        0
#
# Two things follow, and both are the reason the count is run rather than
# the marker list inherited.
#
# `captcha` and `geetest` fire on a GOOD page and on NO refusal. Weibo's
# profile HTML carries its login widget's GeeTest configuration whether or
# not anything is being challenged, so the two most obvious captcha markers
# on a Chinese site are exactly backwards here — a marker that fires on good
# pages and is absent from the bad ones is worse than no marker at all
# (§18). They are not in the set. This is the third site in this family
# where a recommended marker turned out inverted (§23), and the first where
# it was caught before it shipped rather than after.
#
# Every Cloudflare marker this template suggests is 0 on both sides. Weibo
# is not fronted by Cloudflare, so carrying them would be dead code that
# looks load-bearing.
BOT_CHALLENGE_MARKERS = (
    "sina visitor system",
    "weibo.com/login",
    "passport.weibo.com/sso/signin",
    "登录后使用",
)

# Markers for a challenge Weibo COULD render and was not observed rendering.
#
# Anchored on the two vendors the site ACTUALLY loads (CLAUDE.md §18:
# build the set from what you find on the site). Weibo wires BOTH NetEase
# Yidun and GeeTest v4 into its own chrome and preloads them on pages it
# serves normally — counted 2026-09-21 on a profile page behind a visitor
# cookie: `CAPTCHA_TYPE = 'yidun'` x4, `static.geetest.com/v4/gt4.js` x1,
# `ValidateLoader` x1; and `yidun` x2 on the login page.
#
# So neither vendor's NAME nor its LOADER can be a marker here: both are
# what a good page fetches. What only a RENDERED widget carries is the
# runtime vocabulary below, every entry of which counted ZERO across four
# served pages — the cold visitor gate, a profile behind a cookie, a
# hot-feed payload and the login page.
#
# This replaced four guesses that matched nothing either vendor emits. It
# is a better-founded CANDIDATE set, not a measurement of a challenge:
# Weibo rendered none in 21 runs and 55 artefacts.
# Kept separate from the set above so that `detect_bot_challenge` can name
# which of the two fired, and so that nobody reads the empty measurement as
# proof that Weibo has no captcha. It has one — its login page loads GeeTest
# — it simply does not put it in front of these routes for an anonymous
# reader. CLAUDE.md §19: "unsolvable" is a property of a PAGE, never of a
# vendor, and "not implemented here" is a TODO rather than a limitation.
#
# These are deliberately anchored to a CHALLENGE's vocabulary rather than to
# the word `geetest`, precisely because the bare word fires on a served
# page: a rendered GeeTest widget carries its own runtime, a served profile
# page carries only a config key.
CHALLENGE_MARKERS = (
    # GeeTest v4, RENDERED
    "geetest_holder",
    "geetest_panel",
    "geetest_radar",
    "geetest_slider",
    "gcaptcha4",
    # NetEase Yidun, RENDERED
    "yidun_intelli",
    "yidun_panel",
    "yidun-captcha",
    "necaptcha",
    "captcha.yidun",
    "cstaticdun",
)


def detect_bot_challenge(html: Optional[str]) -> Optional[str]:
    """Name the refusal marker present in a response, or None.

    Reads a BOUNDED prefix and unescapes HTML entities over it first, which
    is CLAUDE.md §20's lesson: an edge can entity-escape the punctuation in
    a marker (`https&#58;&#47;&#47;…`) so that a literal matches a browser's
    DOM and silently misses the same page fetched by an HTTP client.
    Unescaping the whole body instead would be both wasteful on a 1.8 MB
    feed and wrong — a post's own text could then read as a marker.
    """
    if not html:
        return None
    head = _html.unescape(html[:20_000]).lower()
    for marker in CHALLENGE_MARKERS:
        if marker in head:
            return marker
    for marker in BOT_CHALLENGE_MARKERS:
        if marker in head:
            return marker
    return None


def detect_page_state(html: Optional[str], status: Optional[int] = None,
                      url: Optional[str] = None) -> str:
    """Classify a response as content / empty / a wall / a transport fault.

    Delegates to page_flow.classify, which holds the table. This wrapper
    exists because the family's engines call `detect_page_state` by that
    name and because page_flow imports FROM here — putting the table here
    would make the import circular.

    Imported lazily for that reason, and only that reason.
    """
    from page_flow import classify  # noqa: WPS433  (circular by design)
    return classify(html, status, url)


# ---------------------------------------------------------------------------
# Small readers
# ---------------------------------------------------------------------------

# Weibo's truncation mark. A long post's `text_raw` ends in one or more
# zero-width spaces where the rest of the text was cut away.
_ZERO_WIDTH = "​‌‍﻿"

_CREATED_AT_RE = re.compile(
    r"^[A-Za-z]{3} ([A-Za-z]{3}) (\d{2}) (\d{2}:\d{2}:\d{2}) ([+-]\d{4}) (\d{4})$")
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


def parse_created_at(raw: Optional[str]) -> Optional[str]:
    """"Sun Sep 21 15:38:42 +0800 2026" -> "2026-09-21T15:38:42+08:00".

    The offset is KEPT rather than normalised to UTC. It was +0800 on every
    row measured, and it is the offset the post was written under — folding
    it away would turn "posted at 15:38 local" into a number a reader has to
    convert back. An ISO-aware consumer gets a real instant either way.

    Returns None rather than a guess when the shape is not the one measured;
    the caller writes the site's own string into `created_at_raw` so a
    failure here is visible instead of looking like a post with no date.
    """
    if not raw:
        return None
    m = _CREATED_AT_RE.match(raw.strip())
    if not m:
        return None
    mon, day, clock, offset, year = m.groups()
    month = _MONTHS.get(mon)
    if not month:
        return None
    return f"{year}-{month:02d}-{day}T{clock}{offset[:3]}:{offset[3:]}"


def clean_region(raw: Optional[str]) -> Optional[str]:
    """"发布于 广东" -> "广东"; "发布于 其他" -> None.

    The prefix means "posted from" and is not part of the place. "其他"
    ("other") is Weibo's bucket for a location it will not name — a null
    rather than a place called Other, because a consumer grouping by region
    would otherwise get a province-sized category that is not a province.
    """
    if not raw:
        return None
    s = re.sub(r"^\s*发布于\s*", "", str(raw)).strip()
    if not s or s in ("其他", "未知"):
        return None
    return s


def clean_client(raw: Optional[str]) -> Optional[str]:
    """The posting client, with its `<a>` wrapper removed.

    Weibo sends `<a href="…" rel="nofollow">iPhone客户端</a>`. The visible
    text is USER-SETTABLE on some accounts — "名号被窝" and "  日常记录 "
    both appeared in a 30-post sample — so this is not a device column and
    the value is written through as found rather than matched against a
    list of devices that would silently drop the custom ones.
    """
    if not raw:
        return None
    text = BeautifulSoup(str(raw), "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def strip_truncation_mark(text: Optional[str]) -> str:
    """Drop the zero-width spaces Weibo appends where it cut a post off."""
    if not text:
        return ""
    return text.rstrip(_ZERO_WIDTH + " \t\r\n")


def looks_truncated(text: Optional[str]) -> bool:
    """Whether a body still ends in a zero-width space.

    NOT a truncation signal, and the measurement is why. Counted 2026-09-21
    over 40 posts from four live hot-feed fetches:

        isLongText  ends U+200B  posts   longtext returned more
        False       True         23      0 of 6
        True        True         17      4 of 6

    **Every post ends in a zero-width space**, truncated or not. Weibo
    appends one to essentially everything it serves, so the mark says
    nothing about whether a body is complete — a marker that matches every
    page is worse than no marker at all (CLAUDE.md §18), and this one
    matches 40 of 40.

    It survives as an OUTPUT-CLEANLINESS check, which is a different job:
    `strip_truncation_mark` removes the mark from every row, so a True from
    this function on a written row means that stripping did not happen. The
    suite asserts no row in the output answers True.
    """
    return bool(text) and text.rstrip(" \t\r\n").endswith(tuple(_ZERO_WIDTH))


def _int_or_none(v: Any) -> Optional[int]:
    """A counter as an int, or None — never a defaulted 0.

    CLAUDE.md §21: a numeric field whose absent state is 0 drags every
    average a consumer computes. Weibo sends real integers on every post
    measured, so this mostly passes them through; what it refuses to do is
    invent a zero for a key that was not there.
    """
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        s = v.replace(",", "").strip()
        if s.isdigit():
            return int(s)
    return None


def _str_or_none(v: Any) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------

def _pic_urls(post: Dict[str, Any]) -> List[str]:
    """The post's own images, largest variant available.

    `pic_infos` is a dict keyed by pic id and each value holds several
    renditions. `largest` is preferred and `original` is the fallback;
    where neither is present the id is skipped rather than guessed at from
    a CDN path template, because the template is a build artefact and the
    ids are the contract.
    """
    infos = post.get("pic_infos") or {}
    out: List[str] = []
    for pid in (post.get("pic_ids") or []):
        info = infos.get(pid) or {}
        for key in ("largest", "original", "mw2000", "large", "bmiddle"):
            node = info.get(key) or {}
            url = node.get("url") if isinstance(node, dict) else None
            if url:
                out.append(url)
                break
    return out


def _video_url(post: Dict[str, Any]) -> Optional[str]:
    """A playable address for a video post, or None.

    `page_info.type` is `"11"` with `object_type: "video"` — the numeric
    type is the site's own and is NOT the string "video", which a naive
    check would look for and never find (measured: 23 of 30 posts in one
    capture carry type 11).

    `page_info.page_url` on those posts is a `sinaweibo://infopage?…` app
    scheme, which no browser can open, so it is never written through as a
    URL. The playable address comes from `media_info`, and when the site
    gives none this returns None rather than the app scheme.
    """
    pi = post.get("page_info") or {}
    if not pi:
        return None
    media = pi.get("media_info") or {}
    for key in ("mp4_720p_mp4", "mp4_hd_url", "mp4_sd_url", "stream_url_hd",
                "stream_url"):
        url = media.get(key)
        if url and str(url).startswith("http"):
            return str(url)
    url = pi.get("page_url")
    if url and str(url).startswith("http"):
        return str(url)
    return None


def _topics(post: Dict[str, Any]) -> List[str]:
    """Hashtags, from `topic_struct[].topic_title`.

    The sibling `title` key on the same object was EMPTY on every topic
    measured, so the obvious-looking field is the wrong one. The value
    carries no `#` marks — Weibo writes a tag as `#拾光纪#` in the body and
    stores the bare word here.
    """
    out = []
    for t in (post.get("topic_struct") or []):
        if isinstance(t, dict):
            title = _str_or_none(t.get("topic_title")) or _str_or_none(t.get("title"))
            if title:
                out.append(title)
    return out


def _links(post: Dict[str, Any]) -> List[str]:
    """Outbound links, as the post published them.

    `short_url` (a t.cn address) is preferred over the `ori_url` beside it,
    because `ori_url` is a `sinaweibo://` app scheme more often than a web
    address — it was one on the first `url_struct` entry measured.
    """
    out = []
    for u in (post.get("url_struct") or []):
        if not isinstance(u, dict):
            continue
        for key in ("short_url", "long_url", "ori_url"):
            val = _str_or_none(u.get(key))
            if val and val.startswith("http"):
                out.append(val)
                break
    return out


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------

def post_url(uid: Optional[str], mblogid: Optional[str]) -> str:
    """`weibo.com/{uid}/{mblogid}` — assembled, because nothing publishes it.

    A feed row carries no absolute address for itself at all: `url_struct`
    holds links to things the post MENTIONS, and the only self-reference is
    a `sinaweibo://` scheme. So this is built from two ids rather than read,
    and it is the one field in this file that is a construction rather than
    a reading. It was verified by fetching several: the two-segment form
    resolves for every post measured.
    """
    if not (uid and mblogid):
        return ""
    return f"https://weibo.com/{uid}/{mblogid}"


def parse_post(node: Dict[str, Any], page: Optional[int] = None,
               position: Optional[int] = None,
               author_followers: Optional[int] = None) -> Optional[Post]:
    """One post object -> one row, or None if it is not a post at all.

    Returns None rather than a half-empty row for a node with no id: a feed
    carries the occasional module card among its posts, and emitting a row
    of nulls for one would put a non-post in the output looking like a post
    whose every field failed to parse.
    """
    if not isinstance(node, dict):
        return None
    mblogid = _str_or_none(node.get("mblogid"))
    mid = _str_or_none(node.get("mid")) or _str_or_none(node.get("idstr"))
    if not (mblogid or mid):
        return None

    user = node.get("user") or {}
    uid = _str_or_none(user.get("idstr")) or _str_or_none(user.get("id"))

    is_long = bool(node.get("isLongText"))
    body = strip_truncation_mark(node.get("text_raw") or "")

    retweet = node.get("retweeted_status") or {}

    return Post(
        source=SOURCE,
        url=post_url(uid, mblogid),
        sku=mblogid or mid,
        title=body or None,
        # Provisional, and settled by `apply_longtext` once the site has
        # been asked. A flagged post starts as a failure and is upgraded,
        # so that a recovery which never ran cannot be mistaken for one
        # that succeeded. A post the site did not flag is whole: 0 of 6
        # unflagged posts had anything to recover when measured.
        text_source="inline" if not is_long else "longtext_failed",
        text_truncated=bool(is_long),
        text_length_claimed=_int_or_none(node.get("textLength")),
        created_at=parse_created_at(node.get("created_at")),
        created_at_raw=_str_or_none(node.get("created_at")),
        author_id=uid,
        author_name=_str_or_none(user.get("screen_name")),
        author_url=(f"https://weibo.com/u/{uid}" if uid else None),
        author_verified=(bool(user.get("verified"))
                         if "verified" in user else None),
        author_verified_type=_int_or_none(user.get("verified_type")),
        author_followers=author_followers,
        reposts_count=_int_or_none(node.get("reposts_count")),
        comments_count=_int_or_none(node.get("comments_count")),
        attitudes_count=_int_or_none(node.get("attitudes_count")),
        region=clean_region(node.get("region_name")),
        client=clean_client(node.get("source")),
        pic_count=_int_or_none(node.get("pic_num")),
        pic_urls=_pic_urls(node),
        video_url=_video_url(node),
        topics=_topics(node),
        links=_links(node),
        is_long_text=is_long,
        is_ad=(bool(node.get("isAd")) if "isAd" in node else None),
        repost_of_sku=_str_or_none(retweet.get("mblogid")) or None,
        repost_of_author=_str_or_none(
            (retweet.get("user") or {}).get("screen_name")) or None,
        mid=mid,
        page=page,
        position=position,
    )


def parse_hot_feed(payload: Any, page: Optional[int] = None) -> List[Post]:
    """`/ajax/feed/hottimeline` -> rows.

    `position` is 1-based within this fetch and `page` is threaded in from
    the engine. Both are needed and neither is sufficient: CLAUDE.md §18
    records a run where `page` was 1 on every row of a two-page run, so 60
    of 119 rows claimed a position another row already held. The suite
    asserts the pair is unique across a multi-page run.
    """
    payload = _payload_dict(payload)
    rows = []
    for i, node in enumerate(payload.get("statuses") or [], start=1):
        row = parse_post(node, page=page, position=i)
        if row:
            rows.append(row)
    return rows


def parse_user_feed(payload: Any, page: Optional[int] = None,
                    author_followers: Optional[int] = None
                    ) -> Tuple[List[Post], Any]:
    """`/ajax/profile/getWaterFallContent` -> (rows, next_cursor).

    The cursor is returned rather than stored, because it is the ONLY thing
    that says whether there is more: this route publishes no total, no page
    count and no "has more" flag. `-1` is the site saying it has finished —
    measured on an account that returned 5 posts and then `-1`, against
    21,947 posts it claims in its own profile. That is an end of listing,
    not a fault, and not the account's archive either; see
    output_writer.run_meta for why the gap goes in the sidecar.
    """
    payload = _payload_dict(payload)
    data = payload.get("data")
    if not isinstance(data, dict):
        return [], None
    rows = []
    for i, node in enumerate(data.get("list") or [], start=1):
        row = parse_post(node, page=page, position=i,
                         author_followers=author_followers)
        if row:
            rows.append(row)
    return rows, data.get("next_cursor")


def parse_comments(payload: Any, parent_sku: Optional[str] = None,
                   page: Optional[int] = None) -> Tuple[List[Post], Any]:
    """`/ajax/statuses/buildComments` -> (rows, max_id).

    A comment is a post: it has an author, a body, a like count and an id of
    its own, so it goes into the same row shape rather than into a second
    dataclass. What it does NOT have is `reposts_count` or `comments_count`
    at the top level, and those stay null rather than being filled with the
    parent's figures — a comment that appears to have 467 comments because
    its parent does is worse than one that admits it has none recorded.

    `max_id` is the continuation token; 0 means there is no more.
    """
    payload = _payload_dict(payload)
    rows = []
    for i, node in enumerate(payload.get("data") or [], start=1):
        if not isinstance(node, dict):
            continue
        user = node.get("user") or {}
        uid = _str_or_none(user.get("idstr")) or _str_or_none(user.get("id"))
        cid = _str_or_none(node.get("idstr")) or _str_or_none(node.get("id"))
        if not cid:
            continue
        body = strip_truncation_mark(
            node.get("text_raw") or BeautifulSoup(
                str(node.get("text") or ""), "html.parser").get_text(" ", strip=True))
        rows.append(Post(
            source=SOURCE,
            url=post_url(uid, _str_or_none(node.get("mblogid"))) or "",
            sku=cid,
            title=body or None,
            text_source="inline",
            text_truncated=False,
            created_at=parse_created_at(node.get("created_at")),
            created_at_raw=_str_or_none(node.get("created_at")),
            author_id=uid,
            author_name=_str_or_none(user.get("screen_name")),
            author_url=(f"https://weibo.com/u/{uid}" if uid else None),
            author_verified=(bool(user.get("verified"))
                             if "verified" in user else None),
            author_verified_type=_int_or_none(user.get("verified_type")),
            attitudes_count=_int_or_none(node.get("like_counts")),
            region=clean_region(node.get("source")),
            pic_count=None,
            topics=_topics(node),
            links=_links(node),
            is_long_text=bool(node.get("isLongText")),
            mid=_str_or_none(node.get("mid")),
            parent_sku=parent_sku,
            comment_id=cid,
            reply_to_comment_id=_str_or_none(node.get("reply_comment_id"))
            or _str_or_none((node.get("reply_comment") or {}).get("idstr")),
            page=page,
            position=i,
        ))
    return rows, payload.get("max_id")


def parse_profile(payload: Any) -> Dict[str, Any]:
    """`/ajax/profile/info` -> the few account facts the rows need.

    Deliberately a dict of four keys rather than a row: an account is not a
    post, and giving it a `Post` would put a row in the output that no
    consumer asked for. `--mode user` calls this once and threads
    `followers_count` onto every row it then writes, which is the only way
    that column is ever populated (a post's embedded `user` object carries
    no follower count at all — 0 of 254 measured).

    `statuses_count` goes to the sidecar, not to a row: it describes the
    account, and a run holding 5 of its 21,947 posts needs to say so
    somewhere a consumer will look.
    """
    payload = _payload_dict(payload)
    data = payload.get("data")
    user = (data or {}).get("user") if isinstance(data, dict) else None
    if not isinstance(user, dict):
        return {}
    return {
        "uid": _str_or_none(user.get("idstr")) or _str_or_none(user.get("id")),
        "screen_name": _str_or_none(user.get("screen_name")),
        "followers_count": _int_or_none(user.get("followers_count")),
        "statuses_count": _int_or_none(user.get("statuses_count")),
        "verified": bool(user.get("verified")) if "verified" in user else None,
    }


def parse_longtext(payload: Any) -> Optional[str]:
    """`/ajax/statuses/longtext` -> the full body, or None.

    Returns None for `{"ok": 1, "data": {}}`, which is what the endpoint
    answers for a post that was never truncated. That is not a failure and
    the caller must not record it as one — it simply means the inline text
    was already whole.
    """
    payload = _payload_dict(payload)
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    text = _str_or_none(data.get("longTextContent"))
    return strip_truncation_mark(text) or None


def apply_longtext(row: Post, full_text: Optional[str],
                   reached: bool = True) -> Post:
    """Settle a flagged row against what /longtext actually said.

    `reached` is the important parameter and it separates two outcomes that
    an earlier version collapsed into one, mislabelling real data:

      reached=True, text longer   the post WAS cut off and is now whole.
                                  `longtext`, not truncated.
      reached=True, nothing back  the endpoint answered `{"ok": 1, "data":
                                  {}}` — there is no more text. The post was
                                  COMPLETE all along and `isLongText` was a
                                  false positive, which is not rare: 2 of 6
                                  flagged posts in a measured sample had
                                  nothing to recover, and one of them was 13
                                  characters long. Marking that row
                                  "truncated" is a lie about the site's data
                                  in the one column that exists to tell the
                                  truth about it. `inline`, not truncated.
      reached=False               the endpoint could not be ASKED — a
                                  timeout, a wall, a transport fault. This
                                  is the only genuine failure, and the only
                                  case where the row stays marked truncated.

    The endpoint is treated as authoritative because it is: it is the site
    answering a direct question about one post. The `isLongText` flag beside
    it is a hint, and `is_long_text` keeps recording it verbatim so a
    consumer can see the disagreement.

    Refuses to SHORTEN a row either way. If the recovered text is no longer
    than the inline text, the reply was about something else — and
    overwriting a correct row with a worse one is the failure CLAUDE.md §4
    names when two views of one value disagree.
    """
    if not reached:
        row.text_source = "longtext_failed"
        row.text_truncated = True
        return row
    if not full_text:
        row.text_source = "inline"
        row.text_truncated = False
        return row
    if row.title and len(full_text) <= len(row.title):
        log.warning("longtext for %s came back no longer than the inline text "
                    "(%d vs %d chars) — keeping the inline text",
                    row.sku, len(full_text), len(row.title))
        row.text_source = "inline"
        row.text_truncated = False
        return row
    row.title = full_text
    row.text_source = "longtext"
    row.text_truncated = False
    return row


def _payload_dict(payload: Any) -> Dict[str, Any]:
    """Accept a parsed dict or a raw body; always return a dict.

    The engines hand back a string (what the browser had) while the tests
    and the HTTP path hand back a parsed object. Accepting both here keeps
    one parse in one place rather than three engines each deciding when to
    call json.loads.
    """
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, (str, bytes)):
        text = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else payload
        s = text.strip()
        if not s.startswith(("{", "[")):
            m = re.search(r"<pre[^>]*>(.*?)</pre>", text, re.S)
            if m:
                s = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        try:
            obj = json.loads(s)
        except (ValueError, TypeError):
            return {}
        return obj if isinstance(obj, dict) else {}
    return {}


def rows_for_mode(mode: str, payload: Any, page: Optional[int] = None,
                  parent_sku: Optional[str] = None,
                  author_followers: Optional[int] = None
                  ) -> Tuple[List[Post], Any]:
    """`(rows, continuation)` for whichever mode is running.

    One entry point the engines share, so that three of them cannot
    disagree about which parser a mode uses — the drift CLAUDE.md §1 says
    to prevent by naming the OPERATION once.
    """
    if mode == "hot":
        return parse_hot_feed(payload, page=page), None
    if mode == "user":
        return parse_user_feed(payload, page=page,
                               author_followers=author_followers)
    if mode == "post":
        return parse_comments(payload, parent_sku=parent_sku, page=page)
    raise ValueError(f"unknown mode: {mode!r}")
