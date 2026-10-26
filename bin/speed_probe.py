# -*- coding: utf-8 -*-
"""
vpnkit - throughput probe (true parallel)

Why not /proxies/{name}/delay:
  That endpoint only measures time-to-first-byte. Asking for a 1MB file
  returns ~230ms while the real transfer takes 1.7s, so it cannot measure
  throughput at all.

Why not serial selector switching:
  66 nodes take 12 minutes and it thrashes whatever the user is browsing.

Method:
  1. start a THROWAWAY core on a private port range (main core untouched)
  2. emit one `listeners` entry per candidate, bound with `proxy: <node name>`
  3. curl each listener in parallel against the same overseas sample,
     read %{speed_download} (bytes/sec) and convert to KB/s
  4. write profiles/nodes_fast.json, then terminate the core

Usage:
  python speed_probe.py [--top 40] [--bytes 1500000] [--threads 20]
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
LOG = ROOT / "09_logs" / "speed.log"
from vkcore import find_core
MIHOMO = find_core()

TMP_PORT = 17900
CTRL_PORT = 19097
LISTEN_BASE = 17910
MAX_LISTENERS = 150
SAMPLE = "https://speed.cloudflare.com/__down?bytes={n}"


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
            "name": f"sp{i}",
            "type": "mixed",
            "port": LISTEN_BASE + i,
            "listen": "127.0.0.1",
            "proxy": p["name"],
        } for i, p in enumerate(proxies)],
        "rules": ["MATCH,PROXY"],
    }
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
                    encoding="utf-8")


def tls_check(port, budget=9, tries=4):
    """Real HTTPS round-trip through this specific listener, repeated.

    Two separate failure modes have to be caught here:

    1. TLS never completes at all. curl on Windows uses Schannel with
       certificate revocation checks; Anthropic/GitHub publish CRLs on hosts
       the free relays cannot reach, so curl returns 000 with no body even
       though the node is fine. Python uses OpenSSL, which validates without
       fetching CRLs -- that is what actually tells us "TLS survives".
       Do not switch this back to curl.

    2. TLS completes once but the relay collapses under load. Measured on
       2026-10-25: a TW vless node passed a single handshake and even showed
       1259 KB/s, yet a 62-site concurrent sweep through it only reached
       33/62, while an http node on the same list reached 51/62. Shared free
       relays throttle per-connection, so a lone success proves nothing.
       Hence `tries` back-to-back handshakes: one failure disqualifies.

    Returns (ok, code, note). ok=True only when EVERY try completed a full
    TLS handshake plus an HTTP response. A 4xx still counts: Cloudflare
    answering 403 proves both layers worked.
    """
    codes = []
    for _ in range(tries):
        s = requests.Session()
        s.trust_env = False          # never route the probe through the system proxy
        s.proxies = {"http": f"http://127.0.0.1:{port}",
                     "https": f"http://127.0.0.1:{port}"}
        s.headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
        try:
            r = s.get("https://www.gstatic.com/generate_204",
                      timeout=budget, allow_redirects=False)
            codes.append(r.status_code)
        except requests.exceptions.SSLError as e:
            return False, 0, f"SSLError@{len(codes)+1}/{tries}:" + str(e)[:70]
        except Exception as e:
            return False, 0, (f"{type(e).__name__}@{len(codes)+1}/{tries}:"
                              + str(e)[:70])
        finally:
            s.close()
    return True, codes[0], f"{len(codes)}/{tries}"


def tls_parallel(port, budget=9, conns=6):
    """Hammer the same relay with `conns` simultaneous TLS handshakes.

    This is the gate that actually correlates with real browsing. Measured on
    2026-10-25: a TW vless relay passed 4 sequential handshakes, showed
    1259 KB/s on a single stream, and still only reached 33/62 on a concurrent
    site sweep -- while an http relay reached 51/62. Free relays throttle per
    connection, so sequential success is not evidence.

    Returns (ok, note). Any single failure disqualifies the node.
    """
    from concurrent.futures import ThreadPoolExecutor

    def one(_):
        s = requests.Session()
        s.trust_env = False
        s.proxies = {"http": f"http://127.0.0.1:{port}",
                     "https": f"http://127.0.0.1:{port}"}
        s.headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
        try:
            r = s.get("https://www.gstatic.com/generate_204",
                      timeout=budget, allow_redirects=False)
            return True, str(r.status_code)
        except Exception as e:
            return False, type(e).__name__ + ":" + str(e)[:50]
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=conns) as ex:
        out = list(ex.map(one, range(conns)))
    bad = [note for ok, note in out if not ok]
    if bad:
        return False, f"parallel {conns - len(bad)}/{conns} {bad[0][:60]}"
    return True, f"parallel {conns}/{conns}"


def curl_speed(port, url, budget):
    """Returns (KB/s, bytes actually downloaded, wall seconds)."""
    try:
        r = subprocess.run(
            ["curl.exe", "-s", "-o", "NUL",
             "-w", "%{speed_download} %{size_download} %{time_total}",
             "-x", f"http://127.0.0.1:{port}", url,
             "--max-time", str(budget)],
            capture_output=True, text=True, timeout=budget + 15,
            encoding="utf-8", errors="replace")
        parts = (r.stdout or "").strip().split()
        if len(parts) < 3:
            return 0.0, 0, float(budget)
        bps, size, secs = float(parts[0]), int(float(parts[1])), float(parts[2])
        return round(bps / 1024, 1), size, secs
    except Exception:
        return 0.0, 0, float(budget)


def ports_free(ports):
    """Bind-probe each port. Do NOT use netstat text matching: the string
       'LISTENING' appears in the global output for any socket, so a naive
       `"LISTENING" in txt` marks every port busy."""
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

    top, nbytes, threads, budget = 40, 1_500_000, 20, 20
    a = sys.argv[1:]
    for i, x in enumerate(a):
        if x == "--top" and i + 1 < len(a):
            top = int(a[i + 1])
        elif x == "--bytes" and i + 1 < len(a):
            nbytes = int(a[i + 1])
        elif x == "--threads" and i + 1 < len(a):
            threads = int(a[i + 1])
        elif x == "--budget" and i + 1 < len(a):
            budget = int(a[i + 1])

    proxies = load_proxies()[:min(top, MAX_LISTENERS)]
    if not proxies:
        log("!! no proxies in vpnkit.yaml - run build_config.py first")
        return

    need = [TMP_PORT, CTRL_PORT] + list(range(LISTEN_BASE, LISTEN_BASE + len(proxies) + 2))
    busy = ports_free(need)
    if busy:
        log(f"!! these ports are busy, stop the leftover temp core first: {busy[:8]}")
        return

    log(f"probing {len(proxies)} nodes, sample {nbytes/1e6:.1f}MB, "
        f"concurrency {threads}, budget {budget}s")

    tmpdir = Path(tempfile.mkdtemp(prefix="vkspeed_", dir=str(ROOT / "bin")))
    cfgp = tmpdir / "speed.yaml"
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

        url = SAMPLE.format(n=nbytes)
        t0 = time.time()
        lock = threading.Lock()
        done = [0]
        best = [0.0]

        def work(item):
            i, p = item
            port = LISTEN_BASE + i
            # Phase 1 only: one sequential handshake batch + a throughput sample.
            # The 6-way concurrency gate deliberately does NOT run here. With
            # `threads` workers each firing 6 sockets, 20 workers means 120
            # simultaneous connections into a single temp core -- that is the
            # probe strangling itself, not the relay failing. Audit proved it:
            # five nodes this gate called "broken" scored 4/4 sequential and
            # 180~1973 KB/s standalone. Phase 2 below runs it at low parallelism.
            tls_ok, tls_code, tls_note = tls_check(port)
            if tls_ok:
                kbps, size, secs = curl_speed(port, url, budget)
            else:
                kbps, size, secs = 0.0, 0, 0.0
            # Carry the FULL proxy record through (uuid/password/cipher/…).
            # This file is the primary input for build_config's merge_pool, so
            # dropping fields here silently strips secrets from every node and
            # the generated config fails `-t` with "has unset fields: uuid".
            rec = {k: v for k, v in p.items() if k != "probe"}
            rec.update({"kbps": kbps, "bytes": size,
                        "secs": round(secs, 1), "speed_url": url,
                        "tls_ok": tls_ok, "tls_code": tls_code,
                        "tls_note": tls_note})
            with lock:
                done[0] += 1
                best[0] = max(best[0], kbps)
                if not tls_ok:
                    log(f"  !! TLS broken  {p['name'][:24]:26s} {tls_note[:60]}")
                elif done[0] % 10 == 0 or kbps >= 300:
                    log(f"  {done[0]}/{len(proxies)} best {best[0]:.0f} KB/s "
                        f"elapsed {time.time()-t0:.0f}s")
            return rec

        with ThreadPoolExecutor(max_workers=threads) as ex:
            results = list(ex.map(work, enumerate(proxies)))

        # Serial retry for TLS-healthy nodes that sampled 0 bytes. Under
        # `threads`-way concurrency several relays lose the race and score 0,
        # which buries a genuinely fast node and drops it out of every tier --
        # the pinned Claude exit measured 370~6300 KB/s standalone while showing
        # kbps=0.0 here. ex.map preserves input order so results[k] owns the port.
        # Runs BEFORE phase 2 so a node recovered here still gets its concurrency
        # check instead of being skipped for looking dead.
        zeros = [k for k, r in enumerate(results)
                 if r.get("tls_ok") and r["kbps"] <= 0.0]
        if zeros:
            log(f"  serial retry for {len(zeros)} node(s) that sampled 0 B")
            for k in zeros:
                port = LISTEN_BASE + k
                kb, sz, sc = curl_speed(port, url, max(budget, 12.0))
                if kb > 0.0:
                    results[k].update({"kbps": kb, "bytes": sz,
                                       "secs": round(sc, 1),
                                       "tls_note": f"{results[k]['tls_note']} "
                                                   f"(retry {kb:.0f}KB/s)"})
                    log(f"    recovered {results[k]['name'][:24]:26s} "
                        f"{kb:8.1f} KB/s")
                else:
                    results[k]["tls_note"] = (f"{results[k]['tls_note']} "
                                              f"(retry still 0)")

        # Phase 2: the 6-way concurrency gate, run at low parallelism so the probe
        # is not the bottleneck. Only nodes that already passed phase 1 and
        # actually moved bytes are worth this check -- it exists to catch relays
        # that answer one handshake fine then throttle under load (measured 33/62
        # vs 51/62 site reach against a vless that looked healthy in isolation).
        alive = [k for k, r in enumerate(results)
                 if r.get("tls_ok") and r["kbps"] > 0.0]
        if alive:
            log(f"  phase 2: concurrency gate on {len(alive)} live node(s), "
                f"4 at a time")

            def par_work(k):
                ok, note = tls_parallel(LISTEN_BASE + k, conns=6)
                return k, ok, note

            with ThreadPoolExecutor(max_workers=4) as ex:
                for k, ok, note in ex.map(par_work, alive):
                    if not ok:
                        results[k].update({"tls_ok": False, "tls_note": note})
                        log(f"    !! concurrency {results[k]['name'][:24]:26s} "
                            f"{note[:50]}")

        results.sort(key=lambda x: -x["kbps"])
        out = PROF / "nodes_fast.json"
        out.write_text(json.dumps(results, ensure_ascii=False, indent=1),
                       encoding="utf-8")

        broken = [r for r in results if not r.get("tls_ok")]
        if broken:
            log(f"  TLS broken: {len(broken)} nodes "
                f"({', '.join(r['name'].split()[1] for r in broken[:12])}"
                f"{' ...' if len(broken) > 12 else ''})")
        for th in (1000, 500, 300, 200, 100, 50):
            n = sum(1 for r in results if r["kbps"] >= th)
            if n:
                log(f"  >= {th:4d} KB/s : {n:3d} nodes")
        log(f"=== done {len(results)} nodes in {time.time()-t0:.0f}s -> {out.name}")
        for r in results[:15]:
            log(f"  {r['name'][:24]:26s} {r['kbps']:8.1f} KB/s  {r.get('type','')}")
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
