# -*- coding: utf-8 -*-
"""Safe-write helper: never let a failed collection round destroy good data.

Every collector (discover/harvest/probe/test_live2/verify_exit/ghscan) used to
write its output file unconditionally. When the VPN was off, the whole chain
produced empty results and the files got overwritten with junk -- that is how
vpnkit's node pool died on 2026-10-25/26.

This module gives every writer the same two guards:

1. atomic_write(path, text)   -- write to <name>.tmp then os.replace, so a
   crash mid-write can never leave a truncated file behind.
2. safe_write(path, text, min_items=1, kind='json'|'lines') -- refuses to
   replace an existing file when the NEW content is empty/undersized and the
   OLD file was healthier ("degradation guard"). Logs a warning instead.

Usage in collectors:
        safe_write(OUT, json.dumps(rows, ...), min_items=1)
"""
import json
import os
import sys

ROOT = None  # filled by init(); collectors pass absolute paths anyway


def _warn(msg):
    try:
        print(f"!! [safe-write] {msg}")
    except Exception:
        pass


def atomic_write(path, text, encoding="utf-8"):
    """Write text via tmp-file + os.replace: crash-safe, always complete."""
    path = os.path.abspath(str(path))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding=encoding, newline="") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _count_items(text, kind):
    """How many usable entries does this content carry?"""
    if not text or not text.strip():
        return 0
    if kind == "json":
        try:
            data = json.loads(text)
        except Exception:
            return 1  # non-JSON text: treat as non-empty
        if isinstance(data, list):
            return len(data)
        if isinstance(data, dict):
            return max(1, len(data))
        return 1
    # lines / plain text
    return sum(1 for ln in text.splitlines() if ln.strip())


def safe_write(path, text, min_items=1, kind="json", encoding="utf-8",
               force=False):
    """atomic_write + degradation guard.

    Refuses to overwrite an existing, healthier file with fewer usable items
    than `min_items` (default 1). `force=True` bypasses the guard (for files
    that are legitimately allowed to be empty, e.g. sources_live.txt).
    Returns True when the file was written.
    """
    path = os.path.abspath(str(path))
    n = _count_items(text, kind)
    if not force and n < min_items:
        try:
            old = os.path.getsize(path)
        except OSError:
            old = 0
        if old > 10:
            _warn(f"{os.path.basename(path)} 新结果只有 {n} 条（少于 {min_items}），"
                  f"保留旧文件 {old} B 不覆写")
            return False
    atomic_write(path, text, encoding=encoding)
    return True


def init(root=None):
    """Record the project root (optional; kept for future use)."""
    global ROOT
    ROOT = root


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # self-test
    import tempfile
    d = tempfile.mkdtemp()
    p = os.path.join(d, "t.json")
    assert safe_write(p, json.dumps([{"a": 1}] * 10))
    assert not safe_write(p, "[]")                 # guard keeps old file
    safe_write(p, json.dumps([1]))                 # 1 item == min_items: allowed
    assert safe_write(p, "[]", force=True)         # force overwrites
    assert safe_write(p, json.dumps([1]))          # over empty file: allowed
    assert not os.path.exists(p + ".tmp")          # atomicity: no tmp left
    print("vkwrite self-test OK")
