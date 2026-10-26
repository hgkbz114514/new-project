# -*- coding: utf-8 -*-
"""
vpnkit :: mihomo 配置生成器
输入: profiles/nodes_verified.json (或 nodes_live.json)
输出: profiles/vpnkit.yaml      —— 可直接被 Clash Verge 载入的完整配置
      profiles/vpnkit_mini.yaml —— 精简版（只要代理）
"""
import json, sys, time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT_FULL = ROOT / "profiles" / "vpnkit.yaml"
OUT_MINI = ROOT / "profiles" / "vpnkit_mini.yaml"
LOG = ROOT / "09_logs" / "build.log"

MIXED_PORT = 7897
CTRL = "127.0.0.1:9097"

# 按地区分组（简写）
REGION = {"CN": " CN", "HK": " HK", "TW": " TW", "SG": " SG", "JP": " JP",
          "KR": " KR", "US": " US", "GB": " UK", "DE": " DE", "NL": " NL",
          "FR": " FR", "CA": " CA", "RU": " RU", "TR": " TR", "IN": " IN"}


def log(m):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {m}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def merge_pool():
    """按 server:port 合并「实测吞吐」与「真实出口」两份数据。

    不能用节点名当 key —— 名字是 `{编号} {国家} {延迟}ms {类型}`，
    编号和延迟每次重建都会变，用它匹配会让测速数据永久错位。
    """
    fast_p = ROOT / "profiles" / "nodes_fast.json"
    ver_p = ROOT / "profiles" / "nodes_verified.json"
    if not fast_p.exists():
        return
    try:
        fast = json.loads(fast_p.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"!! nodes_fast.json 读取失败: {e}")
        return
    ver = []
    if ver_p.exists():
        try:
            ver = json.loads(ver_p.read_text(encoding="utf-8"))
        except Exception:
            ver = []

    def key(n):
        return f"{n.get('server')}:{n.get('port')}"

    # verified 可能是空的（守护轮被污染/还没跑到），这时退回最富的字段银行，
    # 否则 pool 只剩 fast 的测速字段，节点 uuid/password 全丢，mihomo -t 直接拒载。
    bank = list(ver)
    for alt in ("nodes_final.json", "nodes_overseas.json"):
        if len(bank) >= 10:
            break
        ap = ROOT / "profiles" / alt
        try:
            if ap.exists() and ap.stat().st_size > 10:
                cand = json.loads(ap.read_text(encoding="utf-8"))
                if len(cand) > len(bank):
                    bank = cand
        except Exception:
            pass
    vb = {key(n): n for n in bank}
    pool, seen = [], set()
    SPEED_FIELDS = ("server", "port", "type", "kbps", "bytes", "secs",
                    "speed_url", "tls_ok", "tls_code", "tls_note")

    for f in fast:
        k = key(f)
        if k in seen:
            continue
        seen.add(k)
        rec = dict(vb.get(k, {}))          # 先铺真实出口信息（国家/ISP）
        rec.update({kk: f[kk] for kk in SPEED_FIELDS if f.get(kk) is not None})
        pool.append(rec)

    for v in ver:                          # 没那么快、没进测速名单的节点，补在后面
        k = key(v)
        if k in seen:
            continue
        seen.add(k)
        rec = dict(v)
        rec.setdefault("kbps", 0)
        pool.append(rec)

    pool.sort(key=lambda x: -float(x.get("kbps") or 0))
    out = ROOT / "profiles" / "nodes_pool.json"
    out.write_text(json.dumps(pool, ensure_ascii=False, indent=1), encoding="utf-8")

    has_speed = sum(1 for x in pool if float(x.get("kbps") or 0) > 0)
    n100 = sum(1 for x in pool if float(x.get("kbps") or 0) >= 100)
    log(f"合并节点池: {len(pool)} 个，其中有实测速度 {has_speed} 个，"
        f">=100KB/s 的 {n100} 个")


