# -*- coding: utf-8 -*-
"""
vpnkit :: 节点池采集器
把任意形态的订阅（clash yaml / base64 列表 / 明文 uri）统一解析成节点池
输出: E:\\vpnkit\\profiles\\nodes_raw.json
"""
import base64, json, os, re, sys, time, hashlib, urllib.parse, concurrent.futures as cf
from pathlib import Path
from vkwrite import safe_write

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "bin" / "sources.txt"
OUT_RAW = ROOT / "profiles" / "nodes_raw.json"
LOG = ROOT / "09_logs" / "harvest.log"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 ClashforWindows/0.20.39")
TIMEOUT = 20
WORKERS = 24

SCHEMES = ("ss://", "ssr://", "vmess://", "vless://", "trojan://",
           "hysteria://", "hysteria2://", "hy2://", "tuic://", "wireguard://")


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def norm_key(p: dict) -> str:
    """节点去重指纹"""
    base = "|".join(str(p.get(k, "")) for k in
                    ("type", "server", "port", "uuid", "password", "cipher"))
    return hashlib.sha1(base.lower().encode()).hexdigest()


def b64fix(s: str) -> str:
    s = s.strip().replace("-", "+").replace("_", "/")
    return s + "=" * (-len(s) % 4)


def b64dec(s: str) -> str:
    try:
        return base64.b64decode(b64fix(s)).decode("utf-8", "replace")
    except Exception:
        return ""


# ---------------------------------------------------------------- URI 解析
def parse_ss(uri: str):
    body = uri[5:].split("#")[0]
    tag = urllib.parse.unquote(uri.split("#", 1)[1]) if "#" in uri else ""
    if "@" in body:                                  # SIP002
        userinfo, hostport = body.rsplit("@", 1)
        if ":" not in userinfo:
            userinfo = b64dec(userinfo)
        method, _, pwd = userinfo.partition(":")
        hostport = hostport.split("?")[0].split("/")[0]
        server, _, port = hostport.rpartition(":")
    else:                                            # legacy
        raw = b64dec(body.split("?")[0])
        if "@" not in raw:
            return None
        userinfo, hostport = raw.rsplit("@", 1)
        method, _, pwd = userinfo.partition(":")
        server, _, port = hostport.rpartition(":")
    if not server or not port.isdigit():
        return None
    return {"name": tag or f"{server}:{port}", "type": "ss", "server": server,
            "port": int(port), "cipher": method, "password": pwd, "udp": True}


def parse_vmess(uri: str):
    raw = b64dec(uri[8:])
    if not raw.startswith("{"):
        return None
    j = json.loads(raw)
    host = str(j.get("add") or "").strip()
    if not host:
        return None
    net = (j.get("net") or "tcp").lower()
    p = {"name": j.get("ps") or f"{host}:{j.get('port')}",
         "type": "vmess", "server": host,
         "port": int(j.get("port") or 443),
         "uuid": j.get("id"), "alterId": int(j.get("aid") or 0),
         "cipher": j.get("scy") or "auto", "udp": True,
         "tls": (j.get("tls") or "") in ("tls", "reality"),
         "servername": j.get("sni") or j.get("host") or "",
         "skip-cert-verify": True,
         "network": net}
    if net == "ws":
        p["ws-opts"] = {"path": j.get("path") or "/",
                        "headers": {"Host": j.get("host") or host}}
    elif net == "grpc":
        p["grpc-opts"] = {"grpc-service-name": j.get("path") or ""}
    elif net == "h2":
        p["h2-opts"] = {"path": j.get("path") or "/",
                        "host": [j.get("host") or host]}
    return p


def parse_vless(uri: str):
    u = urllib.parse.urlsplit(uri)
    if not u.hostname:
        return None
    q = dict(urllib.parse.parse_qsl(u.query))
    net = (q.get("type") or "tcp").lower()
    sec = (q.get("security") or "").lower()
    tag = urllib.parse.unquote(u.fragment) or f"{u.hostname}:{u.port}"
    p = {"name": tag, "type": "vless", "server": u.hostname,
         "port": u.port or 443, "uuid": u.username, "udp": True,
         "tls": sec in ("tls", "reality", "xtls"),
         "skip-cert-verify": True, "network": net,
         "client-fingerprint": q.get("fp") or "chrome"}
    if q.get("sni") or q.get("host"):
        p["servername"] = q.get("sni") or q.get("host")
    if sec == "reality":
        p["reality-opts"] = {"public-key": q.get("pbk", ""),
                             "short-id": q.get("sid", "")}
    flow = q.get("flow")
    if flow:
        p["flow"] = flow
    if net == "ws":
        p["ws-opts"] = {"path": urllib.parse.unquote(q.get("path", "/")),
                        "headers": {"Host": q.get("host") or u.hostname}}
    elif net == "grpc":
        p["grpc-opts"] = {"grpc-service-name": q.get("serviceName", "")}
    elif net == "tcp" and q.get("headerType") == "http":
        p["network"] = "http"
    return p


def parse_trojan(uri: str):
    u = urllib.parse.urlsplit(uri)
    if not u.hostname:
        return None
    q = dict(urllib.parse.parse_qsl(u.query))
    tag = urllib.parse.unquote(u.fragment) or f"{u.hostname}:{u.port}"
    p = {"name": tag, "type": "trojan", "server": u.hostname,
         "port": u.port or 443, "password": urllib.parse.unquote(u.username or ""),
         "udp": True, "skip-cert-verify": True,
         "sni": q.get("sni") or q.get("peer") or ""}
    net = (q.get("type") or "tcp").lower()
    if net == "ws":
        p["network"] = "ws"
        p["ws-opts"] = {"path": urllib.parse.unquote(q.get("path", "/")),
                        "headers": {"Host": q.get("host") or u.hostname}}
    elif net == "grpc":
        p["network"] = "grpc"
        p["grpc-opts"] = {"grpc-service-name": q.get("serviceName", "")}
    return p


