# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/) as closely
as a CLI toolkit can. A PATCH release means fixes; it does not mean every
flag and default is frozen, and where a default changes in one, the release
notes lead with it.

## [Unreleased]

> **Behaviour change.** `--allow-empty` now writes an empty file only for a
> genuine empty result, and its sidecar says `status: empty` (was
> `failed`). A blocked or failed run with `--allow-empty` no longer
> replaces an earlier output with an empty file. It keeps its exit code
> (3 or 5) and leaves that output and its sidecar untouched.

### Fixed

- **`--allow-empty` could destroy the output it exists to protect.** With
  the flag, a blocked run or a run that never obtained its content wrote
  an empty JSON and CSV over the previous good result, plus a sidecar
  saying `failed`. A genuinely empty, complete run was labelled `failed`
  too, so `diff_runs.py` refused it. Now:
  - A finished run whose answer was nothing writes `[]`, a header-only
    CSV and a sidecar with `status: empty` and its `stop_reason`. It
    exits 4.
  - Blocked (3), never obtained or parser failure (5) and partial (6) keep
    their exit codes. No empty row file is written for them, and an
    earlier output at the same `--out` is kept byte for byte, sidecar
    included.
  - When nothing earlier exists, a blocked or failed `--allow-empty` run
    writes only a sidecar, with `status: blocked` or `failed`.
  - `diff_runs.py` compares an `empty` run like a complete one.

## [0.1.0] — 2026-10-15

> **Behaviour changes in this section.**
> - Output files are created with the mode your umask gives (`0644` under
>   the usual `022`) instead of `0600`, and an existing file keeps its mode.
> - A served page whose posts the parser cannot read now ends as
>   `stop_reason: parser_found_nothing`: exit `5` on the first page, `6`
>   later. It used to be exit `4` (empty) or `0` (complete).
> - `--mode post`: a `/statuses/show` lookup that is refused is exit `3`,
>   one that fails is exit `5`, and a post that does not exist is exit `4`.
>   Every failed lookup used to be exit `4`.
> - A rendered GeeTest or NetEase Yidun challenge is now `blocked` (exit
>   `3`); it used to be retried as an unknown page and end as exit `5`.
> - Newly refused flag values (exit `2`, naming the flag): negative
>   `--retries`, `--delay`, `--retry-delay`, `--proxy-block-retries`;
>   `--concurrency` below 1 or above 1 (it used to warn and run with 1);
>   a non-numeric `--category`; `--fingerprint` on the Puppeteer and
>   Selenium engines, which never applied it; and in
>   `scraper_api_client.py` negative `--retries`/`--retry-delay` and a
>   `--timeout` below 1.

First release. Three modes, three engines, all run live against weibo.com.

### Added

- **A browserless CLI: `python weibo_api.py --mode hot --pages 3`.** The
  README's central claim is that none of the paid products is needed for
  these three modes, and until now no COMMAND stood behind it —
  `WeiboClient` was library-only and every runnable entry point launched
  Chromium. Verified live: all three modes, 30 / 38 / 20 rows, output
  column-for-column identical to the browser engines' (36 columns), and
  the sidecar records `transport: "http"` so a consumer can tell which
  path produced a file. It is a fourth command rather than an
  `--engine http` flag on the three engines, whose one identical flag set
  the suite asserts in both directions. It offers a SUBSET of the family
  flags and deliberately omits `--cdp-endpoint`, `--fingerprint`,
  `--fp-*` and `--headless`: those describe a browser, and there is none
  here.
- `--mode hot` — the public hot feed (`/ajax/feed/hottimeline`).
- `--mode user` — one account's posts, walked by cursor
  (`/ajax/profile/getWaterFallContent`).
- `--mode post` — one post's comments (`/ajax/statuses/buildComments`).
- `weibo_api.py` — the anonymous-visitor handshake against
  `passport.weibo.com`, shared by all three engines and usable on its own as
  a browserless reader.
- Long-text recovery: `/ajax/statuses/longtext` is fetched for every post
  the site flags as truncated, with provenance in `text_source`.
