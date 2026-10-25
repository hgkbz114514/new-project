# -*- coding: utf-8 -*-
"""
vpnkit :: local subscription server

In Clash Verge / ClashX / any clash client, add ONE subscription:
    http://127.0.0.1:7777/sub
It always serves the newest verified config, so the client is configured once
and stays current forever.

  /sub         full clash config
  /sub/mini    trimmed config
  /status      JSON state
  /health      ok / empty

Ownership model (important):
    guard.py is the ONLY writer. It re-harvests, re-measures and deploys on a
    schedule. This server is a pure reader: it watches profiles/vpnkit.yaml and
    swaps the in-memory bytes when the file changes. Running a second refresh
    chain here used to fight guard for the throwaway-core ports and rebuild the
    profile twice per cycle.

    Set VK_SUB_AUTOREFRESH=1 only if you are NOT running guard.py.

  python subserver.py
"""
import json
import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin"
PROF = ROOT / "profiles"
LOG = ROOT / "09_logs" / "subserver.log"

PORT = int(os.environ.get("VK_SUB_PORT", "7777"))
REFRESH_MIN = float(os.environ.get("VK_REFRESH_MIN", "180"))
MIN_NODES = int(os.environ.get("VK_MIN_NODES", "20"))
AUTOREFRESH = os.environ.get("VK_SUB_AUTOREFRESH") == "1"

_state = {"yaml": b"", "mini": b"", "built_at": 0, "nodes": 0,
          "busy": False, "last_error": "", "rounds": 0, "mtime": 0}
_lock = threading.Lock()


def log(m):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {m}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def pool_size():
    """Same priority order build_config uses, so the reported count matches
    the config actually being served."""
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


def reload_files(reason):
    full = PROF / "vpnkit.yaml"
    mini = PROF / "vpnkit_mini.yaml"
    if not full.exists():
        return False
    try:
        mt = full.stat().st_mtime
    except OSError:
        return False
    n, src = pool_size()
    with _lock:
        _state["yaml"] = full.read_bytes()
        _state["mini"] = mini.read_bytes() if mini.exists() else _state["yaml"]
        _state["built_at"] = int(time.time())
        _state["nodes"] = n
        _state["mtime"] = mt
    log(f"loaded ({reason}) nodes={n} src={src} size={len(_state['yaml'])/1024:.0f}KB")
    return True


def watcher():
    """Pure reader: pick up whatever guard.py deployed."""
    while True:
        try:
            full = PROF / "vpnkit.yaml"
            if full.exists():
                mt = full.stat().st_mtime
                if mt != _state["mtime"]:
                    reload_files("changed on disk")
        except Exception:
            log(traceback.format_exc()[-300:])
        time.sleep(20)


# ---- optional self-refresh (only when guard.py is not running) -------------
def _run(script, *args, timeout=7200, env_extra=None):
    import subprocess
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    if env_extra:
        env.update(env_extra)
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable, str(BIN / script), *args], cwd=str(BIN),
                           env=env, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        ok = p.returncode == 0
        tail = (p.stdout or "").strip().splitlines()[-2:]
    except subprocess.TimeoutExpired:
        return False, f"timeout {timeout}s"
    log(f"  {'OK' if ok else 'FAIL'} {script} {time.time()-t0:.0f}s "
        f"{tail[-1] if tail else ''}")
    return ok, ("" if ok else (p.stderr or "")[-400:])


