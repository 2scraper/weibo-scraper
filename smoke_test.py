#!/usr/bin/env python3
"""
smoke_test.py — the offline suite. One file, plain functions, inline
fixtures. No pytest, no conftest, no fixtures directory (CLAUDE.md §10).

    python3 smoke_test.py

It must pass with NO engine library installed at all: every engine import
is guarded and the skip is recorded. `tests/test_smoke.py` wraps this as a
single pytest test so `pytest` also works as an entry point, without a
second copy of the checks.

The fixtures below are cut from real captures taken on 2026-09-21 and are
NOT verbatim. What the site generates — every key, every shape the parser
branches on, the trailing zero-width space, the display-strategy block —
is untouched. What identifies a PERSON is replaced with an obvious
placeholder: author names are `示例用户N` ("Example User N"), bodies are
`示例正文内容` ("example body text") repeated to the original's length band,
ids are renumbered and image URLs point at example.com.

That scrubbing is deliberate and CLAUDE.md §10 is why: republishing a
private individual's name and words is a separate act from the site
showing them on its own page, and these checks need the STRUCTURE of a
post, not the person. The guards below match PATTERNS rather than the old
literals, so a future capture pasted in unscrubbed is caught too.
"""

import ast
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import fields

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FAILURES = []
SKIPS = []
CHECKS = 0


def check(cond, label):
    global CHECKS
    CHECKS += 1
    if not cond:
        FAILURES.append(label)
    return bool(cond)


def eq(got, want, label):
    return check(got == want, f"{label}: got {got!r}, want {want!r}")


def skip(group, why):
    SKIPS.append(f"{group}: {why}")


# ---------------------------------------------------------------------------
# Fixtures (see the module docstring on scrubbing)
# ---------------------------------------------------------------------------

FIXTURES = json.loads(r"""
{
"hot":{
"ok":1,
"statuses":[
{
"visible":{
"type":0,
"list_id":0
},
"created_at":"Tue Sep 15 08:53:25 +0800 2026",
"id":5300000000000002,
"idstr":"5300000000000002",
"mid":"5300000000000002",
"mblogid":"Fixture01",
"user":{
"id":900000001,
"idstr":"900000001",
"screen_name":"示例用户1",
"verified":false,
"verified_type":-1
},
"textLength":341,
"source":" 日常记录 ",
"pic_ids":[
"008GGGRSgy1ih42jn9eu9j31400ivtc1",
"008GGGRSgy1ih42jnd8wqj30d50m8goi"
],
"pic_num":9,
"pic_infos":{
"008GGGRSgy1ih42jn9eu9j31400ivtc1":{
"largest":{
"url":"https://wx1.example/008GGGRSgy1ih42jn9eu9j31400ivtc1.jpg"
},
"original":{
"url":"https://wx1.example/008GGGRSgy1ih42jn9eu9j31400ivtc1_o.jpg"
}
},
"008GGGRSgy1ih42jnd8wqj30d50m8goi":{
"largest":{
"url":"https://wx1.example/008GGGRSgy1ih42jnd8wqj30d50m8goi.jpg"
},
"original":{
"url":"https://wx1.example/008GGGRSgy1ih42jnd8wqj30d50m8goi_o.jpg"
}
}
},
"number_display_strategy":{
"apply_scenario_flag":51,
"display_text_min_number":1000000,
"display_text":"100万+"
},
"reposts_count":4,
"comments_count":157,
"attitudes_count":141,
"isLongText":true,
"topic_struct":[
{
"title":"",
"topic_title":"示例话题"
}
],
"url_struct":[
{
"short_url":"http://t.cn/AXexample",
"url_title":"示例链接",
"ori_url":"sinaweibo://video/vvs?mid=1"
}
],
"isAd":false,
"text_raw":"示例正文内容。示例正文内容。示例正文内容。示例正文内容。示例正文内容。示例正文内容。示例正文内容。示例正文内容。示例正文内容。示例正文内容。示例正文内容。​",
"region_name":"发布于 江苏"
},
{
"visible":{
"type":0,
"list_id":0
},
"created_at":"Sun Sep 20 22:30:14 +0800 2026",
"id":5300000000000003,
"idstr":"5300000000000003",
"mid":"5300000000000003",
"mblogid":"Fixture02",
"user":{
"id":900000002,
"idstr":"900000002",
"screen_name":"示例用户2",
"verified":true,
"verified_type":2
},
"textLength":8,
"source":"微博网页版",
"pic_ids":[
"009TMQ1Jgy1ihahl8t3obj33697ye7wl"
],
"pic_num":1,
"pic_infos":{
"009TMQ1Jgy1ihahl8t3obj33697ye7wl":{
"largest":{
"url":"https://wx1.example/009TMQ1Jgy1ihahl8t3obj33697ye7wl.jpg"
},
"original":{
"url":"https://wx1.example/009TMQ1Jgy1ihahl8t3obj33697ye7wl_o.jpg"
}
}
},
"number_display_strategy":{
"apply_scenario_flag":51,
"display_text_min_number":1000000,
"display_text":"100万+"
},
"reposts_count":3576,
"comments_count":11607,
"attitudes_count":87737,
"isLongText":false,
"isAd":false,
"text_raw":"示例正文内容。​",
"region_name":"发布于 广东"
},
{
"visible":{
"type":0,
"list_id":0
},
"created_at":"Sat Sep 19 12:00:01 +0800 2026",
"id":5300000000000004,
"idstr":"5300000000000004",
"mid":"5300000000000004",
"mblogid":"Fixture03",
"user":{
"id":900000003,
"idstr":"900000003",
"screen_name":"示例用户3",
"verified":true,
"verified_type":7
},
"textLength":257,
"source":"微博视频号",
"pic_ids":[],
"pic_num":0,
"number_display_strategy":{
"apply_scenario_flag":51,
"display_text_min_number":1000000,
"display_text":"100万+"
},
"reposts_count":491,
"comments_count":1333,
"attitudes_count":60504,
"isLongText":false,
"topic_struct":[
{
"title":"",
"topic_title":"示例话题"
}
],
"url_struct":[
{
"short_url":"http://t.cn/AXexample",
"url_title":"示例链接",
"ori_url":"sinaweibo://video/vvs?mid=1"
}
],
"isAd":false,
"text_raw":"示例正文内容。示例正文内容。示例正文内容。示例正文内容。示例正文内容。示例正文内容。示例正文内容。示例正文内容。示例正文内容。示例正文内容。​",
"region_name":"发布于 广东",
"page_info":{
"type":"11",
"object_type":"video",
"page_url":"sinaweibo://infopage?containerid=1",
"media_info":{
"mp4_hd_url":"https://f.video.example/x.mp4"
}
}
},
{
"visible":{
"type":0,
"list_id":0
},
"created_at":"Tue Sep 15 20:11:41 +0800 2026",
"id":5300000000000005,
"idstr":"5300000000000005",
"mid":"5300000000000005",
"mblogid":"Fixture04",
"user":{
"id":900000004,
"idstr":"900000004",
"screen_name":"示例用户4",
"verified":true,
"verified_type":0
},
"textLength":86,
"source":"   🫧   iPhone 15 Pro Max",
"pic_ids":[
"0060nNLYly1ih3lvx6lqdj30u011iq8z",
"0060nNLYly1ih3lvwwbvrj30u011iwk1"
],
"pic_num":9,
"pic_infos":{
"0060nNLYly1ih3lvx6lqdj30u011iq8z":{
"largest":{
"url":"https://wx1.example/0060nNLYly1ih3lvx6lqdj30u011iq8z.jpg"
},
"original":{
"url":"https://wx1.example/0060nNLYly1ih3lvx6lqdj30u011iq8z_o.jpg"
}
},
"0060nNLYly1ih3lvwwbvrj30u011iwk1":{
"largest":{
"url":"https://wx1.example/0060nNLYly1ih3lvwwbvrj30u011iwk1.jpg"
},
"original":{
"url":"https://wx1.example/0060nNLYly1ih3lvwwbvrj30u011iwk1_o.jpg"
}
}
},
"number_display_strategy":{
"apply_scenario_flag":51,
"display_text_min_number":1000000,
"display_text":"100万+"
},
"reposts_count":37,
"comments_count":20,
"attitudes_count":561,
"isLongText":false,
"topic_struct":[
{
"title":"",
"topic_title":"示例话题"
}
],
"url_struct":[
{
"short_url":"http://t.cn/AXexample",
"url_title":"示例链接",
"ori_url":"sinaweibo://video/vvs?mid=1"
}
],
"isAd":false,
"text_raw":"示例正文内容。示例正文内容。示例正文内容。示例正文内容。​",
"region_name":"发布于 辽宁"
},
{
"visible":{
"type":0,
"list_id":0
},
"created_at":"Sun Sep 20 21:35:46 +0800 2026",
"id":5300000000000006,
"idstr":"5300000000000006",
"mid":"5300000000000006",
"mblogid":"Fixture05",
"user":{
"id":900000005,
"idstr":"900000005",
"screen_name":"示例用户5",
"verified":true,
"verified_type":3
},
"textLength":125,
"source":"",
"pic_ids":[],
"pic_num":2,
"number_display_strategy":{
"apply_scenario_flag":51,
"display_text_min_number":1000000,
"display_text":"100万+"
},
"reposts_count":806,
"comments_count":1064,
"attitudes_count":9407,
"isLongText":false,
"topic_struct":[
{
"title":"",
"topic_title":"示例话题"
}
],
"url_struct":[
{
"short_url":"http://t.cn/AXexample",
"url_title":"示例链接",
"ori_url":"sinaweibo://video/vvs?mid=1"
}
],
"isAd":false,
"text_raw":"示例正文内容。示例正文内容。示例正文内容。示例正文内容。示例正文内容。​"
}
]
},
"user":{
"ok":1,
"data":{
"list":[
{
"visible":{
"type":0,
"list_id":0
},
"created_at":"Sun Sep 20 19:29:54 +0800 2026",
"id":5300000000000007,
"idstr":"5300000000000007",
"mid":"5300000000000007",
"mblogid":"Fixture06",
"user":{
"id":900000006,
"idstr":"900000006",
"screen_name":"示例用户6",
"verified":true,
"verified_type":0
},
"textLength":137,
"source":"",
"pic_ids":[],
"pic_num":0,
"number_display_strategy":{
"apply_scenario_flag":51,
"display_text_min_number":1000000,
"display_text":"100万+"
},
"reposts_count":2000,
"comments_count":1479,
"attitudes_count":7798,
"isLongText":false,
"topic_struct":[
{
"title":"",
"topic_title":"示例话题"
}
],
"url_struct":[
{
"short_url":"http://t.cn/AXexample",
"url_title":"示例链接",
"ori_url":"sinaweibo://video/vvs?mid=1"
}
],
"isAd":false,
"text_raw":"示例正文内容。示例正文内容。示例正文内容。示例正文内容。示例正文内容。示例正文内容。​",
"region_name":"发布于 广东",
"page_info":{
"type":"11",
"object_type":"video",
"page_url":"sinaweibo://infopage?containerid=1",
"media_info":{
"mp4_hd_url":"https://f.video.example/x.mp4"
}
}
},
{
"visible":{
"type":0,
"list_id":0
},
"created_at":"Sat Sep 19 21:56:01 +0800 2026",
"id":5300000000000008,
"idstr":"5300000000000008",
"mid":"5300000000000008",
"mblogid":"Fixture07",
"user":{
"id":900000007,
"idstr":"900000007",
"screen_name":"示例用户7",
"verified":true,
"verified_type":0
},
"textLength":99,
"source":"微博视频号",
"pic_ids":[],
"pic_num":0,
"number_display_strategy":{
"apply_scenario_flag":51,
"display_text_min_number":1000000,
"display_text":"100万+"
},
"reposts_count":5705,
"comments_count":6203,
"attitudes_count":19347,
"isLongText":false,
"topic_struct":[
{
"title":"",
"topic_title":"示例话题"
}
],
"url_struct":[
{
"short_url":"http://t.cn/AXexample",
"url_title":"示例链接",
"ori_url":"sinaweibo://video/vvs?mid=1"
}
],
"isAd":false,
"text_raw":"示例正文内容。示例正文内容。示例正文内容。示例正文内容。​",
"region_name":"发布于 湖南",
"page_info":{
"type":"11",
"object_type":"video",
"page_url":"sinaweibo://infopage?containerid=1",
"media_info":{
"mp4_hd_url":"https://f.video.example/x.mp4"
}
}
}
],
"next_cursor":5345145356816203
}
},
"user_end":{
"ok":1,
"data":{
"list":[],
"next_cursor":-1
}
},
"profile":{
"ok":1,
"data":{
"user":{
"id":2803301701,
"idstr":"2803301701",
"screen_name":"示例机构号",
"followers_count":157980405,
"statuses_count":153313,
"verified":true,
"verified_type":3
}
}
},
"comments":{
"ok":1,
"max_id":0,
"data":[
{
"id":5345357930170809,
"idstr":"5345357930170809",
"created_at":"Sun Sep 21 10:11:12 +0800 2026",
"text_raw":"示例评论内容",
"like_counts":2,
"source":"发布于 北京",
"mid":"5345357930170809",
"user":{
"id":900000099,
"idstr":"900000099",
"screen_name":"示例评论者",
"verified":false
}
},
{
"id":5345357930170810,
"idstr":"5345357930170810",
"created_at":"Sun Sep 21 10:12:12 +0800 2026",
"text_raw":"示例回复内容",
"like_counts":0,
"reply_comment_id":"5345357930170809",
"user":{
"id":900000098,
"idstr":"900000098",
"screen_name":"示例回复者",
"verified":false
}
}
]
},
"show":{
"ok":1,
"mid":"5345356649863407",
"idstr":"5345356649863407",
"mblogid":"Fixture01",
"created_at":"Sun Sep 20 22:50:00 +0800 2026",
"text_raw":"示例",
"user":{
"id":900000001,
"idstr":"900000001",
"screen_name":"示例用户1"
}
},
"longtext":{
"ok":1,
"data":{
"longTextContent":"示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。示例完整正文。"
}
},
"longtext_empty":{
"ok":1,
"data":{}
},
"needs_visitor":{
"ok":-100,
"url":"https://weibo.com/login.php?url=https%3A%2F%2Fweibo.com%2F"
},
"login_wall":{
"ok":0,
"message":"前方有点拥堵，请登录后使用"
},
"deleted":{
"ok":0,
"errno":"20101",
"msg":"该微博不存在"
},
"bad_request":{
"error":"Forbidden"
}
}
""")

