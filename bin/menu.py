from pathlib import Path
# -*- coding: utf-8 -*-
"""vpnkit interactive console.

Why this is Python and not a .bat
--------------------------------
The first version of the menu was a pure batch file. It broke on this machine:
cmd.exe parses a batch file with the *OEM* codepage (936/GBK here) and re-reads
it line by line, so a UTF-8 encoded full-width character like `，` (EF BC 8C) is
consumed as a GBK lead byte pair and the *next* ASCII byte gets swallowed too.
The byte stream desynchronises and a line like

    echo   [8] 启动本地订阅服务（后台常驻，客户端一条订阅永久更新）

gets split in the middle, so cmd tries to run `更新）` as a command. `chcp 65001`
does not save you, because the desync already happened while locating the line.

Keeping every Chinese character out of echo lines works but is fragile. Doing the
whole menu in Python removes the parser from the equation: the batch file is now
three lines of pure ASCII and this file owns all the text.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = str(Path(__file__).resolve().parent.parent)
BIN = os.path.join(ROOT, "bin")
PROF = os.path.join(ROOT, "profiles")
PY = sys.executable
PYW = os.path.join(os.path.dirname(PY), "pythonw.exe")
from vkcore import find_core, find_verge_exe

MIHOMO = find_core()
VERGE = find_verge_exe()

MENU = """
============================================================
   vpnkit  电脑端 VPN 控制台
============================================================

  [1] 启动 VPN - 打开系统代理
  [2] 关闭 VPN - 恢复系统代理
  [3] 查看状态 / 当前出口 / 实测带宽
  [4] 测速 - 出口 IP + 关键站点
  [5] 刷新节点池 - 约 8-15 分钟, 含实测带宽

  [6] 安装开机自启 - 守护 + 登录自动启动
  [7] 卸载开机自启

  [8] 启动本地订阅服务 - 客户端一条订阅永久更新
  [9] 查看订阅服务状态
  [k] Claude 出口 - 钉死一个 IP 给 Claude 验证用
  [d] 交付报告 - 分组 / 最快节点 / 国家覆盖
  [a] 打开配置目录
  [b] 校验配置能否被内核吃下
  [c] 打开 Clash Verge 图形界面 - 想开 TUN 全局用这个

  [0] 退出