def refresh_once():
    if _state["busy"]:
        log("previous round still running, skip")
        return
    with _lock:
        _state["busy"] = True
        _state["rounds"] += 1
    try:
        log("=" * 50)
        log("refresh round start")
        _run("discover_sources.py", timeout=1200)
        for script, args, kw in (
            ("harvest.py", (), {}),
            ("probe.py", ("900",), {}),
            ("test_live2.py", (), {"env_extra": {"VK_TIMEOUT": "5000",
                                                 "VK_CONC": "128", "VK_ROUNDS": "2"}}),
        ):
            ok, err = _run(script, *args, **kw)
            if not ok:
                raise RuntimeError(f"{script}: {err}")
        _run("build_config.py")
        _run("speed_probe.py", "--top", "80", "--bytes", "2000000", "--threads", "24",
             timeout=2400)
        _run("ghscan.py", "--top", "80", "--threads", "16", timeout=2400)
        ok, err = _run("build_config.py")
        if not ok:
            raise RuntimeError("build: " + err)

        n, _ = pool_size()
        if n < MIN_NODES:
            log(f"!! {n} < {MIN_NODES}, keeping the previous config")
            with _lock:
                _state["last_error"] = f"nodes {n} < {MIN_NODES}"
        else:
            reload_files("self-refresh")
            with _lock:
                _state["last_error"] = ""
        log("refresh round done")
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        log("!! round failed " + msg)
        log(traceback.format_exc()[-600:])
        with _lock:
            _state["last_error"] = msg
    finally:
        with _lock:
            _state["busy"] = False


def refresher():
    while True:
        try:
            refresh_once()
        except Exception:
            log(traceback.format_exc()[-400:])
        time.sleep(REFRESH_MIN * 60)


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, body: bytes, ctype="text/yaml; charset=utf-8", code=200):
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", 'attachment; filename="vpnkit.yaml"')
            self.send_header("Profile-Update-Interval", str(int(REFRESH_MIN)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            # A client that probes and hangs up (PowerShell -TimeoutSec, a
            # cancelled curl, Clash interrupting a profile update) makes
            # socketserver dump a full traceback into the log. The request is
            # already gone, so there is nothing to answer and nothing to fix.
            self.close_connection = True

    def handle_one_request(self):
        try:
            BaseHTTPRequestHandler.handle_one_request(self)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            self.close_connection = True

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        with _lock:
            y, m, n, t, busy, err = (_state["yaml"], _state["mini"], _state["nodes"],
                                     _state["built_at"], _state["busy"],
                                     _state["last_error"])
        if path in ("/sub", "/subscribe", "/clash", "/"):
            if not y:
                return self._send(b"# not ready yet\n", code=503)
            return self._send(y)
        if path == "/sub/mini":
            return self._send(m or y)
        if path == "/status":
            body = json.dumps({"nodes": n, "built_at": t, "busy": busy,
                               "rounds": _state["rounds"], "last_error": err,
                               "size": len(y), "autorefresh": AUTOREFRESH},
                              ensure_ascii=False, indent=1).encode()
            return self._send(body, "application/json; charset=utf-8")
        if path == "/health":
            return self._send(b"ok" if y else b"empty", "text/plain")
        return self._send(b"404", "text/plain", 404)

    def log_message(self, *a):
        pass


class Srv(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        """Swallow disconnect noise; keep real errors visible."""
        import sys as _s
        et = _s.exc_info()[0]
        if et and issubclass(et, (ConnectionResetError, ConnectionAbortedError,
                                  BrokenPipeError, TimeoutError)):
            return
        log(traceback.format_exc()[-400:])


def main():
    # pythonw.exe gives us stdout=None; harden() must run before any print.
    try:
        from vkstdio import harden
        harden("subserver")
    except Exception:
        pass
    print(f"vpnkit sub server :{PORT}  mode="
          f"{'self-refresh' if AUTOREFRESH else 'reader (guard owns refresh)'}")
    if reload_files("startup") is False:
        print("no profile yet -- run build_config.py first, or set VK_SUB_AUTOREFRESH=1")
    threading.Thread(target=watcher, daemon=True).start()
    if AUTOREFRESH:
        threading.Thread(target=refresher, daemon=True).start()
    try:
        srv = Srv(("127.0.0.1", PORT), H)
    except OSError as e:
        log(f"bind 127.0.0.1:{PORT} failed: {e} -- another subserver is probably alive")
        return
    print(f"subscription url: http://127.0.0.1:{PORT}/sub")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
