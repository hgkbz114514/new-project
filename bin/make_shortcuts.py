# make_shortcuts.py — 在桌面创建 vpnkit 快捷方式
# 用法: python bin\make_shortcuts.py
#
# 为什么用 -EncodedCommand 而不是直接拼命令行：
#   Windows 上 CreateShortcut 只能通过 WScript.Shell COM 调用，而中文文件名
#   经 cmd/powershell 命令行传递时会按 OEM 代码页(936/GBK)重新编码，
#   非 GBK 可表示字符会变成 '?'。改成把整段脚本以 UTF-16LE + base64 传入，
#   全程走 Unicode，任何字符都不会丢。
import base64
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESK = Path(os.environ["USERPROFILE"]) / "Desktop"
ICO = ROOT / "vpnkit.ico"


def find_python(console: bool) -> str:
    """返回 python.exe / pythonw.exe 的绝对路径。

    pythonw 用于「秒开」快捷方式：它没有控制台，双击不会闪黑窗口。
    找不到具体版本就退回 PATH 里的名字，由系统解析。
    """
    exe = "python.exe" if console else "pythonw.exe"
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python"
    for ver in ("313", "312", "311", "310", "39", "38"):
        cand = local / f"Python{ver}" / exe
        if cand.exists():
            return str(cand)
    found = shutil.which(exe)
    return found or exe


PY = find_python(console=True)
PYW = find_python(console=False)
ICON = str(ICO) if ICO.exists() else f"{PY},0"


def q(s: str) -> str:
    """PowerShell 单引号字符串字面量。"""
    return "'" + s.replace("'", "''") + "'"


def build():
    # (文件名, 目标, 参数, 工作目录, 描述, 窗口样式 1=普通 7=最小化)
    plan = [
        ("vpnkit 控制台.lnk", str(ROOT / "vpnkit.bat"), "", str(ROOT),
         "vpnkit — VPN 控制台（启动/关闭/状态/测速/刷新）", 1),
        ("vpnkit 秒开.lnk", PYW, f'"{ROOT / "bin" / "vpn.py"}" start', str(ROOT),
         "vpnkit — 静默启动 VPN（无窗口，双击后约 20 秒生效）", 7),
    ]

    lines = ['$ErrorActionPreference = "Stop"', "$sh = New-Object -ComObject WScript.Shell"]
    for name, target, args, wd, desc, style in plan:
        lnk = str(DESK / name)
        lines += [
            f"$s = $sh.CreateShortcut({q(lnk)})",
            f"$s.TargetPath       = {q(target)}",
            f"$s.Arguments        = {q(args)}",
            f"$s.WorkingDirectory = {q(wd)}",
            f"$s.IconLocation     = {q(ICON)}",
            f"$s.Description      = {q(desc)}",
            f"$s.WindowStyle      = {style}",
            "$s.Save()",
            f'Write-Output ("OK  " + {q(name)})',
        ]

    script = "\r\n".join(lines) + "\r\n"
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = (r.stdout or "").strip()
    if out:
        print(out)
    if r.returncode != 0:
        err = (r.stderr or "").strip()
        if err:
            print(err, file=sys.stderr)
        return 1
    return 0


def verify():
    """回读两个快捷方式，确认字段真的写进去了。"""
    lines = ['$ErrorActionPreference = "Stop"', "$sh = New-Object -ComObject WScript.Shell"]
    for name in ("vpnkit 控制台.lnk", "vpnkit 秒开.lnk"):
        lnk = str(DESK / name)
        lines += [
            f"$s = $sh.CreateShortcut({q(lnk)})",
            f'Write-Output ("--- " + {q(name)})',
            'Write-Output ("  Target : " + $s.TargetPath)',
            'Write-Output ("  Args   : " + $s.Arguments)',
            'Write-Output ("  WorkDir: " + $s.WorkingDirectory)',
            'Write-Output ("  Icon   : " + $s.IconLocation)',
            'Write-Output ("  Style  : " + $s.WindowStyle)',
        ]
    enc = base64.b64encode(("\r\n".join(lines) + "\r\n").encode("utf-16-le")).decode("ascii")
    r = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    print((r.stdout or "").strip())


if __name__ == "__main__":
    print(f"桌面: {DESK}")
    print(f"图标: {ICON}")
    rc = build()
    if rc == 0:
        verify()
    sys.exit(rc)
