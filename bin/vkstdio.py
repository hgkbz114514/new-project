from pathlib import Path
# -*- coding: utf-8 -*-
"""Stdio hardening for vpnkit daemons.

Why this file exists
--------------------
`pythonw.exe` (no console) sets ``sys.stdout`` and ``sys.stderr`` to ``None``.
Two things then break in a plain script:

1. ``sys.stdout.reconfigure(encoding="utf-8")`` -> ``AttributeError``
2. ``print("...")`` -> ``AttributeError: 'NoneType' object has no attribute 'write'``

Either one kills the process at the first log line, which is exactly what
happened when subserver.py was launched hidden: it bound port 7777, printed
its banner, died, and the port went dark.  ``harden()`` re-points missing
streams at a rolling log file before anything else writes.
"""

import os
import sys

LOGDIR = str(Path(__file__).resolve().parent.parent / "09_logs")


def harden(tag: str, also_file: bool = True):
    """Make sys.stdout / sys.stderr printable even under pythonw.exe."""
    os.makedirs(LOGDIR, exist_ok=True)
    path = os.path.join(LOGDIR, f"{tag}.out.log")

    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            try:
                f = open(path, "a", encoding="utf-8", errors="replace", buffering=1)
            except Exception:
                import io
                f = io.StringIO()
            setattr(sys, name, f)

    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    if also_file and not getattr(harden, "_teed", False):
        harden._teed = True
        try:
            _tee(tag)
        except Exception:
            pass


class _Tee:
    """Copy everything written to stdout/stderr into one timestamped file."""

    def __init__(self, *streams):
        self.streams = [s for s in streams if s is not None]

    def write(self, data):
        for s in self.streams:
            try:
                s.write(data)
                s.flush()
            except Exception:
                pass
        return len(data)

    def flush(self):
        for s in self.streams:
            try:
                s.flush()
            except Exception:
                pass

    def reconfigure(self, **kw):
        for s in self.streams:
            try:
                s.reconfigure(**kw)
            except Exception:
                pass

    def isatty(self):
        return False


def _tee(tag: str):
    # NOTE: the filename must NOT be "<tag>.log" -- every vpnkit script already
    # writes its own structured "<tag>.log" through its own log() function.
    # Pointing the tee at the same path made every line land twice (once via
    # log(), once via print->tee), which read like the pipeline ran two rounds.
    path = os.path.join(LOGDIR, f"{tag}.console.log")
    f = open(path, "a", encoding="utf-8", errors="replace", buffering=1)
    sys.stdout = _Tee(sys.stdout, f)
    sys.stderr = _Tee(sys.stderr, f)
