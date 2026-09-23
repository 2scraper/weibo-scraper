# Contributing

Bug reports, site-change reports and pull requests are all welcome. This file
covers the few things specific to a scraper, which are not the usual ones.

## Before you open anything

Run the offline suite. It needs no network, no browser and no API key, and takes
about a second:

```bash
pip install -r requirements.txt
python3 smoke_test.py
```

It prints its own check count, and lists any group it had to skip because an
engine library is absent.

**The suite must pass with no engine installed at all.** CI installs only
`beautifulsoup4` and `requests`, so any import of `playwright_scraper`,
`puppeteer_scraper` or `selenium_scraper` in a test has to sit inside
`try/except ImportError` with the skip recorded. This is easy to get wrong
locally, where you almost certainly have an engine installed and an unguarded
import passes.

If the suite fails on a clean clone, that is itself the bug — say so.

## Never commit a credential

`.env` is in `.gitignore`. Keep it there.

The scrapers mask `user:pass@` in their own log lines, but three things are **not**
masked: raw HTML dumps, the Scraper API's `x-debug` response header, and your
shell history. Before pasting any output into an issue or a PR, replace keys,
proxy passwords and full `ws://user:pass@host:9222` endpoints with `***`.

CI fails the build if something that looks like a credential is committed. That
check is a backstop, not a review — a leaked key has to be rotated whether or
not the check caught it.

## Reporting a site change

Weibo changing its JSON is the normal way this stops working, and it has its
own issue template. The detail that saves the most time is WHICH anchor
broke — and on this site that is not a CSS selector, because the parser does
not read the DOM.

Weibo's front end renders nothing server-side and fetches everything from
`/ajax/` endpoints, and every mode here reads that JSON as an XHR from a
context that has loaded `https://weibo.com/` once:

```
--mode hot    /ajax/feed/hottimeline
--mode user   /ajax/profile/getWaterFallContent   (+ /ajax/profile/info)
--mode post   /ajax/statuses/buildComments
              /ajax/statuses/longtext             for every truncated post
```

So there are only a few things that can break:

1. **The visitor handshake** against `passport.weibo.com`
   (`weibo_api.py`). If it stops minting a cookie, every fetch classifies
   as `needs_visitor`, retries, and the run ends with nothing. Loud.
2. **An endpoint's path or envelope** — the `ok` field and the list the
   posts sit in. A change makes a good payload classify as `unknown`, which
   waits and then reports honestly, rather than returning half a file.
3. **A post's own field names** — `mblogid`, `text_raw`, `isLongText`,
   `reposts_count`, `comments_count`, `attitudes_count`, `region_name`,
   `user`. A rename here is the one that can be QUIET: the row still writes,
   with that column null.
4. **The long-text endpoint.** If it stops answering, flagged posts keep
   their ~150-character `text_raw` and `text_source` reads
   `longtext_failed` — the column says so, but only if someone looks.

If you are reporting a break, say which of those four it is, and attach the
`--dump-html` snapshot. The exact bytes are the only way to tell a parsing
bug from a page that had not arrived.

`--dump-html PATH` writes the exact bytes the parser was given, on success as
well as failure, and a run that finds nothing writes a dump and a screenshot
next to the output on its own.

## Before this repository goes public

One item cannot be undone later, so it belongs on a checklist rather than in
someone's head. **A commit on top cannot reach what a published tag and a
merged PR's refs already hold** — those stay attached to the PR and cannot be
deleted from it. Afterwards, only a fresh repository removes anything.

```bash
python3 .github/ci_checks.py --history-check
```

That applies the same credential rules CI enforces to **every blob that has
ever existed**, not just the working tree. It is deliberately not part of
`--all` and not run by CI: it shells out to git once per object, and a dirty
history needs a decision, not a red check on every push.

Then the rest of the presentation, in the order that matters:

1. `python3 smoke_test.py` green, and the canary dispatched at least once.
   Both of its jobs (the hot feed, and user + comments) run daily with no
   secrets at all and are expected to be green — the README's central claim
   is that these modes need no account, key or proxy, and a green badge is
   that claim under test.
2. The repo description, homepage and topics set (see the family notes on
   what those should say).
