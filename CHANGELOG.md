# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/) as closely
as a CLI toolkit can. A PATCH release means fixes; it does not mean every
flag and default is frozen, and where a default changes in one, the release
notes lead with it.

## [0.1.0] — 2026-09-21

First release. Three modes, three engines, all run live against weibo.com.

### Added

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
- The Scraping Browser path (`--cdp-endpoint`) remains **untested**: the
  only endpoint available had expired (`401 deny_no_user`). The failure
  path was verified — exit 5, credentials masked, no traceback.

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