HOT = FIXTURES["hot"]
USER = FIXTURES["user"]
USER_END = FIXTURES["user_end"]
PROFILE = FIXTURES["profile"]
COMMENTS = FIXTURES["comments"]
SHOW = FIXTURES["show"]
LONGTEXT = FIXTURES["longtext"]
LONGTEXT_EMPTY = FIXTURES["longtext_empty"]
NEEDS_VISITOR = FIXTURES["needs_visitor"]
LOGIN_WALL = FIXTURES["login_wall"]
DELETED = FIXTURES["deleted"]
BAD_REQUEST = FIXTURES["bad_request"]

# A SERVED weibo.com HTML page, scrubbed of its session config and of the
# account it showed. It is here because the JSON fixtures above cannot do
# this job: the two most obvious captcha markers on this site appear in
# MARKUP, not in any payload, so a marker check that only ever sees JSON
# would pass while carrying a marker that fires on every good page.
# CLAUDE.md §21: a guard is only as good as the fixture it runs against.
#
# What it preserves, counted on the real page 2026-09-21:
#     var CAPTCHA_TYPE = 'yidun'            NetEase Yidun, preloaded
#     static.geetest.com/v4/gt4.js          GeeTest v4, preloaded
# Both sit on a page that is being served perfectly normally. Weibo wires
# two captcha vendors into its chrome and loads them whether or not
# anything is being challenged.
SERVED_HTML = "<!doctype html>\n<html lang=\"zh-cn\">\n  <head>\n    <meta charset=\"utf-8\" />\n    <link rel=\"dns-prefetch\" href=\"//h5.sinaimg.cn\" />\n    <meta name=\"viewport\" content=\"width=device-width,initial-scale=1,user-scalable=no,viewport-fit=cover\" />\n    <meta http-equiv=\"X-UA-Compatible\" content=\"IE=edge\" />\n    <meta http-equiv=\"Content-Security-Policy\" content=\"upgrade-insecure-requests\" />\n    <meta content=\"\" name=\"keywords\" />\n    <meta content=\"\" name=\"description\" />\n    <link rel=\"icon\" href=\"https://weibo.com/favicon.ico\" />\n    <link\n      rel=\"stylesheet\"\n      type=\"text/css\"\n      href=\"//h5.sinaimg.cn/m/reward-pc-kits/style.css?version=2.1.5\"\n    />\n    <script src=\"https://js.t.sinajs.cn/static/validate-loader.umd.cjs\"></script>\n    <title>微博</title>\n    <script>\n      if (window.location.protocol !== 'https:') {\n        window.location.href = window.location.href.replace('http:', 'https:');\n      }\n    </script>\n    <script type=\"module\" crossorigin src=\"https://h5.sinaimg.cn/m/weibo-pro-next/assets/index-DArm_q-5.js\"></script>\n    <link rel=\"stylesheet\" crossorigin href=\"https://h5.sinaimg.cn/m/weibo-pro-next/assets/index-BQia-I5S.css\">\n  </head>\n  <body>\n    <script>\n      function scriptLoaded() {\n        if (window.wbBotDetector) {\n          window.wbBotDetector.load({\n            from: 'weibo_pc',\n            isTraceMouse: true,\n            isTraceKeyboard: true,\n            getTimeout: 2000\n          });\n        }\n      }\n    </script>\n    <script\n      src=\"https://passport.sinaimg.cn/js/fp/1.3.2.umd.js\"\n      defer=\"defer\"\n      onload=\"scriptLoaded()\"\n    ></script>\n    <script>\n      try {\n        var CAPTCHA_TYPE = 'yidun';\n        window.CAPTCHA_TYPE = CAPTCHA_TYPE;\n        var dynamicLoader = window.ValidateLoader.dynamicLoader;\n        dynamicLoader\n          .load(CAPTCHA_TYPE)\n          .catch((err) => console.error('Dun preload failed', err));\n      } catch (e) {\n        console.log(e);\n      }\n    </script>\n    <script>\n      window.$VERSION = {\n        CLIENT: 'v1.1.249',\n        SERVER: 'v2026.09.20.1'\n      };\n      try{window.$CONFIG = {\"serverTime\":1789979952798,\"showAriaEntrance\":true,\"enableAria\":true,\"enablePopLogin\":true,\"apmSampleRate\":0.01,\"isNormal\":false,\"flags\":{\"PC_grey\":false,\"trend\":0},\"loginHeader\":{\"poster\":\"https://a.sinaimg.cn/mintra/pic/2112130400/18weibo_login.png\",\"src\":\"https://a.sinaimg.cn/mintra/pic/2112130543/weibo_login.mp4\"}};}catch(e){window.$CONFIG = {};}\n      const s = document.createElement('script');\n      s.src = 'https://i.sso.sina.com.cn/js/qrcode_login_v2.js';\n      document.body.appendChild(s);\n    </script>\n    <div id=\"app\"></div>\n    <script defer src=\"https://static.geetest.com/v4/gt4.js\"></script>\n    <script>\n      try {\n        if (window.$CONFIG.enableAria) {\n          const s = document.createElement('script');\n          s.defer = true;\n          s.src = '//a.sinaimg.cn/mintra/pic/2201111119/wza/aria.js?appid=scrubbed_appid_not_a_credential';\n          document.body.appendChild(s);\n        }\n      } catch (e) {}\n      try {\n        const s = document.createElement('script');\n        s.defer = true;\n        s.src = '//a.sinaimg.cn/mintra/pic/2406250331/48po.js';\n        document.body.appendChild(s);\n      } catch (e) {}\n    </script>\n    <script\n      src=\"//h5.sinaimg.cn/m/reward-pc-kits/sdk.js?version=2.1.5\"\n      defer\n    ></script>\n    <!-- built files will be auto injected  -->\n  </body>\n</html>\n"