def parse_hy2(uri: str):
    u = urllib.parse.urlsplit(uri)
    if not u.hostname:
        return None
    q = dict(urllib.parse.parse_qsl(u.query))
    tag = urllib.parse.unquote(u.fragment) or f"{u.hostname}:{u.port}"
    return {"name": tag, "type": "hysteria2", "server": u.hostname,
            "port": u.port or 443,
            "password": urllib.parse.unquote(u.username or ""),
            "sni": q.get("sni") or "", "skip-cert-verify": True,
            "udp": True}


def parse_tuic(uri: str):
    u = urllib.parse.urlsplit(uri)
    if not u.hostname:
        return None
    q = dict(urllib.parse.parse_qsl(u.query))
    tag = urllib.parse.unquote(u.fragment) or f"{u.hostname}:{u.port}"
    return {"name": tag, "type": "tuic", "server": u.hostname,
            "port": u.port or 443, "uuid": u.username,
            "password": urllib.parse.unquote(u.password or ""),
            "sni": q.get("sni") or "", "skip-cert-verify": True,
            "alpn": ["h3"], "udp": True}


PARSERS = {
    "ss://": parse_ss, "vmess://": parse_vmess, "vless://": parse_vless,
    "trojan://": parse_trojan, "hysteria2://": parse_hy2, "hy2://": parse_hy2,
    "tuic://": parse_tuic,
}


def parse_uri(uri: str):
    uri = uri.strip()
    if not uri or uri.startswith("//"):
        return None
    for scheme, fn in PARSERS.items():
        if uri.lower().startswith(scheme):
            try:
                return fn(uri)
            except Exception:
                return None
    return None


# ---------------------------------------------------------------- 内容识别
def extract_from_text(text: str):
    """从任意文本里抠出所有 proxy uri"""
    out = []
    for line in re.split(r"[\r\n]+", text):
        line = line.strip()
        low = line.lower()
        if any(low.startswith(s) for s in SCHEMES):
            out.append(line)
    return out


def extract_from_clash(text: str):
    try:
        doc = yaml.safe_load(text)
    except Exception:
        return []
    if not isinstance(doc, dict):
        return []
    proxies = doc.get("proxies") or doc.get("Proxy") or []
    good = []
    for p in proxies:
        if isinstance(p, dict) and p.get("server") and p.get("port") and p.get("type"):
            q = dict(p)
            q.setdefault("name", f"{q['server']}:{q['port']}")
            q.setdefault("udp", True)
            if q["type"] in ("vmess", "vless", "trojan", "hysteria2", "tuic"):
                q.setdefault("skip-cert-verify", True)
            good.append(q)
    return good


def fetch(url: str):
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept": "*/*"},
                         timeout=TIMEOUT, verify=False)
        if r.status_code != 200 or len(r.content) < 40:
            return url, None, f"HTTP {r.status_code} len={len(r.content)}"
        return url, r.text, None
    except Exception as e:
        return url, None, f"{type(e).__name__}: {e}"


def harvest():
    import urllib3
    urllib3.disable_warnings()

    urls = []
    seen_url = set()
    for f in (SRC, ROOT / "bin" / "sources_live.txt"):
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and line not in seen_url:
                seen_url.add(line)
                urls.append(line)
    log(f"订阅源 {len(urls)} 条，并发拉取…")

    nodes, ok_src, bad = [], 0, []
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for url, text, err in ex.map(fetch, urls):
            if text is None:
                bad.append((url, err))
                continue
            ok_src += 1
            got = extract_from_clash(text)
            if not got:
                uris = extract_from_text(text)
                if not uris:
                    uris = extract_from_text(b64dec(text.strip()))
                for u in uris:
                    p = parse_uri(u)
                    if p:
                        got.append(p)
            nodes.extend(got)
            log(f"  OK  {len(got):4d} 节点  <- {url.split('/')[-1][:48]}")

    for u, e in bad:
        log(f"  FAIL  {u.split('/')[-1][:52]}  {e[:80]}")

    # 去重 + 命名规整
    seen, uniq = set(), []
    for p in nodes:
        try:
            if not p.get("server") or not p.get("port"):
                continue
            p["port"] = int(p["port"])
            if not (0 < p["port"] < 65536):
                continue
            k = norm_key(p)
            if k in seen:
                continue
            seen.add(k)
        except Exception:
            continue
        p["server"] = str(p["server"]).strip("[]")
        uniq.append(p)

    for i, p in enumerate(uniq):
        p["name"] = f"{i:04d}-{p['type']}-{p['server']}:{p['port']}"

    OUT_RAW.parent.mkdir(parents=True, exist_ok=True)
    safe_write(OUT_RAW, json.dumps(uniq, ensure_ascii=False, indent=1),
            min_items=50)

    by_type = {}
    for p in uniq:
        by_type[p["type"]] = by_type.get(p["type"], 0) + 1
    log(f"汇总: 源 {ok_src}/{len(urls)} 可用 | 去重后 {len(uniq)} 节点 | {by_type}")
    return uniq


if __name__ == "__main__":
    harvest()
