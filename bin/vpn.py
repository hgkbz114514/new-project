# -*- coding: utf-8 -*-
"""
vpnkit :: 一键启动器（不依赖 Clash Verge 的图形界面）

  python vpn.py start      启动内核 + 打开系统代理
  python vpn.py stop       关闭内核 + 恢复系统代理
  python vpn.py status     看状态 / 当前出口
  python vpn.py test       测出口与几个关键站点
  python vpn.py refresh    立刻重跑一整轮（采集->测活->生成->重启内核）
  python vpn.py autostart install|remove   开机自启（计划任务，无需管理员）

系统代理走注册表 HKCU，浏览器 / Electron 应用 / 大部分桌面软件立刻生效。
需要全局（游戏、UDP、命令行原生程序）时，仍可在 Clash Verge 里开 TUN（需管理员）。
"""
import ctypes, json, os, subprocess, sys, time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PROF = ROOT / "profiles"
BIN = ROOT / "bin"
LOG = ROOT / "09_logs" / "vpn.log"
CONF = PROF / "vpnkit.yaml"
PIDF = PROF / ".vpn.pid"
PY = sys.executable
MIXED = 7897
CTRL = 9097


def find_core() -> Path:
    """定位 mihomo 内核，找不到返回 Path 但 .exists() 为假。

    搜索顺序：
      1. VK_CORE 环境变量（显式指定，优先）
      2. 项目自带的 bin/core/
      3. PATH 里的 verge-mihomo / mihomo / clash-meta
      4. Clash Verge 的默认安装位置（%LOCALAPPDATA%、%PROGRAMFILES%、D:、E: 等）
    """
    explicit = os.environ.get("VK_CORE")
    if explicit:
        return Path(explicit)

    names = ("verge-mihomo.exe", "mihomo.exe", "clash-meta.exe", "clash.exe")

    # 项目自带
    for n in names:
        p = BIN / "core" / n
        if p.exists():
            return p

    # PATH
    for n in names:
        for d in os.environ.get("PATH", "").split(os.pathsep):
            if not d:
                continue
            p = Path(d) / n
            try:
                if p.exists():
                    return p
            except OSError:
                continue

    # Clash Verge 常见安装位置
    roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Clash Verge",
        Path(os.environ.get("PROGRAMFILES", "")) / "Clash Verge",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Clash Verge",
        Path("C:/Clash Verge"),
        Path("D:/Clash Verge"),
        Path("E:/Clash Verge"),
    ]
    for r in roots:
        for n in names:
            p = r / n
            try:
                if p.exists():
                    return p
            except OSError:
                continue

    # 都没找到：返回首选名，让调用方报错时能打印出该找什么
    return Path(os.environ.get("VK_CORE") or "verge-mihomo.exe")


MIHOMO = find_core()
# build_config.py 生成 vpnkit.yaml 时把 secret 写成空串（见其 "secret": ""），
# 所以这里不带 Authorization 头；留成常量是为了将来给控制器加密码时只改一处。
SECRET = ""
TASKNAME = "vpnkit-guard"


def log(m):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {m}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ------------------------------------------------------------------ 系统代理
def _refresh_wininet():
    try:
        inet = ctypes.windll.wininet
        INTERNET_OPTION_SETTINGS_CHANGED = 39
        INTERNET_OPTION_REFRESH = 37
        inet.InternetSetOptionW(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
        inet.InternetSetOptionW(0, INTERNET_OPTION_REFRESH, 0, 0)
    except Exception:
        pass


def set_sys_proxy(on: bool, server=f"127.0.0.1:{MIXED}"):
    import winreg
    key = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0,
                        winreg.KEY_READ | winreg.KEY_WRITE) as k:
        if on:
            winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(k, "ProxyServer", 0, winreg.REG_SZ, server)
            try:
                winreg.SetValueEx(k, "ProxyOverride", 0, winreg.REG_SZ,
                                  "localhost;127.*;10.*;172.16.*;172.17.*;172.18.*;"
                                  "172.19.*;172.20.*;172.21.*;172.22.*;172.23.*;"
                                  "172.24.*;172.25.*;172.26.*;172.27.*;172.28.*;"
                                  "172.29.*;172.30.*;172.31.*;192.168.*;<local>")
            except Exception:
                pass
        else:
            winreg.SetValueEx(k, "ProxyEnable", 0, winreg.REG_DWORD, 0)
    _refresh_wininet()
    log(f"系统代理 {'开启 -> ' + server if on else '关闭'}")