ENGINES = ("playwright_scraper", "puppeteer_scraper", "selenium_scraper")
SHARED_MODULES = ("page_flow", "product_parser", "weibo_api", "output_writer",
                  "proxy_pool", "env_config")


# ---------------------------------------------------------------------------
# 1. The parser, asserted on VALUES rather than on coverage
# ---------------------------------------------------------------------------
# CLAUDE.md §10: a column can be 100% populated and entirely wrong. A sibling
# repo shipped a review count read by stripping every digit out of an
# aria-label — 445279961 on every row, while the coverage check said 100%.

def test_parse_hot_feed():
    import product_parser as P
    rows = P.parse_hot_feed(HOT, page=1)
    eq(len(rows), len(HOT["statuses"]), "hot: one row per status")

    r = rows[0]
    src = HOT["statuses"][0]
    eq(r.sku, src["mblogid"], "hot: sku is mblogid, not mid")
    eq(r.mid, str(src["mid"]), "hot: mid kept separately")
    eq(r.url, f"https://weibo.com/{src['user']['idstr']}/{src['mblogid']}",
       "hot: url assembled from uid + mblogid")
    eq(r.page, 1, "hot: page threaded in")
    eq(r.position, 1, "hot: position is 1-based")
    eq(r.source, "weibo.com", "hot: source")

    # page + position must be unique across the run. CLAUDE.md §18: a run
    # once wrote page=1 on every row, so 60 of 119 rows claimed a position
    # another row already held, and the column was worthless.
    pairs = [(x.page, x.position) for x in rows]
    eq(len(set(pairs)), len(pairs), "hot: page+position unique")
    eq(len({x.sku for x in rows}), len(rows), "hot: sku unique")


def test_the_display_strategy_trap():
    """`number_display_strategy.display_text` is a RULE, not a figure.

    Pinned because it is the most inviting wrong answer on this site: every
    post carries `{"display_text_min_number": 1000000, "display_text":
    "100万+"}`, byte-identical, including posts with 18 likes. Read as the
    like count it would put "100万+" on every row of every run while every
    coverage check reported 100%.
    """
    import product_parser as P
    rows = P.parse_hot_feed(HOT, page=1)
    for row, src in zip(rows, HOT["statuses"]):
        check(src["number_display_strategy"]["display_text"] == "100万+",
              "fixture still carries the display-strategy block")
        eq(row.attitudes_count, src["attitudes_count"],
           f"{row.sku}: attitudes is the integer, never display_text")
        check(not isinstance(row.attitudes_count, str),
              f"{row.sku}: no counter is a string")


def test_region_and_client():
    import product_parser as P
    eq(P.clean_region("发布于 广东"), "广东", "region: prefix stripped")
    eq(P.clean_region("发布于 其他"), None, "region: 其他 is not a place")
    eq(P.clean_region(None), None, "region: absent stays absent")
    eq(P.clean_client('<a href="/x" rel="nofollow">iPhone客户端</a>'), "iPhone客户端",
       "client: anchor stripped")
    # The client string is USER-SETTABLE on some accounts. Values measured
    # include ones that name no device at all, so it is written through as
    # found rather than matched against a list that would drop them.
    eq(P.clean_client("  日常记录 "), "日常记录", "client: custom text kept")

    rows = P.parse_hot_feed(HOT, page=1)
    for row, src in zip(rows, HOT["statuses"]):
        if src.get("region_name"):
            check("发布于" not in (row.region or ""),
                  f"{row.sku}: region carries no prefix")
        else:
            eq(row.region, None, f"{row.sku}: no region stated -> null")


def test_created_at():
    import product_parser as P
    eq(P.parse_created_at("Sun Sep 21 15:38:42 +0800 2026"),
       "2026-09-21T15:38:42+08:00", "created_at: Twitter shape -> ISO")
    eq(P.parse_created_at("nonsense"), None,
       "created_at: an unknown shape is None, never a guess")
    rows = P.parse_hot_feed(HOT, page=1)
    for row, src in zip(rows, HOT["statuses"]):
        eq(row.created_at_raw, src["created_at"],
           f"{row.sku}: the site's own string is kept verbatim")
        check((row.created_at or "").startswith("20"),
              f"{row.sku}: created_at parsed")


def test_attachments():
    import product_parser as P
    rows = P.parse_hot_feed(HOT, page=1) + P.parse_user_feed(USER, page=2)[0]
    withpics = [r for r in rows if r.pic_urls]
    check(withpics, "fixture covers a post with pictures")
    for r in withpics:
        check(all(u.startswith("http") for u in r.pic_urls),
              f"{r.sku}: every picture URL is http")

    vids = [r for r in rows if r.video_url]
    check(vids, "fixture covers a video post")
    for r in vids:
        check(r.video_url.startswith("http"),
              f"{r.sku}: video_url is playable, never a sinaweibo:// scheme")

    linked = [r for r in rows if r.links]
    for r in linked:
        check(all(u.startswith("http") for u in r.links),
              f"{r.sku}: links are web addresses, never sinaweibo://")

    topical = [r for r in rows if r.topics]
    check(topical, "fixture covers a post with topics")
    for r in topical:
        check(all(t and "#" not in t for t in r.topics),
              f"{r.sku}: topics carry no hash marks and none is empty")


# ---------------------------------------------------------------------------
# 2. Truncation — the trap that would ship a populated, wrong column
# ---------------------------------------------------------------------------

def test_truncation_semantics():
    """The three outcomes of asking /longtext, kept apart.

    An earlier version collapsed "the endpoint says there is no more text"
    into "the recovery failed", which marked complete 13-character posts as
    truncated — a lie in the one column that exists to tell the truth about
    the body.
    """
    import product_parser as P
    from output_writer import Post

    row = Post(sku="a", title="short", text_source="longtext_failed",
               text_truncated=True)
    P.apply_longtext(row, P.parse_longtext(LONGTEXT), reached=True)
    eq(row.text_source, "longtext", "recovered: source")
    eq(row.text_truncated, False, "recovered: not truncated")
    check(len(row.title) > len("short"), "recovered: the body actually grew")

    row = Post(sku="b", title="short", text_source="longtext_failed",
               text_truncated=True)
    P.apply_longtext(row, P.parse_longtext(LONGTEXT_EMPTY), reached=True)
    eq(row.text_source, "inline", "endpoint said no more: source is inline")
    eq(row.text_truncated, False,
       "endpoint said no more: the post was WHOLE, not truncated")
    eq(row.title, "short", "endpoint said no more: body untouched")

    row = Post(sku="c", title="short", text_source="longtext_failed",
               text_truncated=True)
    P.apply_longtext(row, None, reached=False)
    eq(row.text_source, "longtext_failed", "could not ask: source")
    eq(row.text_truncated, True, "could not ask: stays truncated")

    # Never shorten a row: two views disagreeing means leave the good one
    # alone (CLAUDE.md §4).
    row = Post(sku="d", title="a much longer inline body", text_source="x")
    P.apply_longtext(row, "tiny", reached=True)
    eq(row.title, "a much longer inline body",
       "a shorter 'recovery' never overwrites a longer body")


