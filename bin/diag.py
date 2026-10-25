# -*- coding: utf-8 -*-
"""单节点诊断：抽几个节点起一个临时内核，把日志打出来看为什么失败。

用法：python bin\\diag.py [nodes_alive.json 路径]
默认读 profiles/nodes_alive.json（probe.py 的产物）。
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import requests
import yaml

from vkcore import find_core

ROOT = Path(__file__).resolve().parent.parent
TMP = ROOT / "bin" / "_tmp"
CTRL, SECRET = 19091, "diag"
MIXED_PORT = 17891


def load_nodes(path: Path) -> list:
    if not path.exists():
        print(f"!! 找不到 {path}")
        print("   先跑 python bin\\probe.py 生成它（它读 profiles/nodes_alive.json）")
        sys.exit(1)
    try:
        nodes = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"!! {path.name} 不是合法 JSON：{e}")
        sys.exit(1)
    if not nodes:
        print(f"!! {path.name} 是空的")
        sys.exit(1)
    return nodes


def build_proxies(nodes: list, per_type: int = 3) -> list:
    """每种协议各抽几个，避免全挤在一种协议上看不出问题。"""
    picked = []
    for t in ("vmess", "vless", "trojan", "ss", "hysteria2"):
        picked += [n for n in nodes if n.get("type") == t][:per_type]
    proxies = []
    for i, n in enumerate(picked):
        q = {k: v for k, v in n.items() if k not in ("tcp_rtt", "name")}
        q["name"] = f"d{i:02d}_{n.get('type')}"
        proxies.append(q)
    return proxies


def write_config(proxies: list) -> Path:
    TMP.mkdir(parents=True, exist_ok=True)
    cfg = {
        "mixed-port": MIXED_PORT,
        "log-level": "debug",
        "ipv6": False,
        "external-controller": f"127.0.0.1:{CTRL}",
        "secret": SECRET,
        "dns": {
            "enable": True,
            "ipv6": False,
            "enhanced-mode": "fake-ip",
            "nameserver": ["223.5.5.5", "119.29.29.29", "8.8.8.8"],
        },
        "proxies": proxies,
        "proxy-groups": [
            {"name": "P", "type": "select", "proxies": [p["name"] for p in proxies]}
        ],
        "rules": ["MATCH,P"],
    }
    cfgfile = TMP / "diag.yaml"
    cfgfile.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
                       encoding="utf-8")
    (TMP / "diagwork").mkdir(exist_ok=True)
    return cfgfile


def probe_all(proxies: list) -> None:
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {SECRET}"})
    for p in proxies:
        # 只有带密码 / uuid 的协议才走 delay 接口，http/socks5 裸代理没有可测的凭据
        if not p.get("password") and p.get("type") not in ("vmess", "vless"):
            continue
        try:
            r = s.get(
                f"http://127.0.0.1:{CTRL}/proxies/{p['name']}/delay",
                params={"url": "http://www.gstatic.com/generate_204", "timeout": 3000},
                timeout=8,
            )
            res = r.json()
        except Exception as e:
            res = {"err": str(e)}
        print(f"{p['name']:14s} {p['server']}:{p['port']:5d} -> {res}")


def dump_log(lines: int = 40) -> None:
    logpath = TMP / "diag_core.log"
    print("\n===== 内核日志关键行 =====")
    if not logpath.exists():
        print("(没有日志文件)")
        return
    hits = [
        line.rstrip()[:220]
        for line in logpath.read_text(encoding="utf-8", errors="replace").splitlines()
        if "level=error" in line or "level=warning" in line
    ]
    for line in hits[-lines:]:
        print(line)
    if not hits:
        print("(日志里没有 error / warning)")


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    core = find_core()
    if not core.exists():
        print("!! 没找到 mihomo 内核，诊断需要它来跑临时实例")
        print("   设个环境变量指过去：set VK_CORE=<Clash Verge 安装目录>\\verge-mihomo.exe")
        return 1

    src = Path(argv[1]) if len(argv) > 1 else ROOT / "profiles" / "nodes_alive.json"
    nodes = load_nodes(src)
    proxies = build_proxies(nodes)
    if not proxies:
        print(f"!! {src.name} 里没有可诊断的节点")
        return 1

    print(f"从 {src.name} 抽了 {len(proxies)} 个节点，起临时内核 "
          f"mixed={MIXED_PORT} ctrl={CTRL}")
    cfgfile = write_config(proxies)

    logf = open(TMP / "diag_core.log", "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [str(core), "-d", str(TMP / "diagwork"), "-f", str(cfgfile)],
        stdout=logf, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        time.sleep(3)
        probe_all(proxies)
        time.sleep(1)
    finally:
        # 无论探测怎么炸都要把临时内核收掉，否则它占着 17891/19091
        proc.kill()
        logf.close()

    dump_log()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
