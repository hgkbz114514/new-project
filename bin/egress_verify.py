# -*- coding: utf-8 -*-
"""vpnkit :: egress verifier -- kill "transparent relay" fake nodes.

A transparent relay accepts TCP and moves bytes FAST (it never actually
proxies: your traffic just goes out from your own IP), so both the delay
probe and the throughput probe score it perfectly. The only reliable test:
fetch ip-api through the node and compare the exit IP / country with your
own. This script does exactly that, one listener per node.

Usage: python egress_verify.py [--top N] [--timeout S]
Input : profiles/nodes_pool.json (fallback: fast/verified/live)
Output: survivors written back to pool/fast/live + nodes_real.json
        (kbps/tls_* preserved; a `fake` flag marks nothing -- fakes are
        simply dropped)
"""
import argparse
import concurrent.futures as cf
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests
import yaml

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
PROF = ROOT / "profiles"
TMPDIR = PROF / "vk_verify"
PORT0 = 18400
CTRL_PORT = 19493
MIXED_PORT = 18493

from vkcore import find_core          # noqa: E402
from vkwrite import safe_write       # noqa: E402

LOG = ROOT / "09_logs" / "egress_verify.log"


def log(m):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {m}"
    print(line)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def pick_input():
    for name in ("nodes_pool.json", "nodes_fast.json",
                 "nodes_verified.json", "nodes_live.json"):
        p = PROF / name
        if p.exists() and p.stat().st_size > 10:
            return p
    return None


def ports_free(ports):
    import socket
    busy = []
    for p in ports:
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
        except OSError:
            busy.append(p)
        finally:
            s.close()
    return busy


def kill_orphans():
    """Kill stray verifier kernels only (command line contains vk_verify)."""
    import subprocess as sp
    r = sp.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='verge-mihomo.exe'\" "
                "| Where-Object { $_.CommandLine -like '*vk_verify*' } "
                "| Select-Object -ExpandProperty ProcessId"],
               capture_output=True, text=True, timeout=30)
    killed = 0
    for ln in (r.stdout or "").split():
        if ln.strip().isdigit():
            sp.run(["taskkill", "/F", "/PID", ln.strip()],
                   capture_output=True, timeout=15)
            killed += 1
    return killed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=150)
    ap.add_argument("--timeout", type=int, default=14)
    args = ap.parse_args()

    src = pick_input()
    if not src:
        log("!! no node file found; nothing to verify")
        return 1
    nodes = json.loads(src.read_text(encoding="utf-8"))
    log(f"input {src.name}: {len(nodes)} nodes")

    # keep only nodes with full credentials (mihomo -t rejects the rest)
    SECRET_REQ = {"vmess": "uuid", "vless": "uuid", "trojan": "uuid",
                  "tuic": "uuid", "ss": "password"}
    nodes = [n for n in nodes
             if not SECRET_REQ.get(n.get("type"))
             or n.get(SECRET_REQ.get(n.get("type")))]
    nodes = nodes[:args.top]
    log(f"verifying {len(nodes)} nodes (--top {args.top})")

    core = os.environ.get("VK_CORE") or find_core()
    if not core:
        log("!! mihomo core not found (set VK_CORE); abort")
        return 1

    # ensure ports are free, else clean our own orphans
    busy = ports_free(range(PORT0, PORT0 + len(nodes)))
    if busy:
        k = kill_orphans()
        log(f"ports busy {busy[:5]}... killed {k} orphan verifier kernel(s)")
        busy = ports_free(range(PORT0, PORT0 + len(nodes)))
        if busy:
            log(f"!! ports still busy {busy[:5]}; abort")
            return 1

    tmp = PROF / "vk_verify"
    tmp.mkdir(exist_ok=True)
    for i, n in enumerate(nodes):
        n.setdefault("name", f"EV{i:03d} {n.get('type', '')}")
    cfg = {
        "mixed-port": MIXED_PORT,
        "allow-lan": False,
        "bind-address": "127.0.0.1",
        "mode": "rule",
        "log-level": "warning",
        "ipv6": False,
        "external-controller": f"127.0.0.1:{CTRL_PORT}",
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
            "name": f"ev{i}", "type": "mixed", "port": PORT0 + i,
            "listen": "127.0.0.1", "proxy": p["name"],
        } for i, p in enumerate(nodes)],
    }
    cfgp = tmp / "verify.yaml"
    cfgp.write_text(yaml.safe_dump(cfg, allow_unicode=True,
                                   sort_keys=False), encoding="utf-8")

    # my own egress for the comparison
    try:
        me = requests.get("http://ip-api.com/json/?fields=query,country",
                          timeout=8).json()
        my_ip = me.get("query", "")
    except Exception:
        my_ip = ""
    log(f"own egress: {my_ip or 'unknown'}")

    proc = subprocess.Popen([core, "-f", str(cfgp), "-d", str(tmp)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=0x08000000)
    t0 = time.time()
    try:
        import urllib.request
        up = False
        for _ in range(40):
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{CTRL_PORT}/version", timeout=2)
                up = True
                break
            except Exception:
                time.sleep(0.5)
        if not up:
            log("!! temp kernel did not come up; abort")
            return 1

        def check(item):
            i, n = item
            port = PORT0 + i
            s = requests.Session()
            s.trust_env = False
            s.proxies = {"http": f"http://127.0.0.1:{port}",
                         "https": f"http://127.0.0.1:{port}"}
            s.headers["User-Agent"] = ("Mozilla/5.0 (Windows NT 10.0; "
                                       "Win64; x64) Chrome/131")
            try:
                r = s.get("http://ip-api.com/json/?fields=query,country",
                          timeout=args.timeout)
                d = r.json()
                ip, cc = d.get("query", ""), d.get("country", "")
            except Exception as e:
                return i, n, None, f"{type(e).__name__}: {str(e)[:40]}"
            fake = (not ip) or ip == my_ip or cc == "China"
            return i, n, (None if fake else (ip, cc)), (
                f"fake/transparent (exit={ip or '?'} {cc})" if fake else "")

        keep, fakes, dead = [], 0, 0
        with cf.ThreadPoolExecutor(max_workers=16) as ex:
            for i, n, res, note in ex.map(check, enumerate(nodes)):
                if res is None:
                    if note.startswith("fake"):
                        fakes += 1
                        log(f"  xx {n.get('name'):26s} {note}")
                    else:
                        dead += 1
                    continue
                n = dict(n)
                n["exit_ip"] = res[0]
                n["exit_cc"] = res[1]
                keep.append(n)

        log(f"=== {time.time()-t0:.0f}s  real egress {len(keep)}/"
            f"{len(nodes)}  (transparent fakes {fakes}, unreachable {dead})")
        cc_count = {}
        for n in keep:
            cc_count[n.get("exit_cc", "?")] = \
                cc_count.get(n.get("exit_cc", "?"), 0) + 1
        log("exit countries: " + json.dumps(cc_count, ensure_ascii=False))

        if keep:
            for fname in ("nodes_pool.json", "nodes_fast.json",
                          "nodes_live.json"):
                safe_write(PROF / fname,
                           json.dumps(keep, ensure_ascii=False, indent=1),
                           min_items=5)
            safe_write(PROF / "nodes_real.json",
                       json.dumps(keep, ensure_ascii=False, indent=1),
                       min_items=5)
            log(f"written back {len(keep)} real-egress nodes to "
                f"pool/fast/live/real")
            return 0
        log("!! no real-egress survivors; existing files untouched")
        return 1
    finally:
        try:
            proc.kill()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