- 36-column row schema, JSON + CSV, with a `<out>.meta.json` sidecar per run.

### Fixed

- **One paid solve per page, across all its retries.** The solve allowance
  was created per attempt, so a page could buy a solve on every attempt.
  It is now created once per page in all three engines. Under the default
  `--solve-captcha when-blocked`, nothing is bought until a response has
  been read and classified as a challenge; `always` also checks the landing
  page before each attempt.
- **A rendered challenge is classified as one.** The `challenge` state had
  a policy row and no producer. The solve path builds reCAPTCHA tasks only;
  this repo does not implement GeeTest v4 or NetEase Yidun solving, and the
  README now says so.
- **Failures outside the per-page guard no longer crash with exit 1.** A
  timeout on the `--mode user` profile call (which only adds context
  columns), on the `--mode post` lookup, or a visitor handshake that cannot
  reach passport during a retry, is handled as a page or lookup failure, so
  rows from earlier pages are still written.
- **CSV formula escaping also covers a leading line feed**, and a
  `csv_cells_escaped` value passed in by a caller is no longer overwritten.
- **`--category` reaches the request.** It was accepted and ignored; the
  hot feed's group id is now the one given (only the default `102803` is
  verified). `weibo_api.py` accepts it too.
- **`scraper_api_client.py` defaulted to `--mode profile`**, a mode its
  parser does not have, and its `--category` help described behaviour it
  does not have.
- **pyppeteer applies `--locale`** (as Chromium's `--lang`), and an
  authenticated `--proxy` falls back to CDP's `Fetch` domain when
  `page.authenticate` fails because current Chromium lacks the method it
  needs. That fallback is covered offline and not yet run against a live
  proxy.
- **The credential scan** judges its exclusions relative to the
  repository, so a clone under a directory named `venv` no longer scans
  zero files and passes; a scan that reads nothing now fails; and `.env*`
  backups, `.json`, `.html` and `.csv` files are read.
- `weibo_api.py` retries a transient answer (`--retries`, `--retry-delay`,
  the same extra-attempt meaning as the engines) and never retries a wall.
- Comments and help text that cited private working notes now state the
  rule itself; a suite check keeps such citations out. Leftover wording
  about prices and product pages was removed.
- **Every file is now written atomically**, to a temporary file beside the
  target and then renamed over it, with an `fsync` first. `open(path,
  "w")` truncates BEFORE writing, so a crash, a kill or a full disk
  halfway through left a shorter file where a complete one had been —
  breaking the same promise `save()` keeps when it refuses to overwrite
  good output with an empty result. Raised by a third-party audit, which
  ranked it last; it belongs first. The sidecar is covered too, which the
  audit did not mention and which matters more: it is the file a consumer
  branches on. Measured across the family the same day: 37 of 43 repos
  write in place. The implementation is lifted from the six that do not.
- **CSV cells that begin `=`, `+`, `-` or `@` are neutralised** with a
  leading apostrophe, so a spreadsheet reads them as text rather than
  executing them. The text on this site is written by whoever wrote the
  post, so a payload aimed at somebody else's export is ordinary rather
  than hypothetical. **The JSON is untouched** — it carries the site's
  bytes — and the run's sidecar records `csv_cells_escaped`, so the
  divergence between the two outputs is declared rather than discovered.
  Measured: 1 cell in 454 rows from earlier runs, 2 in a live 30-row run.
  A negative number stays a number; escaping it would turn a count into
  text. Family-wide: 0 of 43 repos did this.
- **A usage error now exits 2, not 1.** Every CLI raised a bare
  `SystemExit("message")` for a missing or unreadable `--url`, which exits
  1 — the code for a crash in this project's own table — while argparse's
  errors in the same command already exited 2. Four call sites in each of
  the four CLIs.
- **`--mode hot` no longer logs "page N".** That feed answers `max_id: 1`
  forever and re-rolls, so N is N fetches of a moving feed; the log now
  says `fetch 1`, `fetch 2`, and the README warns at the example rather
  than 160 lines below it. The FLAG keeps its name: `--pages` is the
  family contract across ~30 repos and this repo's own suite asserts the
  three engines declare exactly that set, so renaming it here would break
  the one thing that makes the family comparable.
- **The Scraper API engine failed on every `--wait-text` / `--wait-element` /
  `--wait-state` call, and was billed for it.** It sent `waitFor` as a
  JSON-encoded string; measured 2026-09-23 the live API answers that with
  HTTP 422 "params.waitFor must be an object" and still charges $0.0005,
  while the same request with an object is answered 200. It is now sent as
  an object. **And the target site's status was never seen:** the client
  read the response's `status`, which is the API's own verdict string
  ("success"), instead of `http_code`, the target's HTTP status — so a
  target 403/503 reached the page classifier as "success". It now reads
  `http_code` (falling back to `status` only if that is an int). A
  regression check drives the real `fetch_html` with `requests.post`
  stubbed.
- `diff_runs.py` diffed BBB's columns (`rating_grade`, `city`,
  `complaints_total`, …), none of which a Weibo row has, so it could never
  report a change. It now tracks this repo's own columns (body, engagement
  counts, author, region, media) and reports a body difference that comes
  with a `text_source` difference as `source_changed`. BBB's sort and
  225-row-cap refusals, which could not fire here, are gone; a hot-feed run
  now carries a note that its `added`/`removed` describe the feed re-rolling.
  The `--help` description named bbb-scraper.
