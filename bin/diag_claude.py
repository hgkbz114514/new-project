from pathlib import Path
# -*- coding: utf-8 -*-
"""Claude 打不开的现场取证：组状态、钉子、逐节点实测。"""
import json
import os
import time

import requests
import urllib3

urllib3.disable_warnings()

PROXY = "http://127.0.0.1:7897"
CTRL = "http://127.0.0.1:9097"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")

TARGETS = [
    ("claude.ai/login",   "https://claude.ai/login"),
    ("claude.ai",         "https://claude.ai/"),
    ("api.anthropic",     "https://api.anthropic.com/v1/models"),
    ("portal.anthropic",  "https://portal.anthropic.com/verify"),
]


def ctrl(path, method="GET", payload=None):
    s = requests.Session()
    s.trust_env = False
    if method == "PUT":
        return s.put(CTRL + path, json=payload, timeout=8)
    return s.get(CTRL + path, timeout=8)


def hit(url, timeout=15):
    t0 = time.time()
    try:
        r = requests.get(url, proxies={"http": PROXY, "https": PROXY},
                         timeout=timeout, verify=False, allow_redirects=False,
                         headers={"User-Agent": UA,
                                  "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
        return r.status_code, int((time.time() - t0) * 1000), len(r.content), ""
    except Exception as e:
        return 0, int((time.time() - t0) * 1000), 0, type(e).__name__


def main():
    print("=== 1) 内核与端口 ===")
    import subprocess
    p = subprocess.run(["tasklist", "/FI", "IMAGENAME eq verge-mihomo.exe"],
                       capture_output=True, text=True, errors="ignore")
    print("verge-mihomo:", "存活" if "verge-mihomo" in p.stdout else "未运行")

    print("\n=== 2) CLAUDE 组状态 ===")
    for g in ("PROXY", "CLAUDE", "SPEED-TOP"):
        r = ctrl(f"/proxies/{g}")
        if r.ok:
            d = r.json()
            print(f"  {g:<10} now={d.get('now')}  type={d.get('type')}  候选={len(d.get('all', []))}")

    print("\n=== 3) 钉子文件 ===")
    pin_path = str(Path(__file__).resolve().parent.parent / "profiles" / "claude_pin.json")
    if os.path.exists(pin_path):
        with open(pin_path, encoding="utf-8") as f:
            pin = json.load(f)
        print(f"  name={pin.get('name')}")
        print(f"  key ={pin.get('key')}  cc={pin.get('cc')}  wins={pin.get('wins')}")
        print(f"  since={pin.get('since')}")
        print(f"  candidates={len(pin.get('candidates', []))}")
    else:
        print("  !! claude_pin.json 不存在")

    print("\n=== 4) 当前选中的 CLAUDE 节点逐目标实测 ===")
    for label, url in TARGETS:
        code, ms, size, err = hit(url)
        mark = "OK " if 0 < code < 500 else "-- "
        print(f"  {mark}{label:<16} {code:>3} {ms:>6}ms {size:>8}B {err}")

    print("\n=== 5) 逐个测试 CLAUDE 组成员 ===")
    r = ctrl("/proxies/CLAUDE")
    if not r.ok:
        print("  拿不到 CLAUDE 组")
        return
    members = r.json().get("all", [])
    original = r.json().get("now")
    print(f"  共 {len(members)} 个成员，测前 12 个\n")
    good = []
    for i, name in enumerate(members[:12]):
        ctrl("/proxies/CLAUDE", "PUT", {"name": name})
        code, ms, size, err = hit("https://claude.ai/login", timeout=12)
        ok = code in (200, 403, 302, 307)
        if ok:
            good.append((name, code, ms, size))
        mark = "OK " if ok else "-- "
        print(f"  {mark}{i:>2} {name:<28} {code:>3} {ms:>6}ms {size:>7}B {err}", flush=True)

    print(f"\n  可用 {len(good)}/{min(12, len(members))}")
    if good:
        best = min(good, key=lambda x: x[2])
        print(f"  最佳: {best[0]}  {best[1]} {best[2]}ms {best[3]}B")
        ctrl("/proxies/CLAUDE", "PUT", {"name": best[0]})
        print(f"  已切到 {best[0]}")
        print("\n  验证新选中节点：")
        for label, url in TARGETS:
            code, ms, size, err = hit(url)
            mark = "OK " if 0 < code < 500 else "-- "
            print(f"    {mark}{label:<16} {code:>3} {ms:>6}ms {size:>8}B {err}")
    else:
        print("  !! 前 12 个成员全部不可用")

    # A diagnostic must not leave the group somewhere else: the whole point of
    # pinning is a stable exit IP, and the loop above rotates the selector. Put
    # the previous choice back so running this is side-effect free.
    if original:
        ctrl("/proxies/CLAUDE", "PUT", {"name": original})
        print(f"\n  已恢复原选择 {original}（诊断不改变出口）")


if __name__ == "__main__":
    main()
