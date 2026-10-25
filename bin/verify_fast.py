# -*- coding: utf-8 -*-
"""
vpnkit :: 快速真实出口验证
对每个节点：切换 PROXY 选择器 -> 走本地 mixed 端口取 ip-api 出口 -> 记录真实国家/ISP
比旧版快 10 倍：单目标、单次、硬超时、无重试；失败即跳过。
  python verify_fast.py [ctrl_hostport] [mixed_port]
"""
import json, sys, time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
PROF = ROOT / "profiles"
LOG = ROOT / "09_logs" / "verify_fast.log"

CTRL = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1:9097"
MIXED = int(sys.argv[2]) if len(sys.argv) > 2 else 7897
BASE = f"http://{CTRL}"
PX = {"http": f"http://127.0.0.1:{MIXED}", "https": f"http://127.0.0.1:{MIXED}"}
TIMEOUT = 7


def log(m):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {m}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    src = PROF / "nodes_final.json"
    if not src.exists():
        src = PROF / "nodes_overseas.json"
    nodes = json.loads(src.read_text(encoding="utf-8"))

    try:
        allp = requests.get(f"{BASE}/proxies", timeout=10).json()["proxies"]
    except Exception as e:
        log(f"!! 控制器不可用 {e}")
        return
    names = [n for n in allp if n[:3].isdigit() and " " in n]
    log(f"待验证 {len(names)} 个")

    ok = []
    t0 = time.time()
    for i, name in enumerate(names, 1):
        try:
            r = requests.put(f"{BASE}/proxies/PROXY",
                             json={"name": name}, timeout=4)
            if r.status_code not in (200, 204):
                continue
        except Exception:
            continue
        time.sleep(0.05)
        got = None
        for url in ("http://ip-api.com/json/?fields=status,country,countryCode,isp,query",
                    "https://ipinfo.io/json"):
            try:
                rr = requests.get(url, proxies=PX, timeout=TIMEOUT)
                d = rr.json()
                if d.get("status") == "fail":
                    break
                got = {"exit_ip": d.get("query") or d.get("ip"),
                       "exit_cc": d.get("countryCode") or d.get("country"),
                       "exit_country": d.get("country"),
                       "exit_isp": d.get("isp") or d.get("org")}
                break
            except Exception:
                continue
        if got and got.get("exit_ip"):
            n = dict(nodes[int(name[:3])]) if int(name[:3]) < len(nodes) else {}
            n.update(got)
            n["name"] = name
            ok.append(n)
        if i % 10 == 0:
            log(f"  {i}/{len(names)} 成功 {len(ok)}  已用 {time.time()-t0:.0f}s")

    ok.sort(key=lambda x: x.get("delay", 9999))
    out = PROF / "nodes_verified.json"
    out.write_text(json.dumps(ok, ensure_ascii=False, indent=1), encoding="utf-8")
    stat = {}
    for n in ok:
        stat[n.get("exit_cc") or "?"] = stat.get(n.get("exit_cc") or "?", 0) + 1
    log(f"=== 真实验证通过 {len(ok)}/{len(names)}  耗时 {time.time()-t0:.0f}s")
    log(f"真实出口分布: {dict(sorted(stat.items(), key=lambda x: -x[1]))}")
    for n in ok[:15]:
        log(f"  {n['name'][:20]:22s} {n.get('exit_cc',''):4s} {str(n.get('exit_country',''))[:16]:18s} {str(n.get('exit_isp',''))[:20]:22s} {n.get('delay')}ms")


if __name__ == "__main__":
    main()