def test_zero_width_is_not_a_truncation_signal():
    """Pinned as a KNOWN NON-SIGNAL, with the measurement (CLAUDE.md §10).

    Counted 2026-09-21 over 40 posts from four live hot-feed fetches: every
    single one ends in a zero-width space, flagged long or not. A marker
    that matches every page is worse than no marker (§18), so nothing
    branches on it — `isLongText` does, and the endpoint settles it.
    """
    import product_parser as P
    raw = [n["text_raw"] for n in HOT["statuses"]]
    marked = sum(1 for t in raw if t.rstrip().endswith("​"))
    check(marked == len(raw),
          f"fixture preserves the site's habit: {marked} of {len(raw)} raw "
          "bodies end in U+200B")
    flagged = sum(1 for n in HOT["statuses"] if n.get("isLongText"))
    check(flagged < len(raw),
          "and the mark is present on posts the site did NOT flag as long, "
          "which is why it cannot be the signal")

    # Every row written must have been cleaned.
    rows = P.parse_hot_feed(HOT, page=1)
    for r in rows:
        check(not P.looks_truncated(r.title),
              f"{r.sku}: the zero-width mark is stripped from the output")


# ---------------------------------------------------------------------------
# 3. The other two modes
# ---------------------------------------------------------------------------

def test_parse_user_feed():
    import product_parser as P
    rows, cursor = P.parse_user_feed(USER, page=1, author_followers=157980405)
    check(rows, "user: rows parsed")
    eq(cursor, USER["data"]["next_cursor"], "user: cursor returned, not stored")
    for r in rows:
        eq(r.author_followers, 157980405,
           f"{r.sku}: follower count threaded in from the profile call")

    rows2, cursor2 = P.parse_user_feed(USER_END, page=2)
    eq(rows2, [], "user: an exhausted cursor yields no rows")
    eq(cursor2, -1, "user: -1 is the site saying it has finished")


def test_parse_comments():
    import product_parser as P
    rows, max_id = P.parse_comments(COMMENTS, parent_sku="Fixture01", page=1)
    eq(len(rows), 2, "post: one row per comment")
    eq(max_id, 0, "post: max_id 0 means no more")
    r = rows[0]
    eq(r.parent_sku, "Fixture01", "post: parent threaded in")
    eq(r.comment_id, r.sku, "post: sku carries the comment id")
    eq(r.attitudes_count, 2, "post: like_counts read")
    # A comment has no repost or comment count of its own. Filling those
    # with the PARENT's figures would make a comment look like it had 467
    # comments because its parent did.
    eq(r.reposts_count, None, "post: a comment carries no repost count")
    eq(r.comments_count, None, "post: a comment carries no comment count")
    eq(rows[1].reply_to_comment_id, "5345357930170809",
       "post: a reply names the comment it answers")


def test_parse_profile():
    import product_parser as P
    got = P.parse_profile(PROFILE)
    eq(got["uid"], "2803301701", "profile: uid")
    eq(got["followers_count"], 157980405, "profile: followers")
    eq(got["statuses_count"], 153313, "profile: the site's own post count")
    eq(P.parse_profile({"ok": 1}), {}, "profile: a shapeless payload is {}")


def test_url_readers():
    import product_parser as P
    eq(P.uid_from_url("https://weibo.com/u/2803301701"), "2803301701", "uid from /u/")
    eq(P.uid_from_url("2803301701"), "2803301701", "uid from a bare number")
    eq(P.uid_from_url("https://weibo.com/2803301701/RiPCAfklU"), "2803301701",
       "uid from a post URL")
    eq(P.post_id_from_url("https://weibo.com/2803301701/RiPCAfklU"),
       ("RiPCAfklU", None), "post URL gives the base-62 id")
    eq(P.post_id_from_url("https://weibo.com/detail/5344883403653158"),
       (None, "5344883403653158"), "detail URL gives the numeric id")
    eq(P.post_id_from_url("https://weibo.com/u/2803301701"), (None, None),
       "an account URL is not a post")


def test_host_refusals_name_the_reason():
    """A bare False sends the reader hunting for a typo (CLAUDE.md §5)."""
    import product_parser as P
    ok, why = P.is_supported_host("https://weibo.com/u/1")
    check(ok, "weibo.com is supported")

    ok, why = P.is_supported_host("https://m.weibo.cn/u/1")
    check(not ok, "m.weibo.cn is refused")
    check("m.weibo.cn is Weibo's mobile site" in why,
          "the refusal does NOT claim m.weibo.cn is not Weibo — it is")
    check("-100" in why, "the refusal names the measured reason")

    ok, why = P.is_supported_host("https://s.weibo.com/weibo?q=x")
    check(not ok, "s.weibo.com is refused")
    check("account" in why.lower(), "search is refused for wanting an account")
    check("does not implement" in why,
          "and says THIS REPO does not implement it — a TODO, not a claim "
          "about what is possible (CLAUDE.md §19)")


# ---------------------------------------------------------------------------
# 4. Classification and policy
# ---------------------------------------------------------------------------

def test_classifier():
    import page_flow as F
    cases = [
        (json.dumps(HOT), 200, "content", "a feed with posts"),
        (json.dumps({"ok": 1, "statuses": []}), 200, "empty", "a feed with none"),
        (json.dumps(USER), 200, "content", "a cursor page with posts"),
        (json.dumps(USER_END), 200, "empty", "an exhausted cursor"),
        (json.dumps(SHOW), 200, "content", "the BARE post object /show returns"),
        (json.dumps(LONGTEXT), 200, "content", "recovered long text"),
        (json.dumps(LONGTEXT_EMPTY), 200, "empty", "no long text to recover"),
        (json.dumps(NEEDS_VISITOR), 200, "needs_visitor", "ok:-100"),
        (json.dumps(LOGIN_WALL), 403, "login_wall", "the 403 account wall"),
        (json.dumps(DELETED), 200, "empty", "a deleted post is an ANSWER"),
        (json.dumps(BAD_REQUEST), 403, "bad_request", "the wrong-shape 403"),
        ("<html><body>ERR_PROXY_CONNECTION_FAILED</body></html>", None,
         "transport_error", "Chromium's own error page"),
        ("", 200, "unknown", "an empty body"),
    ]
    for body, status, want, label in cases:
        eq(F.classify(body, status), want, f"classify: {label}")


def test_chromium_error_page_is_not_a_block():
    """CLAUDE.md §18's inverted-detection case.

    Chromium's built-in network-error page carries the requested HOSTNAME in
    its own `<title>`, so a title check calls a page that never left the
    browser a page the site served. It must classify as a transport fault,
    which wants a different exit, and never as a refusal.
    """
    import page_flow as F
    page = ("<html><head><title>weibo.com</title></head><body>"
            "<div>ERR_TUNNEL_CONNECTION_FAILED</div></body></html>")
    eq(F.classify(page, None), "transport_error", "a browser error page")
    check(not F.counts_as_blocked("transport_error"),
          "a dead proxy is not the site refusing")
    check(F.should_retry("transport_error"), "a transport fault is retryable")


def test_state_policy_is_coherent():
    import page_flow as F
    for state, row in F.STATE_POLICY.items():
        eq(sorted(row), ["blocked", "parse", "retry", "solve"],
           f"policy {state}: has exactly the four decisions")
        check(not (row["parse"] and row["blocked"]),
              f"policy {state}: a parseable page is not blocked")
        check(not (row["solve"] and not row["blocked"]),
              f"policy {state}: nothing pays for a solve on a page that is "
              "not gated")

    # The login wall is the one that must NOT retry: it wants an account,
    # and no number of attempts or exit addresses supplies one.
    check(not F.should_retry("login_wall"),
          "login_wall does not spend the user's retry budget")
    check(not F.should_solve("login_wall"),
          "login_wall buys no solve — nothing is being tested, access is "
          "being declined (CLAUDE.md §19)")
    check(F.counts_as_blocked("login_wall"), "login_wall is blocked")

    # needs_visitor is the opposite: cheap to fix, so retry and do NOT
    # report exit 3 for something the next request clears.
    check(F.should_retry("needs_visitor"), "needs_visitor retries")
    check(not F.counts_as_blocked("needs_visitor"),
          "needs_visitor is not exit 3 — the fix costs one request")
    check(F.needs_visitor_cookie("needs_visitor"), "and is named as such")

    check(not F.should_retry("bad_request"),
          "bad_request does not retry: the next identical request is refused "
          "identically")


