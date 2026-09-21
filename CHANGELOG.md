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
- `scraper_api_client.py` is **unverified**: no 2captcha key was available
  while this repo was written. Whether it works depends on whether the
  Scraper API issues its fetch as an XHR-shaped GET or as a navigation — see
  the 403 above. You are unlikely to need it, since the same data is free
  over plain HTTPS.
- `--category` accepts a hot-feed group id; only the default `102803` (推荐)
  was tested.

[0.1.0]: https://github.com/2scraper/weibo-scraper/releases/tag/v0.1.0
