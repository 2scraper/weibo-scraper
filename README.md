# weibo-scraper

[![release](https://img.shields.io/github/v/release/2scraper/weibo-scraper?sort=semver)](https://github.com/2scraper/weibo-scraper/releases)
[![tests](https://github.com/2scraper/weibo-scraper/actions/workflows/tests.yml/badge.svg)](https://github.com/2scraper/weibo-scraper/actions/workflows/tests.yml)
[![canary](https://github.com/2scraper/weibo-scraper/actions/workflows/canary.yml/badge.svg)](https://github.com/2scraper/weibo-scraper/actions/workflows/canary.yml)
[![python](https://img.shields.io/badge/python-3.9%20%E2%80%93%203.13-blue)](pyproject.toml)
[![licence](https://img.shields.io/badge/licence-MIT-green)](LICENSE)
[![engines](https://img.shields.io/badge/engines-Playwright%20%7C%20Selenium%20%7C%20Puppeteer-informational)](#engines)
[![runs without an account](https://img.shields.io/badge/runs%20without-an%20account-success)](#do-you-need-to-buy-anything)

Scrapes [weibo.com](https://weibo.com) — the public hot feed, one account's
posts, and one post's comments — in JSON or CSV, through Playwright,
Selenium or Puppeteer.

```bash
pip install -r requirements.txt -r requirements-playwright.txt
python -m playwright install chromium

python playwright_scraper.py --mode hot --pages 3 --format both --out weibo_posts
```

That command needs no account, no API key and no proxy. The section below
says what was measured, and from where.

---

## Do you need to buy anything?

**For these three modes: no.** Not a 2Captcha key, not a proxy, and — the
one most people expect to need on a Chinese site — **not a Chinese exit
IP.**

Measured on **2026-09-21**, from a Hetzner datacenter address in **Finland**
(AS24940), with no proxy and no key:

| What | Result |
|---|---|
| Any geographic block on weibo.com | **None.** `/ajax/side/hotSearch` returned 22 KB of JSON to a bare `curl`, cold, no cookies |
| The anonymous-visitor handshake | **5 mints out of 5**, `retcode: 20000000`, ~1.5 s each |
| Rate limiting | **100 requests in 83 seconds, zero refusals** across two endpoints |
| Hot feed, user posts, comments | All returned data on every attempt |

### Then what is actually closed, and why a Chinese proxy will not open it

Three routes refuse, and they refuse for a reason no exit address changes:

| Route | Answer | What it wants |
|---|---|---|
| `/ajax/statuses/mymblog` | HTTP 403, `请登录后使用` ("please log in") | an **account** |
| `m.weibo.cn/api/container/*` | `{"ok": -100}` + a login redirect | an **account** |
| `s.weibo.com` (search) | redirect to the login page | an **account** |

The wall is a **login wall, not a geographic one**. A request from Beijing
gets the same "please log in" as a request from Helsinki, because what is
missing is a signed-in session cookie, not proximity. So if you were
considering Chinese proxies for this: they would not help with those three,
and they are not needed for anything else. **This repo does not implement
account login** — that is a TODO, not a limit of the site or of any product,
and if you add it, those routes open.

### What the paid products *do* buy here

Honestly, and narrowly:

* **volume from many addresses.** One address reading a public feed all day
  is a different proposition from one address reading it a few times. The
  measurement above is 100 requests, not 100,000.
* **a specific exit country**, if you want the feed a particular region
  sees. Not measured here — every run in this repo came out of Finland.
* **no browser infrastructure**, via `--cdp-endpoint` (the Scraping Browser
  API). **Untested here:** the only endpoint available while this was
  written had expired — a profile's credentials live about a day — and it
  answered `401 deny_no_user`. What that did verify is the failure path:
  the engine reports exit 5 with the password masked in every occurrence,
  rather than crashing. To test it properly, put a fresh endpoint in
  `.env` as `WEIBO_CDP_ENDPOINT` (the per-site prefix matters — a variable
  under another repo's name is reported as unrecognised and ignored).

  The other browserless option, `scraper_api_client.py`, **does not reach
  these URLs** — measured, see below.

What they do **not** buy is captcha solving, because nothing challenged this
code — see [Captchas](#captchas).

---

## The three modes

```bash
# the public hot feed (推荐). Not paginated — see below.
python playwright_scraper.py --mode hot --pages 3

# one account's posts, walked by cursor
python playwright_scraper.py --mode user --url https://weibo.com/u/2803301701 --pages 5

# one post's comments
python playwright_scraper.py --mode post --url https://weibo.com/2803301701/RiPCAfklU
```

| Mode | Endpoint | Pagination |
|---|---|---|
| `hot` | `/ajax/feed/hottimeline` | **none** — see below |
| `user` | `/ajax/profile/getWaterFallContent` | a cursor the site hands back |
| `post` | `/ajax/statuses/buildComments` | a `max_id` continuation |

---

## Traps that look like bugs

Every one of these was hit while building this, and each would otherwise
read as a fault in the scraper.

### `text_raw` is not the post

A post whose `isLongText` is true arrives **cut off at about 150
characters**, and nothing in the field says so — it is a populated string
that reads like a whole post. This scraper fetches
`/ajax/statuses/longtext` for every flagged row and records which read
produced the text in **`text_source`**.

How often it matters varies enough that a single fraction would be stale by
the next run: on 2026-09-21 it was 4 of 30 posts across two saved captures
and **5 of 10** on a live hot feed an hour later. It is not a cosmetic
difference — two of those five went from **149 and 146 characters to 1,034
and 1,235**.

And the site's own flag over-reports: some flagged posts have nothing to
recover (one was 13 characters long). When the endpoint answers "there is no
more text", the row is marked **whole**, not truncated. `text_truncated` is
True only when the endpoint could not be asked.

### The number that is not a number

Every post carries this, identically, including posts with 18 likes:

```json
"number_display_strategy": {"display_text_min_number": 1000000, "display_text": "100万+"}
```

That is the site's **display rule** — "print 1M+ once a count passes a
million" — not the post's figure. Read as a like count it would put
`100万+` on every row of every run while every coverage check reported 100%.
This scraper reads the integers beside it and never that string.

Whether those integers themselves cap is **unverified**: the largest figure
in a 254-post sample was 104,804 likes, so the ceiling was never exercised.

### A 403 that is not a block

Navigating a browser straight to one of the API URLs answers
`403 {"error":"Forbidden"}` — 21 bytes. The same URL, same cookies, fetched
the way the site's own front end fetches it, returns the payload. Measured,
one session, three ways:

| How | Result |
|---|---|
| `page.goto(api_url)` | **403**, 21 bytes |
| `context.request.get(api_url, headers)` | 200, 188,547 bytes, 10 posts |
| `fetch()` from a loaded weibo.com page | 200, 272,917 bytes, 10 posts |

Weibo declines a top-level **navigation** to its own API. The engines here
land on `https://weibo.com/` once and read every payload as an XHR from
that context. If you see that 403, it is the wrong *kind* of request — not
geography, not a rate limit, not a block.

### The hot feed does not paginate

`/ajax/feed/hottimeline` answers `max_id: 1` to every request, **including
the request that passes `max_id=1` back to it**, and re-rolls its contents.
There is no page 2; there is only "fetch it again and see what is there
now". `--pages 3` in `--mode hot` therefore means *three fetches of a moving
feed*, which the sidecar records as `stop_reason: feed_not_addressable` and
`feed_rerolled: true`. `--concurrency` above 1 is refused for the same
reason: a worker cannot be handed a page that has no address.

### "Complete" and "the whole account" are different things

`--mode user` walks a cursor until the site says `-1`. How far that goes is
the account's business, not the run's. Two accounts, same day:

| Account | Posts it claims | Rows over the cursor |
|---|---|---|
| 人民日报 | 153,313 | 118 over six pages, still going |
| 雷军 | 21,947 | **5**, then `next_cursor: -1` |

(Both figures are from 2026-09-21 and both are live counters: the first read
153,313 and 153,314 an hour apart during testing, and the follower count on
the same account moved three times in one afternoon. Treat them as the shape
of the gap, not as the gap.)

Both runs are `status: complete` — the site served everything it would
serve. Neither is the account's archive. The sidecar records
`statuses_claimed` beside `products` so a five-row file cannot be mistaken
for an account that posts twice a year.

### Other small ones

* **`region` needs its prefix removed.** The site sends `发布于 广东`
  ("posted from Guangdong"). It is present on roughly a third of posts —
  older ones predate the disclosure — so a null means "the site did not
  say", not "unknown location".
* **The posting client is user-settable.** Values include ones naming no
  device at all. It is not a device column.
* **`textLength` counts something else.** It read 341 where the recovered
  text was 183 characters. Recorded, never relied on.
* **Zero-width spaces are everywhere.** Every post ends in one, truncated or
  not (40 of 40 measured), so it is not a truncation signal. Stripped from
  the output.
* **`m.weibo.cn` is not a fallback.** Every container route on it answers
  `{"ok": -100}` to an anonymous session *and* to a visitor-cookie one.

---

## Captchas

**No challenge was rendered on any route this repo reads.** Five captures,
zero widgets, zero challenge iframes.

That is not the same as "Weibo has no captcha". It has two wired into its
ordinary page chrome, and both are preloaded on a page that is being served
perfectly normally:

```
var CAPTCHA_TYPE = 'yidun';                      NetEase Yidun
<script defer src="https://static.geetest.com/v4/gt4.js">   GeeTest v4
```

Counted on a served profile page: `CAPTCHA_TYPE` ×4, `geetest` ×1, `yidun`
×1 — **on a good page, with nothing being challenged**. So on this site not
even a vendor's loader is a usable block marker. Anything keying on the word
`captcha` or `geetest` calls every page a challenge; the block detection
here keys on the site's own `ok` field and HTTP status instead.

`--solve-captcha` is wired and defaults to `when-blocked`, and is expected
never to fire. It is there because a challenge appearing on these routes
later would be a change in the site rather than an impossibility. If one
ever does appear, that is worth an issue.

---

### The Scraper API does not reach these URLs

Measured 2026-09-21 with a live key, so the bill proves the call happened:

| Request | Upstream | Bytes | Price |
|---|---|---|---|
| `https://weibo.com/` | 200 | 3,444 | $0.0005 |
| an `/ajax/` feed URL | **403** | 21 | $0.0005 |
| …plus `X-Requested-With` + `Referer` | **403** | 21 | $0.0005 |
| …via `requestHeaders` instead | **403** | 21 | $0.0005 |

Those 21 bytes are `{"error":"Forbidden"}` — the same signature a browser
`page.goto()` gets. The product fetches by **navigation**, and that is the
shape Weibo refuses on its own API; custom headers did not change it.

This is a fact about how the fetch arrives, not about the product — it
returns weibo.com's own pages perfectly well, and sibling repos in this
family use it successfully. `scraper_api_client.py` is kept so the
measurement has somewhere to live and so a future change on either side has
a client ready to test it, and it now exits 5 with that explanation rather
than reporting "0 posts".

---

## Output

One row shape across all three modes, 36 columns, JSON and CSV in the same
order. See [`sample_output.json`](sample_output.json) — cut from a real run.

```
source  scraped_at  url  sku  title  text_source  text_truncated …
```

* `sku` is `mblogid`, the base-62 id that appears in a URL. `mid`, the
  numeric id, is its own column because the comments endpoint takes *that*
  one and a row without it cannot be followed up.
* `title` carries the post body. A microblog post has no title, and every
  repo in this family writes this column, so one schema works across them.
* Every run writes `<out>.meta.json` beside the data: status, stop reason,
  which fetches failed by number, and the mode-specific honesty fields.

**Exit codes:** `0` ok · `1` crash · `2` bad usage · `3` blocked ·
`4` zero posts · `5` remote API error · `6` partial.

**A run that finds nothing writes nothing** — last night's good output is
not replaced with an empty file. `--allow-empty` opts out.

---

## Engines

Playwright is primary. The other two exist for parity and must agree on exit
codes, run status and rows; all three were run live.

| | Playwright | Puppeteer | Selenium |
|---|---|---|---|
| Reads the API without JavaScript | yes, `context.request` | no, in-page `fetch` | no, `execute_async_script` |
| Authenticated `--cdp-endpoint` | yes | yes | **no** — see below |
| Authenticated `--proxy` | yes | yes | **no**, stripped with a warning |
| `--fingerprint` | yes | no | no |

**Selenium cannot use an authenticated CDP endpoint.** Playwright's
`connect_over_cdp` and Puppeteer's `browserWSEndpoint` take a full
`ws://user:pass@host:port` and authenticate on the WebSocket upgrade;
chromedriver's `debuggerAddress` takes a bare `host:port` with nowhere to
put a password. It is not a generic "cannot connect to CDP".

**Install exactly one engine per virtualenv.** Playwright and pyppeteer
declare mutually unsatisfiable pins, and pyppeteer and selenium collide on
`urllib3`.

```bash
python -m venv venv-playwright && ./venv-playwright/bin/pip install -r requirements.txt -r requirements-playwright.txt
```

---

## Configuration

Credentials live in `.env` next to the scripts, never on a command line — a
secret in `argv` is readable by anything that can run `ps`.

```bash
cp .env.example .env
python3 env_config.py     # prints what was picked up, WITHOUT secrets
```

Precedence: **an explicit flag → an exported variable → `.env` → default.**

---

## Tests

```bash
python3 smoke_test.py        # offline; passes with no engine installed
python3 .github/ci_checks.py --all
```

The suite runs ~4,900 checks with inline fixtures cut from real captures.
Fixture authors and bodies are placeholders: republishing a private
individual's name and words is a separate act from the site showing them on
its own page, and the checks need the structure of a post, not the person.

The canary runs a real 3-page scrape daily **from a bare GitHub runner with
no secrets**, because the claim at the top of this file is that you do not
need any. If Weibo ever closes these routes, that badge goes red the next
morning.

---

## Licence and scope

MIT. This reads content Weibo serves publicly to an anonymous visitor. It
does not log in, does not post, and does not touch anything behind an
account. Please read Weibo's terms and your local law before running it at
volume, and be considerate with `--delay`.
