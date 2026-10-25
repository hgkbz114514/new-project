# -*- coding: utf-8 -*-
"""
vpnkit :: 给节点标注出口国家
1) 批量查 server IP 的国家（ip-api.com 批量接口，100 IP/次）
2) 对 CDN 类（Cloudflare/无意义 IP）标注 CDN
写回 nodes_overseas.json 的 cc / country / isp 字段
  python geo_tag.py
"""
import json, socket, sys, time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
PROF = ROOT / "profiles"
LOG = ROOT / "09_logs" / "geo_tag.log"

CDN_HINT = ("cloudflare", "fastly", "akamai", "cloudfront", "gcore",
            "bunny", "stackpath", "incapsula", "imperva")
CF_NETS = ("104.16.", "104.17.", "104.18.", "104.19.", "104.20.",
           "172.64.", "172.65.", "172.66.", "172.67.", "172.68.",
           "188.114.", "162.158.", "162.159.", "185.148.", "198.41.")


def log(m):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {m}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def resolve(host, cache):
    if host in cache:
        return cache[host]
    ip = None
    if host.replace(".", "").isdigit():
        ip = host
    else:
        try:
            ip = socket.gethostbyname(host)
        except Exception:
            ip = None
    cache[host] = ip
    return ip


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    src = PROF / "nodes_overseas.json"
    if not src.exists():
        src = PROF / "nodes_live.json"
    nodes = json.loads(src.read_text(encoding="utf-8"))
    log(f"载入 {src.name}: {len(nodes)} 节点")

    cache, ips = {}, []
    for n in nodes:
        ip = resolve(n.get("server", ""), cache)
        if ip and ip not in ips:
            ips.append(ip)
    log(f"去重 server IP {len(ips)} 个")

    info = {}
    for i in range(0, len(ips), 100):
        batch = ips[i:i + 100]
        try:
            r = requests.post("http://ip-api.com/batch?fields=status,country,countryCode,isp,query",
                              json=batch, timeout=25)
            for e in r.json():
                if e.get("status") == "success":
                    info[e["query"]] = (e.get("country", ""), e.get("countryCode", ""),
                                        e.get("isp", ""))
            log(f"  批次 {i//100+1}: {len(batch)} 个 -> 命中 {len(info)}")
        except Exception as e:
            log(f"  !! 批次失败 {e}")
        time.sleep(1.2)

    for n in nodes:
        ip = cache.get(n.get("server", ""))
        if not ip:
            n["cc"], n["country"], n["isp"] = "??", "unresolved", ""
            continue
        cc, cn, isp = info.get(ip, ("", "", ""))
        low = (isp or "").lower()
        if any(k in low for k in CDN_HINT) or any(ip.startswith(p) for p in CF_NETS):
            cc = "CDN" if not cc else cc
            n["cdn"] = True
        n["cc"] = cc or "??"
        n["country"] = cn or "unknown"
        n["isp"] = isp
        n["resolved_ip"] = ip

    out = PROF / "nodes_final.json"
    out.write_text(json.dumps(nodes, ensure_ascii=False, indent=1), encoding="utf-8")

    stat = {}
    for n in nodes:
        stat[n["cc"]] = stat.get(n["cc"], 0) + 1
    log(f"出口国家分布: {dict(sorted(stat.items(), key=lambda x: -x[1]))}")
    for n in nodes[:10]:
        log(f"  {n.get('name','')[:22]:24s} {n.get('cc'):4s} {n.get('country','')[:18]:20s} {n.get('isp','')[:22]}")
    log(f"已写出 {out}")


if __name__ == "__main__":
    main()
