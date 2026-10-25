# -*- coding: utf-8 -*-
"""
vpnkit :: 出口验证器
delay 通只说明能转发 generate_204，这里真发请求看出口 IP / 国家 / 是否被墙内容可达
输入: profiles/nodes_live.json   输出: profiles/nodes_verified.json
"""
import json, os, subprocess, sys, time, re
from pathlib import Path

import requests
import urllib3
import yaml

urllib3.disable_warnings()

ROOT = Path(__file__).resolve().parent.parent
from vkcore import find_core
MIHOMO = find_core()
IN = ROOT / "profiles" / "nodes_live.json"
OUT = ROOT / "profiles" / "nodes_verified.json"
TMPDIR = ROOT / "bin" / "_tmp"
LOG = ROOT / "09_logs" / "verify.log"
EH = ROOT / "bin" / "ech_override.json"

CTRL_PORT = 19092
SECRET = "vkverify"
CONCURRENCY = 24
CN = re.compile(r"[\u4e00-\u9fff]")


def log(m):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {m}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    nodes = json.loads(IN.read_text(encoding="utf-8"))
    proxies = []
    for i, n in enumerate(nodes):
        q = {k: v for k, v in n.items() if k not in ("tcp_rtt", "delay")}
        q["name"] = n["name"]
        proxies.append(q)
    log(f"待验证 {len(proxies)} 个节点（已通过 delay 测试）")

    TMPDIR.mkdir(parents=True, exist_ok=True)
    work = TMPDIR / "vwork2"
    work.mkdir(exist_ok=True)
    names = [p["name"] for p in proxies]
    cfg = {
        "mixed-port": 17892, "allow-lan": False, "mode": "rule",
        "log-level": "silent", "ipv6": False,
        "external-controller": f"127.0.0.1:{CTRL_PORT}", "secret": SECRET,
        "unified-delay": True, "tcp-concurrent": True,
        "find-process-mode": "off", "geodata-mode": False, "geo-auto-update": False,
        "profile": {"store-selected": False},
        "proxies": proxies,
        "proxy-groups": [{"name": "VERIFY", "type": "select", "proxies": names}],
        "rules": ["MATCH,VERIFY"],
    }
    cf = TMPDIR / "verify.yaml"
    cf.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    proc = subprocess.Popen([str(MIHOMO), "-d", str(work), "-f", str(cf)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    t0 = time.time()
    while time.time() - t0 < 60:
        try:
            if requests.get(f"http://127.0.0.1:{CTRL_PORT}/version",
                            headers={"Authorization": f"Bearer {SECRET}"},
                            timeout=1).status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.4)
    else:
        log("!! 内核未就绪")
        proc.kill()
        return

    s = requests.Session()
    s.trust_env = False
    import concurrent.futures as cfx

    def check(p):
        # 1) 切到该节点
        try:
            r = s.put(f"http://127.0.0.1:{CTRL_PORT}/proxies/VERIFY",
                      json={"name": p["name"]},
                      headers={"Authorization": f"Bearer {SECRET}"}, timeout=5)
            if r.status_code not in (200, 204):
                return p, None
        except Exception:
            return p, None
        time.sleep(0.05)
        # 2) 走代理取出口信息
        px = {"http": "http://127.0.0.1:17892", "https": "http://127.0.0.1:17892"}
        info = {}
        for url, key in (("http://ip-api.com/json/?fields=status,country,countryCode,isp,query", "geo"),
                         ("https://www.gstatic.com/generate_204", "g204"),
                         ("https://www.youtube.com/generate_204", "yt")):
            try:
                r = requests.get(url, proxies=px, timeout=8, verify=False)
                if key == "geo":
                    j = r.json()
                    if j.get("status") == "success":
                        info.update(exit_ip=j.get("query"), country=j.get("country"),
                                    cc=j.get("countryCode"), isp=(j.get("isp") or "")[:28])
                else:
                    info[key] = r.status_code in (200, 204)
            except Exception:
                info.setdefault(key, False)
        if not info.get("exit_ip"):
            return p, None
        return p, info

    # mihomo 的 selector 是串行资源，必须一个一个切
    verified = []
    total = len(proxies)
    for i, p in enumerate(proxies):
        pr, info = check(p)
        if info:
            pr["exit_ip"] = info.get("exit_ip")
            pr["country"] = info.get("country")
            pr["cc"] = info.get("cc")
            pr["isp"] = info.get("isp")
            pr["https_ok"] = bool(info.get("g204"))
            pr["yt_ok"] = bool(info.get("yt"))
            verified.append(pr)
        if (i + 1) % 20 == 0:
            log(f"  {i+1}/{total} 已验证 {len(verified)}")

    proc.kill()
    try:
        proc.wait(timeout=8)
    except Exception:
        pass

    verified.sort(key=lambda x: x["delay"])
    OUT.write_text(json.dumps(verified, ensure_ascii=False, indent=1), encoding="utf-8")
    cc = {}
    for p in verified:
        cc[p.get("cc") or "??"] = cc.get(p.get("cc") or "??", 0) + 1
    log(f"出口验证通过 {len(verified)}/{total}  地区分布 {cc}")
    log(f"其中 https 通 {sum(1 for p in verified if p.get('https_ok'))}，"
        f"YouTube 通 {sum(1 for p in verified if p.get('yt_ok'))}")


if __name__ == "__main__":
    main()
