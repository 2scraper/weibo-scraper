#!/usr/bin/env python3
"""
diff_runs.py
-------------
Compares two output files from this project (JSON, as written by
output_writer.save) and reports what changed between them, keyed on `sku`.

    python3 diff_runs.py --old weibo_posts.2026-09-20.json \\
                          --new weibo_posts.2026-09-21.json

Typical use is a scheduled re-run of one account kept under a dated
filename, diffed against the previous one:

    python3 playwright_scraper.py --mode user \\
        --url https://weibo.com/u/2803301701 --out "user_$(date +%F)"
    python3 diff_runs.py --old "$(ls -t user_*.json | grep -v meta | sed -n 2p)" \\
                          --new "user_$(date +%F).json" --out diff.json

Four buckets, each keyed on sku (the post's mblogid, or the comment id in
`--mode post`):

  added          — sku present in --new, absent from --old
  removed        — sku present in --old, absent from --new (deleted, or
                   simply outside how far the cursor walked this time)
  changed        — sku present in both, with a different text, engagement
                   count, region or media. See TRACKED_FIELDS.
  source_changed — sku present in both, but the two rows read the body from
                   different places (`text_source`: `inline`, `longtext`,
                   `longtext_failed`) and differ on the body. Reported
                   separately because a truncated body against a recovered
                   one says something about our own two snapshots rather
                   than about the post — and --fail-on-change deliberately
                   ignores it.

TWO THINGS TO KNOW BEFORE READING A DIFF OF THIS SITE
-----------------------------------------------------
**`--mode hot` is not a census.** The hot feed does not paginate and
re-rolls its contents on every fetch (`feed_rerolled: true` in the
sidecar), so `added`/`removed` between two hot runs describe the feed's
churn, not posts appearing or disappearing.

**`removed` in `--mode user` does not mean deleted.** A user run walks a
cursor until the site says stop, and how far that goes is the account's
business: the sidecar's `statuses_claimed` sits beside `products` for that
reason. A post can leave the file because the walk stopped earlier.

`position` and `page` are deliberately not tracked: new posts arriving at
the top of an account shift every position below them.

A row this project's parser could not recover a sku for (None) cannot be
matched across runs at all, so it is counted and reported separately rather
than silently folded into "added"/"removed", which would be wrong on its face.
"""

import argparse
import json
import re
import sys
from typing import Dict, List, Optional, Tuple

from output_writer import UNIQUE_BY_SKU_MODES

# What is worth watching on a microblog post, and nothing else.
#
# The engagement counters are the integers the site publishes, never the
# "100万+" display rule beside them (see output_writer.py). `pic_urls`,
# `topics` and `links` are lists and compare element-wise.
#
# Deliberately NOT tracked: `position` and `page` (see the module
# docstring), and `scraped_at`, which differs on every run by definition.
TRACKED_FIELDS = (
    # the body, and whether it was recovered
    "title", "text_truncated",
    # engagement
    "reposts_count", "comments_count", "attitudes_count",
    # the author as the row states them
    "author_name", "author_verified", "author_followers",
    # where and what
    "region", "pic_count", "pic_urls", "video_url", "topics", "links",
    "is_ad",
)

# The subset whose value depends on WHICH read produced the body.
#
# A post flagged `isLongText` arrives cut off at about 150 characters in
# `text_raw`; `/ajax/statuses/longtext` returns the rest. When the two rows
# disagree on `text_source`, a body difference is an artefact of one run
# recovering the text and the other not, so it is reported as
# `source_changed` rather than as a change — this family's rule (§8: a
# difference that comes with a provenance difference says something about
# our own two snapshots, not about the site).
TEXT_SOURCE_FIELDS = ("title", "text_truncated")


