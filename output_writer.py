"""
output_writer.py
-----------------
Shared row models + JSON/CSV writers used by all three scrapers.

Three modes, one row shape
--------------------------
    --mode hot     /ajax/feed/hottimeline            the public hot feed
    --mode user    /ajax/profile/getWaterFallContent one account's posts
    --mode post    /ajax/statuses/buildComments      one post's comments

The leaf Weibo publishes is a POST (a 微博), and all three modes produce
rows of the same shape: `hot` and `user` differ only in which feed the
posts came from, and `post` returns the COMMENTS on one post — which are
themselves posts, with an author, a body and their own like count.

Weibo publishes NO JSON-LD anywhere. Counted on 2026-09-21 across five
captures (hot feed, hot band, hot search, a profile and a waterfall page):
**zero** `application/ld+json` blocks. There is nothing to fall back FROM,
because the site's own JSON API is the primary source and is richer than
anything it renders — CLAUDE.md §4's "count the blocks first", answered.

What the site calls things, and what this file calls them
---------------------------------------------------------
Weibo's JSON uses three different ids for one post and they are not
interchangeable:

    id / idstr   5344883403653158    the numeric id
    mid          5344883403653158    the same number, as a string
    mblogid      "RiPCAfklU"         the base-62 id that appears in a URL

`sku` is `mblogid`, because it is the one that round-trips: it is what
`weibo.com/{uid}/{mblogid}` needs, what `/ajax/statuses/longtext?id=` takes,
and what a human can paste back into a browser. `mid` is kept in its own
column because `/ajax/statuses/buildComments?id=` wants THAT one, and a
scraper that has only the mblogid cannot ask for the comments.

The truncation trap
--------------------
`text_raw` is NOT the post. A post whose `isLongText` is true arrives cut
off at ~150 characters, ending in a zero-width space (U+200B), and nothing
about the field says so: it is a populated string that reads like a whole
post. `/ajax/statuses/longtext?id={mblogid}` returns the rest.

How OFTEN it happens varies enough that a fraction would be a lie by the
next run: on 2026-09-21 it was 4 of 30 posts across two saved captures and
**5 of 10** on a live hot feed an hour later. The recovery is not cosmetic
at that rate — two of those five went from 149 and 146 characters to 1,034
and 1,235, so more than eighty per cent of each body was missing from the
field that looked populated.

So `title` carries the FULL text where one could be recovered, and
`text_source` records which read produced it (`inline` / `longtext` /
`longtext_failed`). This is CLAUDE.md §10's amazon `review_count` lesson:
a column can be 100% populated and entirely wrong, and coverage will not
tell you. Never write `text_raw` through without checking `isLongText`.

`textLength` is NOT a character count of anything this file writes — it
was 341 where the recovered text was 183 characters. It is recorded as the
site states it and nothing is asserted about it.

The number that is not a number
--------------------------------
Every post carries `number_display_strategy`, and on a post with 18 likes
it reads `{"display_text_min_number": 1000000, "display_text": "100万+"}`.
That is the site's DISPLAY RULE — "print 1M+ once a count passes a
million" — not this post's figure, and it is byte-identical on every post
measured. Read as the like count it would put "100万+" on every row of
every run while coverage reported 100%. The integer counters are the
facts; that string is never read.

Whether the integers themselves cap is UNVERIFIED: the largest figure in a
254-post sample on 2026-09-21 was 104,804 likes and 751,449 reposts, and
no post above a million appeared, so the ceiling was never exercised. A
count this file cannot demonstrate is not a count it claims (CLAUDE.md
§20: a column you never saw take its other value is not verified).
"""
import csv
import json
from dataclasses import dataclass, asdict, field, fields
from datetime import datetime, timezone
from typing import Optional, List, Set, Sequence, Any, Type


# The hostname a row came from. Weibo serves the whole world from ONE host
# and has no country TLDs, no locale paths and no per-market catalogue, so
# this column is `weibo.com` on every row of every run. It is kept because
# consumers read it by name across this family.
#
# `m.weibo.cn` is NOT a second source: measured 2026-09-21, every
# `/api/container/getIndex` route on it answers `{"ok":-100}` and a login
# redirect to anonymous and visitor-cookie sessions alike, so no row in
# this repo comes from there.
SOURCE_DEFAULT = "weibo.com"


