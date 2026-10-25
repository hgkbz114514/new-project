"""vkcore.py — 定位 mihomo 内核与 Clash Verge 的 geo 数据。

原来每个脚本里都写死 `Path(r"E:\\Clash Verge\\verge-mihomo.exe")`，
换台机器或换个安装位置就全部失效。集中到一处，按下面的顺序找：

  1. VK_CORE 环境变量（显式指定，永远优先）
  2. 项目自带的 bin/core/
  3. PATH 里的 verge-mihomo / mihomo / clash-meta / clash
  4. 各盘符下的常见安装目录

geo 数据（geoip.dat / geosite.dat / Country.mmdb / ASN.mmdb）同理：
内核缺这些文件会尝试联网下载，在受限网络里必然超时，所以必须从
Clash Verge 的 resources 目录复制过来。找不到就返回 None，让调用方跳过复制。
"""
import os
import string
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "bin"

CORE_NAMES = ("verge-mihomo.exe", "verge-mihomo", "mihomo.exe", "mihomo",
              "clash-meta.exe", "clash-meta", "clash.exe")

# Clash Verge 安装目录相对各盘符根的候选路径
CORE_SUBDIRS = (
    "Clash Verge",
    "Program Files/Clash Verge",
    "Program Files (x86)/Clash Verge",
)

GEO_FILES = ("geoip.dat", "geosite.dat", "Country.mmdb", "ASN.mmdb",
             "geoip.metadb", "GeoSite.dat")


def _drives():
    """枚举存在的盘符根目录。"""
    out = []
    for letter in string.ascii_uppercase:
        root = Path(f"{letter}:/")
        try:
            if root.exists():
                out.append(root)
        except OSError:
            continue
    return out


def _candidates():
    """产出所有可能的内核路径，按优先级。"""
    for n in CORE_NAMES:
        yield BIN / "core" / n

    path_dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    for d in path_dirs:
        for n in CORE_NAMES:
            yield Path(d) / n

    roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Clash Verge",
        Path(os.environ.get("PROGRAMFILES", "")) / "Clash Verge",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Clash Verge",
    ]
    for r in roots:
        for n in CORE_NAMES:
            yield r / n

    for drive in _drives():
        for sub in CORE_SUBDIRS:
            for n in CORE_NAMES:
                yield drive / sub.replace("/", os.sep) / n


def find_core() -> Path:
    """返回内核路径；找不到时返回一个不存在的 Path（用 .exists() 判断）。"""
    explicit = os.environ.get("VK_CORE")
    if explicit:
        return Path(explicit)

    for p in _candidates():
        try:
            if p.is_file():
                return p
        except OSError:
            continue

    name = CORE_NAMES[0] if os.name == "nt" else CORE_NAMES[1]
    return Path(name)


def find_geo_dir():
    """返回 Clash Verge 的 resources 目录（含 geo 数据），找不到返回 None。"""
    explicit = os.environ.get("VK_GEO")
    if explicit:
        p = Path(explicit)
        return p if p.is_dir() else None

    roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Clash Verge",
        Path(os.environ.get("PROGRAMFILES", "")) / "Clash Verge",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Clash Verge",
    ]
    for drive in _drives():
        for sub in CORE_SUBDIRS:
            roots.append(drive / sub.replace("/", os.sep))

    for r in roots:
        for probe in (r / "resources", r):
            try:
                if probe.is_dir() and any((probe / f).exists() for f in GEO_FILES):
                    return probe
            except OSError:
                continue
    return None


def find_verge_exe():
    """返回 Clash Verge 图形界面的 exe，找不到返回 None。"""
    names = ("clash-verge.exe", "Clash Verge.exe", "clash-verge")
    for p in _candidates():
        for n in names:
            cand = p.parent / n
            try:
                if cand.is_file():
                    return cand
            except OSError:
                continue
    return None


CORE = find_core()


if __name__ == "__main__":
    print(f"内核     : {CORE}" + ("" if CORE.exists() else "  (未找到)"))
    geo = find_geo_dir()
    print(f"geo 目录 : {geo if geo else '(未找到)'}")
    if geo:
        for f in GEO_FILES:
            p = geo / f
            if p.exists():
                print(f"  {f:<16} {p.stat().st_size:>10,} B")
    verge = find_verge_exe()
    print(f"图形界面 : {verge if verge else '(未找到)'}")