- `scraper_api_client.py` logged "BBB did not serve the Scraper API's
  request", with BBB's measurements, behind a check for a `"blocked"` state
  this repo's `page_flow` does not define. It now fires on any state the
  policy counts as blocked and names Weibo's login wall.
- `captcha_solver.py`: the docstring and the "what is not here" note
  described BBB's Cloudflare gate. They now describe what was measured here.
- `CONTRIBUTING.md` was about BBB throughout (markup anchors, sort, page cap,
  Cloudflare markers, forms). Rewritten for this repo from the README.
- Issue templates described Etsy (DataDome, currency by exit IP, an etsy.com
  example URL) and linked a `TROUBLESHOOTING.md` this repo does not have.
- `claude.yml` / `claude-code-review.yml` told you to set the secret on
  `2scraper/bbb-scraper`; `.dockerignore` listed `bbb_businesses.*` instead
  of this repo's `weibo_posts.*`; the Dockerfile example wrote to
  `/out/restaurants`; `SECURITY.md` mentioned Cloudflare.
- README: the release badge is removed, since no release has been published
  on GitHub yet.
- `captcha_solver.py`'s docstring pointed at a "No DataDome solver" section
  that does not exist in this repo (it came with the copied core). Removed.

### CI

- The canary also runs `--mode post` on the most-commented post from the
  user run, and the browserless `weibo_api.py`, each with row floors and
  non-null core fields. `weibo_api.py` is in the `--help` checks.

### Measured, on 2026-09-21, from a datacenter address in Finland

- **No geographic block and no Chinese exit needed.** The visitor handshake
  succeeded 5 times out of 5, and 100 requests in 83 seconds drew zero
  refusals. The routes that do refuse (`/ajax/statuses/mymblog`, every
  `m.weibo.cn` container route, `s.weibo.com`) want a logged-in **account**,
  which no exit address supplies. Account login is not implemented here.
- **No captcha was rendered on any route this repo reads.** Weibo preloads
  two vendors (NetEase Yidun and GeeTest v4) into its ordinary page chrome,
  on pages that are being served normally — so neither vendor's name nor its
  loader is a usable block marker on this site. Block detection keys on the
  site's own `ok` field and the HTTP status instead.
- **`text_raw` is truncated on long posts** with no marker a consumer would
  notice: 4 of 30 posts across two captures, 5 of 10 on a live feed an hour
  later, with two recovered bodies growing from ~148 characters to over a
  thousand.
- **`number_display_strategy.display_text` reads `100万+` on a post with 18
  likes.** It is the site's display rule, not a figure, and is never read.
- **A browser navigation to an API URL answers `403 {"error":"Forbidden"}`**
  while the same URL fetched as an XHR returns the payload (21 bytes against
  188,547).

### Known limitations

