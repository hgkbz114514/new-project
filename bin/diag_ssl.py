# -*- coding: utf-8 -*-
"""Show the actual TLS failure, and test each SPEED-TOP node individually."""
import json
import os
import sys

import requests
import urllib3

urllib3.disable_warnings()

PROXY = "http://127.0.0.1:7897"
CTRL = "http://127.0.0.1:9097"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")


def ctrl(path, method="GET", payload=None):
    s = requests.Session()
    s.trust_env = False
    url = CTRL + path
    if method == "PUT":
        return s.put(url, json=payload, timeout=8)
    return s.get(url, timeout=8)


def show_error(url):
    print(f"\n--- 详细错误: {url} ---")
    try:
        requests.get(url, proxies={"http": PROXY, "https": PROXY}, timeout=15,
                     verify=False, headers={"User-Agent": UA})
    except Exception as e:
        cur = e
        depth = 0
        while cur is not None and depth < 6:
            print(f"  [{depth}] {type(cur).__module__}.{type(cur).__name__}: {cur}")
            cur = cur.__cause__ or (cur.args[0] if cur.args and isinstance(cur.args[0], BaseException) else None)
            depth += 1


def main():
    # 1) current selection
    r = ctrl("/proxies/PROXY")
    if r.ok:
        d = r.json()
        print(f"PROXY 组当前选中: {d.get('now')}")
        print(f"候选数: {len(d.get('all', []))}")

    for g in ("SPEED-TOP", "AUTO-FAST", "GIT"):
        try:
            r = ctrl(f"/proxies/{g}")
            if r.ok:
                print(f"{g:<12} now={r.json().get('now')}")
        except Exception as e:
            print(f"{g}: {e}")

    # 2) detailed error
    show_error("https://www.google.com/")

    print("\n\n=== 逐个节点实测（切 PROXY selector） ===")
    r = ctrl("/proxies/SPEED-TOP")
    if not r.ok:
        print("拿不到 SPEED-TOP，退出")
        return
    members = r.json().get("all", [])
    print(f"SPEED-TOP {len(members)} 个成员")

    test_url = "https://www.google.com/generate_204"
    results = []
    for i, name in enumerate(members):
        ctrl("/proxies/PROXY", "PUT", {"name": name})
        t0 = os.times().elapsed
        try:
            rr = requests.get(test_url, proxies={"http": PROXY, "https": PROXY},
                              timeout=8, verify=False, allow_redirects=False,
                              headers={"User-Agent": UA})
            ms = int((os.times().elapsed - t0) * 1000)
            results.append((name, rr.status_code, ms))
            print(f"  {i:>2} {name:<28} {rr.status_code} {ms}ms", flush=True)
        except Exception as e:
            ms = int((os.times().elapsed - t0) * 1000)
            results.append((name, 0, ms))
            print(f"  {i:>2} {name:<28} XX {ms}ms {type(e).__name__}", flush=True)

    ok = [x for x in results if x[1] in (200, 204)]
    print(f"\n可用 {len(ok)}/{len(results)}")
    if ok:
        best = min(ok, key=lambda x: x[2])
        print(f"最佳: {best[0]} {best[2]}ms -> 已切回")
        ctrl("/proxies/PROXY", "PUT", {"name": best[0]})
    else:
        print("!! 没有任何节点能通 google")


if __name__ == "__main__":
    main()