def proxy_enabled():
    import winreg
    key = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            return winreg.QueryValueEx(k, "ProxyEnable")[0] == 1
    except Exception:
        return False


# ------------------------------------------------------------------ 内核
def running_pid():
    if PIDF.exists():
        try:
            pid = int(PIDF.read_text().strip())
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                 capture_output=True, text=True)
            if "verge-mihomo" in out.stdout.lower() or "mihomo" in out.stdout.lower():
                return pid
        except Exception:
            pass
    return None


def port_listening(port):
    """True only if THIS port has a LISTENING socket on loopback.

    Do not test `f":{port} " in stdout and "LISTENING" in stdout` -- the
    "LISTENING" half matches anywhere in the whole netstat dump, so one open
    port makes every port look open (that bug hid a dead core for a while).
    """
    r = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    for line in (r.stdout or "").splitlines():
        # columns: Proto  Local Address  Foreign Address  State  PID
        parts = line.split()
        if len(parts) < 4 or parts[0].upper() != "TCP":
            continue
        if parts[3].upper() != "LISTENING":
            continue
        if parts[1].rsplit(":", 1)[-1] == str(port):
            return True
    return False


def resolve_pin_name(pin):
    """Map the pinned key (server:port) to the node name in the CURRENT config.

    Node names embed a rebuild counter (`046 IT 286ms ss`), and every
    build_config run renumbers them. Pinning by name therefore breaks on the
    first rebuild -- exactly what happened when the pin was `006 IT` and the
    rebuild produced `046 IT`. The key is stable, so resolve through it.
    """
    key = str(pin.get("key") or "")
    name = str(pin.get("name") or "")
    if not key:
        return name                     # legacy pin without a key
    try:
        cfg = yaml.safe_load((PROF / "vpnkit.yaml").read_text(encoding="utf-8"))
    except Exception:
        return name
    for p in cfg.get("proxies") or []:
        if f"{p.get('server')}:{p.get('port')}" == key:
            return str(p.get("name") or name)
    return name


def enforce_pin():
    """Force the CLAUDE group back onto the pinned node after a restart.

    mihomo persists the last manual group selection in cache.db, so a restart
    silently restores whatever was picked by hand -- measured: the pin was
    001 US but the group came back on 000 US. For Claude that is not cosmetic:
    a different node means a different exit IP, which is exactly what breaks
    KYC. Re-assert the selection every time the core starts.
    """
    pinf = PROF / "claude_pin.json"
    if not pinf.exists():
        return
    try:
        pin = json.loads(pinf.read_text(encoding="utf-8"))
    except Exception:
        return
    name = resolve_pin_name(pin)
    if not name:
        return
    import requests
    hdr = {"Authorization": f"Bearer {SECRET}"} if SECRET else {}
    try:
        got = requests.get(f"http://127.0.0.1:{CTRL}/proxies/CLAUDE",
                           headers=hdr, timeout=6).json().get("now")
    except Exception:
        return
    if got == name:
        log(f"CLAUDE 已固定在 {name}")
        return
    try:
        requests.put(f"http://127.0.0.1:{CTRL}/proxies/CLAUDE",
                     headers=hdr, json={"name": name}, timeout=8)
        log(f"CLAUDE 重新钉回 {name}（此前被缓存成 {got}）")
    except Exception as e:
        log(f"!! CLAUDE 钉回失败: {type(e).__name__}")


def enforce_proxy_choice():
    """Reset the PROXY group to the first member listed in the config.

    mihomo persists every manual group selection in cache.db, so a restart
    silently restores whatever was clicked last time. That is how PROXY stayed
    on SPEED-TOP even after the config started listing AUTO-FAST first -- and
    SPEED-TOP is a `select` group, so it never fails over on its own. On
    2026-10-25 exactly that combination left PROXY glued to a dead vless node
    and broke every site except github/bilibili.

    AUTO-FAST is a url-test group: it re-measures every 240s and switches by
    itself, and its candidate set is already filtered by throughput + TLS. So
    "first entry wins" is the right policy here, and this function makes the
    config's ordering actually take effect.
    """
    want = "AUTO-FAST"
    try:
        cfg = yaml.safe_load(CONF.read_text(encoding="utf-8"))
    except Exception:
        return
    for g in cfg.get("proxy-groups") or []:
        if g.get("name") == "PROXY":
            first = (g.get("proxies") or [None])[0]
            if isinstance(first, str):
                want = first
            break
    import requests
    hdr = {"Authorization": f"Bearer {SECRET}"} if SECRET else {}
    try:
        got = requests.get(f"http://127.0.0.1:{CTRL}/proxies/PROXY",
                           headers=hdr, timeout=6).json().get("now")
    except Exception:
        return
    if got == want:
        return
    try:
        requests.put(f"http://127.0.0.1:{CTRL}/proxies/PROXY",
                     headers=hdr, json={"name": want}, timeout=8)
        log(f"PROXY 重置为 {want}（此前被缓存成 {got}）")
    except Exception as e:
        log(f"!! PROXY 重置失败: {type(e).__name__}")


