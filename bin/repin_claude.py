from pathlib import Path
# -*- coding: utf-8 -*-
"""把 claude_pin.json 的钉子换成 TLS 实测通过的那个，并把坏节点踢出候选。

背景：claude_pin.py 的探针用 requests 测 api.anthropic.com 与 claude.ai/login，
但它在 2026-10-24 钉住的 `001 US 180ms vless`(104.16.65.1:443) 今天 TLS 已经坏了
（SSLEOFError: UNEXPECTED_EOF_WHILE_READING，5015ms 超时）。
原因是那轮扫描窗口里该节点短暂可用，3 轮复测恰好都撞上，属于"闪活"。

本脚本做三件事：
  1. 现场重测 CLAUDE 组全部成员，按 claude.ai/login 实通排序
  2. 把钉子改写成当前最优（含 wins/since/candidates 重算）
  3. 把 TLS 坏掉的 key 记进 dead_keys，让 build_config 下次不再把它们排进候选前段
"""
import json
import os
import time

import requests
import urllib3

urllib3.disable_warnings()

PROXY = "http://127.0.0.1:7897"
CTRL = "http://127.0.0.1:9097"
PROF = str(Path(__file__).resolve().parent.parent / "profiles")
PIN = os.path.join(PROF, "claude_pin.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36")

# claude.ai/login 返回 403 是 Cloudflare 正常拦截（证明 TLS+HTTP 都通），
# 只要拿到 <500 且 body 非空就算该节点能转发 Anthropic。
OK_CODES = (200, 206, 301, 302, 307, 403, 429)


def ctrl(path, method="GET", payload=None):
    s = requests.Session()
    s.trust_env = False
    if method == "PUT":
        return s.put(CTRL + path, json=payload, timeout=8)
    return s.get(CTRL + path, timeout=8)


def hit(url, timeout=12):
    t0 = time.time()
    try:
        r = requests.get(url, proxies={"http": PROXY, "https": PROXY},
                         timeout=timeout, verify=False, allow_redirects=False,
                         headers={"User-Agent": UA})
        return r.status_code, int((time.time() - t0) * 1000), len(r.content), ""
    except Exception as e:
        return 0, int((time.time() - t0) * 1000), 0, type(e).__name__


def main():
    with open(PIN, encoding="utf-8") as f:
        pin = json.load(f)
    old_name, old_key = pin.get("name"), pin.get("key")

    r = ctrl("/proxies/CLAUDE")
    if not r.ok:
        print("拿不到 CLAUDE 组")
        return
    members = r.json().get("all", [])
    print(f"CLAUDE 组 {len(members)} 个成员，全量实测 claude.ai/login ...\n")

    rows = []
    for i, name in enumerate(members):
        ctrl("/proxies/CLAUDE", "PUT", {"name": name})
        code, ms, size, err = hit("https://claude.ai/login", timeout=10)
        ok = code in OK_CODES and size > 500
        rows.append({"idx": i, "name": name, "code": code, "ms": ms,
                     "size": size, "err": err, "ok": ok})
        if ok:
            print(f"  OK {i:>2} {name:<28} {code:>3} {ms:>6}ms {size:>7}B", flush=True)
        elif i < 40:
            print(f"  -- {i:>2} {name:<28} {code:>3} {ms:>6}ms {err}", flush=True)

    alive = [x for x in rows if x["ok"]]
    dead = [x for x in rows if not x["ok"]]
    print(f"\n可用 {len(alive)}/{len(rows)}，坏 {len(dead)}")

    if not alive:
        print("!! 没有任何成员能用，保留原钉子等下一轮 guard 重扫")
        return

    # server:port 反查：节点名形如 "016 IT 292ms ss"，从 vpnkit.yaml 的 proxies 顺序取
    keymap = {}
    yml = os.path.join(PROF, "vpnkit.yaml")
    if os.path.exists(yml):
        import re
        order = 0
        cur = None
        with open(yml, encoding="utf-8") as f:
            for line in f:
                m = re.match(r"\s*- name: (\S+ \S+ \S+ \w+)\s*$", line)
                if m:
                    order += 1
                    idx = f"{order - 1:03d}"
                    keymap[m.group(1)] = idx
                    cur = m.group(1)
                m2 = re.match(r"\s*server: (\S+)\s*$", line)
                if m2 and cur:
                    keymap.setdefault("_srv_" + cur, m2.group(1))
                m3 = re.match(r"\s*port: (\d+)\s*$", line)
                if m3 and cur:
                    keymap.setdefault("_port_" + cur, m3.group(1))

    def key_of(name):
        srv = keymap.get("_srv_" + name, "")
        prt = keymap.get("_port_" + name, "")
        return f"{srv}:{prt}" if srv and prt else ""

    # 排序：优先快点，但同时要求连续两次都通（避免再次钉到闪活节点）
    alive.sort(key=lambda x: x["ms"])
    best = None
    for cand in alive[:6]:
        ctrl("/proxies/CLAUDE", "PUT", {"name": cand["name"]})
        wins = 0
        for _ in range(3):
            code, ms, size, err = hit("https://claude.ai/login", timeout=10)
            if code in OK_CODES and size > 500:
                wins += 1
            time.sleep(0.4)
        cand["wins"] = wins
        print(f"  复测 {cand['name']:<28} {wins}/3")
        if wins >= 2:
            best = cand
            break

    if best is None:
        best = alive[0]
        best["wins"] = 1
        print(f"  没有 3 轮稳定节点，退回最快的 {best['name']}")

    print(f"\n新钉子: {best['name']}  code={best['code']} ms={best['ms']} wins={best['wins']}/3")

    dead_keys = set(pin.get("dead_keys", []))
    for d in dead:
        k = key_of(d["name"])
        if k:
            dead_keys.add(k)

    newpin = {
        "name": best["name"],
        "key": key_of(best["name"]) or old_key,
        "cc": best["name"].split()[1] if len(best["name"].split()) > 1 else "",
        "ms": best["ms"],
        "api_code": None,
        "web_code": best["code"],
        "wins": best["wins"],
        "since": time.strftime("%Y-%m-%d %H:%M:%S"),
        "candidates": [key_of(x["name"]) for x in alive if key_of(x["name"])][:12],
        "dead_keys": sorted(dead_keys),
    }
    # api_code 补测一次
    code, ms, size, err = hit("https://api.anthropic.com/v1/models", timeout=10)
    newpin["api_code"] = code

    with open(PIN, "w", encoding="utf-8") as f:
        json.dump(newpin, f, ensure_ascii=False, indent=2)

    print(f"\n已写 {PIN}")
    print(f"  name={newpin['name']}")
    print(f"  key ={newpin['key']}  cc={newpin['cc']}  api={newpin['api_code']} web={newpin['web_code']}")
    print(f"  dead_keys={len(newpin['dead_keys'])} 个")
    print(f"  旧钉子 {old_name} ({old_key}) 已替换")

    ctrl("/proxies/CLAUDE", "PUT", {"name": newpin["name"]})
    print("\n最终验证：")
    for label, url in [("claude.ai/login", "https://claude.ai/login"),
                       ("claude.ai", "https://claude.ai/"),
                       ("api.anthropic", "https://api.anthropic.com/v1/models"),
                       ("portal.anthropic", "https://portal.anthropic.com/verify")]:
        code, ms, size, err = hit(url)
        mark = "OK " if 0 < code < 500 else "-- "
        print(f"  {mark}{label:<16} {code:>3} {ms:>6}ms {size:>8}B {err}")


if __name__ == "__main__":
    main()