@dataclass
class Post:
    source: str = SOURCE_DEFAULT
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    # The absolute post URL, built as `weibo.com/{author_id}/{mblogid}`.
    # Weibo publishes no absolute URL on a feed row at all — `url_struct`
    # holds t.cn short links to things the post MENTIONS, and the only
    # deep link it carries is a `sinaweibo://` app scheme that a browser
    # cannot open. The web address is assembled, never read.
    url: str = ""
    # `mblogid` — the base-62 id, "RiPCAfklU". See the module docstring for
    # why this one and not `mid`.
    sku: Optional[str] = None
    # The post body, in full: `text_raw` when the post is short, the
    # recovered long text when `isLongText` said it was cut off.
    #
    # Named `title` because every repo in this family writes this column
    # and a consumer reading several of them reads one schema. A microblog
    # post has no title of its own, so the body is the most title-like
    # thing there is — quora-scraper made the same call for an answer.
    title: Optional[str] = None
    # Which read produced `title`, so a consumer can tell a whole post from
    # a salvaged one rather than guessing from its length:
    #   inline          the post was short; `text_raw` is the whole thing
    #   longtext        it was long and /longtext returned the rest
    #   longtext_failed it was long and the recovery call did not answer —
    #                   `title` holds the truncated text and `text_truncated`
    #                   is True, because a short post and a salvage failure
    #                   must not look alike
    text_source: Optional[str] = None
    # True when the site said `isLongText` and the full text was NOT
    # recovered. False on a whole post. This is the column to filter on
    # before computing anything about post length.
    text_truncated: Optional[bool] = None
    # `textLength` as the site states it. Recorded, never asserted on, and
    # never used to decide truncation — see the module docstring.
    text_length_claimed: Optional[int] = None

    # ---- when -----------------------------------------------------------
    # `created_at` normalised to ISO-8601. The site sends the Twitter-era
    # format — "Sun Sep 21 15:38:42 +0800 2026" — whose offset is Beijing
    # time on every row measured. Parsed with the offset kept, so an
    # ISO-aware consumer gets a real instant rather than a wall clock.
    created_at: Optional[str] = None
    # The site's own string, kept verbatim. A parse that silently failed
    # would otherwise be indistinguishable from a post with no date.
    created_at_raw: Optional[str] = None

    # ---- who ------------------------------------------------------------
    author_id: Optional[str] = None
    author_name: Optional[str] = None
    author_url: Optional[str] = None
    # `verified` plus `verified_type`. Weibo verifies individuals and
    # organisations differently and the type is the only way to tell a
    # person's blue check from a state broadcaster's.
    author_verified: Optional[bool] = None
    author_verified_type: Optional[int] = None
    # Null on a feed row, and that is the site's doing rather than an
    # extraction gap: a post's embedded `user` object carries no
    # `followers_count` at all (measured, 0 of 254 posts). It is populated
    # only where the run also fetched /ajax/profile/info for that account —
    # `--mode user` does, `--mode hot` does not. A null here means "not
    # asked for", never "zero followers".
    author_followers: Optional[int] = None

    # ---- what the post did ----------------------------------------------
    # Plain integers from the site's own JSON. See the module docstring for
    # the display-strategy trap sitting next to them.
    reposts_count: Optional[int] = None
    comments_count: Optional[int] = None
    attitudes_count: Optional[int] = None

    # ---- where from -----------------------------------------------------
    # `region_name` with its prefix removed: the site sends "发布于 广东"
    # ("posted from Guangdong") and only the province is the datum. Present
    # on 9 of 30 posts measured — older posts predate the disclosure, so a
    # null is common and means "the site did not say".
    region: Optional[str] = None
    # `source` — the client the post was made from ("微博网页版", "iPhone
    # 客户端"). Arrives wrapped in an `<a>` tag, and the visible text is
    # USER-SETTABLE on some accounts: values measured include "名号被窝"
    # and "  日常记录 ", which name no device at all. Tags are stripped and
    # the text is written through as found; it is not a device column and
    # nothing should be inferred from it.
    client: Optional[str] = None

    # ---- attachments ----------------------------------------------------
    pic_count: Optional[int] = None
    pic_urls: List[str] = field(default_factory=list)
    # `page_info.type == "11"` with `object_type == "video"` is the video
    # card. The `page_url` on it is a `sinaweibo://` app scheme, so the
    # playable address is taken from `media_info` where the site gives one.
    video_url: Optional[str] = None
    # `topic_struct[].topic_title` — the hashtag WITHOUT its hash marks.
    # The sibling `title` field on that object was empty on every topic
    # measured, which is why the less obvious key is the one read.
    topics: List[str] = field(default_factory=list)
    # `url_struct[].short_url` — the t.cn links a post points at. Kept as
    # the short form because that is what the post actually published; the
    # `ori_url` beside it is a `sinaweibo://` scheme more often than a web
    # address.
    links: List[str] = field(default_factory=list)

    # ---- shape of the post ----------------------------------------------
    is_long_text: Optional[bool] = None
    # `isAd`. Weibo sells promoted posts into the hot feed and they are
    # marked. A consumer measuring organic reach needs to drop them, and
    # cannot if the column is not there.
    is_ad: Optional[bool] = None
    # `retweeted_status.mblogid` when this post quotes another, else null.
    # Measured 0 of 254 on the waterfall route, which returns originals —
    # the column exists because the hot feed is not the same population and
    # a repost there must not silently lose the thing it reposted.
    repost_of_sku: Optional[str] = None
    repost_of_author: Optional[str] = None
    # `mid` — the numeric id. Carried because /ajax/statuses/buildComments
    # and /ajax/statuses/likeShow both take THIS id and not `mblogid`, so a
    # row without it cannot be followed up.
    mid: Optional[str] = None

    # ---- --mode post only -----------------------------------------------
    # The post these comments hang off. Null on `hot` and `user` rows, so a
    # merged file can still be split by where a row came from.
    parent_sku: Optional[str] = None
    # A comment's own id. `sku` holds it too (so one column name works
    # family-wide), but comments and posts share a namespace only by
    # accident and diff_runs.py refuses to compare the two modes anyway.
    comment_id: Optional[str] = None
    # A reply to a reply. Weibo nests one level and the site marks it.
    reply_to_comment_id: Optional[str] = None

    # ---- where in the feed ----------------------------------------------
    # Which fetch this row came from and where it sat. `page` is threaded
    # in from the engine rather than defaulted: a page number that is 1 on
    # every row of a multi-page run is the bug CLAUDE.md §18 names, and the
    # suite asserts `page` + `position` is unique across a run.
    page: Optional[int] = None
    position: Optional[int] = None