- Selenium cannot use an authenticated `--cdp-endpoint`, and cannot
  authenticate a `--proxy` at all; both are refused or stripped with the
  reason stated.
- `scraper_api_client.py` **does not reach these URLs**, and that is now
  measured rather than guessed: with a live key it returned upstream 403 and
  21 bytes (`{"error":"Forbidden"}`) for an `/ajax/` URL, with and without
  custom request headers, while returning `https://weibo.com/` itself at 200
  and 3,444 bytes. The product fetches by navigation, which is the shape
  Weibo refuses on its API. It exits 5 with that explanation, and the file
  is kept so the measurement has somewhere to live.
- The fingerprint path was broken until a key arrived: the engines called a
  `fetch_fingerprint` helper that `fingerprint_client` has never defined (it
  is `get_fingerprint`), and the engine's own hand-rolled field translation
  read `fp["timezone"]` and `fp["locale"]`, which the API puts under
  `fp["intl"]`. Both are fixed by delegating to the module's own
  `playwright_context_kwargs`. Measured accepted `--fp-tags` values:
  **Windows, Microsoft Windows, Linux, Android**, case-insensitively —
  everything else tested answers HTTP 400, including every Apple platform.
- The captcha handler called a `solve_on_page` helper that `captcha_solver`
  has never defined, in all three engines. Rewritten against the real API
  (static detection, live detection, reconcile, solve, inject). It would
  have raised AttributeError at the moment a challenge first appeared.
- The proxy path was broken four ways, all of them unreachable until a
  proxy or a CDP endpoint was passed: `ProxyPool.from_args` (a module
  function, not a classmethod), `pool.next()` and `pool.mark_failed()`
  (neither has ever existed), and `--proxy-rotate` defaulting to
  `on-block` while `proxy_pool.ROTATE_MODES` accepts only `per-run` and
  `per-page` — so **every run that passed a proxy raised before fetching
  anything**. The engines now read the constant instead of restating it,
  and a rotation rebuilds the browser rather than swapping the exit under
  a live session.
- The engines also hand-rolled three helpers the shared module already
  provides correctly (`to_playwright`, `split_credentials`, `mask`); they
  now call them.
- `--proxy` with `--cdp-endpoint` is refused with exit 2 (bad usage) and
  the check runs BEFORE the pool is built — it used to sit after, so the
  run crashed on the way to the message instead of reading it.
- The Scraping Browser path (`--cdp-endpoint`) is **verified**, and fixing
  it found a defect worth naming: the engines minted the visitor cookie
  over HTTP from the LOCAL address and installed it into the remote
  browser. Measured in one session, the remote browser left from a
  residential US address (AS11776, California) while the handshake left
  from a Finnish datacenter (AS24940) — a cookie "issued against exit
  A, replayed from exit B", and it also wasted what the Scraping Browser is
  bought for, since the cookie IS the session identity. A remote session
  now navigates to weibo.com and lets the SITE run its own handshake.
  Playwright and pyppeteer both verified live; Selenium refuses an
  authenticated endpoint by design and says why.
- Counted the block markers on a page fetched THROUGH the Scraping Browser,
  which every earlier count in this repo had missed: its auto-solve
  extension injects `cf-turnstile`, `turnstile`, `hunter.js` and
  `chrome-extension://` into every page, and takes `captcha` from 4
  occurrences to 19 — on a page Weibo served normally. The marker set was
  already narrow enough to return None, and the suite now pins that against
  a fixture built from those counts.

### Checks added, each controlled by planting the fault

- The signature-binding check now covers `fingerprint_client` and
  `captcha_solver`, and attribute calls on from-imported CLASSES. It had
  covered neither, which is how three non-existent function names shipped.
- A new check binds methods called on a parameter against the class its
  ANNOTATION declares — the case the binding check cannot see, because a
  local name shadows a module by design.
- A new check asserts every value a flag offers is one the shared module
  accepts.
- `--category` accepts a hot-feed group id; only the default `102803` (推荐)
  was tested.

[0.1.0]: https://github.com/2scraper/weibo-scraper/releases/tag/v0.1.0