def _load(path: str) -> List[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _by_sku(products: List[dict]) -> Tuple[Dict[str, dict], int]:
    indexed = {}
    unmatchable = 0
    for p in products:
        sku = p.get("sku")
        if sku is None:
            unmatchable += 1
            continue
        # A run's own output can already hold a duplicate sku (two rows in the
        # same category, or a rerun of dedupe_by_sku's job on older output
        # written before it existed) — keep the first and count the rest as
        # unmatchable rather than letting one clobber the other silently.
        if sku in indexed:
            unmatchable += 1
            continue
        indexed[sku] = p
    return indexed, unmatchable


def diff_products(old: List[dict], new: List[dict]) -> dict:
    old_by_sku, old_unmatchable = _by_sku(old)
    new_by_sku, new_unmatchable = _by_sku(new)

    added = [new_by_sku[sku] for sku in new_by_sku.keys() - old_by_sku.keys()]
    removed = [old_by_sku[sku] for sku in old_by_sku.keys() - new_by_sku.keys()]

    changed, source_changed = [], []
    for sku in old_by_sku.keys() & new_by_sku.keys():
        before, after = old_by_sku[sku], new_by_sku[sku]
        field_changes = {
            field: {"old": before.get(field), "new": after.get(field)}
            for field in TRACKED_FIELDS
            if before.get(field) != after.get(field)
        }
        if not field_changes:
            continue

        # A row whose `text_source` differs between runs is not comparable
        # on the body: one read recovered the long text and the other did
        # not. The other columns still compare fine.
        sources = (before.get("text_source"), after.get("text_source"))
        if sources[0] != sources[1] and any(f in field_changes
                                            for f in TEXT_SOURCE_FIELDS):
            text_part = {f: v for f, v in field_changes.items()
                            if f in TEXT_SOURCE_FIELDS}
            other_part = {f: v for f, v in field_changes.items()
                          if f not in TEXT_SOURCE_FIELDS}
            source_changed.append({
                "sku": sku, "title": after.get("title"),
                "text_source": {"old": sources[0], "new": sources[1]},
                "changes": text_part,
            })
            field_changes = other_part
            if not field_changes:
                continue

        changed.append({"sku": sku, "title": after.get("title"),
                        "changes": field_changes})

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "source_changed": source_changed,
        "unmatchable_old": old_unmatchable,
        "unmatchable_new": new_unmatchable,
    }


def _print_summary(result: dict) -> None:
    print(f"[+] {len(result['added'])} added, {len(result['removed'])} removed, "
          f"{len(result['changed'])} changed, "
          f"{len(result['source_changed'])} not comparable across text sources.")
    for p in result["added"]:
        print(f"  + {p.get('sku')}  {p.get('author_name')}  "
              f"{(p.get('title') or '')[:40]}")
    for p in result["removed"]:
        print(f"  - {p.get('sku')}  {p.get('author_name')}  "
              f"{(p.get('title') or '')[:40]}")
    for c in result["changed"]:
        deltas = ", ".join(f"{f}: {v['old']!r} -> {v['new']!r}"
                           for f, v in c["changes"].items())
        print(f"  ~ {c['sku']}  {deltas}")
    for c in result["source_changed"]:
        src = c["text_source"]
        deltas = ", ".join(f"{f}: {v['old']!r} -> {v['new']!r}"
                           for f, v in c["changes"].items())
        print(f"  ? {c['sku']}  {deltas}  "
              f"[text_source {src['old']!r} -> {src['new']!r}: one run "
              f"recovered the long text and the other did not, so this is "
              f"not a change in the post]")
    unmatchable = result["unmatchable_old"] + result["unmatchable_new"]
    if unmatchable:
        print(f"[!] {unmatchable} row(s) across both files had no sku or a "
              f"duplicate sku, and could not be matched across runs.")


def _run_status(path: str) -> Tuple[Optional[str], Optional[dict]]:
    """Read the `<out>.meta.json` sidecar beside a run's JSON output.

    Returns (status, meta), or (None, None) when there is no sidecar — which
    is the normal case for output written before run metadata existed, or by
    `scraper_api_client.py` (single fetch, no pagination to cut short).
    """
    meta_path = re.sub(r"\.json$", "", path) + ".meta.json"
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None, None
    return meta.get("status"), meta