# The family's name for the row class, kept as an alias.
#
# Four siblings do the same (`Product = Post`, `= Market`, `= Answer`,
# `= Item`), and the reason is concrete rather than cosmetic: tooling
# written against this family — including a workflow step that imports the
# row class by name — expects `Product`. This repo shipped without it and
# CI went red on the first push. The fix there was to stop the workflow
# reimplementing a shipped check at all; this alias is the second half,
# so anything else reaching for the family name finds it.
Product = Post

ROW_CLASS_BY_MODE = {"hot": Post, "user": Post, "post": Post}

# Modes whose rows are one-per-sku, and therefore safe to dedupe on `sku`
# and to hand to diff_runs.py. All three of this repo's modes qualify: a
# feed names each post once, and `--mode post` dedupes on the comment id
# that `sku` carries for that mode.
UNIQUE_BY_SKU_MODES = ("hot", "user", "post")


def dedupe_by_key(rows: Sequence[Any], seen: Set[str], key: str = "sku") -> List[Any]:
    """Drop rows whose key already appeared earlier in this same run.

    `seen` is mutated in place, so callers thread the same set across pages
    — a repeated page then re-parses without duplicating its rows into the
    final output.

    On Weibo this fires for a REASON worth knowing about, because the hot
    feed is not a paginated list: `/ajax/feed/hottimeline` answers
    `max_id: 1` forever and re-rolls its contents on every call. Four
    consecutive fetches returned 10 posts each and 40 distinct ids on
    2026-09-21, so the overlap was zero that time — but nothing about the
    endpoint promises that, and two fetches a second apart can legitimately
    return the same post. A non-zero drop count in `--mode hot` is normal;
    in `--mode user`, where the cursor really does walk backwards through
    one account, it means a cursor was re-fetched.

    A row with no key is always kept: there is nothing to check a duplicate
    against, and dropping it would be a silent data loss rather than a
    duplicate removal.

    `--mode post` overrides `key` to the comment id, because many comments
    share one parent post and deduping those on the parent would delete
    every comment but the first.
    """
    fresh = []
    for r in rows:
        val = getattr(r, key, None)
        if val is None or val not in seen:
            if val is not None:
                seen.add(val)
            fresh.append(r)
    return fresh