def start():
    pid = running_pid()
    if pid:
        log(f"内核已在运行 pid={pid}")
    else:
        if not CONF.exists():
            log("!! 缺少 profiles/vpnkit.yaml —— 先跑  python vk.py quick 或 refresh")
            return False
        probe = subprocess.run([str(MIHOMO), "-t", "-f", str(CONF), "-d", str(PROF)],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=120)
        if probe.returncode != 0:
            log("!! 配置校验失败")
            log((probe.stdout or "")[-800:])
            return False
        logf = ROOT / "09_logs" / "mihomo.out.log"
        errf = ROOT / "09_logs" / "mihomo.err.log"
        p = subprocess.Popen([str(MIHOMO), "-f", str(CONF), "-d", str(PROF)],
                             stdout=open(logf, "w", encoding="utf-8"),
                             stderr=open(errf, "w", encoding="utf-8"),
                             creationflags=subprocess.CREATE_NO_WINDOW)
        PIDF.write_text(str(p.pid), encoding="utf-8")
        log(f"内核启动 pid={p.pid}")
        for _ in range(40):
            if port_listening(MIXED):
                break
            time.sleep(0.25)
        else:
            log("!! 内核没能在 10s 内监听端口")
            log(errf.read_text(encoding="utf-8", errors="replace")[-600:])
            return False
        log(f"端口 {MIXED} 已监听")

    enforce_pin()
    enforce_proxy_choice()
    set_sys_proxy(True)
    print()
    test(quick=True)
    print()
    log("启动完成 —— 浏览器直接可用；想全局请开 Clash Verge 的 TUN 模式")
    return True


def stop():
    pid = running_pid()
    if pid:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        log(f"内核已停止 pid={pid}")
    try:
        PIDF.unlink()
    except Exception:
        pass
    set_sys_proxy(False)
    log("已关闭")


def pool_size():
    """Freshest pool, using build_config's own priority order so the number
    shown here matches the config that is actually loaded."""
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


def speed_summary():
    """How many nodes were measured above the tier thresholds."""
    p = PROF / "nodes_fast.json"
    if not p.exists():
        return None
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    ks = [float(r.get("kbps") or 0) for r in rows]
    if not ks:
        return None
    return {"tested": len(ks),
            "ge500": sum(1 for k in ks if k >= 500),
            "ge300": sum(1 for k in ks if k >= 300),
            "ge100": sum(1 for k in ks if k >= 100),
            "max": max(ks)}


def status():
    pid = running_pid()
    print("=" * 60)
    print(f"内核      : {'运行中 pid=' + str(pid) if pid else '未运行'}")
    print(f"混合端口  : {MIXED}  {'LISTEN' if port_listening(MIXED) else 'NOT LISTEN'}")
    print(f"控制端口  : {CTRL}  {'LISTEN' if port_listening(CTRL) else 'NOT LISTEN'}")
    print(f"系统代理  : {'开' if proxy_enabled() else '关'}")
    n, src = pool_size()
    print(f"节点数    : {n}" + (f"  (来自 {src})" if src else ""))
    sp = speed_summary()
    if sp:
        print(f"实测带宽  : 最快 {sp['max']:.0f} KB/s  |  "
              f">=500 {sp['ge500']} 个  >=300 {sp['ge300']} 个  >=100 {sp['ge100']} 个  "
              f"(共测 {sp['tested']})")
    if CONF.exists():
        print(f"配置文件  : {CONF}  {CONF.stat().st_size/1024:.0f} KB  "
              f"{time.strftime('%m-%d %H:%M', time.localtime(CONF.stat().st_mtime))}")
    if pid:
        import requests
        try:
            g = requests.get(f"http://127.0.0.1:{CTRL}/proxies/PROXY", timeout=4).json()
            print(f"当前分组  : {g.get('now')}")
        except Exception:
            print("当前分组  : 查询失败")
        print(f"当前出口  : {exit_ip()}")
        print(f"Claude 出口: {claude_pin_state()}")
    print("=" * 60)


