"""lint_bat.py — 检查仓库里的 .bat 是否会踩 cmd.exe 的那些坑。

cmd.exe 有几个反直觉行为，全都实际害过这个项目：

1. `echo ... ->  ...` 里的 `>` 是重定向符。`echo [1/4] Python %PYVER% -> %PY%`
   实际执行成 `echo ... - 1>"C:\\...\\python.exe"`，直接去截断一个正在运行的
   python.exe，并且那行字永远打不出来。

2. `chcp 65001` 救不了中文 .bat。cmd 按 OEM 代码页逐行重读批处理文件，
   UTF-8 全角字符会被当成 GBK 双字节、吞掉后面一个 ASCII 字节，字节流错位后
   下半句变成"不是内部或外部命令"。所以 .bat 必须是纯 ASCII，中文放进 .py。

3. `for /f ... in ('"<带空格的路径>" -c "..."')` 会在内层引号处断开。

4. `set /p VAR=<file` 写在括号块里会在解析整个块时就绑定重定向，那时文件
   还没生成，报 "The system cannot find the file specified"，VAR 留空。

跑法：python bin\\lint_bat.py [目录]
退出码：0 干净 / 1 有问题
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def check_echo_redirection(path: Path, lines: list[str]) -> list[str]:
    """echo 行里出现未转义的 > 或 <。"""
    bad = []
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if not s.lower().startswith("echo"):
            continue
        # 先把转义形式 ^> ^< 去掉，剩下的裸符号才是危险的
        bare = re.sub(r"\^[<>]", "", s)
        if re.search(r"[<>]", bare):
            bad.append(f"{path.name}:{i}  echo 行里有未转义的 > 或 <：{s}")
    return bad


def check_non_ascii(path: Path) -> list[str]:
    """文件本身含非 ASCII 字节。"""
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return [f"{path.name}  有 UTF-8 BOM（cmd 会把它当成命令的一部分）"]
    try:
        raw.decode("ascii")
        return []
    except UnicodeDecodeError as e:
        line = raw[: e.start].count(b"\n") + 1
        return [f"{path.name}:{line}  含非 ASCII 字节 {raw[e.start:e.start + 8]!r}"
                f"（.bat 必须纯 ASCII，中文请放进 .py）"]


def check_for_quoted_path(path: Path, lines: list[str]) -> list[str]:
    """for /f 的 in ('"<路径>" -c "..."') 引号嵌套陷阱。

    cmd 把 in ('...') 里的内容交给 `cmd /c` 执行，内层再出现成对引号时会在
    中途断开，报 `'C:\\...\\python.exe" -c "print' 不是内部或外部命令`。
    触发形状就是「单引号后面紧跟双引号」。不管路径里有没有空格都一样危险，
    所以这里不做空格判断。
    """
    bad = []
    for i, line in enumerate(lines, 1):
        if not re.match(r"\s*for\s+/f", line, re.I):
            continue
        if re.search(r"in\s*\(\s*'\"", line):
            bad.append(f"{path.name}:{i}  for /f 的 in ('\"...\"') 会踩引号嵌套，"
                       f"改成先重定向到临时文件再 set /p：{line.strip()}")
    return bad


def check_setp_in_block(path: Path, lines: list[str]) -> list[str]:
    """set /p VAR=<file 出现在括号块内。

    只做保守判断：记录括号深度，遇到 set /p 且带 < 重定向时看深度是否 > 0。
    """
    bad = []
    depth = 0
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.lower().startswith("rem") or stripped.startswith("::"):
            continue
        if re.search(r"\bset\s+/p\b.*<", stripped, re.I) and depth > 0:
            bad.append(f"{path.name}:{i}  set /p 带重定向却写在括号块里（cmd 会提前绑定）："
                       f"{stripped}")
        depth += stripped.count("(") - stripped.count(")")
        if depth < 0:
            depth = 0
    return bad


def lint(path: Path) -> list[str]:
    raw = path.read_bytes()
    # 用 ascii+replace 解码，坏字节不会让后续检查崩掉
    lines = raw.decode("ascii", errors="replace").splitlines()
    out = []
    out += check_non_ascii(path)
    out += check_echo_redirection(path, lines)
    out += check_for_quoted_path(path, lines)
    out += check_setp_in_block(path, lines)
    return out


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    target = Path(argv[1]).resolve() if len(argv) > 1 else ROOT
    files = sorted(target.rglob("*.bat")) if target.is_dir() else [target]
    files = [f for f in files if ".git" not in f.parts]

    if not files:
        print(f"没找到 .bat 文件：{target}")
        return 0

    problems: list[str] = []
    for f in files:
        problems += lint(f)

    print(f"检查了 {len(files)} 个 .bat：{', '.join(f.name for f in files)}")
    if problems:
        print(f"\n发现 {len(problems)} 个问题：")
        for p in problems:
            print(f"  !! {p}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