def test_solve_budget_is_actually_enforced():
    """CLAUDE.md §23: this constant enforced NOTHING across the family.

    Every engine calls the captcha handler TWICE per attempt — once before
    the response is classified, once after — and only the second call was
    ever counted, so one page could buy three solves on a site where a
    challenge renders on every fetch. The budget object is what makes the
    number true; this check is what keeps it true.
    """
    import page_flow as F
    b = F.SolveBudget()
    check(b.may_solve(), "a fresh budget allows one solve")
    b.charge()
    check(not b.may_solve(), "and exactly one")
    eq(F.SOLVES_PER_PAGE, 1, "the documented cap")

    for name in ENGINES:
        path = os.path.join(HERE, f"{name}.py")
        src = open(path, encoding="utf-8").read()
        tree = ast.parse(src)
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and getattr(n.func, "id", getattr(n.func, "attr", None))
                 == "handle_captcha_if_present"]
        check(len(calls) == 2,
              f"{name}: exactly two captcha call sites, found {len(calls)}")
        for c in calls:
            passes_budget = any(
                getattr(a, "id", None) == "budget" for a in c.args
            ) or any(k.arg == "budget" for k in c.keywords)
            check(passes_budget,
                  f"{name}: every captcha call site is handed the shared "
                  "budget — the §23 defect is one call site that is not")
        eq(src.count("budget.charge()"), 1,
           f"{name}: exactly one place charges the budget")
        eq(src.count("budget.may_solve()"), 1,
           f"{name}: exactly one place guards on it")


def test_policy_constants_have_consumers():
    """A policy constant nothing reads is the same defect as dead code.

    CLAUDE.md §17: `RETRY_ON_BLOCKED` carried a paragraph of measured
    justification in a sibling repo and NO engine consulted it, so setting
    it False changed nothing while the prose read like enforcement.
    """
    names = ["RETRY_ON_BLOCKED", "BLOCK_RETRIES_WITHOUT_POOL",
             "SOLVES_PER_PAGE", "MIN_CARD_MATCHES", "VISITOR_MINTS_PER_PAGE"]
    sources = {}
    for f in ENGINES + SHARED_MODULES + ("weibo_api",):
        p = os.path.join(HERE, f"{f}.py")
        if os.path.exists(p):
            sources[f] = open(p, encoding="utf-8").read()
    import page_flow
    for name in names:
        # Both halves, because each without the other passes for the wrong
        # reason: a constant the engines mention but page_flow no longer
        # defines is an AttributeError on the first blocked page, and a
        # constant page_flow defines but nothing reads is prose pretending
        # to be enforcement (CLAUDE.md §17).
        check(hasattr(page_flow, name),
              f"page_flow defines {name} — the engines read it by that name")
        consumers = [f for f, s in sources.items()
                     if f != "page_flow" and name in s]
        check(consumers, f"{name} is read somewhere outside page_flow")


# ---------------------------------------------------------------------------
# 5. Markers — counted, not inherited
# ---------------------------------------------------------------------------

def test_no_marker_fires_on_a_good_payload():
    """CLAUDE.md §18: count every marker on a page you KNOW is good.

    This is the check that matters most on this site, because the obvious
    markers are inverted here. Counted 2026-09-21 over 6 served responses
    and 4 refusals:

        marker                      served   refused
        "ok":-100                     0        2
        请登录 / 登录后使用             0        1
        weibo.com/login               0        2
        Sina Visitor System           0        1
        geetest                       1        0   <-- INVERTED
        captcha                       1        0   <-- INVERTED
        cf-turnstile                  0        0
        challenges.cloudflare.com     0        0

    Weibo's profile HTML carries its login widget's GeeTest configuration
    whether or not anything is being challenged, so the two most obvious
    captcha markers on a Chinese site fire on good pages and on no refusal.
    """
    import product_parser as P
    good = [json.dumps(HOT), json.dumps(USER), json.dumps(PROFILE),
            json.dumps(COMMENTS), json.dumps(SHOW), json.dumps(LONGTEXT),
            SERVED_HTML]
    for payload in good:
        got = P.detect_bot_challenge(payload)
        eq(got, None, f"no marker fires on a served payload (got {got!r})")

    # Serialised the way the site actually serves it. `json.dumps` escapes
    # non-ASCII by default, which would turn 请登录 into \uXXXX and hide a
    # marker that matches perfectly well on the real wire.
    for bad, label in ((json.dumps(NEEDS_VISITOR, ensure_ascii=False),
                        "the -100 login redirect"),
                       (json.dumps(LOGIN_WALL, ensure_ascii=False),
                        "the 403 account wall")):
        check(P.detect_bot_challenge(bad), f"a marker fires on {label}")


def test_both_captcha_vendors_are_preloaded_on_a_good_page():
    """Weibo wires TWO captcha vendors into its ordinary page chrome.

    Counted on a served profile page, 2026-09-21: `CAPTCHA_TYPE` appears 4
    times, `geetest` once, `yidun` once — on a page that answered HTTP 200
    with the account on it and nothing being challenged.

    So on this site not even a vendor's LOADER is a marker, which is §18 at
    full strength: `static.geetest.com/v4/gt4.js` is what a good page
    fetches. Anything that keys on the vendor's name or its script URL will
    call every page a challenge.
    """
    import product_parser as P
    for needle, n in (("CAPTCHA_TYPE", 3), ("geetest", 1), ("yidun", 1)):
        check(SERVED_HTML.lower().count(needle.lower()) >= n,
              f"the served-page fixture still carries {needle!r}")
    eq(P.detect_bot_challenge(SERVED_HTML), None,
       "and NONE of them makes a served page look like a challenge")


def test_the_inverted_markers_stay_out():
    """Pinned so an editor cannot reintroduce them (CLAUDE.md §10).

    `captcha` and `geetest` are measured USELESS here, and worse than
    useless: they fire on served pages and on no refusal. Every Cloudflare
    marker this family's template suggests counts 0 on both sides — Weibo is
    not fronted by Cloudflare, so carrying them would be dead code that
    looks load-bearing.
    """
    import product_parser as P
    allmarkers = tuple(P.BOT_CHALLENGE_MARKERS) + tuple(P.CHALLENGE_MARKERS)
    for banned in ("cf-turnstile", "challenges.cloudflare.com", "_cf_chl",
                   "turnstile", "akamai", "datadome", "perimeterx"):
        check(banned not in allmarkers,
              f"{banned!r} is not carried: 0 occurrences on both sides here")
    # The bare words, as opposed to a challenge's own vocabulary.
    check("captcha" not in P.BOT_CHALLENGE_MARKERS,
          "the bare word 'captcha' is not a marker — it fires on a served "
          "profile page")
    check("geetest" not in P.BOT_CHALLENGE_MARKERS,
          "and neither is the bare vendor name")
    check(all("geetest" != m for m in P.CHALLENGE_MARKERS),
          "the challenge set anchors on a rendered widget's vocabulary, not "
          "on the vendor's name")


def test_markers_survive_entity_escaping():
    """CLAUDE.md §20: a marker must survive BOTH encodings of one page.

    An edge can entity-escape the punctuation in a marker so that a literal
    matches a browser's DOM and silently misses the same page fetched by an
    HTTP client.
    """
    import product_parser as P
    escaped = ('{"ok":-100,"url":"https&#58;&#47;&#47;weibo&#46;com&#47;'
               'login&#46;php"}')
    check(P.detect_bot_challenge(escaped),
          "a marker is found through HTML entity escaping")


# ---------------------------------------------------------------------------
# 6. Engine parity
# ---------------------------------------------------------------------------

def _parser_flags(path):
    """Flags the CLI declares — scoped to parse_args on purpose.

    ChromeOptions ALSO has an `add_argument` method, so a walk over the
    whole module counts `--no-sandbox` as a CLI flag and reports three
    phantom differences between engines.
    """
    tree = ast.parse(open(path, encoding="utf-8").read())
    fn = next((n for n in tree.body
               if isinstance(n, ast.FunctionDef) and n.name == "parse_args"), None)
    if fn is None:
        return None
    out = set()
    for n in ast.walk(fn):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "add_argument"):
            for a in n.args:
                if isinstance(a, ast.Constant) and str(a.value).startswith("--"):
                    out.add(a.value)
    return out


# The family contract (CLAUDE.md §9), plus the five flags that list omitted
# for months while nearly every repo shipped them.
CONTRACT_FLAGS = {
    "--url", "--pages", "--category", "--format", "--out", "--delay",
    "--retries", "--retry-delay", "--concurrency", "--proxy", "--proxy-file",
    "--proxy-rotate", "--proxy-shuffle", "--proxy-block-retries",
    "--twocaptcha-key", "--captcha-api", "--solve-captcha", "--min-score",
    "--cdp-endpoint", "--allow-empty", "--dump-html", "--headless",
    "--headful", "--fingerprint", "--fp-country", "--fp-tags", "--locale",
    "--mode",
}


