# -*- coding: utf-8 -*-
"""单节点诊断：把内核日志打出来，看为什么失败"""
import json, os, subprocess, sys, time
from pathlib import Path
import requests, yaml

ROOT = Path(__file__).resolve().parent.parent
from vkcore import find_core
MIHOMO = find_core()
TMP = ROOT / "bin" / "_tmp"
CTRL, SECRET = 19091, "diag"

sys.stdout.reconfigure(encoding="utf-8")
nodes = json.loads((ROOT / "profiles" / "nodes_alive.json").read_text(encoding="utf-8"))
# 抽样：vmess / vless / trojan / ss 各取 3 个
picked = []
for t in ("vmess", "vless", "trojan", "ss", "hysteria2"):
    picked += [n for n in nodes if n["type"] == t][:3]

proxies = []
for i, n in enumerate(picked):
    q = {k: v for k, v in n.items() if k not in ("tcp_rtt", "name")}
    q["name"] = f"d{i:02d}_{n['type']}"
    proxies.append(q)

cfg = {
    "mixed-port": 17891, "log-level": "debug", "ipv6": False,
    "external-controller": f"127.0.0.1:{CTRL}", "secret": SECRET,
    "dns": {"enable": True, "ipv6": False, "enhanced-mode": "fake-ip",
            "nameserver": ["223.5.5.5", "119.29.29.29", "8.8.8.8"]},
    "proxies": proxies,
    "proxy-groups": [{"name": "P", "type": "select", "proxies": [p["name"] for p in proxies]}],
    "rules": ["MATCH,P"],
}
TMP.mkdir(parents=True, exist_ok=True)
cfgfile = TMP / "diag.yaml"
cfgfile.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
(TMP / "diagwork").mkdir(exist_ok=True)

logf = open(TMP / "diag_core.log", "w", encoding="utf-8", errors="replace")
proc = subprocess.Popen([str(MIHOMO), "-d", str(TMP / "diagwork"), "-f", str(cfgfile)],
                        stdout=logf, stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW)
time.sleep(3)
s = requests.Session()
s.headers.update({"Authorization": f"Bearer {SECRET}"})

for p in proxies:
    if not p.get("password") and p["type"] not in ("vmess", "vless"):
        continue
    try:
        r = s.get(f"http://127.0.0.1:{CTRL}/proxies/{p['name']}/delay",
                  params={"url": "http://www.gstatic.com/generate_204", "timeout": 3000},
                  timeout=8)
        res = r.json()
    except Exception as e:
        res = {"err": str(e)}
    print(f"{p['name']:14s} {p['server']}:{p['port']:5d} -> {res}")

time.sleep(1)
proc.kill()
logf.close()
print("\n===== 内核日志关键行 =====")
for line in open(TMP / "diag_core.log", encoding="utf-8", errors="replace"):
    if "level=error" in line or "level=warning" in line:
        print(line.rstrip()[:220])
