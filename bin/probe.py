# -*- coding: utf-8 -*-
"""
vpnkit :: 节点体检器 (第一轮 TCP 连通筛选)
输入: profiles/nodes_raw.json
输出: profiles/nodes_alive.json  (TCP 可连 + 记录 RTT)
"""
import json, socket, sys, time, concurrent.futures as cf
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IN = ROOT / "profiles" / "nodes_raw.json"
OUT = ROOT / "profiles" / "nodes_alive.json"
LOG = ROOT / "09_logs" / "probe.log"

WORKERS = int(sys.argv[1]) if len(sys.argv) > 1 else 600
TIMEOUT = 2.0


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def tcp_probe(p):
    host, port = p["server"], int(p["port"])
    t0 = time.perf_counter()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(TIMEOUT)
    try:
        s.connect((host, port))
        rtt = (time.perf_counter() - t0) * 1000
        return p, rtt
    except Exception:
        return p, None
    finally:
        try:
            s.close()
        except Exception:
            pass


def main():
    nodes = json.loads(IN.read_text(encoding="utf-8"))
    log(f"载入 {len(nodes)} 节点，{WORKERS} 并发 TCP 体检 (timeout {TIMEOUT}s)")

    alive, dead = [], 0
    t0 = time.time()
    done = 0
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for p, rtt in ex.map(tcp_probe, nodes):
            done += 1
            if rtt is not None:
                p["tcp_rtt"] = round(rtt, 1)
                alive.append(p)
            else:
                dead += 1
            if done % 2000 == 0:
                log(f"  进度 {done}/{len(nodes)}  活 {len(alive)}  ({time.time()-t0:.0f}s)")

    alive.sort(key=lambda x: x["tcp_rtt"])
    OUT.write_text(json.dumps(alive, ensure_ascii=False, indent=1), encoding="utf-8")

    by_type = {}
    for p in alive:
        by_type[p["type"]] = by_type.get(p["type"], 0) + 1
    log(f"TCP 存活 {len(alive)}/{len(nodes)}  ({dead} 死)  耗时 {time.time()-t0:.0f}s")
    log(f"存活类型分布: {by_type}")
    if alive:
        log(f"最快 5: " + " | ".join(f"{p['server']}:{p['port']} {p['tcp_rtt']}ms"
                                    for p in alive[:5]))


if __name__ == "__main__":
    main()
