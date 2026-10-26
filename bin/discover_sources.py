# -*- coding: utf-8 -*-
"""
vpnkit :: 订阅源自愈发现器
把一大批候选源全部试一遍，只把「200 且真的抠得出节点」的写回 sources_live.txt
harvest.py 会同时读 sources.txt + sources_live.txt —— 源失效自动被淘汰，新源自动进池
"""
import sys
import time
import concurrent.futures as cf
from pathlib import Path

from vkwrite import safe_write
import requests
import urllib3
urllib3.disable_warnings()

ROOT = Path(__file__).resolve().parent.parent
CAND = ROOT / "bin" / "sources_candidates.txt"
OUT = ROOT / "bin" / "sources_live.txt"
LOG = ROOT / "09_logs" / "discover.log"

UA = "clash-verge/v2.4.4 clash.meta/1.19.0"
TIMEOUT = 25

sys.path.insert(0, str(ROOT / "bin"))
from harvest import extract_from_clash, extract_from_text, parse_uri, b64dec  # noqa


def log(m):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {m}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def check(url):
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT, verify=False)
        if r.status_code != 200 or len(r.content) < 50:
            return url, 0, f"HTTP {r.status_code}"
        txt = r.text
        n = len(extract_from_clash(txt))
        if n == 0:
            uris = extract_from_text(txt) or extract_from_text(b64dec(txt.strip()))
            n = sum(1 for u in uris if parse_uri(u))
        return url, n, None if n else "0 节点"
    except Exception as e:
        return url, 0, type(e).__name__


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    urls = [l.strip() for l in CAND.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")]
    log(f"候选源 {len(urls)} 条，开始自愈扫描")

    live, dead = [], 0
    with cf.ThreadPoolExecutor(max_workers=32) as ex:
        for url, n, err in ex.map(check, urls):
            if n > 0:
                live.append((n, url))
                log(f"  OK  {n:6d} 节点  {url}")
            else:
                dead += 1
                log(f"  --  {url[:88]}  {err}")

    live.sort(reverse=True)
    safe_write(OUT, "\n".join(u for _, u in live) + "\n",
               min_items=5, kind="lines", force=True)
    log(f"存活源 {len(live)}/{len(urls)}（{dead} 死），总节点 {sum(n for n, _ in live)}")
    log(f"已写回 {OUT}")


if __name__ == "__main__":
    main()