"""


def claude_menu():
    """Claude/Anthropic 出口管理。

    为什么要单独一个入口：Anthropic 在封号理由里明写"从不支持的位置创建账户"，
    KYC 过程中出口 IP 一跳国家 Persona 就判失败。所以这条链上的域名走的是
    select 型的 CLAUDE 组，第一个成员固定 = 一个 IP 不动的节点。
    """
    import json
    pinf = os.path.join(PROF, "claude_pin.json")
    print("\n" + "=" * 58)
    print("  Claude / Anthropic 出口")
    print("=" * 58)
    if os.path.exists(pinf):
        try:
            pin = json.load(open(pinf, encoding="utf-8"))
            print(f"  当前钉住  : {pin.get('name')}")
            print(f"  出口国家  : {pin.get('cc')}")
            print(f"  钉住时间  : {pin.get('since')}")
            print(f"  稳定性    : {pin.get('wins', '?')}/3 轮")
            print(f"  api/web   : {pin.get('api_code')} / {pin.get('web_code')}")
            print(f"  备选数量  : {len(pin.get('candidates') or [])}")
        except Exception as e:
            print(f"  pin 文件读取失败: {e}")
    else:
        print("  还没钉过 —— 选 [1] 生成")
    print("=" * 58)
    print("  [1] 重新扫描并钉一个（约 3-5 分钟）")
    print("  [2] 只查看当前状态")
    print("  [3] 重启并强制钉回配置里的那个")
    print("  [0] 返回")
    try:
        c = input("\n请选择: ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if c == "1":
        print("\n扫描中：每个节点要过 api.anthropic.com(401) + claude.ai(403) 双门禁，")
        print("命中后再做 3 轮稳定性复测，只有过 2 轮以上的才会被钉住。\n")
        run("claude_pin.py", "--top", "70", "--threads", "14", "--force")
        print()
        if run("build_config.py").returncode == 0:
            run("vk.py", "deploy")
            run("vpn.py", "stop")
            run("vpn.py", "start")
    elif c == "2":
        run("vpn.py", "status")
    elif c == "3":
        run("vpn.py", "stop")
        run("vpn.py", "start")


def run(*args, **kw):
    return subprocess.run([PY, os.path.join(BIN, args[0])] + list(args[1:]), **kw)


def pause():
    try:
        input("\n按回车返回菜单...")
    except (EOFError, KeyboardInterrupt):
        pass


def sub_up() -> bool:
    """Is the local subscription server answering?"""
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:7777/health", timeout=4) as r:
            return r.status == 200
    except Exception:
        return False


def sub_status():
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:7777/status", timeout=6) as r:
            print(r.read().decode("utf-8", "replace"))
    except Exception:
        print("订阅服务没在跑 —— 用 [8] 启动")


def sub_start():
    if sub_up():
        print("订阅服务已经在跑: http://127.0.0.1:7777/sub")
        return
    exe = PYW if os.path.exists(PYW) else PY
    subprocess.Popen([exe, os.path.join(BIN, "subserver.py")],
                     cwd=BIN, creationflags=subprocess.CREATE_NO_WINDOW)
    import time
    for _ in range(20):
        time.sleep(0.5)
        if sub_up():
            print("订阅服务已启动: http://127.0.0.1:7777/sub")
            sub_status()
            return
    print("启动失败 —— 看 E:\\vpnkit\\09_logs\\subserver.console.log")


def open_verge():
    """Hand the proxy over to the Clash Verge GUI.

    Our own kernel binds 7897 and the GUI's bundled core wants the same port,
    so launching the GUI while ours runs leaves the GUI with a dead core.
    Stop ours first, then start the GUI: it picks up the already-deployed
    vpnkit-* profile itself and you get the tray icon + TUN switch.
    """
    if not os.path.exists(VERGE):
        print("没找到 " + VERGE)
        return
    print("先把命令行内核停掉，避免和 GUI 抢 7897 ...")
    run("vpn.py", "stop")
    subprocess.Popen([VERGE])
    print("Clash Verge 已启动。")
    print("  配置页选 vpnkit-* 那条 -> 打开「系统代理」或「TUN 模式」")
    print("  想切回命令行模式：先退出 Clash Verge，再用 [1]")


def validate():
    """Ask the real kernel whether the generated config is loadable."""
    if not os.path.exists(MIHOMO):
        print("没找到 " + MIHOMO)
        return
    subprocess.run([MIHOMO, "-t", "-f", os.path.join(PROF, "vpnkit.yaml"),
                    "-d", PROF])


def main():
    try:
        from vkstdio import harden
        harden("menu", also_file=False)
    except Exception:
        pass

    actions = {
        "1": lambda: run("vpn.py", "start"),
        "2": lambda: run("vpn.py", "stop"),
        "3": lambda: run("vpn.py", "status"),
        "4": lambda: run("vpn.py", "test"),
        "5": lambda: run("vpn.py", "refresh"),
        "6": lambda: run("vpn.py", "autostart", "install"),
        "7": lambda: run("vpn.py", "autostart", "remove"),
        "8": sub_start,
        "9": sub_status,
        "k": claude_menu,
        "d": lambda: run("report.py"),
        "a": lambda: subprocess.run(["explorer", PROF]),
        "b": validate,
        "c": open_verge,
    }

    while True:
        print(MENU)
        try:
            c = input("请选择: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if c in ("0", "q", "exit", ""):
            return
        fn = actions.get(c)
        if not fn:
            continue
        try:
            fn()
        except KeyboardInterrupt:
            pass
        except Exception as e:
            print(f"出错: {e}")
        if c in actions:
            pause()


if __name__ == "__main__":
    main()
