# -*- coding: utf-8 -*-
"""Probe a catalogue of interesting sites through the local vpnkit mixed proxy.

Each row: (category, label, url, note)
We record HTTP status, wall time and body size so the user gets measured
answers instead of a wish list. Sites that need a login still return 200/302
when reachable, which is enough to prove the tunnel works for them.
"""
import concurrent.futures as cf
import time
import urllib3

import requests

PROXY = "http://127.0.0.1:7897"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")

urllib3.disable_warnings()

SITES = [
    # ---- AI ----
    ("AI",    "ChatGPT",            "https://chatgpt.com/"),
    ("AI",    "Claude",             "https://claude.ai/login"),
    ("AI",    "Gemini",             "https://gemini.google.com/"),
    ("AI",    "Grok",               "https://grok.com/"),
    ("AI",    "Perplexity",         "https://www.perplexity.ai/"),
    ("AI",    "Copilot",            "https://copilot.microsoft.com/"),
    ("AI",    "HuggingFace",        "https://huggingface.co/"),
    ("AI",    "LM Arena 盲测",       "https://lmarena.ai/"),
    ("AI",    "Suno 作曲",           "https://suno.com/"),
    ("AI",    "Runway 视频",         "https://runwayml.com/"),
    ("AI",    "Civitai 模型站",      "https://civitai.com/"),
    ("AI",    "Midjourney",         "https://www.midjourney.com/"),

    # ---- 开发 / 技术 ----
    ("DEV",   "GitHub",             "https://github.com/"),
    ("DEV",   "Hacker News",        "https://news.ycombinator.com/"),
    ("DEV",   "Stack Overflow",     "https://stackoverflow.com/"),
    ("DEV",   "npm",                "https://www.npmjs.com/"),
    ("DEV",   "PyPI",               "https://pypi.org/"),
    ("DEV",   "Docker Hub",         "https://hub.docker.com/"),
    ("DEV",   "Cloudflare",         "https://www.cloudflare.com/"),
    ("DEV",   "Vercel",             "https://vercel.com/"),
    ("DEV",   "Product Hunt",       "https://www.producthunt.com/"),
    ("DEV",   "DevDocs",            "https://devdocs.io/"),

    # ---- 视频 / 音乐 ----
    ("MEDIA", "YouTube",            "https://www.youtube.com/"),
    ("MEDIA", "Netflix",            "https://www.netflix.com/"),
    ("MEDIA", "Twitch",             "https://www.twitch.tv/"),
    ("MEDIA", "Spotify",            "https://open.spotify.com/"),
    ("MEDIA", "SoundCloud",         "https://soundcloud.com/"),
    ("MEDIA", "Bilibili 港澳台",     "https://www.bilibili.com/"),
    ("MEDIA", "Niconico",           "https://www.nicovideo.jp/"),
    ("MEDIA", "Vimeo",              "https://vimeo.com/"),

    # ---- 动漫 / 二次元 ----
    ("ACGN",  "MyAnimeList",        "https://myanimelist.net/"),
    ("ACGN",  "AniList",            "https://anilist.co/"),
    ("ACGN",  "Danbooru",           "https://danbooru.donmai.us/"),
    ("ACGN",  "Pixiv",             "https://www.pixiv.net/"),
    ("ACGN",  "E-Hentai",           "https://e-hentai.org/"),
    ("ACGN",  "nhentai",            "https://nhentai.net/"),
    ("ACGN",  "Sukebei",            "https://sukebei.nyaa.si/"),
    ("ACGN",  "Hanime",             "https://hanime.tv/"),

    # ---- 资讯 / 知识 ----
    ("INFO",  "Wikipedia",          "https://en.wikipedia.org/"),
    ("INFO",  "Reddit",             "https://www.reddit.com/"),
    ("INFO",  "BBC",                "https://www.bbc.com/"),
    ("INFO",  "Reuters",            "https://www.reuters.com/"),
    ("INFO",  "NHK",                "https://www3.nhk.or.jp/"),
    ("INFO",  "Internet Archive",   "https://archive.org/"),
    ("INFO",  "Google Scholar",     "https://scholar.google.com/"),
    ("INFO",  "arXiv",              "https://arxiv.org/"),

    # ---- 工具 / 资源 ----
    ("TOOL",  "Google",             "https://www.google.com/"),
    ("TOOL",  "Gmail",              "https://mail.google.com/"),
    ("TOOL",  "Telegram Web",       "https://web.telegram.org/"),
    ("TOOL",  "Discord",            "https://discord.com/"),
    ("TOOL",  "Speedtest",          "https://www.speedtest.net/"),
    ("TOOL",  "Z-Library 镜像",      "https://z-lib.io/"),
    ("TOOL",  "Anna's Archive",     "https://annas-archive.org/"),
    ("TOOL",  "LibGen",             "https://libgen.is/"),
    ("TOOL",  "Rutracker",          "https://rutracker.org/"),
    ("TOOL",  "Nyaa 种子站",         "https://nyaa.si/"),
    ("TOOL",  "Steam",              "https://store.steampowered.com/"),
    ("TOOL",  "eBay",               "https://www.ebay.com/"),

    # ---- 生活 / 购物 ----
    ("LIFE",  "Amazon JP",          "https://www.amazon.co.jp/"),
    ("LIFE",  "Mercari JP",         "https://jp.mercari.com/"),
    ("LIFE",  "Booking",            "https://www.booking.com/"),
    ("LIFE",  "Airbnb",             "https://www.airbnb.com/"),
]


def probe(row):
    cat, label, url, *_ = row
    t0 = time.time()
    try:
        r = requests.get(
            url, proxies={"http": PROXY, "https": PROXY},
            timeout=18, allow_redirects=True, verify=False,
            headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
        )
        ms = int((time.time() - t0) * 1000)
        return (cat, label, url, r.status_code, ms, len(r.content), "")
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        kind = type(e).__name__
        return (cat, label, url, 0, ms, 0, kind)


def main():
    rows = [(c, l, u, n) for (c, l, u, *rest) in
            [(c, l, u) for (c, l, u, *_) in SITES] for n in [""]]
    rows = [(x[0], x[1], x[2]) for x in rows]
    # rebuild tuples properly
    rows = [(cat, label, url) for (cat, label, url, *_ ) in SITES]

    results = []
    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        for res in ex.map(probe, rows):
            results.append(res)
            cat, label, url, code, ms, size, err = res
            mark = "OK " if 200 <= code < 400 else ("-- " if code == 0 else f"{code} ")
            print(f"{mark:>4} {cat:<6} {label:<18} {ms:>6}ms {size:>9}B  {err}", flush=True)

    print("\n" + "=" * 70)
    ok = [r for r in results if 200 <= r[3] < 400]
    bad = [r for r in results if not (200 <= r[3] < 400)]
    print(f"可达 {len(ok)}/{len(results)}")
    print("\n不可达：")
    for cat, label, url, code, ms, size, err in bad:
        print(f"  {cat:<6} {label:<18} code={code} {err}")

    # grouped summary
    print("\n按分类：")
    cats = {}
    for r in results:
        cats.setdefault(r[0], []).append(r)
    for c, rs in cats.items():
        good = sum(1 for r in rs if 200 <= r[3] < 400)
        print(f"  {c:<6} {good}/{len(rs)}")


if __name__ == "__main__":
    main()