def _check_comparable(args) -> bool:
    """Refuse an assortment diff between runs that are not both complete.

    This is the failure mode the sidecar exists for: a run cut short on page
    3 of 10 is missing every product on pages 4-10, and diffing it against
    yesterday's full run reports all of them as `removed` — reading as "these
    products were delisted" when in fact they were simply never fetched.
    Prices of the SKUs both runs DID see are still comparable, which is why
    this is a refusal with a --force escape hatch rather than a hard error.
    """
    problems = []
    modes = {}
    for label, path in (("--old", args.old), ("--new", args.new)):
        status, meta = _run_status(path)
        if status is None:
            continue  # no sidecar: nothing to check, see _run_status
        mode = (meta or {}).get("mode")
        if mode:
            modes[label] = mode
        if mode and mode not in UNIQUE_BY_SKU_MODES:
            # This tool's whole premise is one row per `sku`, diffed on
            # price. A mode that produces many rows per sku would give a diff
            # whose every line is an artefact of two rows sharing an id, so
            # it is refused outright rather than answered. Both of this
            # repo's current modes qualify; the check is here so that adding
            # one that does not is caught rather than discovered.
            problems.append(
                f"{label} ({path}) is a {mode!r} run, which is not one row "
                f"per sku. This tool diffs one row per sku on price, so there "
                f"is nothing here it can compare.")
        if status != "complete":
            problems.append(
                f"{label} ({path}) was a {status!r} run — stopped after "
                f"{meta.get('pages_completed')} of {meta.get('pages_requested')} "
                f"page(s), reason {meta.get('stop_reason')!r}")
    if len(set(modes.values())) > 1:
        problems.append(
            f"the two runs are different modes ({modes}). A post row and a "
            f"comment row carry different fields and different ids, so "
            f"`added`/`removed` would describe the mode change rather than "
            f"the content.")

    # And whether either run was a hot-feed run, which changes what
    # `added`/`removed` mean.
    for label, path in (("--old", args.old), ("--new", args.new)):
        _, meta = _run_status(path)
        if (meta or {}).get("feed_rerolled"):
            print(f"[i] {label} ({path}) is a hot-feed run, and that feed "
                  f"re-rolls on every fetch — `added`/`removed` describe the "
                  f"feed's churn, not posts appearing or disappearing.")

    if not problems:
        return True

    # A generic headline, because the reasons below are no longer only about
    # completeness: a mode mismatch and a reviews run are refused too, and a
    # message naming the wrong reason sends the reader looking in the wrong
    # place.
    print("[!] Refusing to diff these two runs:")
    for line in problems:
        print(f"      {line}")
    print("    Re-run the incomplete side, or pass --force to compare anyway "
          "(added/removed will include posts that were simply never "
          "fetched).")
    return False


def parse_args():
    p = argparse.ArgumentParser(
        description="Diff two weibo-scraper JSON outputs by sku.")
    p.add_argument("--old", required=True, help="Earlier run's JSON output.")
    p.add_argument("--new", required=True, help="Later run's JSON output.")
    p.add_argument("--out", default=None,
                   help="Write the full diff as JSON to this path too.")
    p.add_argument("--fail-on-change", action="store_true",
                   help="Exit 1 if anything was added, removed or changed — "
                        "for a cron job that should only notify on a real diff.")
    p.add_argument("--force", action="store_true",
                   help="Diff even when a run's .meta.json says it was partial "
                        "or failed. Products never fetched by the short run will "
                        "appear as added/removed.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not args.force and not _check_comparable(args):
        return 2

    try:
        old = _load(args.old)
        new = _load(args.new)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[!] Could not read one of the input files: {e}")
        return 2

    result = diff_products(old, new)
    _print_summary(result)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[+] Full diff written to {args.out}")

    # `source_changed` is not a reason to fail: it means one run recovered a
    # post's long text and the other did not. That says something about our
    # own two snapshots rather than about the post, and alerting on it would
    # train whoever reads the alert to ignore it.
    if args.fail_on_change and (result["added"] or result["removed"] or result["changed"]):
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