def test_flag_parity():
    """Assert in BOTH directions (CLAUDE.md §20).

    A new unshared flag fails, and so does closing a documented difference —
    because the exception list IS the documentation. This check was skipped
    in a sibling repo and, when finally written, found TWELVE flags the
    primary engine had and its twins did not, nine of them predating the
    work, while the README promised "same CLI".
    """
    sets = {}
    for name in ENGINES:
        got = _parser_flags(os.path.join(HERE, f"{name}.py"))
        check(got is not None, f"{name}: has a parse_args function")
        sets[name] = got or set()

    base = sets[ENGINES[0]]
    for name in ENGINES[1:]:
        missing = base - sets[name]
        extra = sets[name] - base
        check(not missing, f"{name} is missing flags the primary has: "
                           f"{sorted(missing)}")
        check(not extra, f"{name} has flags the primary does not: "
                         f"{sorted(extra)}")

    eq(base, CONTRACT_FLAGS, "the engines declare exactly the contract's flags")


def test_removed_flags_stay_removed():
    """Scoped to the ENGINES (CLAUDE.md §10).

    `--country` is banned on a scraper because it could disagree with the
    URL, and legitimate on fingerprint_client.py where it picks a
    fingerprint locale — so the check must not be repo-wide.
    """
    for name in ENGINES:
        flags = _parser_flags(os.path.join(HERE, f"{name}.py")) or set()
        # Assembled, not written out: a check that NAMES a banned phrase
        # is a use of it, and this file is scanned too.
        for banned in ("--country", "--" + "anti" + "detect", "--state",
                       "--sort"):
            check(banned not in flags, f"{name}: {banned} stays removed")


def test_site_constants_agree_across_engines():
    import importlib
    values = {}
    for name in ENGINES:
        src = open(os.path.join(HERE, f"{name}.py"), encoding="utf-8").read()
        tree = ast.parse(src)
        got = {}
        for n in tree.body:
            if isinstance(n, ast.Assign) and len(n.targets) == 1:
                t = n.targets[0]
                if isinstance(t, ast.Name) and t.id in (
                        "DEFAULT_FEED_GROUP", "LANDING_URL", "MODES",
                        "NEXT_PAGE_SELECTOR"):
                    try:
                        got[t.id] = ast.literal_eval(n.value)
                    except ValueError:
                        pass
        values[name] = got
    base = values[ENGINES[0]]
    for name in ENGINES[1:]:
        eq(values[name], base, f"{name}: site constants match the primary")
    eq(base.get("MODES"), ("hot", "user", "post"), "the three modes")
    eq(base.get("NEXT_PAGE_SELECTOR"), None,
       "there is NO next-page selector: neither feed publishes one, and "
       "inventing one is how a run reports complete while holding page 1 "
       "(CLAUDE.md §7)")


# ---------------------------------------------------------------------------
# 7. Structural checks — the five worth stealing (CLAUDE.md §17)
# ---------------------------------------------------------------------------

def _bound_names(tree):
    """Every name bound anywhere in a file.

    Needed because a name bound in the calling file SHADOWS a same-named
    module: an engine takes `pool` as a parameter, so `pool.next()` is a
    method call and not a module attribute. Without this rule the binding
    check below reported twenty-one false positives on a clean repo
    (CLAUDE.md §22).
    """
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.arg):
            out.add(n.arg)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            out.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
    return out


def test_shared_calls_bind_against_real_signatures():
    """CLAUDE.md §17's check #1 — the one that earns its keep.

    A sibling repo had two of three engines calling `classify(html, url=…)`,
    putting the URL where the status belongs, and BOTH crashed on their
    FIRST fetch while import, --help, compileall, the undefined-name walk
    and 400+ green assertions all passed. None of those calls a function the
    way a live run does.

    Note what this does when it cannot RESOLVE a name: it FAILS. CLAUDE.md
    §22 records a version that resolved with `getattr(owner, attr, None)`
    and skipped anything not callable — so a name the shared module does
    not define at all returned None, was not callable, and was silently
    skipped. The loudest thing the check could have said was the one case
    it stayed quiet about.
    """
    import importlib
    bound_total = 0
    for name in ENGINES + ("weibo_api",):
        path = os.path.join(HERE, f"{name}.py")
        tree = ast.parse(open(path, encoding="utf-8").read())
        shadowed = _bound_names(tree)

        modules, direct = {}, {}
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    modules[a.asname or a.name.split(".")[0]] = a.name
            elif isinstance(n, ast.ImportFrom) and n.module:
                for a in n.names:
                    direct[a.asname or a.name] = (n.module, a.name)

        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            target = None
            if isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name):
                mod = n.func.value.id
                if mod in shadowed or mod not in modules:
                    continue
                if modules[mod].split(".")[0] not in SHARED_MODULES:
                    continue
                target = (modules[mod], n.func.attr)
            elif isinstance(n.func, ast.Name) and n.func.id in direct:
                mod, attr = direct[n.func.id]
                if mod.split(".")[0] not in SHARED_MODULES:
                    continue
                target = (mod, attr)
            if not target:
                continue

            mod_name, attr = target
            module = importlib.import_module(mod_name)
            fn = getattr(module, attr, None)
            if not check(fn is not None,
                         f"{name}:{n.lineno} calls {mod_name}.{attr}, which "
                         f"{mod_name} does not define"):
                continue
            if not check(callable(fn),
                         f"{name}:{n.lineno} calls {mod_name}.{attr}, which "
                         "is not callable"):
                continue
            try:
                inspect.signature(fn).bind(
                    *[object()] * len(n.args),
                    **{k.arg: object() for k in n.keywords if k.arg})
                bound_total += 1
            except TypeError as exc:
                check(False, f"{name}:{n.lineno} {mod_name}.{attr}(...) does "
                             f"not match the real signature: {exc}")

    # A binding check that binds nothing passes for the wrong reason (§22).
    check(bound_total > 30,
          f"the binding check actually scanned something ({bound_total} calls)")


def test_no_undefined_names():
    """Five lines that cover the branches an offline suite cannot execute.

    A sibling repo's engine died with `NameError` on a line reached only
    while fetching, after an import had been removed. The module imported
    cleanly, --help worked, compileall passed, the whole offline suite
    passed and CI was green (CLAUDE.md §10).

    Deliberately COARSE — it pools every binding in a file rather than
    tracking scopes — so it under-reports rather than inventing problems.
    """
    import builtins
    # The module dunders are real at runtime and are in no builtins list.
    known_builtins = set(dir(builtins)) | {
        "__file__", "__name__", "__doc__", "__package__", "__spec__",
        "__loader__", "__builtins__", "__debug__"}
    for name in ENGINES + SHARED_MODULES + ("weibo_api",):
        path = os.path.join(HERE, f"{name}.py")
        if not os.path.exists(path):
            continue
        tree = ast.parse(open(path, encoding="utf-8").read())
        bound = _bound_names(tree) | known_builtins
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    bound.add(a.asname or a.name.split(".")[0])
            elif isinstance(n, ast.ImportFrom):
                for a in n.names:
                    bound.add(a.asname or a.name)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                bound.add(n.name)
            elif isinstance(n, (ast.Global, ast.Nonlocal)):
                bound.update(n.names)
        for n in ast.walk(tree):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                check(n.id in bound,
                      f"{name}:{n.lineno} uses {n.id!r}, which is never "
                      "imported, defined or assigned in that file")


def test_no_unreachable_code():
    """A statement after return/raise/break/continue in the same block.

    CLAUDE.md §22: this found the SAME fifteen lines in six repos, byte for
    byte, present since each one's first commit — a function whose `def`
    line had been lost, leaving its docstring and body absorbed into the end
    of the function above it. It parses, it imports, --help works,
    compileall passes, and the undefined-name walk above cannot see it and
    should not: that walk pools bindings rather than tracking scopes, so a
    name in the dead block resolves against a real parameter elsewhere.
    Six finds, zero false positives across eighteen repos.
    """
    terminators = (ast.Return, ast.Raise, ast.Break, ast.Continue)
    scanned = 0
    for name in ENGINES + SHARED_MODULES + ("weibo_api", "smoke_test"):
        path = os.path.join(HERE, f"{name}.py")
        if not os.path.exists(path):
            continue
        tree = ast.parse(open(path, encoding="utf-8").read())
        for node in ast.walk(tree):
            for field in ("body", "orelse", "finalbody"):
                block = getattr(node, field, None)
                if not isinstance(block, list):
                    continue
                scanned += 1
                for i, stmt in enumerate(block[:-1]):
                    if isinstance(stmt, terminators):
                        nxt = block[i + 1]
                        check(False,
                              f"{name}:{nxt.lineno} is unreachable — the "
                              f"statement at line {stmt.lineno} always exits "
                              "this block")
    check(scanned > 50, f"the unreachable-code walk scanned something ({scanned})")


