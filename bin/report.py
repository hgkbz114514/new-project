from pathlib import Path
# -*- coding: utf-8 -*-
"""One-shot delivery report: what the running proxy actually gives you."""

import json
import os
import sys
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vkstdio import harden  # noqa: E402

harden("report", also_file=False)

CTRL = "http://127.0.0.1:9097"
HDR = {"Authorization": "Bearer set-your-secret"}
PROF = str(Path(__file__).resolve().parent.parent / "profiles")


def api(path):
    req = urllib.request.Request(CTRL + path, headers=HDR)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def main():
    print("=" * 62)
    print("  vpnkit 交付报告")
    print("=" * 62)

    try:
        d = api("/proxies")["proxies"]
    except Exception as e:
        print(f"控制接口不可用: {e}")
        d = {}

    if d:
        groups = sorted(k for k in d
                        if k.startswith("AUTO-") and len(k) == 7)
        print("\n[分组]")
        for g in ("PROXY", "SPEED-TOP", "AUTO-FAST", "AUTO-FALLBACK",
                  "LOAD-BALANCE", "GIT", "MEDIA", "AI", "TELEGRAM"):
            if g in d:
                now = d[g].get("now") or "(按需分发)"
                cnt = len(d[g].get("all") or [])
                print(f"  {g:14} -> {now}   (候选 {cnt})")
        print(f"  按国家分组     {len(groups)} 个: {', '.join(x[5:] for x in groups)}")

    # nodes_fast.json only carries what the speed probe measured, so join it
    # against nodes_pool.json (same server:port key) to get the exit country.
    geo = {}
    pool_p = os.path.join(PROF, "nodes_pool.json")
    if os.path.exists(pool_p):
        for n in json.load(open(pool_p, encoding="utf-8")):
            geo[f"{n.get('server')}:{n.get('port')}"] = n

    p = os.path.join(PROF, "nodes_fast.json")
    rows = []
    if os.path.exists(p):
        rows = [r for r in json.load(open(p, encoding="utf-8"))
                if float(r.get("kbps") or 0) > 0]
        rows.sort(key=lambda r: -float(r.get("kbps") or 0))
    if rows:
        print("\n[实测最快 8 个节点]")
        for r in rows[:8]:
            g = geo.get(f"{r.get('server')}:{r.get('port')}") or {}
            cc = g.get("exit_cc") or g.get("cc") or "?"
            print("  {:16} {:>6}  {:3}  {:7.1f} KB/s   {} {}".format(
                str(r.get("server"))[:16], str(r.get("port")), cc,
                float(r.get("kbps") or 0),
                str(g.get("exit_isp") or "")[:26], str(r.get("type") or "")))

    # Country spread of the whole pool.
    pool = os.path.join(PROF, "nodes_pool.json")
    if os.path.exists(pool):
        nodes = json.load(open(pool, encoding="utf-8"))
        cc = {}
        for n in nodes:
            k = n.get("exit_cc") or n.get("cc") or "??"
            cc[k] = cc.get(k, 0) + 1
        top = sorted(cc.items(), key=lambda kv: -kv[1])
        print(f"\n[节点池 {len(nodes)} 个，覆盖 {len(cc)} 个国家]")
        print("  " + "  ".join(f"{k}:{v}" for k, v in top))


if __name__ == "__main__":
    main()
