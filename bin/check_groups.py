# -*- coding: utf-8 -*-
"""Post-refresh sanity check: pin node health + group membership."""
import json
from pathlib import Path

import requests

PROF = Path(__file__).resolve().parent.parent / "profiles"
CTRL = 9097


def main():
    fast = {f"{r['server']}:{r['port']}": r for r in
            json.loads((PROF / "nodes_fast.json").read_text(encoding="utf-8"))}
    pin = json.loads((PROF / "claude_pin.json").read_text(encoding="utf-8"))
    key = pin["key"]
    rec = fast.get(key)
    # Node names carry a rebuild counter (`006` -> `046` after a rebuild), so the
    # stored name goes stale. Always show the name the current pool gives for the
    # pinned key, otherwise this line contradicts check_claude.py.
    shown = (rec or {}).get("name") or pin.get("name")
    print(f"钉子 {shown}  key={key}")
    if shown != pin.get("name"):
        print(f"  （claude_pin.json 里记的是旧名 {pin.get('name')}，按 key 解析）")
    print(f"  在测速表里: {'有' if rec else '没有'}")
    if rec:
        print(f"  tls_ok={rec.get('tls_ok')}  kbps={rec.get('kbps')}  "
              f"note={rec.get('tls_note')}")

    broken = [k for k, v in fast.items() if v.get("tls_ok") is False]
    print(f"\nTLS 断裂节点: {len(broken)} / {len(fast)}")

    print()
    for g in ("CLAUDE", "SPEED-TOP", "AUTO-FAST", "GIT"):
        j = requests.get(f"http://127.0.0.1:{CTRL}/proxies/{g}", timeout=5).json()
        now = j.get("now")
        allm = j.get("all", [])
        print(f"{g:<10} now={now}  成员={len(allm)}")

    # Every auto-group member must be tls_ok — that is the whole point of the gate.
    bad_in_auto = []
    for g in ("SPEED-TOP", "AUTO-FAST"):
        j = requests.get(f"http://127.0.0.1:{CTRL}/proxies/{g}", timeout=5).json()
        for name in j.get("all", []):
            if name == "DIRECT":
                continue
            hit = next((v for v in fast.values() if v.get("name") == name), None)
            if hit and hit.get("tls_ok") is False:
                bad_in_auto.append(f"{g}:{name}")
    print(f"\n自动组里混入的 TLS 断裂节点: "
          f"{bad_in_auto if bad_in_auto else '无（门禁生效）'}")


if __name__ == "__main__":
    main()