def test_engines_import_their_driver_at_module_level():
    """CLAUDE.md §10, and it drifts back silently.

    A sibling repo's pyppeteer engine imported `launch`/`connect` inside the
    launch path, so the module imported cleanly with no pyppeteer installed:
    the suite's "engine absent" group never skipped, and the CI job that
    exists to fail on unexpected skips could not have caught a broken
    import. It also let CI run against a stub version for a while.
    """
    expected = {"playwright_scraper": "playwright",
                "puppeteer_scraper": "pyppeteer",
                "selenium_scraper": "selenium"}
    for name, driver in expected.items():
        tree = ast.parse(open(os.path.join(HERE, f"{name}.py"),
                              encoding="utf-8").read())
        top = set()
        for n in tree.body:            # module level ONLY, not ast.walk
            if isinstance(n, ast.Import):
                top.update(a.name.split(".")[0] for a in n.names)
            elif isinstance(n, ast.ImportFrom) and n.module:
                top.add(n.module.split(".")[0])
        check(driver in top,
              f"{name} imports {driver} at MODULE level, so the suite really "
              "skips when the driver is absent")


def test_dockerfile_copies_what_the_entrypoint_imports():
    """CI never builds the image, which is how three repos shipped a broken
    one — `ModuleNotFoundError` on every invocation, --help included,
    because one module was missing from the COPY list (CLAUDE.md §10).
    This check needs no Docker.
    """
    path = os.path.join(HERE, "Dockerfile")
    if not os.path.exists(path):
        return skip("dockerfile", "no Dockerfile")
    docker = open(path, encoding="utf-8").read()
    # Join the backslash continuations FIRST. A COPY list long enough to
    # need wrapping is exactly the one worth checking, and a line-oriented
    # regex silently reads none of it — which made this check report every
    # module missing, including one that was right there.
    joined = re.sub(r"\\\s*\n\s*", " ", docker)
    copied = set()
    for line in joined.splitlines():
        if not line.strip().upper().startswith("COPY "):
            continue
        parts = line.split()[1:-1]          # drop "COPY" and the destination
        copied.update(p for p in parts if not p.startswith("--"))

    entry = re.search(r'(?:ENTRYPOINT|CMD)\s*\[\s*"[^"]*"\s*,\s*"([^"]+\.py)"',
                      docker)
    roots = [entry.group(1)] if entry else ["playwright_scraper.py"]
    needed, seen = set(), set()
    while roots:
        mod = roots.pop()
        if mod in seen:
            continue
        seen.add(mod)
        p = os.path.join(HERE, mod)
        if not os.path.exists(p):
            continue
        needed.add(mod)
        tree = ast.parse(open(p, encoding="utf-8").read())
        for n in ast.walk(tree):
            names = []
            if isinstance(n, ast.Import):
                names = [a.name.split(".")[0] for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.module:
                names = [n.module.split(".")[0]]
            for x in names:
                if os.path.exists(os.path.join(HERE, f"{x}.py")):
                    roots.append(f"{x}.py")
    for mod in sorted(needed):
        check(mod in copied or any(mod in c for c in copied),
              f"Dockerfile COPYs {mod}, which the entrypoint's import graph "
              "needs — without it the image dies on every invocation")


# ---------------------------------------------------------------------------
# 8. Wording, secrets and the output contract
# ---------------------------------------------------------------------------

def test_banned_wording():
    """Assembled from pieces, so this file can scan ITSELF.

    CLAUDE.md §22: three repos' versions of this check exempted
    `smoke_test.py` wholesale — the file most likely to acquire a stray
    phrase, or a pasted credential, was the one file nobody scanned.
    Building the needles at runtime removes the need for an exemption.
    """
    banned = [("anti" + "detect browser"), ("cloud" + " browser"),
              ("2scraper Anti" + "detect Browser"), ("gate." + "2prx.com"),
              ("--anti" + "detect"), ("ANTI" + "DETECT_LOCAL_API")]
    scanned = 0
    for root, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs
                   if d not in {".git", "__pycache__", "captures"}
                   and not os.path.exists(os.path.join(root, d, "pyvenv.cfg"))]
        for fn in files:
            if not fn.endswith((".py", ".md", ".txt", ".toml", ".yml", ".yaml",
                                ".example", "Dockerfile")):
                continue
            p = os.path.join(root, fn)
            try:
                text = open(p, encoding="utf-8").read().lower()
            except (OSError, UnicodeDecodeError):
                continue
            scanned += 1
            for needle in banned:
                check(needle not in text,
                      f"{os.path.relpath(p, HERE)} contains a banned phrase "
                      "(CLAUDE.md §12) — say 'Scraping Browser API'")
    check(scanned > 10, f"the wording scan read something ({scanned} files)")


def test_fixtures_are_scrubbed():
    """Guard with PATTERNS, not the old literals (CLAUDE.md §10).

    Matching the specific names that were scrubbed would pass forever while
    the NEXT capture pasted in unscrubbed sails through. These match the
    SHAPES that carry identity and session material.
    """
    src = open(os.path.join(HERE, "smoke_test.py"), encoding="utf-8").read()
    # Assembled from pieces for the same reason the wording check is: a
    # guard that spells out the shape it forbids matches ITSELF, and this
    # file is one of the files it scans.
    for pattern, what in [
            ('"session' + 'Id"', "a session id"),
            ("anti-" + "csrftoken", "a CSRF token"),
            (r"\bSUB=_2A" + r"[A-Za-z0-9]{10,}", "a real visitor cookie"),
            (r"\b[0-9a-f]{3" + r"2}\b", "a 32-hex string, the shape of a key"),
            (r"https://wx\d\.sina" + r"img\.cn", "a real image CDN URL")]:
        check(not re.search(pattern, src),
              f"smoke_test.py fixtures still carry {what}")

    # And the positive half: the placeholders really are in place.
    check("示例用户" in src, "author names are the placeholder form")
    for post in HOT["statuses"]:
        check(post["user"]["screen_name"].startswith("示例"),
              "every fixture author is a placeholder, not a real account")
        check(int(post["user"]["idstr"]) >= 900000000,
              "every fixture uid is renumbered out of the real range")


def test_env_example_matches_what_the_code_reads():
    """Equal in BOTH directions. A documented-but-unread variable is worse
    than an undocumented one (CLAUDE.md §3)."""
    import env_config
    path = os.path.join(HERE, ".env.example")
    if not os.path.exists(path):
        return check(False, ".env.example exists")
    declared = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", open(path, encoding="utf-8").read(), re.M))
    read = set(env_config.ENV_KEYS)
    check(declared == read,
          f".env.example documents exactly what env_config reads "
          f"(only-in-file={sorted(declared - read)}, "
          f"only-in-code={sorted(read - declared)})")
    for name in read:
        check(name == "TWOCAPTCHA_KEY" or name.startswith("WEIBO_"),
              f"{name}: per-site variables carry the site prefix, or a .env "
              "entry is silently ignored")


def test_copied_env_example_reads_as_unset():
    """CLAUDE.md §17: `cp .env.example .env` must not read as CONFIGURED.

    A literal-only placeholder check passed both credentialled URLs through,
    so a copied example connected with the string `{login}-zone-…` as its
    username and got a 401 a long way from its cause.
    """
    import env_config
    example = open(os.path.join(HERE, ".env.example"), encoding="utf-8").read()
    for line in example.splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
        if not m:
            continue
        name, value = m.group(1), m.group(2).strip()
        if not value:
            continue
        looks_placeholder = (
            value in getattr(env_config, "_PLACEHOLDERS", set())
            or bool(getattr(env_config, "_BRACED_PLACEHOLDER_RE").search(value)))
        check(looks_placeholder,
              f".env.example's {name} is recognised as a placeholder — a "
              "copied example must never be sent to an API as a real value")


def test_output_contract():
    import output_writer as O
    eq(O.EXIT_NO_PRODUCTS, 4, "exit 4 = zero products")
    eq(O.EXIT_BLOCKED, 3, "exit 3 = blocked")
    eq(O.EXIT_API_ERROR, 5, "exit 5 = remote API error")
    eq(O.EXIT_PARTIAL, 6, "exit 6 = partial")

    names = [f.name for f in fields(O.Post)]
    eq(names[:5], ["source", "scraped_at", "url", "sku", "title"],
       "the family row prefix is byte-identical and in order")
    check("sku" in names, "the id column is called sku family-wide")

    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "x")
        rc = O.save([], out, "both")
        eq(rc, O.EXIT_NO_PRODUCTS, "an empty run returns exit 4")
        check(not os.path.exists(out + ".json"),
              "and writes NOTHING, so last night's good output survives")

        rc = O.save([], out, "both", allow_empty=True)
        check(os.path.exists(out + ".csv"), "--allow-empty writes the file")
        header = open(out + ".csv", encoding="utf-8").readline().strip()
        eq(header.split(",")[:5], ["source", "scraped_at", "url", "sku", "title"],
           "an empty CSV still carries its header")

        row = O.Post(sku="A1", title="t", topics=["x", "y"], page=1, position=1)
        O.save([row], out, "both")
        line = open(out + ".csv", encoding="utf-8").read().splitlines()[1]
        check(" | " in line, "a list cell is joined readably, not repr()'d")