def pick_input():
    for name in ("nodes_pool.json", "nodes_fast.json", "nodes_verified.json",
                 "nodes_final.json", "nodes_overseas.json", "nodes_live.json",
                 "nodes_alive.json"):
        p = ROOT / "profiles" / name
        if p.exists() and p.stat().st_size > 10:
            return p
    return None


def ensure_geo():
    """把 Clash Verge 自带的 geo 数据复制到 profiles，避免内核联网下载失败。

    内核缺 geoip.dat / GeoSite.dat 时会尝试联网下载，在受限网络里必然
    `context deadline exceeded`，所以必须预先放好。源目录由 vkcore 探测，
    也可以用 VK_GEO 环境变量显式指定。找不到源就跳过（内核仍可能启动失败，
    但至少不会因为这一步崩掉）。
    """
    import shutil

    from vkcore import find_geo_dir

    src = find_geo_dir()
    if src is None:
        log("!! 没找到 Clash Verge 的 geo 数据目录，跳过复制")
        log("   装好 Clash Verge 后重跑，或 set VK_GEO=<resources 目录>")
        return

    # 全新克隆时 profiles\ 还不存在，不先建好 copy2 会报 WinError 3
    (ROOT / "profiles").mkdir(parents=True, exist_ok=True)

    need = {"geoip.dat": "geoip.dat", "geosite.dat": "GeoSite.dat",
            "Country.mmdb": "Country.mmdb", "ASN.mmdb": "ASN.mmdb"}
    for s, d in need.items():
        sp, dp = src / s, ROOT / "profiles" / d
        if sp.exists() and (not dp.exists() or dp.stat().st_size < sp.stat().st_size):
            try:
                shutil.copy2(sp, dp)
                log(f"geo 数据 {d} <- {sp}")
            except Exception as e:
                log(f"!! 复制 {d} 失败: {e}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ensure_geo()
    merge_pool()
    src = pick_input()
    if not src:
        log("!! 没有可用的节点文件")
        log("   先跑 python bin\\vk.py all 采集节点，或 python bin\\harvest.py")
        # 返回非零，让 setup.bat / guard.py 能区分「真的生成了配置」和「没东西可生成」
        return 1
    nodes = json.loads(src.read_text(encoding="utf-8"))
    log(f"载入 {src.name}: {len(nodes)} 节点")

    proxies, names = [], []
    for i, n in enumerate(nodes):
        # mihomo validates EVERY entry in proxies: never emit a node without its secret.
        t0 = n.get('type')
        if t0 in ('vmess', 'vless', 'trojan', 'tuic', 'anytls') and not n.get('uuid'):
            continue
        if t0 == 'ss' and not n.get('password'):
            continue
        q = {k: v for k, v in n.items()
             if k not in ("tcp_rtt", "delay", "exit_ip", "country", "cc",
                          "isp", "https_ok", "yt_ok", "probe", "resolved_ip",
                          "cdn", "exit_cc", "exit_country", "exit_isp")}
        cc = n.get("exit_cc") or n.get("cc") or ""
        tag = REGION.get(cc, f" {cc}" if cc else "")
        delay = n.get("delay", 9999)
        nm = f"{i:03d}{tag} {delay}ms {n.get('type','')}"
        q["name"] = nm
        cf = q.get("client-fingerprint")
        if cf and cf not in ("chrome", "firefox", "safari", "ios", "android",
                             "edge", "360", "qq", "random"):
            q.pop("client-fingerprint", None)
        if q.get("type") in ("vless", "vmess", "trojan", "tuic", "hysteria2",
                             "anytls") and "client-fingerprint" not in q:
            q["client-fingerprint"] = "chrome"
        proxies.append(q)
        names.append(nm)

    log(f"生成 {len(proxies)} 个代理条目")

    # 按地区切分出子组
    groups_by_cc = {}
    for i, n in enumerate(nodes):
        if i >= len(names):
            continue
        cc = n.get("exit_cc") or n.get("cc") or "OT"
        groups_by_cc.setdefault(cc, []).append(names[i])

    auto_groups = []
    for cc in sorted(groups_by_cc, key=lambda c: -len(groups_by_cc[c])):
        if len(groups_by_cc[cc]) < 2:
            continue
        auto_groups.append({
            "name": f"AUTO-{cc}",
            "type": "url-test",
            "proxies": groups_by_cc[cc],
            "url": "https://www.gstatic.com/generate_204",
            "interval": 300, "tolerance": 60, "lazy": True,
            "timeout": 5000,
        })

    # github 专项：只有部分节点能完成 github.com 的 TLS 握手（免费节点被中间盒
    # 或 SNI 阻断的概率很高），实测筛出这批做成专用组，避免 github 随机挂掉。
    # 用 server:port 作为稳定 key —— 节点名含编号/延迟，每次重建都会变。
    gh_file = ROOT / "profiles" / "github_ok.json"
    gh_names = []
    if gh_file.exists():
        try:
            raw = json.loads(gh_file.read_text(encoding="utf-8"))
            keys = {str(x) for x in raw}
            for i, n in enumerate(nodes):
                if f"{n.get('server')}:{n.get('port')}" in keys:
                    gh_names.append(names[i])
        except Exception:
            gh_names = []
    if gh_names:
        log(f"github 可用节点 {len(gh_names)} 个 -> 专用组 GIT")
    else:
        gh_names = names

    # Claude/Anthropic 专项：必须**钉死一个出口**，不能做 url-test。
    #
    # Anthropic 的封号理由里明写着"从不支持的位置创建账户"，而 KYC 过程中
    # 出口 IP 一跳国家，Persona 会直接判验证失败。url-test 每隔几分钟按延迟
    # 重选一次，正好是最坏的选择 —— 它会在 US/NL/RO 之间来回跳。
    #
    # 所以这里用 select 组，并把 claude_pin.py 钉住的那个节点放在第一位。
    # 默认选中第一个，等于每次重建都回到同一个出口。用户也可以手动改选。
    pin_file = ROOT / "profiles" / "claude_pin.json"
    cl_names = []
    pin_key, pin_cc = "", ""
    if pin_file.exists():
        try:
            pin = json.loads(pin_file.read_text(encoding="utf-8"))
            pin_key = str(pin.get("key") or "")
            pin_cc = str(pin.get("cc") or "")
            # 先放钉子，再按 claude_pin.json 里记录的顺序放其余复核过的候选。
            # candidates 是按"地区合格 + 稳定性"排过序的，全部实测能同时
            # 通 api.anthropic.com 与 claude.ai，比全量节点表靠谱得多。
            want = [pin_key] + [str(k) for k in (pin.get("candidates") or [])]
            key2name = {f"{n.get('server')}:{n.get('port')}": names[i]
                        for i, n in enumerate(nodes)}
            seen = set()
            for k in want:
                nm = key2name.get(k)
                if nm and nm not in seen:
                    seen.add(nm)
                    cl_names.append(nm)
        except Exception:
            cl_names = []
    if cl_names:
        # 其余候选放在后面，钉子失效时可以手动切，但默认不选。
        cl_names += [n for n in names if n not in cl_names]
        log(f"Claude 出口已钉死: {cl_names[0]} (cc={pin_cc}, key={pin_key}), "
            f"备选 {len(cl_names)-1} 个")
        # 节点名里带重建计数器（006 → 重建后变 030），写回 pid 记录，避免
        # claude_pin.json 里的名字永久停留在旧编号上误导排查。key 才是主键。
        try:
            if pin.get("name") != cl_names[0]:
                pin["name"] = cl_names[0]
                pin_file.write_text(json.dumps(pin, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
        except Exception:
            pass
    else:
        cl_names = names
        log("!! 未找到 claude_pin.json 或钉子节点已不在池中，CLAUDE 组回退全量")
        log("   先跑 python claude_pin.py 钉一个支持地区的出口")

    # 自动组候选：必须用**实测吞吐**筛，不能用 204 延迟筛。
    #
    # url-test 只测"拿到响应头"的耗快，与吞吐完全不相关：实测里
    # `001 DE` 延迟 211ms 却能跑 702 KB/s，而有些节点 170ms 只有 8 KB/s。
    # 如果自动组放进后者，url-test 会因为延迟低而一直选中它，
    # 表现就是"连上了但网页打不开"。
    #
    # 分层：>=300KB/s 进自动组；>=500KB/s 单独做一个 SPEED-TOP 直选组。
    #
    # 但带宽不是唯一门槛。2026-10-25 实测：11 个 SPEED-TOP 成员里有 4 个
    # vless 节点 TCP 通、带宽也有，但 TLS 握手里程碑断在中间（
    # ssl.SSLEOFError: UNEXPECTED_EOF_WHILE_READING），表现为"连上了但
    # 所有 https 站点全挂"。所以 tls_ok=False 的节点一律不进自动组，
    # 也不进 SPEED-TOP —— 它们只留在全量列表里供手动应急。
    #
    # dead_keys 来自 claude_pin.json：claude_pin 实测过 claude.ai 全挂的
    # key，同样直接排除，避免它又被选成 Claude 出口。
    dead_keys = set()
    pin_path = ROOT / "profiles" / "claude_pin.json"
    if pin_path.exists():
        try:
            dead_keys = set(json.loads(
                pin_path.read_text(encoding="utf-8")).get("dead_keys") or [])
        except Exception:
            dead_keys = set()

    def usable(n):
        if n.get("tls_ok") is False:
            return False
        key = f"{n.get('server')}:{n.get('port')}"
        return key not in dead_keys

    TIERS = [(500.0, "SPEED-TOP"), (300.0, "AUTO-FAST"), (100.0, "AUTO-OK")]

    bad_tls = sum(1 for n in nodes if n.get("tls_ok") is False)
    if bad_tls:
        log(f"剔除 TLS 断裂节点 {bad_tls} 个（不进自动组/SPEED-TOP）")
    if dead_keys:
        log(f"剔除 dead_keys 节点 {len(dead_keys)} 个")

    def by_speed(th):
        return [names[i] for i, n in enumerate(nodes)
                if float(n.get("kbps") or 0) >= th and usable(n)]

    top_list = by_speed(500.0)
    fast_ok = by_speed(300.0)
    ok_list = by_speed(100.0)

    if len(fast_ok) < 4:
        log(f"!! >=300KB/s 只有 {len(fast_ok)} 个，自动组降级到 >=100KB/s")
        fast_ok = ok_list
    if len(fast_ok) < 4:
        log("!! 实测速度数据不足，自动组回退全部节点（先去跑 speed_probe.py）")
        fast_ok = names
    if len(top_list) < 2:
        top_list = fast_ok

    log(f"实测 >=500KB/s {len(top_list)} 个 -> SPEED-TOP")
    log(f"实测 >=300KB/s {len(fast_ok)} 个 -> AUTO-FAST / FALLBACK / LOAD-BALANCE")
    log(f"实测 >=100KB/s {len(ok_list)} 个")

    proxy_groups = [{
        # AUTO-FAST 排第一 = PROXY 的默认选择。它不是随便挑的：
        # SPEED-TOP 是 select 组，只认第 0 个成员，节点一死整条链路就断，
        # 而且不会自己换 —— 2026-10-25 就是它锁死在一个 TLS 已坏的 vless 上，
        # 造成除 github/bilibili 外全部 SSLError。AUTO-FAST 是 url-test，
        # 每 240s 重测并在成员间自动切换，候选集又已经过吞吐+TLS 双重过滤，
        # 所以默认走它才是"能自己活下去"的选择。SPEED-TOP 保留给手动指定。
        "name": "PROXY",
        "type": "select",
        "proxies": ["AUTO-FAST", "SPEED-TOP", "AUTO-FALLBACK", "LOAD-BALANCE"]
                   + [g["name"] for g in auto_groups] + names + ["DIRECT"],
    }, {
        "name": "SPEED-TOP",
        "type": "select",
        "proxies": top_list,
    }, {
        "name": "AUTO-FAST",
        "type": "url-test",
        "proxies": fast_ok,
        "url": "https://www.gstatic.com/generate_204",
        "interval": 240, "tolerance": 80, "lazy": True, "timeout": 5000,
        "max-failed-times": 3,
    }, {
        "name": "AUTO-FALLBACK",
        "type": "fallback",
        "proxies": fast_ok,
        "url": "https://www.gstatic.com/generate_204",
        "interval": 300, "timeout": 5000, "lazy": True,
    }, {
        "name": "LOAD-BALANCE",
        "type": "load-balance",
        "proxies": fast_ok,
        "url": "https://www.gstatic.com/generate_204",
        "interval": 300, "strategy": "consistent-hashing", "lazy": True,
    }, {
        "name": "MEDIA",
        "type": "select",
        "proxies": ["PROXY", "AUTO-FAST"] + names + ["DIRECT"],
    }, {
        "name": "GIT",
        "type": "url-test",
        "proxies": gh_names,
        "url": "https://github.com",
        "interval": 300, "tolerance": 100, "lazy": True, "timeout": 8000,
        "max-failed-times": 3,
    }, {
        # select 而非 url-test：默认永远选中第 0 个 = claude_pin.py 钉住的
        # 节点，出口国家不会随延迟波动而漂移。其余候选仅用于手动兜底。
        "name": "CLAUDE",
        "type": "select",
        "proxies": cl_names + ["DIRECT"],
    }, {
        "name": "AI",
        "type": "select",
        "proxies": ["PROXY", "AUTO-FAST"] + names + ["DIRECT"],
    }, {
        "name": "TELEGRAM",
        "type": "select",
        "proxies": ["PROXY", "AUTO-FAST"] + names + ["DIRECT"],
    }, {
        "name": "GLOBAL-OUT",
        "type": "select",
        "proxies": ["PROXY", "AUTO-FAST", "AUTO-FALLBACK", "LOAD-BALANCE", "DIRECT"],
    }] + auto_groups

    rules = [
        # 注意：不要在这里放 GEOIP,LAN —— fake-ip 的 198.18.0.0/16 会被 mihomo
        # 的 LAN 库命中，导致所有域名被误判直连（曾把 github.com 判成 lan）。
        "GEOSITE,private,DIRECT",
        "GEOSITE,category-ads-all,REJECT",
        # Claude/Anthropic 必须排在 cn 之前，且走钉死出口的 CLAUDE 组。
        # GEOSITE,anthropic 覆盖不全（claude.ai / portal.anthropic.com /
        # persona 的验证页都不在里面），所以显式列出域名。
        "DOMAIN-SUFFIX,claude.ai,CLAUDE",
        "DOMAIN-SUFFIX,claude.com,CLAUDE",
        "DOMAIN-SUFFIX,anthropic.com,CLAUDE",
        "DOMAIN-SUFFIX,anthropic.cloud,CLAUDE",
        "DOMAIN-SUFFIX,claudeusercontent.com,CLAUDE",
        "DOMAIN-SUFFIX,withpersona.com,CLAUDE",
        "DOMAIN-SUFFIX,persona.com,CLAUDE",
        "GEOSITE,anthropic,CLAUDE",
        "GEOSITE,cn,DIRECT",
        "GEOSITE,bilibili,DIRECT",
        "GEOSITE,youtube,MEDIA",
        "GEOSITE,netflix,MEDIA",
        "GEOSITE,disney,MEDIA",
        "GEOSITE,spotify,MEDIA",
        "GEOSITE,openai,AI",
        "GEOSITE,anthropic,AI",
        "GEOSITE,google-gemini,AI",
        "GEOSITE,category-ai-!cn,AI",
        "GEOSITE,telegram,TELEGRAM",
        "GEOSITE,github,GIT",
        "DOMAIN-SUFFIX,githubusercontent.com,GIT",
        "DOMAIN-SUFFIX,githubassets.com,GIT",
        "DOMAIN-SUFFIX,github.io,GIT",
        "GEOSITE,twitter,PROXY",
        "GEOSITE,google,PROXY",
        "GEOSITE,microsoft,DIRECT",
        "GEOIP,CN,DIRECT",
        "MATCH,PROXY",
    ]

    full = {
        "mixed-port": MIXED_PORT,
        "allow-lan": False,
        "bind-address": "127.0.0.1",
        "mode": "rule",
        "log-level": "info",
        "ipv6": False,
        "unified-delay": True,
        "tcp-concurrent": True,
        "find-process-mode": "off",
        "external-controller": CTRL,
        "secret": "",
        "geo-auto-update": False,
        "geodata-mode": False,
        "profile": {"store-selected": True, "store-fake-ip": True},
        "sniffer": {"enable": True, "force-dns-mapping": True,
                    "parse-pure-ip": True,
                    "override-destination": False,
                    "sniff": {"HTTP": {"ports": [80, "8080-8880"]},
                              "TLS": {"ports": [443, "8443"]},
                              "QUIC": {"ports": [443, "8443"]}}},
        "dns": {
            "enable": True,
            "ipv6": False,
            "enhanced-mode": "fake-ip",
            "fake-ip-range": "198.18.0.1/16",
            # 关键：本机 hosts 被 Steam++/Watt Toolkit 之类的加速器写入了
            # 大量 "127.0.0.1 github.com / api.github.com / raw.githubusercontent.com"
            # 的屏蔽条目。mihomo 默认会读取系统 hosts（use-hosts 缺省为 true），
            # 结果 github 全系域名被解析成 127.0.0.1，导致走代理也必然 TLS 失败。
            # 关掉它，让域名走真实 DNS，hosts 文件本身不动（可逆、不影响别的软件）。
            "use-hosts": False,
            "use-system-hosts": False,
            "fake-ip-filter": ["*.lan", "*.local", "*.localdomain", "localhost.*",
                               "+.msftconnecttest.com", "+.msftncsi.com",
                               "time.*.com", "ntp.*.com", "+.stun.*.*",
                               "music.163.com", "+.126.net", "+.qq.com"],
            "default-nameserver": ["223.5.5.5", "119.29.29.29", "1.1.1.1"],
            "nameserver": ["https://doh.pub/dns-query",
                           "https://dns.alidns.com/dns-query",
                           "https://1.1.1.1/dns-query"],
            "fallback": ["https://1.1.1.1/dns-query", "https://8.8.8.8/dns-query",
                         "tls://dns.google:853"],
            "fallback-filter": {"geoip": True, "geoip-code": "CN", "ipcidr": []},
            "nameserver-policy": {"geosite:cn": ["https://doh.pub/dns-query"],
                                  "geosite:private": ["system"]},
        },
        "proxies": proxies,
        "proxy-groups": proxy_groups,
        "rules": rules,
    }

    OUT_FULL.write_text(yaml.safe_dump(full, allow_unicode=True, sort_keys=False,
                                       width=4096), encoding="utf-8")
    mini = {"mixed-port": MIXED_PORT, "mode": "rule", "log-level": "warning",
            "proxies": proxies,
            "proxy-groups": [g for g in proxy_groups if g["name"] in
                             ("PROXY", "AUTO-FAST", "AUTO-FALLBACK")],
            "rules": ["GEOIP,CN,DIRECT", "MATCH,PROXY"]}
    OUT_MINI.write_text(yaml.safe_dump(mini, allow_unicode=True, sort_keys=False,
                                       width=4096), encoding="utf-8")

    log(f"已生成 {OUT_FULL}")
    log(f"已生成 {OUT_MINI}")
    log(f"分组: PROXY / SPEED-TOP / AUTO-FAST / AUTO-FALLBACK / LOAD-BALANCE / "
        f"MEDIA / AI / TELEGRAM / GIT / CLAUDE + {len(auto_groups)} 个地区组")


if __name__ == "__main__":
    sys.exit(main() or 0)