# Kept under its old name: the engines and smoke tests in this family all
# call it, and a listing run does dedupe by sku.
def dedupe_by_sku(rows: Sequence[Any], seen: Set[str]) -> List[Any]:
    return dedupe_by_key(rows, seen, key="sku")


# CSV cannot hold a list. Joining with " | " keeps the cell readable in a
# spreadsheet and round-trippable by splitting on the same separator; the
# JSON output keeps the real list, so nothing is lost for a consumer that
# wants structure. `repr()` of a Python list (the default if this is not
# handled) is neither readable nor parseable by anything but Python.
LIST_CSV_SEPARATOR = " | "


def _csv_value(v: Any) -> Any:
    if isinstance(v, (list, tuple)):
        return LIST_CSV_SEPARATOR.join(str(x) for x in v)
    return v


def write_json(rows: Sequence[Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(r) for r in rows], f, ensure_ascii=False, indent=2)


def write_csv(rows: Sequence[Any], path: str, row_cls: Type = Post) -> None:
    # An empty result still gets the header row. A zero-byte file makes a
    # consumer fail on read (no columns to parse) instead of reading a valid
    # table with zero rows — and "an empty result is still a well-formed
    # result" is the same principle as `save` refusing to overwrite good data.
    #
    # The header comes from `row_cls`, not from the first row, so an empty
    # run still writes the columns of the mode that produced it.
    fieldnames = [f.name for f in fields(row_cls)]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: _csv_value(v) for k, v in asdict(r).items()})


# Exit code used when a run completes but produced nothing. Distinct from 1
# (crash) so a caller can tell "ran, found nothing" from "blew up".
EXIT_NO_PRODUCTS = 4

# Exit code for a run blocked by a bot-check/challenge page before parsing
# even started — distinct from EXIT_NO_PRODUCTS so a caller can tell "the
# feed genuinely held nothing" from "something stood between us and the
# content". See product_parser.detect_bot_challenge.
#
# On Weibo this code does NOT cover an empty answer. A feed that returns
# `{"ok": 1}` with an empty `statuses` list was served exactly as asked and
# is EXIT_NO_PRODUCTS.
#
# What EXIT_BLOCKED means here is the LOGIN wall, and Weibo states it in
# two shapes, both measured 2026-09-21 from a datacenter address:
#
#   {"ok": -100, "url": "https://passport.weibo.com/sso/signin?…"}
#                       HTTP 200. The JSON API's way of saying "sign in".
#                       Every m.weibo.cn container route answers this, to
#                       an anonymous session AND to a visitor-cookie one.
#   {"ok": 0, "message": "前方有点拥堵，请登录后使用"}
#                       HTTP 403, on /ajax/statuses/mymblog. Literally "it
#                       is a bit congested ahead, please log in" — a
#                       refusal wearing a capacity message, which is why
#                       the `ok` field and the status code are what get
#                       read and the prose is not.
#
# Neither is a captcha and neither is geographic. A visitor cookie clears
# the routes this repo uses; the two above want an ACCOUNT, and no exit IP
# changes that. See page_flow.detect_page_state.
EXIT_BLOCKED = 3

