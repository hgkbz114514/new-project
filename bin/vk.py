# -*- coding: utf-8 -*-
"""
vpnkit :: 一键流水线
  python vk.py all        全流程：发现源 -> 采集 -> TCP体检 -> 真实测活 -> 出口验证 -> 生成 -> 部署
  python vk.py quick      快速：采集 -> TCP -> 测活 -> 生成 -> 部署（跳过出口验证）
  python vk.py build      只重新生成配置
  python vk.py deploy     只把已有配置部署进 Clash Verge
  python vk.py status     看当前状态
"""
import json, os, shutil, subprocess, sys, time, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin"
PROF = ROOT / "profiles"
VERGE = Path(os.environ["APPDATA"]) / "io.github.clash-verge-rev.clash-verge-rev"
PROFILES_DIR = VERGE / "profiles"
PROFILES_YAML = VERGE / "profiles.yaml"
LOG = ROOT / "09_logs" / "pipeline.log"

PY = sys.executable


def log(m):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {m}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(script, *args, timeout=3600, env_extra=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    if env_extra:
        env.update(env_extra)
    log(f"▶ {script} {' '.join(args)}")
    t0 = time.time()
    p = subprocess.run([PY, str(BIN / script), *args], cwd=str(BIN), env=env,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    tail = (p.stdout or "").strip().splitlines()[-6:]
    err = (p.stderr or "").strip().splitlines()[-4:]
    for l in tail:
        log(f"  | {l}")
    for l in err:
        log(f"  ! {l}")
    log(f"✔ {script} 完成 rc={p.returncode} 用时 {time.time()-t0:.0f}s")
    return p.returncode == 0


def deploy():
    """把 profiles/vpnkit.yaml 注册进 Clash Verge 并设为当前"""
    src = PROF / "vpnkit.yaml"
    if not src.exists():
        log("!! 缺少 profiles/vpnkit.yaml")
        return False

    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    uid = "vpnkit" + uuid.uuid4().hex[:10]
    dst = PROFILES_DIR / f"{uid}.yaml"
    shutil.copy2(src, dst)
    log(f"复制配置 -> {dst}")

    import yaml
    if PROFILES_YAML.exists():
        doc = yaml.safe_load(PROFILES_YAML.read_text(encoding="utf-8")) or {}
    else:
        doc = {}
    doc.setdefault("items", [])

    # 清掉旧的 vpnkit 条目
    doc["items"] = [it for it in doc["items"]
                    if not (isinstance(it, dict) and str(it.get("uid", "")).startswith("vpnkit"))]
    doc["items"].append({
        "uid": uid,
        "type": "remote",
        "name": f"vpnkit-{time.strftime('%m%d-%H%M')}",
        "desc": f"{time.strftime('%Y-%m-%d %H:%M')} 自动生成 / vpnkit",
        "file": f"{uid}.yaml",
        "url": "",
        "updated": int(time.time()),
        "option": {"update_interval": 0, "allow_auto_update": False,
                   "danger_accept_invalid_certs": False},
    })
    doc["current"] = uid
    PROFILES_YAML.write_text(
        yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    log(f"profiles.yaml 已更新，current = {uid}")

    # 清理旧文件
    for old in PROFILES_DIR.glob("vpnkit*.yaml"):
        if old.name != f"{uid}.yaml":
            try:
                old.unlink()
            except Exception:
                pass
    log("部署完成 → 在 Clash Verge 里点一下「刷新/切换配置」即可生效")
    return True


def status():
    print("=" * 62)
    for f in ("nodes_raw.json", "nodes_alive.json", "nodes_live.json",
              "nodes_verified.json", "vpnkit.yaml"):
        p = PROF / f
        if p.exists():
            try:
                n = len(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                n = "-"
            print(f"  {f:24s} {n:>6} 条   {p.stat().st_size/1024:8.1f} KB")
        else:
            print(f"  {f:24s}    --")
    print("=" * 62)
    try:
        import yaml
        d = yaml.safe_load(PROFILES_YAML.read_text(encoding="utf-8"))
        cur = d.get("current")
        for it in d.get("items", []):
            mark = "*" if it.get("uid") == cur else " "
            print(f" {mark} {it.get('uid'):22s} {it.get('name')}  {it.get('file')}")
    except Exception as e:
        print("  profiles.yaml 读取失败:", e)
    print("=" * 62)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    t0 = time.time()

    if cmd == "all":
        run("discover_sources.py", timeout=900)
        run("harvest.py", timeout=1800)
        run("probe.py", "900", timeout=1800)
        run("test_live2.py", timeout=5400,
            env_extra={"VK_TIMEOUT": "5000", "VK_CONC": "128", "VK_ROUNDS": "2"})
        run("verify_exit.py", timeout=5400)
        run("build_config.py")               # 先出一版，给下面的探测提供节点名
        run("speed_probe.py", "--top", "150", "--bytes", "2000000",
            "--threads", "24", timeout=1800)  # 真并行吞吐实测
        run("ghscan.py", timeout=1800)        # github 专用组
        run("build_config.py")               # 按实测吞吐重排 + 分层分组
        deploy()
    elif cmd == "quick":
        run("harvest.py", timeout=1800)
        run("probe.py", "900", timeout=1800)
        run("test_live2.py", timeout=5400,
            env_extra={"VK_TIMEOUT": "5000", "VK_CONC": "128", "VK_ROUNDS": "1"})
        run("build_config.py")
        run("speed_probe.py", "--top", "80", "--bytes", "1500000",
            "--threads", "20", timeout=900)
        run("ghscan.py", timeout=1800)
        run("build_config.py")
        deploy()
    elif cmd == "build":
        run("build_config.py")
    elif cmd == "deploy":
        deploy()
    elif cmd == "status":
        status()
    else:
        print(__doc__)
        return
    log(f"== 全流程结束，总耗时 {time.time()-t0:.0f}s ==")


if __name__ == "__main__":
    main()
