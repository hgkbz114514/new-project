# -*- coding: utf-8 -*-
"""Is the parallel TLS gate producing false negatives?

The gate marked 15 of the 20 lowest-latency nodes broken. If those nodes work
fine when tested alone, the gate is killing good relays and the tiers collapse
for no reason. Test each suspect serially through the main core: 4 sequential
handshakes, then a 1.5MB throughput sample.
"""
import sys
import time

import requests

CTRL = 9097
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
G204 = "https://www.gstatic.com/generate_204"

SUSPECTS = [
    "001 NL 191ms http",
    "002 US 192ms http",
    "004 DE 167ms http",
    "005 DE 181ms http",
    "006 NL 217ms http",
]


def session():
    s = requests.Session()
    s.trust_env = False
    s.proxies = {"http": "http://127.0.0.1:7897",
                 "https": "http://127.0.0.1:7897"}
    s.headers["User-Agent"] = UA
    return s


def main():
    s = session()
    for name in SUSPECTS:
        try:
            requests.put(f"http://127.0.0.1:{CTRL}/proxies/PROXY",
                         json={"name": name}, timeout=8)
        except Exception as e:
            print(f"{name}: cannot switch: {e}")
            continue
        time.sleep(0.6)

        oks = 0
        detail = []
        for _ in range(4):
            try:
                r = s.get(G204, timeout=10)
                oks += 1
                detail.append(str(r.status_code))
            except Exception as e:
                detail.append(type(e).__name__)
        serial = f"{oks}/4 ({','.join(detail)})"

        kbps = 0.0
        try:
            t0 = time.time()
            total = 0
            r = s.get("https://speed.cloudflare.com/__down?bytes=1500000",
                      timeout=25, stream=True)
            for chunk in r.iter_content(65536):
                total += len(chunk)
                if time.time() - t0 > 18:
                    break
            kbps = total / 1024 / max(time.time() - t0, 0.01)
        except Exception:
            pass

        verdict = "GOOD (gate was wrong)" if oks >= 3 else "really broken"
        print(f"{name:<22} serial={serial:<28} "
              f"{kbps:7.1f} KB/s  {verdict}", flush=True)

    requests.put(f"http://127.0.0.1:{CTRL}/proxies/PROXY",
                 json={"name": "AUTO-FAST"}, timeout=8)
    print("\nPROXY 已切回 AUTO-FAST")


if __name__ == "__main__":
    sys.exit(main())
