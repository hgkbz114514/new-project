# -*- coding: utf-8 -*-
"""
vpnkit - github reachability scan (parallel, throwaway core)

Why this is not a /delay scan:
  mihomo's /delay endpoint does NOT verify certificates and only measures
  time-to-first-byte, so nodes whose TLS is hijacked by a middlebox still
  report a healthy delay (measured: 65/67 "ok" via /delay, only 2 actually
  delivered github content). Every candidate therefore needs a real
  https://github.com/ GET that returns a sizeable body.

Why not switch the PROXY selector for that check:
  serial, ~10s per node, and it hijacks whatever the user is browsing.

Method: spin a throwaway core, bind one `listeners` entry per candidate with
`proxy: <node name>`, then curl github through each listener in parallel.
Output profiles/github_ok.json keyed by "server:port" (node names contain the
index and delay, which change on every rebuild, so they are useless as keys).

Usage: python ghscan.py [--top 70] [--threads 16]
"""
import json
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
PROF = ROOT / "profiles"
LOG = ROOT / "09_logs" / "ghscan.log"
from vkcore import find_core
MIHOMO = find_core()

TMP_PORT = 17900
CTRL_PORT = 19097
LISTEN_BASE = 17910
GH = "https://github.com/"
MIN_BYTES = 50000


def log(m):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {m}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_proxies():
    import yaml
    cfg = yaml.safe_load((PROF / "vpnkit.yaml").read_text(encoding="utf-8"))
    return cfg.get("proxies", [])


def build_tmp_cfg(proxies, path):
    import yaml
    cfg = {
        "mixed-port": TMP_PORT,
        "allow-lan": False,
        "bind-address": "127.0.0.1",
        "mode": "rule",
        "log-level": "warning",
        "ipv6": False,
        "external-controller": f"127.0.0.1:{CTRL_PORT}",
        "secret": "",
        "dns": {
            "enable": True,
            "ipv6": False,
            "enhanced-mode": "fake-ip",
            "fake-ip-range": "198.18.0.1/16",
            "use-hosts": False,
            "use-system-hosts": False,
            "default-nameserver": ["223.5.5.5"],
            "nameserver": ["https://doh.pub/dns-query"],
        },
        "proxies": proxies,
        "proxy-groups": [{
            "name": "PROXY",
            "type": "select",
            "proxies": [p["name"] for p in proxies],
        }],
        "listeners": [{
            "name": f"gp{i}",
            "type": "mixed",
            "port": LISTEN_BASE + i,
            "listen": "127.0.0.1",
            "proxy": p["name"],
        } for i, p in enumerate(proxies)],
        "rules": ["MATCH,PROXY"],
    }
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")


def curl_gh(port, budget=20):
    """Returns (ok, http_code, bytes). Real GET with certificate verification."""
    try:
        r = subprocess.run(
            ["curl.exe", "-s", "-o", "NUL",
             "-w", "%{http_code} %{size_download}",
             "-x", f"http://127.0.0.1:{port}", GH,
             "-H", "User-Agent: Mozilla/5.0", "--max-time", str(budget)],
            capture_output=True, text=True, timeout=budget + 15,
            encoding="utf-8", errors="replace")
        parts = (r.stdout or "").strip().split()
        if len(parts) < 2:
            return False, 0, 0
        code, size = int(parts[0]), int(float(parts[1]))
        return (0 < code < 400 and size >= MIN_BYTES), code, size
    except Exception:
        return False, 0, 0


def ports_free(ports):
    import socket
    busy = []
    for p in ports:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", p))
        except OSError:
            busy.append(p)
        finally:
            s.close()
    return busy


def main():
    sys.stdout.reconfigure(encoding="utf-8")

    top, threads = 70, 16
    a = sys.argv[1:]
    for i, x in enumerate(a):
        if x == "--top" and i + 1 < len(a):
            top = int(a[i + 1])
        elif x == "--threads" and i + 1 < len(a):
            threads = int(a[i + 1])

    proxies = load_proxies()[:top]
    if not proxies:
        log("!! no proxies in vpnkit.yaml")
        return

    need = [TMP_PORT, CTRL_PORT] + list(range(LISTEN_BASE, LISTEN_BASE + len(proxies) + 2))
    busy = ports_free(need)
    if busy:
        log(f"!! ports busy, stop the leftover temp core: {busy[:8]}")
        return

    log(f"github scan: {len(proxies)} nodes, concurrency {threads}")
    tmpdir = Path(tempfile.mkdtemp(prefix="vkgh_", dir=str(ROOT / "bin")))
    cfgp = tmpdir / "gh.yaml"
    build_tmp_cfg(proxies, cfgp)

    v = subprocess.run([str(MIHOMO), "-t", "-f", str(cfgp), "-d", str(tmpdir)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=180)
    if v.returncode != 0:
        log("!! temp config rejected: " + ((v.stdout or "") + (v.stderr or ""))[-500:])
        return

    pr = subprocess.Popen(
        [str(MIHOMO), "-f", str(cfgp), "-d", str(tmpdir)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW)

    try:
        for _ in range(80):
            try:
                requests.get(f"http://127.0.0.1:{CTRL_PORT}/version", timeout=1)
                break
            except Exception:
                time.sleep(0.25)
        else:
            log("!! temp core never became ready")
            return

        t0 = time.time()
        lock = threading.Lock()
        done = [0]

        def work(item):
            i, p = item
            ok, code, size = curl_gh(LISTEN_BASE + i)
            with lock:
                done[0] += 1
                if ok or done[0] % 15 == 0:
                    log(f"  {done[0]}/{len(proxies)} {p['name'][:22]:24s} "
                        f"{'OK' if ok else '--'} {code} {size//1024}KB "
                        f"({time.time()-t0:.0f}s)")
            return (p, ok, code, size)

        with ThreadPoolExecutor(max_workers=threads) as ex:
            res = list(ex.map(work, enumerate(proxies)))

        confirmed = [f"{p.get('server')}:{p.get('port')}" for p, ok, _, _ in res if ok]
        (PROF / "github_ok.json").write_text(
            json.dumps(confirmed, ensure_ascii=False, indent=1), encoding="utf-8")

        n_ok = len(confirmed)
        log(f"=== {time.time()-t0:.0f}s  github reachable {n_ok}/{len(proxies)}")
        for p, ok, code, size in res:
            if ok:
                log(f"  * {p['name'][:22]:24s} {size//1024}KB "
                    f"key={p.get('server')}:{p.get('port')}")
    finally:
        try:
            pr.terminate()
            pr.wait(timeout=5)
        except Exception:
            try:
                pr.kill()
            except Exception:
                pass


if __name__ == "__main__":
    main()