3. Only then the row in the org profile README — and check it with an
   ANONYMOUS request rather than your own logged-in browser. A row pointing
   at a private repo is a 404 for every visitor, which costs more trust than
   the missing row.

## Pull requests

**Add a test for the behaviour you are changing.** `smoke_test.py` is a single
file of plain functions with inline HTML/JSON fixtures — no pytest, no
conftest, no fixtures directory. Copy the nearest existing check and edit it.

Some properties in this repo exist because they were once absent or were
measured against expectation, and cost real time. Tests pin them, so a PR
that breaks one will fail rather than silently regress. The README's "Traps
that look like bugs" section has the measurements behind each:

- **`text_raw` is not the post.** A post whose `isLongText` is true arrives
  cut off at about 150 characters, with nothing in the field saying so.
  `/ajax/statuses/longtext` is fetched for every flagged row and
  `text_source` records which read produced `title`. When the endpoint
  answers "there is no more text", the row is whole, not truncated.
- **`number_display_strategy` is not a count.** It reads `100万+` on every
  post, including posts with 18 likes — the site's display rule, not a
  figure. Only the integer counters are read.
- **An `/ajax/` URL fetched as a navigation answers 403 with 21 bytes.** The
  same URL as an XHR is served. That is `bad_request` in
  `page_flow.STATE_POLICY` — not retried, not blocked.
- **The hot feed does not paginate.** It answers `max_id: 1` to every
  request and re-rolls its contents, so `--pages N` in `--mode hot` is N
  fetches of a moving feed, recorded as `feed_rerolled: true`, and
  `--concurrency` above 1 is refused.
- **A complete user run is not the account's archive.** The cursor ends
  where the site says `-1`, and the sidecar records `statuses_claimed`
  beside `products`.
- **A login wall is not a challenge.** HTTP 403 with `请登录后使用` wants an
  account; `STATE_POLICY` never retries it and never spends on it. And the
  captcha words are not markers here: `CAPTCHA_TYPE`, `geetest` and `yidun`
  appear on pages served perfectly normally, and the Scraping Browser's
  auto-solve extension adds more. The block detection keys on the site's
  own `ok` field and HTTP status instead.

Plus the family's own invariants, which are not negotiable:

- **A run that finds nothing writes nothing.** It must not replace a good
  output file with `[]`. `--allow-empty` is the opt-out.
- **Exit codes are a contract**, not decoration: `0` ok, `1` crash, `2` bad
  usage, `3` blocked, `4` zero rows — including a query that genuinely
  matched nothing, which is a correct answer — `5` remote API error, `6`
  partial. A pipeline branches on these.
- **An EMPTY page is never retried and never counted as blocked.** A query
  that matched nothing was served exactly as asked.
- **Credentials never reach argv or a log, and an exception message is a
  log.** The masker is global rather than first-occurrence: a Playwright
  connection error repeats the endpoint five times.
- **Merge in page order, not arrival order**, so concurrency cannot change
  the output.

### If your change needs a live run

Most do not — the suite covers the parser, the writers, the captcha classifier
and the CLI contract against inline fixtures. If yours genuinely needs
weibo.com, say in the PR what you ran, which mode and URL, from which exit,
and what you got — including the sidecar's `status`, `stop_reason` and, for
`--mode user`, `statuses_claimed`.

The three modes need no exit at all: they were measured from a datacentre
address with no proxy and no key, so "it worked from my laptop" is
reproducible here. A `login_wall` from any address is not a finding — it is
a route this repo does not read without an account.

**Run more than the primary engine.** "Mirror them exactly" is a design rule,
not a verification: the first live run of the pyppeteer engine crashed on its
FIRST fetch on a signature mismatch that four separate offline checks and 400
green assertions had not caught.

Do not add anything that posts, comments, likes or reposts. This project
reads; it must never write to Weibo.

## Scope

This repo reads what weibo.com serves an anonymous visitor: the public hot
feed, one account's posts, and one post's comments.

Out of scope: anything behind a login, anything that writes to the site,
and anything that defeats a protection rather than passing it the way an
ordinary browser does. Account login is a TODO rather than a decision
against it, but it is a product change, not a bug fix.

## Licence

MIT. By opening a pull request you agree your contribution ships under it.
