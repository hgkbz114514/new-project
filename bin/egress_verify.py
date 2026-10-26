# -*- coding: utf-8 -*-
"""Egress-verify every node in nodes_real.json with per-node listeners, then
write back the survivors with kbps/tls data zeroed (transparent fakes excluded)."""
import concurrent.futures as cf
import json
import subprocess
import sys
import time
from pathlib import Path

import requests
import yaml

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
PROF = ROOT / "profiles"
CORE = r"E:\Clash Verge\verge-mihomo.exe"
PORT0 = 18400

nodes = json.loads((PROF / "nodes_real.json").read_text(encoding="utf-8"))
# drop the 12 known-dead-by-name? no — re-verify ALL, fresh.
for i, n in enumerate(nodes):
    n.setdefault("name", f"RV{i:03d} {n.get('type', '')}")

tmp = PROF / "vk_verify"
tmp.mkdir(exist_ok=True)
mini = {
    "mixed-port": 17993,
    "allow-lan": False,
    "bind-address": "127.0.0.1",
    "mode": "rule",
    "log-level": "warning",
    "ipv6": False,
    "external-controller": "127.0.0.1:19193",
    "secret": "",
    "dns": {
        "enable": True, "ipv6": False, "enhanced-mode": "fake-ip",
        "fake-ip-range": "198.18.0.1/16", "use-hosts": False,
        "use-system-hosts": False, "default-nameserver": ["223.5.5.5"],
        "nameserver": ["https://doh.pub/dns-query"],
    },
    "proxies": nodes,
    "proxy-groups": [{
        "name": "PROXY", "type": "select",
        "proxies": [p["name"] for p in nodes],
    }],
    "listeners": [{
        "name": f"rv{i}", "type": "mixed", "port": PORT0 + i,
        "listen": "127.0.0.1", "proxy": p["name"],
    } for i, p in enumerate(nodes)],
}
cfgp = tmp / "cfg.yaml"
cfgp.write_text(yaml.safe_dump(mini, allow_unicode=True), encoding="utf-8")

proc = subprocess.Popen(
    [CORE, "-f", str(cfgp), "-d", str(tmp)],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
time.sleep(3)


def check(item):
    i, p = item
    s = requests.Session()
    s.trust_env = False
    s.proxies = {"http": f"http://127.0.0.1:{PORT0 + i}",
                 "https": f"http://127.0.0.1:{PORT0 + i}"}
    s.headers["User-Agent"] = "Mozilla/5.0 Chrome/131"
    try:
        r = s.get("http://ip-api.com/json/?fields=query,country", timeout=12)
        j = r.json()
        return i, j.get("country"), j.get("query")
    except Exception as e:
        return i, "FAIL", type(e).__name__


try:
    with cf.ThreadPoolExecutor(14) as ex:
        res = list(ex.map(check, enumerate(nodes)))
finally:
    proc.kill()
    proc.wait()

keep = []
for i, country, ip in res:
    nm = nodes[i]["name"]
    ok = country not in ("FAIL", "China")
    print(("OK  " if ok else "BAD "), nm, nodes[i]["server"], "->", country, ip)
    if ok:
        n = dict(nodes[i])
        n["kbps"] = 0.0          # unknown throughput; AUTO-* tier will sort by tier fallback
        n["tls_ok"] = True
        n["exit_cc"] = country
        keep.append(n)

print(f"REAL egress: {len(keep)}/{len(nodes)}")
(PROF / "nodes_real.json").write_text(
    json.dumps(keep, ensure_ascii=False, indent=1), encoding="utf-8")
(PROF / "nodes_pool.json").write_text(
    json.dumps(keep, ensure_ascii=False, indent=1), encoding="utf-8")
(PROF / "nodes_fast.json").write_text(
    json.dumps(keep, ensure_ascii=False, indent=1), encoding="utf-8")
(PROF / "nodes_live.json").write_text(
    json.dumps(keep, ensure_ascii=False, indent=1), encoding="utf-8")
print("written: nodes_real/pool/fast/live")
