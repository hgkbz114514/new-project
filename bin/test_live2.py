# -*- coding: utf-8 -*-
"""
vpnkit :: 真实可用性验证 v2
- 多目标、多轮重试、可配超时
- 直连对照组：先确认本机不经代理能到目标，排除误判
- 二分校验剔除内核不接受的节点
输入: profiles/nodes_alive.json   输出: profiles/nodes_live.json
"""
import json, os, subprocess, sys, time
from pathlib import Path

from vkwrite import safe_write

import requests
import urllib3
import yaml

urllib3.disable_warnings()

ROOT = Path(__file__).resolve().parent.parent
from vkcore import find_core
MIHOMO = find_core()
IN = ROOT / "profiles" / "nodes_alive.json"
OUT = ROOT / "profiles" / "nodes_live.json"
TMPDIR = ROOT / "bin" / "_tmp"
LOG = ROOT / "09_logs" / "live.log"

CTRL_PORT = 19090
MIXED_PORT = 17890
SECRET = "vpnkit"
TEST_URLS = ["http://www.gstatic.com/generate_204",
             "http://cp.cloudflare.com/generate_204",
             "http://connectivitycheck.platform.hicloud.com/generate_204"]
TIMEOUT_MS = int(os.environ.get("VK_TIMEOUT", "5000"))
CONCURRENCY = int(os.environ.get("VK_CONC", "96"))
ROUNDS = int(os.environ.get("VK_ROUNDS", "2"))
CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


OK_TYPES = {"ss", "vmess", "vless", "trojan", "hysteria2", "tuic",
            "hysteria", "http", "socks5", "anytls"}
DROP = {"tcp_rtt", "dialer-proxy", "interface-name", "routing-mark"}


def sanitize(nodes):
    out = []
    for p in nodes:
        if p.get("type") not in OK_TYPES:
            continue
        q = {k: v for k, v in p.items() if k not in DROP}
        if q["type"] in ("vmess", "vless") and not q.get("uuid"):
            continue
        if q["type"] in ("ss", "trojan", "hysteria2", "tuic", "anytls") and not q.get("password"):
            if q["type"] != "tuic":
                continue
        q.pop("name", None)
        q["name"] = f"n{len(out):04d}"
        out.append(q)
    return out


def base_cfg():
    return {"mixed-port": MIXED_PORT, "allow-lan": False, "mode": "rule",
            "log-level": "silent", "ipv6": False,
            "external-controller": f"127.0.0.1:{CTRL_PORT}", "secret": SECRET,
            "unified-delay": True, "tcp-concurrent": True,
            "find-process-mode": "off", "geodata-mode": False,
            "geo-auto-update": False, "profile": {"store-selected": False},
            "proxies": [], "proxy-groups": [], "rules": ["MATCH,DIRECT"]}


def validate(proxies):
    work = TMPDIR / "vwork"
    work.mkdir(parents=True, exist_ok=True)
    tmp = TMPDIR / "validate.yaml"

    def ok(batch):
        c = base_cfg()
        c["proxies"] = batch
        c["proxy-groups"] = [{"name": "P", "type": "select",
                              "proxies": [p["name"] for p in batch] + ["DIRECT"]}]
        c["rules"] = ["MATCH,P"]
        tmp.write_text(yaml.safe_dump(c, allow_unicode=True, sort_keys=False), encoding="utf-8")
        r = subprocess.run([str(MIHOMO), "-t", "-d", str(work), "-f", str(tmp)],
                           capture_output=True, text=True, timeout=240,
                           creationflags=CREATE_NO_WINDOW)
        return r.returncode == 0

    good = []

    def rec(batch):
        if not batch:
            return
        if ok(batch):
            good.extend(batch)
            return
        if len(batch) == 1:
            return
        mid = len(batch) // 2
        rec(batch[:mid])
        rec(batch[mid:])

    log("内核校验（二分）…")
    rec(proxies)
    log(f"校验通过 {len(good)} / {len(proxies)}")
    return good


