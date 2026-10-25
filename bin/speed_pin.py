# -*- coding: utf-8 -*-
"""Measure the real throughput of the pinned Claude exit.

speed_probe records kbps=0.0 for this node because the TLS gate passes but the
throughput sample loses the race under 24-way concurrency. That is a sampling
artifact, not a broken node -- this script measures it alone, at full speed.
"""
import time

import requests

CTRL = 9097
PIN = "046 IT 286ms ss"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def main():
    requests.put(f"http://127.0.0.1:{CTRL}/proxies/CLAUDE",
                 json={"name": PIN}, timeout=8)
    time.sleep(1)

    s = requests.Session()
    s.trust_env = False
    s.proxies = {"http": "http://127.0.0.1:7897",
                 "https": "http://127.0.0.1:7897"}
    s.headers["User-Agent"] = UA

    for i in (1, 2):
        t0 = time.time()
        total = 0
        try:
            r = s.get("https://speed.cloudflare.com/__down?bytes=3000000",
                      timeout=40, stream=True)
            for chunk in r.iter_content(65536):
                total += len(chunk)
                if time.time() - t0 > 25:
                    break
        except Exception as e:
            print(f"  run{i}: {type(e).__name__}: {str(e)[:70]}")
            continue
        dt = time.time() - t0
        print(f"  run{i}: {total} B / {dt:.1f}s = {total / 1024 / dt:.1f} KB/s")


if __name__ == "__main__":
    main()