def claude_pin_state():
    """One line showing the pinned Claude node and whether it is in force."""
    pinf = PROF / "claude_pin.json"
    if not pinf.exists():
        return "未钉死（跑 python claude_pin.py --force 生成）"
    try:
        pin = json.loads(pinf.read_text(encoding="utf-8"))
    except Exception:
        return "pin 文件损坏"
    name = resolve_pin_name(pin) or "?"
    cc = str(pin.get("cc") or "?")
    since = str(pin.get("since") or "?")
    line = f"{name}  ({cc}, 自 {since}"
    if pin.get("wins"):
        line += f", 稳定性 {pin['wins']}/3"
    line += ")"
    import requests
    try:
        now = requests.get(f"http://127.0.0.1:{CTRL}/proxies/CLAUDE",
                           timeout=4).json().get("now")
    except Exception:
        return line + "  [组状态未知]"
    if now != name:
        return line + f"  !! 当前组选的是 {now}"
    return line


def exit_ip():
    """Best-effort exit IP, printed as one friendly line.

    ip-api.com gives country+ISP but is HTTP-only and routinely needs >8s once
    the selected node is a slow one, which made `status` print a raw
    requests traceback under a line the user reads every time. Try the cheap
    Cloudflare trace first (same endpoint the kernel itself uses, HTTPS, tiny
    body), then fall back to ip-api, then give up quietly.
    """
    import requests
    px = {"http": f"http://127.0.0.1:{MIXED}", "https": f"http://127.0.0.1:{MIXED}"}
    try:
        r = requests.get("https://www.cloudflare.com/cdn-cgi/trace", proxies=px,
                         timeout=6)
        if r.status_code == 200:
            d = dict(line.split("=", 1) for line in r.text.splitlines() if "=" in line)
            if d.get("ip"):
                return f"{d['ip']}  {d.get('loc', '?')}  ({d.get('colo', '?')})"
    except Exception:
        pass
    try:
        r = requests.get("http://ip-api.com/json/?fields=query,country,isp",
                         proxies=px, timeout=6)
        j = r.json()
        if j.get("query"):
            return f"{j['query']}  {j.get('country', '?')}  {j.get('isp', '?')}"
    except Exception:
        pass
    return "查询超时（节点慢, 不影响使用）"


def test(quick=False):
    import requests
    px = {"http": f"http://127.0.0.1:{MIXED}", "https": f"http://127.0.0.1:{MIXED}"}
    print(f"出口 IP   : {exit_ip()}")
    sites = ["https://www.gstatic.com/generate_204",
             "https://github.com",
             "https://www.youtube.com",
             "https://api.openai.com/v1/models"]
    if quick:
        sites = sites[:2]
    for u in sites:
        t0 = time.time()
        try:
            r = requests.get(u, proxies=px, timeout=12, allow_redirects=True)
            ms = int((time.time() - t0) * 1000)
            ok = "OK " if r.status_code < 400 or r.status_code in (401, 403) else "?? "
            print(f"  {ok}{r.status_code}  {ms:5d}ms  {u}")
        except Exception as e:
            print(f"  XX  ----  {u}  {type(e).__name__}")


def autostart(action):
    if action == "install":
        cmd = f'"{PY}" "{BIN / "guard.py"}" --once'
        r = subprocess.run(["schtasks", "/Create", "/TN", TASKNAME, "/TR", cmd,
                            "/SC", "HOURLY", "/MO", "4", "/F"], capture_output=True,
                           text=True, encoding="gbk", errors="replace")
        log(f"计划任务安装 rc={r.returncode} {r.stdout.strip()[:200]}{r.stderr.strip()[:200]}")
        # 登录时也启动内核
        ps = (f"$a=New-Object -ComObject WScript.Shell;"
              f"$s=$a.CreateShortcut('{Path(os.environ['APPDATA'])/'Microsoft'/'Windows'/'Start Menu'/'Programs'/'Startup'/'vpnkit.lnk'}');"
              f"$s.TargetPath='{PY}';$s.Arguments='\"{BIN/'vpn.py'}\" start';"
              f"$s.WorkingDirectory='{BIN}';$s.WindowStyle=7;$s.Save()")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True)
        log("已添加上登录启动快捷方式 vpnkit.lnk")
    elif action == "remove":
        subprocess.run(["schtasks", "/Delete", "/TN", TASKNAME, "/F"],
                       capture_output=True, text=True)
        lnk = Path(os.environ['APPDATA']) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs' / 'Startup' / 'vpnkit.lnk'
        if lnk.exists():
            lnk.unlink()
        log("已移除自启")