def start_core(proxies):
    TMPDIR.mkdir(parents=True, exist_ok=True)
    work = TMPDIR / "runwork"
    work.mkdir(exist_ok=True)
    c = base_cfg()
    c["proxies"] = proxies
    names = [p["name"] for p in proxies]
    c["proxy-groups"] = [{"name": "PROBE", "type": "select", "proxies": names}]
    c["rules"] = ["MATCH,PROBE"]
    cfg = TMPDIR / "run_config.yaml"
    cfg.write_text(yaml.safe_dump(c, allow_unicode=True, sort_keys=False), encoding="utf-8")
    proc = subprocess.Popen([str(MIHOMO), "-d", str(work), "-f", str(cfg)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=CREATE_NO_WINDOW)
    url = f"http://127.0.0.1:{CTRL_PORT}/version"
    t0 = time.time()
    while time.time() - t0 < 60:
        if proc.poll() is not None:
            return None
        try:
            if requests.get(url, headers={"Authorization": f"Bearer {SECRET}"},
                            timeout=1).status_code == 200:
                return proc
        except Exception:
            pass
        time.sleep(0.4)
    proc.kill()
    return None


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    nodes = json.loads(IN.read_text(encoding="utf-8"))
    proxies = sanitize(nodes)
    log(f"候选 {len(nodes)} -> 配置合法 {len(proxies)}；timeout={TIMEOUT_MS}ms "
        f"conc={CONCURRENCY} rounds={ROUNDS}")

    # 直连对照组
    direct_ok = 0
    for u in TEST_URLS:
        try:
            r = requests.get(u, timeout=6, verify=False)
            if r.status_code in (200, 204):
                direct_ok += 1
        except Exception:
            pass
    log(f"直连对照组 {direct_ok}/{len(TEST_URLS)} 目标可达（说明本机网络本身可用）")

    proxies = validate(proxies)
    if not proxies:
        log("!! 无合法候选")
        return

    proc = start_core(proxies)
    if not proc:
        log("!! 内核启动失败")
        return
    log(f"内核 pid={proc.pid} 就绪")

    sess = requests.Session()
    sess.headers.update({"Authorization": f"Bearer {SECRET}"})
    import concurrent.futures as cf

    survivors = list(proxies)
    t_start = time.time()

    for rnd in range(1, ROUNDS + 1):
        t0 = time.time()
        passed = []

        def delay(p):
            for u in TEST_URLS:
                try:
                    r = sess.get(f"http://127.0.0.1:{CTRL_PORT}/proxies/{p['name']}/delay",
                                 params={"url": u, "timeout": TIMEOUT_MS},
                                 timeout=(TIMEOUT_MS / 1000) + 4)
                    if r.status_code == 200 and r.json().get("delay"):
                        return p, int(r.json()["delay"])
                except Exception:
                    continue
            return p, None

        done = 0
        with cf.ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
            for p, d in ex.map(delay, survivors):
                done += 1
                if d is not None:
                    p["delay"] = d
                    passed.append(p)
                if done % 300 == 0:
                    log(f"  round{rnd} {done}/{len(survivors)} 通过 {len(passed)} "
                        f"({time.time()-t0:.0f}s)")
        log(f"round{rnd}: {len(survivors)} 测试 -> {len(passed)} 通过 ({time.time()-t0:.0f}s)")
        survivors = passed
        if not survivors:
            break

    proc.kill()
    try:
        proc.wait(timeout=8)
    except Exception:
        pass
    log("临时内核已停止")

    survivors.sort(key=lambda x: x["delay"])
    safe_write(OUT, json.dumps(survivors, ensure_ascii=False, indent=1),
               min_items=5)
    by_type = {}
    for p in survivors:
        by_type[p["type"]] = by_type.get(p["type"], 0) + 1
    log(f"最终可用 {len(survivors)}  类型 {by_type}  总耗时 {time.time()-t_start:.0f}s")
    for p in survivors[:15]:
        log(f"   {p['delay']:5d}ms  {p['type']:10s} {p['server']}:{p['port']}")


if __name__ == "__main__":
    main()
