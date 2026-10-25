# -*- coding: utf-8 -*-
"""Check the Claude chain through the CLAUDE group exactly as a browser would.

A 403 with a ~5.6KB body is Cloudflare's normal bot page: TLS and HTTP both
worked, so the relay is fine. A 403 with a tiny body, or a 401/403 swap on
api.anthropic.com, means the exit IP itself is being blocked -- a different
problem, and the fix is to repin rather than to keep retrying.
"""
import json
from pathlib import Path

import requests

PROF = Path(__file__).resolve().parent.parent / "profiles"
CTRL = 9097
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
TARGETS = [
    ("claude.ai/login", "https://claude.ai/login"),
    ("claude.ai", "https://claude.ai/"),
    ("api.anthropic", "https://api.anthropic.com/v1/models"),
    ("portal.anthropic", "https://portal.anthropic.com/verify?org=2e336532-d77d-4231-8566-0c025b300763"),
]


def resolve_pin_name(pin):
    """Node names embed a rebuild counter, so always resolve through the key.

    The pin stores `202.50.253.36:19999`, which is stable; the display name was
    `006 IT 286ms ss` before a rebuild and `046 IT 286ms ss` after. Comparing
    the stale name would report a permanent mismatch.
    """
    key = str(pin.get("key") or "")
    name = str(pin.get("name") or "")
    if not key:
        return name
    try:
        import yaml
        cfg = yaml.safe_load((PROF / "vpnkit.yaml").read_text(encoding="utf-8"))
    except Exception:
        return name
    for p in cfg.get("proxies") or []:
        if f"{p.get('server')}:{p.get('port')}" == key:
            return str(p.get("name") or name)
    return name


def main():
    pin = json.loads((PROF / "claude_pin.json").read_text(encoding="utf-8"))
    want = resolve_pin_name(pin)
    now = requests.get(f"http://127.0.0.1:{CTRL}/proxies/CLAUDE",
                       timeout=5).json().get("now")
    print(f"钉子      : {want}  key={pin['key']}")
    print(f"组当前选中: {now}")
    print(f"一致      : {'是' if now == want else '否 !!'}")
    print()

    s = requests.Session()
    s.trust_env = False
    s.proxies = {"http": "http://127.0.0.1:7897",
                 "https": "http://127.0.0.1:7897"}
    s.headers["User-Agent"] = UA

    bad = 0
    for label, url in TARGETS:
        try:
            r = s.get(url, timeout=20, allow_redirects=False)
            body = len(r.content)
            # 5KB+ Cloudflare page, or a plain redirect/401 => relay healthy.
            ok = r.status_code in (200, 301, 302, 307, 401) or body >= 3000
            if not ok:
                bad += 1
            print(f"  {'OK ' if ok else '!! '} {label:<18} "
                  f"{r.status_code:<4} {body:>7}B")
        except Exception as e:
            bad += 1
            print(f"  !!  {label:<18} {type(e).__name__}: {str(e)[:60]}")

    print()
    if bad == 0:
        print("链路健康 —— 浏览器可以正常打开 claude.ai")
    else:
        print(f"{bad} 个目标异常。若都是小体积 403，说明出口 IP 被 Cloudflare "
              f"标记，跑 python claude_pin.py --force 换一个出口。")


if __name__ == "__main__":
    main()
