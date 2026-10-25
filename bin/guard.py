# -*- coding: utf-8 -*-
"""
vpnkit :: long-term watchdog

One round:
  harvest -> probe -> test_live2 -> build_config -> speed_probe -> ghscan
          -> claude_pin -> build_config -> deploy

Health gate: if the fresh pool is smaller than VK_MIN_NODES, the previous
profile is kept and nothing gets deployed, so a bad source day never leaves
the user with an empty config.

Usage:
  python guard.py            # resident, loops on VK_INTERVAL_H
  python guard.py --once     # one round (for schtasks)

Env:
  VK_INTERVAL_H  hours between rounds, default 4
  VK_MIN_NODES   minimum pool size to deploy, default 20
  VK_SKIP_EXIT   1 = skip the slow exit-IP verification
  VK_TOPN        how many nodes to throughput-test, default 80
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin"
PROF = ROOT / "profiles"
LOG = ROOT / "09_logs" / "guard.log"
STATE = PROF / "guard_state.json"
LOCK = PROF / ".guard.lock"
PY = sys.executable

INTERVAL_H = float(os.environ.get("VK_INTERVAL_H", "4"))
MIN_NODES = int(os.environ.get("VK_MIN_NODES", "20"))
TOPN = os.environ.get("VK_TOPN", "80")


def log(m):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {m}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(script, *args, timeout=7200, env_extra=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    if env_extra:
        env.update(env_extra)
    t0 = time.time()
    try:
        p = subprocess.run([PY, str(BIN / script), *args], cwd=str(BIN), env=env,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        log(f"  ! {script} timeout {timeout}s")
        return False
    ok = p.returncode == 0
    for l in (p.stdout or "").strip().splitlines()[-4:]:
        log(f"  | {l}")
    if not ok:
        for l in (p.stderr or "").strip().splitlines()[-4:]:
            log(f"  ! {l}")
    log(f"  {'OK' if ok else 'FAIL'} {script} {time.time()-t0:.0f}s")
    return ok


def pool_size():
    """Size of the freshest pool, in build_config's own priority order."""
    for name in ("nodes_pool.json", "nodes_fast.json", "nodes_verified.json",
                 "nodes_live.json", "nodes_alive.json"):
        p = PROF / name
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, list) and data:
            return len(data), name
    return 0, None


def round_():
    log("=" * 56)
    log(f"round start  interval={INTERVAL_H}h  min_nodes={MIN_NODES}")

    run("discover_sources.py", timeout=1200)

    if not run("harvest.py", timeout=2400):
        log("!! harvest failed, round skipped")
        return False
    if not run("probe.py", "900", timeout=2400):
        log("!! tcp probe failed, round skipped")
        return False
    if not run("test_live2.py", timeout=7200,
               env_extra={"VK_TIMEOUT": "5000", "VK_CONC": "128", "VK_ROUNDS": "2"}):
        log("!! liveness test failed, round skipped")
        return False

    if not os.environ.get("VK_SKIP_EXIT"):
        run("verify_exit.py", timeout=7200)

    # First build so the speed / github probes have node names to bind to.
    if not run("build_config.py"):
        log("!! build failed, round skipped")
        return False

    n, src = pool_size()
    log(f"pool {n} nodes (from {src}), threshold {MIN_NODES}")
    if n < MIN_NODES:
        log("!! below threshold, keeping the previous profile")
        return False

    # Real parallel throughput. This is what makes AUTO groups pick nodes that
    # actually move bytes, instead of low-TTFB nodes that stall mid-page.
    run("speed_probe.py", "--top", TOPN, "--bytes", "2000000",
        "--threads", "24", timeout=2400)
    run("ghscan.py", "--top", TOPN, "--threads", "16", timeout=2400)

    # Pin one stable exit for Claude. Without this the CLAUDE group would be
    # rebuilt from scratch on every round and land on a different node -- which
    # means a different exit IP, which is exactly what gets an Anthropic account
    # flagged mid-verification. claude_pin.py keeps the previous pin when it is
    # still healthy, so the IP survives refreshes.
    run("claude_pin.py", "--top", TOPN, "--threads", "14", timeout=2400)

    if not run("build_config.py"):
        log("!! rebuild failed")
        return False
    if not run("vk.py", "deploy"):
        log("!! deploy failed")
        return False

    fast = {}
    try:
        fp = PROF / "nodes_fast.json"
        if fp.exists():
            for r in json.loads(fp.read_text(encoding="utf-8")):
                if float(r.get("kbps") or 0) >= 300:
                    fast["n300"] = fast.get("n300", 0) + 1
            fast["tested"] = len(json.loads(fp.read_text(encoding="utf-8")))
    except Exception:
        pass

    STATE.write_text(json.dumps({
        "last_run": time.strftime("%Y-%m-%d %H:%M:%S"),
        "live_nodes": n, "source": src, "speed": fast,
        "next_run_epoch": time.time() + INTERVAL_H * 3600,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"round done, next in {INTERVAL_H}h")
    return True


def locked():
    if not LOCK.exists():
        return None
    try:
        pid = int(LOCK.read_text().strip())
    except Exception:
        return None
    import ctypes
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if h:
        ctypes.windll.kernel32.CloseHandle(h)
        return pid
    return None


def main():
    # pythonw.exe / scheduled tasks can hand us stdout=None; harden first.
    try:
        from vkstdio import harden
        harden("guard")
    except Exception:
        pass
    if "--once" in sys.argv:
        pid = locked()
        if pid:
            log(f"another guard pid={pid} is running, exit")
            return
        LOCK.write_text(str(os.getpid()), encoding="utf-8")
        try:
            round_()
        finally:
            try:
                LOCK.unlink()
            except Exception:
                pass
        return

    pid = locked()
    if pid:
        log(f"already running pid={pid}, exit")
        return
    LOCK.write_text(str(os.getpid()), encoding="utf-8")
    log(f"watchdog up pid={os.getpid()} interval {INTERVAL_H}h min {MIN_NODES}")
    try:
        while True:
            try:
                round_()
            except Exception as e:
                log(f"!! round crashed {type(e).__name__}: {e}")
            time.sleep(INTERVAL_H * 3600)
    finally:
        try:
            LOCK.unlink()
        except Exception:
            pass


if __name__ == "__main__":
    main()
