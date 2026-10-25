# -*- coding: utf-8 -*-
"""
vpnkit - pick and PIN one exit node for Anthropic/Claude traffic.

Why this exists:
  Anthropic bans accounts that appear to move across countries ("从不支持的位置
  创建账户" is an explicit ban reason), and a KYC flow fails outright when the
  exit IP changes mid-verification. A normal url-test group is the wrong tool:
  it re-picks by latency every few minutes and happily hops between countries.
  What Claude traffic needs is ONE node that stays the same across every config
  rebuild.

How the pin survives rebuilds:
  profiles/claude_pin.json stores {"key": "server:port", "cc": "US", ...}.
  build_config.py reads it and puts that node first in the CLAUDE group, so a
  rebuild keeps the same exit as long as the node is still in the pool. This
  script only re-pins when the old key is gone or has stopped working.

What counts as "Claude reachable":
  NOT https://claude.ai/ -- Cloudflare Bot Management answers curl with 403 no
  matter how good the node is, so it cannot distinguish anything. The honest
  probe is https://api.anthropic.com/v1/models: an unauthenticated request is
  expected to return 401, which proves TLS completed end-to-end against
  Anthropic's own infrastructure. Any non-zero status below 500 is a pass;
  0 (timeout/reset) and 5xx are fails.

Usage:
  python claude_pin.py [--top 70] [--threads 16] [--force]
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
LOG = ROOT / "09_logs" / "claude_pin.log"
from vkcore import find_core
MIHOMO = find_core()

PIN_FILE = PROF / "claude_pin.json"
OK_FILE = PROF / "claude_ok.json"

TMP_PORT = 17950
CTRL_PORT = 19150
LISTEN_BASE = 17960

API = "https://api.anthropic.com/v1/models"
CLAUDE = "https://claude.ai/login"
TRACE = "https://www.cloudflare.com/cdn-cgi/trace"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# Anthropic's supported-region list, restricted to the countries that actually
# show up in this node pool. Everything else (RU, HK, TW, CN) is deliberately
# absent: traffic from those regions is what triggers the account restriction.
GOOD_CC = {"US", "GB", "CA", "DE", "NL", "FR", "IT", "IE", "SG", "JP",
           "AU", "NZ", "CH", "NO", "SE", "ES", "PT", "BE", "AT", "DK",
           "FI", "PL", "KR"}


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
            "name": f"cp{i}",
            "type": "mixed",
            "port": LISTEN_BASE + i,
            "listen": "127.0.0.1",
            "proxy": p["name"],
        } for i, p in enumerate(proxies)],
        "rules": ["MATCH,PROXY"],
    }
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")


def http(port, url, budget):
    """Returns (http_code, ms). 0 means the request never produced a status.

    Uses requests, NOT curl.exe. Windows curl is built against Schannel, which
    performs a certificate revocation check by default; Anthropic's chain lists
    a CRL distribution point that is unreachable through a free node, so
    Schannel aborts and curl reports 000 with no error body. Measured:
        curl  https://claude.ai/                 -> 000 (0.6s)
        curl  https://claude.ai/ --ssl-no-revoke -> 403 (2.2s, 5346 bytes)
        requests                                 -> 403 (0.8s)
    OpenSSL (what requests uses) does not do that check, so it sees reality.
    """
    px = {"http": f"http://127.0.0.1:{port}", "https": f"http://127.0.0.1:{port}"}
    t0 = time.time()
    try:
        r = requests.get(url, proxies=px, headers={"User-Agent": UA},
                         timeout=budget, allow_redirects=False)
        return r.status_code, int((time.time() - t0) * 1000)
    except Exception:
        return 0, int((time.time() - t0) * 1000)


def trace_cc(port, budget=8):
    """Exit country as Cloudflare sees it."""
    px = {"http": f"http://127.0.0.1:{port}", "https": f"http://127.0.0.1:{port}"}
    try:
        r = requests.get(TRACE, proxies=px, timeout=budget)
        for line in r.text.splitlines():
            if line.startswith("loc="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


def kill_orphans():
    """Kill leftover temp cores from previous runs.

    A crashed or Ctrl-C'd scan leaves its throwaway core alive, holding
    LISTEN_BASE..+N. The next run then bails out with "ports busy" and the user
    has to clean up by hand. Identify orphans by the temp dir in the command
    line (every temp core runs with `-d ...\\bin\\vkcl_*`), never by name: the
    main core is also verge-mihomo and must survive.
    """
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='verge-mihomo.exe'\" | "
         "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"],
        capture_output=True, text=True, timeout=60, encoding="utf-8",
        errors="replace")
    try:
        procs = json.loads(out.stdout or "[]")
    except Exception:
        return 0
    if isinstance(procs, dict):
        procs = [procs]
    killed = 0
    for p in procs:
        if "vkcl_" not in str(p.get("CommandLine") or ""):
            continue
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(p["ProcessId"])],
                           capture_output=True, timeout=20)
            killed += 1
        except Exception:
            pass
    if killed:
        time.sleep(1.5)
    return killed


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


def probe(item):
    i, p = item
    port = LISTEN_BASE + i

    # Two independent checks, both must pass.
    #
    # 1) api.anthropic.com -- a bare unauthenticated GET is *expected* to answer
    #    401. Any 2xx-4xx proves TLS completed end-to-end against Anthropic.
    #
    # 2) claude.ai -- the app itself, which is what the desktop client actually
    #    loads. Cloudflare Bot Management answers curl with 403, so 403 counts
    #    as a pass: it means a real TLS session reached Cloudflare's edge. 0
    #    means the node choked.
    #
    # Checking only (1) is what produced a bad pin the first time: an `http`
    # type free proxy passed the tiny JSON request while claude.ai (full TLS +
    # CDN page) timed out at 10s.
    code_api, ms_api = http(port, API, 10)
    code_web, ms_web = http(port, CLAUDE, 10)

    # api.anthropic.com without a key answers 401; anything 2xx-4xx means the
    # TLS path reached Anthropic. claude.ai/login answers 403 to a headless
    # client because Cloudflare Bot Management sits in front of the app -- that
    # is a pass, it proves the session got to Cloudflare's edge and was read.
    api_ok = 0 < code_api < 500
    web_ok = code_web in (200, 206, 301, 302, 307, 403, 429)
    cc = trace_cc(port) if (api_ok and web_ok) else ""
    good_region = cc in GOOD_CC
    return {
        "key": f"{p.get('server')}:{p.get('port')}",
        "name": p["name"],
        "idx": i,
        "_p": p,
        "api_code": code_api,
        "web_code": code_web,
        "ms": ms_api + ms_web,
        "cc": cc,
        "region_ok": good_region,
        "ok": api_ok and web_ok,
    }


def score(r):
    # Region first (a working node in an unsupported country is still a ban
    # risk), then pool order. The pool in vpnkit.yaml is already sorted by
    # measured throughput descending, so a lower index means a faster node --
    # and speed is what keeps a KYC upload from stalling.
    return (0 if r["region_ok"] else 1, r["idx"])


def recheck(cand, tries=3):
    """Re-probe a candidate several times; a node must pass most rounds.

    A single pass is not enough: `http` nodes whose server address is a
    Cloudflare edge IP (e.g. 172.64.68.89:443) pass once and then die with
    `dial tcp ...: i/o timeout`. Pinning such a node means the user's Claude
    session drops mid-verification, which is exactly what this whole exercise
    is meant to prevent.
    """
    wins = 0
    for _ in range(tries):
        r = probe((cand["idx"], cand["_p"]))
        if r["ok"]:
            wins += 1
        time.sleep(0.4)
    cand["stability"] = f"{wins}/{tries}"
    return wins, tries


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    top, threads, force = 70, 16, False
    a = sys.argv[1:]
    for i, x in enumerate(a):
        if x == "--top" and i + 1 < len(a):
            top = int(a[i + 1])
        elif x == "--threads" and i + 1 < len(a):
            threads = int(a[i + 1])
        elif x == "--force":
            force = True

    proxies = load_proxies()[:top]
    if not proxies:
        log("!! no proxies in vpnkit.yaml")
        return 1

    need = [TMP_PORT, CTRL_PORT] + list(
        range(LISTEN_BASE, LISTEN_BASE + len(proxies) + 2))
    busy = ports_free(need)
    if busy:
        log(f"!! ports busy {busy[:6]}, clearing orphan temp cores")
        n = kill_orphans()
        log(f"   killed {n} orphan(s)")
        busy = ports_free(need)
    if busy:
        log(f"!! still busy: {busy[:8]} -- not a vkcl_ core, check by hand")
        return 1

    log(f"claude pin scan: {len(proxies)} nodes, concurrency {threads}")
    tmpdir = Path(tempfile.mkdtemp(prefix="vkcl_", dir=str(ROOT / "bin")))
    cfgp = tmpdir / "cl.yaml"
    build_tmp_cfg(proxies, cfgp)

    v = subprocess.run([str(MIHOMO), "-t", "-f", str(cfgp), "-d", str(tmpdir)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=180)
    if v.returncode != 0:
        log("!! temp config rejected: " + ((v.stdout or "") + (v.stderr or ""))[-600:])
        return 1

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
            return 1

        t0 = time.time()
        lock = threading.Lock()
        done = [0]

        def work(item):
            r = probe(item)
            with lock:
                done[0] += 1
                if r["ok"] or done[0] % 15 == 0:
                    log(f"  {done[0]}/{len(proxies)} {r['name'][:22]:24s} "
                        f"{'OK' if r['ok'] else '--'} "
                        f"api={r['api_code']:<4} web={r['web_code']:<4} "
                        f"cc={r['cc'] or '?':<3} {r['ms']}ms")
            return r

        with ThreadPoolExecutor(max_workers=threads) as ex:
            res = list(ex.map(work, enumerate(proxies)))

        alive = [r for r in res if r["ok"]]
        alive.sort(key=score)

        good = [r for r in alive if r["region_ok"]]
        log(f"=== {time.time()-t0:.0f}s  api+web both reachable "
            f"{len(alive)}/{len(proxies)}, in supported regions {len(good)}")

        # Only nodes that keep working may be pinned. Probe the best region-ok
        # candidates repeatedly until one survives; a single flaky pass is how
        # 018 US got pinned last time and then timed out for real.
        shortlist = [r for r in alive if r["region_ok"]][:8] or alive[:5]
        stable = []
        for cand in shortlist:
            wins, tries = recheck(cand)
            log(f"  stability {cand['name'][:22]:24s} {wins}/{tries}")
            if wins >= 2:
                cand["wins"] = wins
                stable.append(cand)
        if stable:
            stable.sort(key=lambda r: (-r["wins"], r["idx"]))
            alive = stable + [r for r in alive if r not in stable]
        else:
            log("!! no candidate survived the stability recheck")

        OK_FILE.write_text(
            json.dumps([{k: v for k, v in r.items() if k != "_p"}
                        for r in alive], ensure_ascii=False, indent=1),
            encoding="utf-8")

        # Keep the existing pin when it is still healthy, so the exit IP does
        # not change on every refresh. That stability is the whole point.
        old = {}
        if PIN_FILE.exists() and not force:
            try:
                old = json.loads(PIN_FILE.read_text(encoding="utf-8"))
            except Exception:
                old = {}
        old_key = str(old.get("key") or "")
        keep = next((r for r in alive if r["key"] == old_key), None)
        if keep:
            keep["reused"] = True
            keep["since"] = old.get("since") or time.strftime("%Y-%m-%d %H:%M:%S")
            chosen = keep
            log(f"pin kept: {chosen['name']} cc={chosen['cc']} "
                f"key={chosen['key']} since={chosen['since']}")
        elif alive:
            chosen = alive[0]
            chosen["since"] = time.strftime("%Y-%m-%d %H:%M:%S")
            log(f"pin set:  {chosen['name']} cc={chosen['cc']} "
                f"key={chosen['key']} stability={chosen.get('wins', '?')}")
            if old_key:
                log(f"  (previous pin {old_key} is gone, dead or unstable)")
        else:
            log("!! nothing reachable -- keeping the previous pin untouched")
            return 1

        PIN_FILE.write_text(
            json.dumps({
                "key": chosen["key"],
                "name": chosen["name"],
                "cc": chosen["cc"],
                "ms": chosen["ms"],
                "api_code": chosen["api_code"],
                "web_code": chosen.get("web_code"),
                "wins": chosen.get("wins"),
                "since": chosen["since"],
                "candidates": [r["key"] for r in alive[:12]],
            }, ensure_ascii=False, indent=1), encoding="utf-8")
        return 0
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
    sys.exit(main())
