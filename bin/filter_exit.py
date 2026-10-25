# -*- coding: utf-8 -*-
"""
vpnkit :: 出口筛选 —— 用一个已跑起来的 mihomo 对所有节点做并发"能否访问境外"判定

判定逻辑：
  /proxies/{name}/delay?url=https://www.google.com/generate_204
  能在 5s 内返回的，说明该节点真的能出境（中国大陆出口的 http 代理必然失败）
  再加 https://www.youtube.com/generate_204 与 https://cp.cloudflare.com/generate_204 交叉确认

输出 profiles/nodes_overseas.json（只含出境节点，带 delay）
  python filter_exit.py [controller] [secret]
"""
import json, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
PROF = ROOT / "profiles"
LOG = ROOT / "09_logs" / "filter_exit.log"

CTRL = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1:9097"
SECRET = sys.argv[2] if len(sys.argv) > 2 else ""
BASE = f"http://{CTRL}"
H = {"Authorization": f"Bearer {SECRET}"} if SECRET else {}

TARGETS = [
    ("https://www.google.com/generate_204", "G"),
    ("https://www.youtube.com/generate_204", "Y"),
    ("https://cp.cloudflare.com/generate_204", "C"),
]
TIMEOUT_MS = 6000


def log(m):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {m}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def test(name):
    res = {}
    for url, tag in TARGETS:
        try:
            r = requests.get(f"{BASE}/proxies/{requests.utils.quote(name)}/delay",
                             params={"url": url, "timeout": TIMEOUT_MS},
                             headers=H, timeout=(TIMEOUT_MS / 1000) + 4)
            if r.status_code == 200:
                res[tag] = r.json().get("delay", 0)
        except Exception:
            pass
    return name, res


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    meta = {}
    for f in ("nodes_live.json", "nodes_verified.json", "nodes_alive.json"):
        p = PROF / f
        if p.exists():
            try:
                for n in json.loads(p.read_text(encoding="utf-8")):
                    meta[n.get("name")] = n
            except Exception:
                pass

    names = list(meta)
    # 名字是 build_config 重新生成的 000 xxxms type，用序号对齐
    src = PROF / "nodes_live.json"
    if src.exists():
        raw = json.loads(src.read_text(encoding="utf-8"))
    else:
        raw = []

    r = requests.get(f"{BASE}/proxies", headers=H, timeout=10).json()["proxies"]
    live_names = [n for n in r if n[:3].isdigit() and " " in n]
    log(f"控制器内节点 {len(live_names)} 个")

    ok = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=64) as ex:
        for i, (name, res) in enumerate(ex.map(test, live_names), 1):
            if "G" in res or "Y" in res:
                idx = int(name[:3])
                node = dict(raw[idx]) if idx < len(raw) else {}
                node["name"] = name
                node["probe"] = res
                node["delay"] = min(res.values()) if res else 0
                ok.append(node)
            if i % 25 == 0:
                log(f"  {i}/{len(live_names)} 出境 {len(ok)}")

    ok.sort(key=lambda x: x.get("delay", 9999))
    out = PROF / "nodes_overseas.json"
    out.write_text(json.dumps(ok, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"=== 出境节点 {len(ok)} / {len(live_names)}  耗时 {time.time()-t0:.0f}s -> {out}")
    by_type = {}
    for n in ok:
        by_type[n.get("type")] = by_type.get(n.get("type"), 0) + 1
    log(f"类型: {by_type}")
    for n in ok[:12]:
        log(f"  {n['name']:24s} {n.get('server')}:{n.get('port')} {n.get('delay')}ms {n.get('probe')}")


if __name__ == "__main__":
    main()