# Exit code for a run that gathered SOME rows and then stopped early — a
# page-load timeout, a 503 throttle, or a challenge on page 3 of 10. The
# output file is still written (throwing away three good pages would be
# worse), but it is not a complete picture, and a consumer that cannot tell
# the difference will read the pages that were never fetched as products that
# disappeared from the catalogue. See write_run_meta.
# A REMOTE service failed — the Scraping Browser refusing the connection
# (`profile_locked` is the common one: a profile allows a single live
# connection), or the Scraper API answering an error. Distinct from 1 (a
# crash in this code) and from 2 (bad usage) because it means "try again, or
# use a different profile", not "there is a bug here". Defined once, here,
# because the browser engines and scraper_api_client.py both return it and
# two definitions of the same code is exactly how a family's exit contract
# drifts.
EXIT_API_ERROR = 5

EXIT_PARTIAL = 6


# Exit code for a run that never GOT its pages: a navigation timeout, a dead
# or unauthenticated proxy, a DNS failure, or an edge answering with
# something that is not the page that was asked for.
#
# Distinct from EXIT_NO_PRODUCTS because those are opposite facts. Exit 4 is
# a statement about the CATALOGUE — "we asked, and the answer was nothing" —
# so handing it to a run that never reached the site tells a pipeline the
# listing is empty when nothing was read at all.
#
# 5 rather than a new number, and 5 rather than EXIT_PARTIAL:
#
#   * this family's contract already reserves 5 for a transport failure
#     (scraper_api_client has used it for a remote API error since it was
#     written), so this needs no new code and no per-repo table for a caller
#     driving more than one of these scrapers;
#   * EXIT_PARTIAL (6) means "some rows were gathered and the output is
#     incomplete". A run holding nothing writes no output at all, so a
#     consumer that reads the file on a 6 finds either nothing or the
#     PREVIOUS run's good data, which `save` deliberately does not
#     overwrite. Exit 5 promises no file.
#
# Deliberately NOT applied when rows WERE gathered: a timeout on page 7 of
# 10 is a partial run (exit 6, output written), which is already right. This
# decides only what a run holding nothing reports.
EXIT_FETCH_FAILED = 5


def write_run_meta(out_prefix: str, meta: dict) -> str:
    """Write a run-metadata sidecar next to the output, return its path.

    Deliberately a separate `<out>.meta.json` rather than columns on every
    row: this describes the RUN, not the product, and repeating it across
    every row would both bloat the output and change the schema every
    consumer of this project already parses.

    diff_runs.py reads it to refuse a comparison between runs that are not
    both complete, and between runs of different `mode`.
    """
    path = f"{out_prefix}.meta.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"[+] Wrote run metadata -> {path} (status={meta.get('status')})")
    return path


