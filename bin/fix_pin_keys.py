from pathlib import Path
# -*- coding: utf-8 -*-
"""修 claude_pin.json 的 key/candidates/dead_keys。

repin_claude.py 里的 yaml 正则假定 name 在 server/port 之前，实际结构是
  - port: 443
    server: 82.65.162.76
    name: 004 FR 233ms http
所以 key 全部匹配失败，回退成旧值。这里用更稳的办法：解析出所有
(port, server, name) 三元组，用 name 反查 server:port。
"""
import json
import os
import re

PROF = str(Path(__file__).resolve().parent.parent / "profiles")
PIN = os.path.join(PROF, "claude_pin.json")
YML = os.path.join(PROF, "vpnkit.yaml")


def build_keymap(path):
    """返回 {节点名: 'server:port'}。用块级解析，不依赖字段顺序。"""
    keymap = {}
    port = server = name = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            # 新条目以 "- " 开头
            if re.match(r"\s*-\s+\w+:", line):
                if name and server and port:
                    keymap[name] = f"{server}:{port}"
                port = server = name = None
                line = line.strip()[2:]
            m = re.match(r"\s*port:\s*(\d+)\s*$", line)
            if m:
                port = m.group(1)
                continue
            m = re.match(r"\s*server:\s*([^\s#]+)\s*$", line)
            if m:
                server = m.group(1)
                continue
            m = re.match(r"\s*name:\s*(.+?)\s*$", line)
            if m:
                name = m.group(1).strip().strip('"').strip("'")
    if name and server and port:
        keymap[name] = f"{server}:{port}"
    return keymap


def main():
    keymap = build_keymap(YML)
    print(f"vpnkit.yaml 解析出 {len(keymap)} 个节点映射")
    for k in list(keymap)[:5]:
        print(f"  {k:<28} -> {keymap[k]}")

    with open(PIN, encoding="utf-8") as f:
        pin = json.load(f)

    old_key = pin.get("key")
    old_name = pin.get("name")

    # 1) 修钉子 key
    new_key = keymap.get(old_name, "")
    print(f"\n钉子 name={old_name}")
    print(f"  旧 key = {old_key}  (错)")
    print(f"  新 key = {new_key}  {'(正确)' if new_key else '(!! 仍找不到)'}")
    if new_key:
        pin["key"] = new_key

    # 2) 修 candidates：只用名字能解析出 key 的
    raw_cands = pin.get("candidates", [])
    fixed_cands = []
    for c in raw_cands:
        if ":" in c and not c.startswith(":"):
            fixed_cands.append(c)
        else:
            mapped = keymap.get(c, "")
            if mapped:
                fixed_cands.append(mapped)
    print(f"\ncandidates: {len(raw_cands)} -> {len(fixed_cands)} 个有效 key")

    # 3) dead_keys：把 vless 里 TLS 全坏的记进去（本轮实测 000/001/002/003/013/017/018/019/022/023）
    broken_names = [
        "000 US 158ms vless", "001 US 180ms vless", "002 US 179ms vless",
        "003 US 151ms vless", "013 DE 193ms vless", "017 SG 141ms vless",
        "018 SG 301ms vless", "019 SG 401ms vless", "022 SG 137ms vless",
        "023 SG 222ms vless",
    ]
    dead = set(pin.get("dead_keys", []))
    added = 0
    for n in broken_names:
        k = keymap.get(n, "")
        if k and k not in dead:
            dead.add(k)
            added += 1
    pin["dead_keys"] = sorted(dead)
    print(f"dead_keys: 新增 {added} 个，共 {len(pin['dead_keys'])} 个")

    pin["candidates"] = fixed_cands[:12]
    pin["note"] = ("dead_keys 里的节点 TLS 握手已坏（SSLEOFError），build_config/claude_pin "
                   "应跳过。由 repin_claude 修复脚本于 2026-10-25 写入。")

    with open(PIN, "w", encoding="utf-8") as f:
        json.dump(pin, f, ensure_ascii=False, indent=2)

    print(f"\n已写 {PIN}")
    print(json.dumps({k: pin[k] for k in ("name", "key", "cc", "api_code", "web_code", "wins", "since")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