def refresh():
    """Re-harvest, re-probe, re-measure, rebuild and hot-redeploy.

    Runs the same chain as guard.py so the manual menu and the scheduled
    watchdog can never drift apart again.
    """
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
               VK_TIMEOUT="5000", VK_CONC="128", VK_ROUNDS="2",
               VK_MIN_NODES="20")

    def step(s, *a, timeout=7200):
        log(f"▶ {s} {' '.join(a)}".rstrip())
        try:
            p = subprocess.run([PY, str(BIN / s), *a], cwd=str(BIN), env=env,
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            log(f"  ! {s} 超时")
            return False
        for l in (p.stdout or "").strip().splitlines()[-2:]:
            log("  | " + l)
        return p.returncode == 0

    if not step("harvest.py"):
        log("!! 采集失败，保留现有配置")
        return
    if not step("probe.py", "900"):
        log("!! TCP 体检失败，保留现有配置")
        return
    if not step("test_live2.py"):
        log("!! 测活失败，保留现有配置")
        return

    step("build_config.py")
    step("speed_probe.py", "--top", "80", "--bytes", "2000000",
         "--threads", "24", timeout=2400)
    step("ghscan.py", "--top", "80", "--threads", "16", timeout=2400)
    if not step("build_config.py"):
        log("!! 生成配置失败，保留现有配置")
        return

    step("vk.py", "deploy")
    log("配置已更新，重启内核加载")
    stop()
    start()


def doctor():
    """体检：内核在哪、配置能不能被吃下、端口是否空闲。

    setup.bat 靠它的退出码判断要不要提示用户去装 Clash Verge，所以
    「配置缺失」不能算失败 —— 全新克隆时它必然缺失，那是正常的下一步，
    不是环境问题。只有「内核找不到」和「依赖缺失」才是真失败。
    """
    ok = True

    print(f"项目根   : {ROOT}")
    has_conf = CONF.exists()
    print(f"配置     : {CONF}  " + ("存在" if has_conf else "还没有（跑完 vk.py all 就有了）"))

    print(f"内核     : {MIHOMO}")
    if MIHOMO.exists():
        try:
            r = subprocess.run([str(MIHOMO), "-v"], capture_output=True,
                               text=True, timeout=15,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            ver = (r.stdout or r.stderr or "").strip().splitlines()
            print(f"版本     : {ver[0] if ver else '（无输出）'}")
        except Exception as e:
            print(f"版本     : 读取失败 {e}")
    else:
        print("           未找到 —— 装 Clash Verge Rev，或 set VK_CORE=<路径>")
        ok = False

    print(f"Python   : {sys.version.split()[0]}  ({sys.executable})")
    try:
        import yaml as _y  # noqa: F401
        import requests as _r  # noqa: F401
        print(f"依赖     : requests {_r.__version__} / pyyaml {_y.__version__}")
    except Exception as e:
        print(f"依赖     : 缺失 {e}")
        ok = False

    for p in (MIXED, CTRL):
        busy = port_listening(p)
        print(f"端口 {p:<5}: " + ("占用（内核已在跑，正常）" if busy else "空闲"))
    pid = running_pid()
    if pid:
        print(f"内核进程 : 运行中 pid={pid}")

    print()
    if not ok:
        print("体检未通过，按上面提示处理")
        return 1
    if not has_conf:
        print("环境就绪 —— 下一步跑 python bin\\vk.py all 生成配置（约 6 分钟）")
    else:
        print("体检通过 —— 可以 python bin\\vpn.py start")
    return 0


def main():
    # vpnkit.lnk in Startup may launch this windowless; harden before printing.
    try:
        from vkstdio import harden
        harden("vpn")
    except Exception:
        pass
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "start":
        start()
    elif cmd == "stop":
        stop()
    elif cmd == "status":
        status()
    elif cmd == "test":
        test()
    elif cmd == "refresh":
        refresh()
    elif cmd == "autostart":
        autostart(sys.argv[2] if len(sys.argv) > 2 else "install")
    elif cmd == "doctor":
        sys.exit(doctor())
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