def run_meta(status: str, stop_reason: str, pages_requested: int,
             pages_completed: int, start_url: str, final_url: str,
             products: int, pages_failed: Optional[List[int]] = None,
             mode: str = "listing", source: str = SOURCE_DEFAULT,
             extra: Optional[dict] = None) -> dict:
    """Build the metadata dict for a finished run.

    `status` is the field a consumer branches on:
      complete — every requested page was fetched, or the site's own
                 pagination genuinely ran out (nothing more existed to get)
      partial  — rows were gathered, then the run stopped early
      failed   — nothing was gathered at all

    `mode` and `source` are recorded because `mode` is not implied by the
    repo: the same output prefix can hold a hot-feed run, a user run or a
    comments run, and those populate different columns — `--mode post`
    fills `parent_sku` and `comment_id`, which are null everywhere else.
    diff_runs.py refuses a pair whose modes or sources differ. `source` is
    `weibo.com` on every row of every run here, since the site has one host
    and no per-market catalogue; it is kept because consumers read these
    columns by name across the family.

    `extra` carries facts about the run that are not about any single row.
    On Weibo it is where the honesty about DEPTH lives, and the sidecar is
    the only place it can live.

    `--mode user` walks a cursor, and how far it walks is the account's
    business rather than the run's: 人民日报 (153,313 posts claimed) yielded
    118 over six cursor pages and kept going, while 雷军 (21,947 claimed)
    returned 5 and then `next_cursor: -1` — an end of listing after five
    rows. Both runs are `complete` in the sense CLAUDE.md §9 means: the
    site served everything it would serve. Neither is the account's
    archive. So `statuses_claimed` (the site's own count for that account)
    goes in beside `products`, and a consumer can see 5-of-21,947 for what
    it is instead of reading it as an account that posts twice a year.

    `--mode hot` records `feed_rerolled`, because that feed is not
    addressable at all — see dedupe_by_key.

    `pages_failed` lists the pages that did not yield data, by number.
    `pages_completed` alone was enough only while pages were fetched strictly
    in order, where "3 of 10 completed" could only mean 1-2-3: a count is not
    a description once pages can be fetched independently and page 3 can fail
    while 4 and 5 succeed. Recording the numbers keeps the sidecar honest
    about WHICH part of the catalogue is missing, not just how much.
    """
    meta = {
        "source": source,
        "mode": mode,
        "status": status,
        "stop_reason": stop_reason,
        "pages_requested": pages_requested,
        "pages_completed": pages_completed,
        "pages_failed": pages_failed or [],
        # Named "products" even though these are posts, and kept that
        # way deliberately: every repo in this family writes this key, and a
        # consumer reading several of them reads one sidecar shape.
        # quora-scraper made the same call for answers. The row TYPE is
        # `mode` plus `source`, which are right beside it.
        "products": products,
        "start_url": start_url,
        "final_url": final_url,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        # Merged rather than nested under a key, so a consumer reads
        # `shop_rating` at the top level beside `products`. Run fields win a
        # name collision: a caller cannot accidentally overwrite `status`.
        meta.update({k: v for k, v in extra.items() if k not in meta})
    return meta


def save(rows: Sequence[Any], out_prefix: str, fmt: str,
         allow_empty: bool = False, row_cls: Type = Post) -> int:
    """Write JSON/CSV and return a process exit code.

    Returns 0 when rows were written, EXIT_NO_PRODUCTS when there were none.
    Callers are expected to exit with it.

    On zero rows, nothing is written at all unless `allow_empty`. Two reasons,
    and a live run demonstrated both. A page-load timeout produced
    `Saved 0 posts -> out.json` and exit 0: a two-byte `[]` that a
    consuming pipeline reads as a successful run with no stock. Worse, if the
    file already held a good result from an earlier run, that result is now
    gone — the failure destroyed the last known good data. So an empty result
    leaves the previous file intact and says why.

    `allow_empty=True` is for the legitimate case: a filter that genuinely
    matches nothing, where an empty file is the answer.
    """
    if not rows and not allow_empty:
        print(f"[!] 0 posts — refusing to write {out_prefix}.json/.csv, so an "
              f"earlier good result isn't overwritten with an empty one. "
              f"Pass --allow-empty if an empty result is the expected answer.")
        return EXIT_NO_PRODUCTS

    if fmt in ("json", "both"):
        write_json(rows, f"{out_prefix}.json")
        print(f"[+] Saved {len(rows)} posts -> {out_prefix}.json")
    if fmt in ("csv", "both"):
        write_csv(rows, f"{out_prefix}.csv", row_cls=row_cls)
        print(f"[+] Saved {len(rows)} posts -> {out_prefix}.csv")
    return 0 if rows else EXIT_NO_PRODUCTS


# Stop reasons that mean the run saw everything there was to see. Anything
# else ended the page loop early, so the result is only a partial view.
#
# "no_new_products" belongs here and "pagination_exhausted" is kept for the
# engines that still stop on a missing next-link: the first is a property
# of the DATA (a page contributed nothing not already seen, so the listing
# is over), while the second is a property of a SELECTOR and is therefore
# the weaker signal.
#
# On Weibo the strongest signal is the site's own cursor. `--mode user`
# ends when `next_cursor` comes back `-1`, which is the account saying
# there is no more to serve — "cursor_exhausted", and a COMPLETE run even
# when it holds five rows against an account claiming 21,947 (see
# run_meta: the sidecar records the gap rather than the status hiding it).
#
# "feed_not_addressable" is complete by construction and is `--mode hot`'s:
# `/ajax/feed/hottimeline` answers `max_id: 1` to every request and
# re-rolls its contents, so there is no page 2 to fail to reach. A run of N
# fetches holds N fetches of a moving feed, which the sidecar says and the
# status must not contradict.
#
# "single_page_mode" is complete by construction: one post's comments are
# asked for once.
COMPLETE_STOP_REASONS = ("completed", "pagination_exhausted", "no_new_products",
                         "cursor_exhausted", "feed_not_addressable",
                         "single_page_mode")


def finish_run(rows: Sequence[Any], out_prefix: str, fmt: str,
               allow_empty: bool, *, blocked: bool, stop_reason: str,
               pages_requested: int, pages_completed: int,
               start_url: str, final_url: str,
               pages_failed: Optional[List[int]] = None,
               mode: str = "listing", source: str = SOURCE_DEFAULT,
               extra: Optional[dict] = None) -> int:
    """Write output + the run-metadata sidecar; return the exit code.

    Shared by all three browser engines so the status/exit-code mapping
    cannot drift between them.

    The metadata sidecar is written ONLY when the row file was written.
    Otherwise a failed run would leave a "status": "failed" sidecar next to
    the previous run's still-intact good output (which `save` deliberately
    does not overwrite) — the two files would contradict each other, and
    diff_runs.py would refuse to compare data that is in fact fine.
    """
    # Completeness is decided by the reason AND by the evidence. A named
    # list of stop reasons cannot cover a failure recorded somewhere else,
    # and `pages_failed` is somewhere else: a run whose loop ended for a
    # COMPLETE reason while individual pages failed reported exit 0 and
    # `status: complete` with a non-empty `pages_failed` in the same
    # sidecar — a file that contradicts itself, and a pipeline branching
    # on `status` reading a short run as a whole one.
    #
    # Found by a third-party audit of a sibling repo and measured across
    # the family by CALLING each `finish_run` rather than grepping for the
    # fix: 28 of 32 repos behaved this way. Same shape as the exit-code
    # unification this file already carries — a rule keyed on a list of
    # names has a hole for every name nobody added to it.
    complete = stop_reason in COMPLETE_STOP_REASONS and not pages_failed
    row_cls = ROW_CLASS_BY_MODE.get(mode, Post)
    rc = save(rows, out_prefix, fmt, allow_empty=allow_empty, row_cls=row_cls)
    wrote_output = bool(rows) or allow_empty

    if wrote_output:
        status = "complete" if (rows and complete) else (
            "partial" if rows else "failed")
        write_run_meta(out_prefix, run_meta(
            status=status, stop_reason=stop_reason,
            pages_requested=pages_requested, pages_completed=pages_completed,
            pages_failed=pages_failed, mode=mode, source=source,
            start_url=start_url, final_url=final_url, products=len(rows),
            extra=extra))

    if not rows:
        # Nothing gathered at all, and WHY decides the code. The three
        # outcomes are different facts and a pipeline branches on them
        # (blocked is not empty is not "never reached"):
        #
        #   blocked            something stood between the run and the content
        #   did not complete   we never got the pages — a dead proxy, a load
        #                      timeout, an edge serving something else
        #   completed          we asked, and the answer was nothing
        #
        # Keyed on `not complete` rather than on a list of stop reasons, on
        # purpose: a list cannot cover a reason nobody has added to it yet,
        # so a new one falls silently through to "the catalogue is empty" —
        # which is the defect this branch exists to prevent.
        if blocked:
            return EXIT_BLOCKED
        if not complete:
            print(f"[!] Nothing was gathered and the run did not finish "
                  f"({stop_reason}) — exit {EXIT_FETCH_FAILED}, NOT an empty "
                  f"result (exit {EXIT_NO_PRODUCTS}). Nothing can be "
                  f"concluded about the catalogue from this run.")
            return EXIT_FETCH_FAILED
        return rc
    if not complete:
        print(f"[!] Partial run: stopped after {pages_completed} of "
              f"{pages_requested} page(s) ({stop_reason}). The output holds "
              f"what was gathered, but it is NOT a complete view — see "
              f"{out_prefix}.meta.json.")
        return EXIT_PARTIAL
    return rc
