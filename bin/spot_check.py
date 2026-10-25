# -*- coding: utf-8 -*-
"""Browser-path spot check: same UA, same system-proxy route, no redirects."""
import time

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

SITES = [
    ("youtube", "https://www.youtube.com"),
    ("x.com", "https://x.com"),
    ("chatgpt", "https://chatgpt.com"),
    ("claude", "https://claude.ai/login"),
    ("netflix", "https://www.netflix.com"),
    ("telegram", "https://web.telegram.org"),
    ("github", "https://github.com"),
    ("wikipedia", "https://en.wikipedia.org"),
]


def main():
    s = requests.Session()
    s.trust_env = False
    s.proxies = {"http": "http://127.0.0.1:7897",
                 "https": "http://127.0.0.1:7897"}
    s.headers["User-Agent"] = UA

    ok = 0
    for label, url in SITES:
        t0 = time.time()
        try:
            r = s.get(url, timeout=25, allow_redirects=False)
            ms = (time.time() - t0) * 1000
            good = 0 < r.status_code < 500
            ok += 1 if good else 0
            print(f"  {'OK ' if good else '!! '}{label:<10} {r.status_code:<4} "
                  f"{len(r.content):>8}B {ms:6.0f}ms")
        except Exception as e:
            print(f"  !! {label:<10} {type(e).__name__}: {str(e)[:48]}")
    print(f"\n{ok}/{len(SITES)} 可达")


if __name__ == "__main__":
    main()