def test_complete_stop_reasons():
    import output_writer as O
    for reason in ("cursor_exhausted", "feed_not_addressable",
                   "single_page_mode", "no_new_products"):
        check(reason in O.COMPLETE_STOP_REASONS,
              f"{reason} is a COMPLETE run")
    check("error_on_page_2" not in O.COMPLETE_STOP_REASONS,
          "an error is not a complete run")


def test_pagination_is_asked_per_url():
    """CLAUDE.md §18: the question is per URL, not per site."""
    import page_flow as F
    import product_parser as P
    check(not F.pagination_is_addressable(P.hot_feed_url()),
          "the hot feed is NOT addressable: it answers max_id:1 forever and "
          "re-rolls, so there is no page 2 to fetch")
    check(F.pagination_is_addressable(P.user_feed_url("123", 0)),
          "a user cursor URL is a real address")
    check(not F.pagination_is_addressable("not a url"), "junk is refused")


def test_masking_is_global_and_keeps_host_and_port():
    import weibo_api as W
    got = W.mask_secrets("key=aaa key=bbb key=ccc")
    eq(got, "key=*** key=*** key=***",
       "every occurrence is masked, not just the first (CLAUDE.md §8)")
    got = W.mask_secrets("http://user:secret@1.2.3.4:8080/x")
    check("secret" not in got, "a proxy password is masked")
    check("1.2.3.4:8080" in got,
          "host and port are KEPT — which exit a run used is the point of "
          "the log and is not the secret")


def test_visitor_client_fails_loudly():
    """A function returning {} on error is this codebase's most common
    historical bug class (CLAUDE.md §8)."""
    import weibo_api as W
    check(issubclass(W.VisitorError, Exception), "VisitorError exists")
    check(issubclass(W.TransportError, Exception),
          "TransportError is its own type: a dead proxy is not a timeout and "
          "the two want opposite responses")
    check(W.VisitorError is not W.TransportError, "and they are distinct")
    sig = inspect.signature(W.mint_visitor_cookies)
    eq(list(sig.parameters)[:4], ["session", "user_agent", "proxy", "timeout"],
       "mint_visitor_cookies signature")
    check("timeout" in sig.parameters, "every remote call is bounded")


# ---------------------------------------------------------------------------
# 9. Engine groups — each guarded, each skip RECORDED
# ---------------------------------------------------------------------------
# The suite must pass with no engine library installed at all. For that to
# mean anything CI installs each engine in its own venv and fails if that
# engine's group reports a skip: "skipped, engine absent" reads identically
# to a real import error (CLAUDE.md §10).

def _engine_checks(mod, name):
    args = mod.parse_args(["--mode", "hot", "--pages", "2", "--out", "x"])
    eq(args.mode, "hot", f"{name}: --mode parsed")
    eq(args.pages, 2, f"{name}: --pages parsed")
    eq(args.headless, True, f"{name}: headless is the default")
    eq(mod.parse_args(["--headful"]).headless, False, f"{name}: --headful")

    for bad in (["--pages", "0"], ["--pages", "99999"]):
        try:
            mod.parse_args(bad)
            check(False, f"{name}: {bad} should be refused")
        except SystemExit:
            check(True, f"{name}: {bad} refused")

    # --mode user/post without a URL is a usage error, not a crash later.
    for mode in ("user", "post"):
        try:
            mod._resolve_target(mod.parse_args(["--mode", mode]))
            check(False, f"{name}: --mode {mode} with no --url must be refused")
        except SystemExit:
            check(True, f"{name}: --mode {mode} needs --url")

    eq(mod._resolve_target(mod.parse_args(["--mode", "hot"])), (None, None, None),
       f"{name}: --mode hot needs no target")
    uid, bid, mid = mod._resolve_target(
        mod.parse_args(["--mode", "user", "--url", "https://weibo.com/u/123456"]))
    eq(uid, "123456", f"{name}: account id read from the URL")

    # A proxy failure is named, and is not a timeout (CLAUDE.md §8).
    eq(mod._proxy_failure(Exception("net::ERR_TUNNEL_CONNECTION_FAILED at x")),
       "ERR_TUNNEL_CONNECTION_FAILED", f"{name}: a dead proxy is recognised")
    eq(mod._proxy_failure(Exception("Timeout 30000ms exceeded")), "",
       f"{name}: a timeout is NOT reported as a proxy failure")

    check(mod.MIN_CARD_MATCHES > 1,
          f"{name}: MIN_CARD_MATCHES > 1 — waiting for ONE match resolves on "
          "something unrelated long before the feed is there (§5)")

    # Credentials never reach a command line (§8).
    # A DISTINCTIVE password, because a one-letter one is a substring of
    # "http" and an assertion that it is absent from the server string
    # passes for the wrong reason.
    #
    # Spelled `user:secret@` on purpose: that exact form is already in
    # ci_checks.py's CREDENTIAL_ALLOWED list for this family's masking
    # fixtures, so this line reuses an allowlist entry instead of widening
    # the list. Adding an entry should be a decision, not a side effect of
    # picking a password.
    PROXY = "http://user:secret@1.2.3.4:8080"
    if hasattr(mod, "_playwright_proxy"):
        got = mod._playwright_proxy(PROXY)
        check("secret" not in got["server"], f"{name}: no password in "
              "the server string, which becomes a browser command line")
        eq(got.get("password"), "secret",
           f"{name}: the password goes in the driver's own field")
        eq(got.get("server"), "http://1.2.3.4:8080",
           f"{name}: host and port survive")
    if hasattr(mod, "_split_proxy"):
        server, user, pw = mod._split_proxy(PROXY)
        check("secret" not in server,
              f"{name}: no password in the launch args")
        eq(user, "user", f"{name}: username read out")
    if hasattr(mod, "strip_proxy_credentials"):
        server, had = mod.strip_proxy_credentials(PROXY)
        check(had, f"{name}: credentials were detected")
        check("secret" not in server and "user@" not in server,
              f"{name}: credentials STRIPPED, because this driver cannot "
              "authenticate a proxy at all")


def test_playwright_engine():
    try:
        import playwright_scraper as m
    except ImportError as exc:
        return skip("playwright", str(exc))
    _engine_checks(m, "playwright")
    eq(m._chrome_ua("140.0.7339.5"),
       "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
       "playwright: the UA is built from the installed Chromium, not a "
       "literal (CLAUDE.md §8)")


def test_puppeteer_engine():
    try:
        import puppeteer_scraper as m
    except ImportError as exc:
        return skip("puppeteer", str(exc))
    _engine_checks(m, "puppeteer")


def test_selenium_engine():
    try:
        import selenium_scraper as m
    except ImportError as exc:
        return skip("selenium", str(exc))
    _engine_checks(m, "selenium")


def test_cdp_and_proxy_are_refused_together():
    """The Scraping Browser already proxies (CLAUDE.md §20)."""
    for name in ENGINES:
        src = open(os.path.join(HERE, f"{name}.py"), encoding="utf-8").read()
        check("cannot be combined with --cdp-endpoint" in src,
              f"{name}: refuses --proxy with --cdp-endpoint, rather than "
              "stacking two exits")


def test_selenium_states_what_it_cannot_do():
    """A known limitation, pinned rather than half-guarded (§10)."""
    src = open(os.path.join(HERE, "selenium_scraper.py"), encoding="utf-8").read()
    check("debuggerAddress" in src and "nowhere to put a password" in src,
          "selenium names the real reason an authenticated CDP endpoint is "
          "refused — it is not a generic 'cannot connect to CDP'")
    check("cannot authenticate a proxy" in src,
          "and says plainly that proxy credentials do not work here")


def test_no_javascript_in_the_shared_modules():
    """CLAUDE.md §1: let NO JavaScript cross that boundary.

    Selenium's `execute_script` takes a function BODY with an explicit
    `return` while Playwright and pyppeteer take `() => expr`, so a shared
    snippet quietly acquires one driver's dialect.
    """
    for name in SHARED_MODULES:
        path = os.path.join(HERE, f"{name}.py")
        if not os.path.exists(path):
            continue
        src = open(path, encoding="utf-8").read()
        for tell in ("=> {", "document.querySelector", "arguments[0]",
                     "window.scrollTo"):
            check(tell not in src,
                  f"{name}.py contains JavaScript ({tell!r}) — it belongs in "
                  "the engine that speaks that dialect")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — a crashing check is a failure
            FAILURES.append(f"{fn.__name__} raised {type(exc).__name__}: {exc}")

    print(f"\n{'=' * 62}")
    print(f"{len(tests)} groups, {CHECKS} checks")
    if SKIPS:
        print(f"\nskipped ({len(SKIPS)}):")
        for s in SKIPS:
            print(f"  - {s}")
    if FAILURES:
        print(f"\nFAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print(f"  FAILED: {f}")
        print(f"{'=' * 62}")
        return 1
    print(f"\nok       all {CHECKS} checks passed")
    print(f"{'=' * 62}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
