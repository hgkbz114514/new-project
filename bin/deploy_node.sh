#!/usr/bin/env bash
# ============================================================================
# vpnkit :: 自建节点一键部署（这才是真正的"长期"方案）
#
# 免费公共节点的现实：单节点存活期通常 2 小时 ~ 3 天，到期连不上是常态。
# 你自己 VPS 上的节点，只要续费就一直在，带宽独享、不封号、不限速。
#
# 用法（VPS 上 root 执行）：
#   bash deploy_node.sh
#   bash deploy_node.sh --proto hysteria2 --port 443
#   bash deploy_node.sh --proto vless-reality
#
# 支持: hysteria2(推荐,抗封锁) / vless-reality(最稳,无需域名) /
#       vmess-ws-tls(需域名) / tuic-v5 / trojan
# 产出: 一条可直接贴进 Clash Verge 的 clash 配置 + 一条分享链接
# ============================================================================
set -euo pipefail

PROTO="hysteria2"
PORT=""
DOMAIN=""
SNI="www.microsoft.com"
SINGBOX_VER="1.11.15"
CFG_DIR="/etc/sing-box"
OUT="/root/vpnkit_out"
UUID=$(cat /proc/sys/kernel/random/uuid)
HY2_PASS=$(head -c 18 /dev/urandom | base64 | tr -d '/+=' | head -c 24)

while [[ $# -gt 0 ]]; do
  case "$1" in
    --proto) PROTO="$2"; shift 2 ;;
    --port)  PORT="$2";  shift 2 ;;
    --domain) DOMAIN="$2"; shift 2 ;;
    --sni)   SNI="$2";   shift 2 ;;
    *) echo "未知参数 $1"; exit 1 ;;
  esac
done

die(){ echo "[!] $*" >&2; exit 1; }
info(){ echo "[*] $*"; }

[[ $EUID -eq 0 ]] || die "需要 root"
command -v curl >/dev/null || die "缺 curl"

# ---------------------------------------------------------------- 系统准备
info "系统依赖"
if command -v apt-get >/dev/null; then
  apt-get update -qq && apt-get install -y -qq curl tar openssl jq qrencode >/dev/null 2>&1 || true
elif command -v dnf >/dev/null; then
  dnf install -y -q curl tar openssl jq qrencode >/dev/null 2>&1 || true
elif command -v yum >/dev/null; then
  yum install -y -q curl tar openssl jq >/dev/null 2>&1 || true
fi

# BBR + 网络调优
info "开启 BBR 与内核调优"
cat > /etc/sysctl.d/99-vpnkit.conf <<'EOF'
net.core.default_qdisc=fq
net.ipv4.tcp_congestion_control=bbr
net.core.rmem_max=67108864
net.core.wmem_max=67108864
net.ipv4.tcp_rmem=4096 87380 33554432
net.ipv4.tcp_wmem=4096 65536 33554432
net.ipv4.tcp_fastopen=3
net.ipv4.ip_forward=1
fs.file-max=1048576
EOF
sysctl --system >/dev/null 2>&1 || true

# ---------------------------------------------------------------- 安装内核
info "安装 sing-box ${SINGBOX_VER}"
ARCH=$(uname -m)
case "$ARCH" in
  x86_64|amd64) A=amd64 ;;
  aarch64|arm64) A=arm64 ;;
  armv7l) A=armv7 ;;
  *) die "不支持的架构 $ARCH" ;;
esac
if ! command -v sing-box >/dev/null; then
  TMP=$(mktemp -d)
  URL="https://github.com/SagerNet/sing-box/releases/download/v${SINGBOX_VER}/sing-box-${SINGBOX_VER}-linux-${A}.tar.gz"
  curl -fsSL "$URL" -o "$TMP/sb.tgz" || die "下载失败: $URL"
  tar -xzf "$TMP/sb.tgz" -C "$TMP"
  install -m755 "$TMP"/sing-box-*/sing-box /usr/local/bin/sing-box
  rm -rf "$TMP"
fi
sing-box version | head -1

mkdir -p "$CFG_DIR" "$OUT"

# ---------------------------------------------------------------- 生成配置
gen_hy2(){
  local p="${PORT:-443}"
  cat > "$CFG_DIR/config.json" <<EOF
{
  "log": { "level": "warn", "timestamp": true },
  "inbounds": [{
    "type": "hysteria2",
    "tag": "hy2-in",
    "listen": "::",
    "listen_port": $p,
    "users": [{ "password": "$HY2_PASS" }],
    "masquerade": "https://www.bing.com",
    "ignore_client_bandwidth": false,
    "tls": {
      "enabled": true,
      "server_name": "$SNI",
      "alpn": ["h3"],
      "certificate_path": "$CFG_DIR/cert.pem",
      "key_path": "$CFG_DIR/key.pem"
    }
  }],
  "outbounds": [{ "type": "direct", "tag": "direct" }]
}
EOF
}

gen_reality(){
  local p="${PORT:-443}"
  [ -f "$CFG_DIR/reality.key" ] || sing-box generate reality-keypair > "$CFG_DIR/reality.key"
  local PRIV PUB
  PRIV=$(awk '/PrivateKey/{print $2}' "$CFG_DIR/reality.key")
  PUB=$(awk '/PublicKey/{print $2}' "$CFG_DIR/reality.key")
  local SID
  SID=$(openssl rand -hex 8)
  cat > "$CFG_DIR/config.json" <<EOF
{
  "log": { "level": "warn", "timestamp": true },
  "inbounds": [{
    "type": "vless",
    "tag": "vless-in",
    "listen": "::",
    "listen_port": $p,
    "users": [{ "uuid": "$UUID", "flow": "xtls-rprx-vision" }],
    "tls": {
      "enabled": true,
      "server_name": "$SNI",
      "reality": {
        "enabled": true,
        "handshake": { "server": "$SNI", "server_port": 443 },
        "private_key": "$PRIV",
        "short_id": ["$SID"]
      }
    }
  }],
  "outbounds": [{ "type": "direct", "tag": "direct" }]
}
EOF
  echo "$PUB" > "$CFG_DIR/reality.pub"
  echo "$SID" > "$CFG_DIR/reality.sid"
}

gen_vmess_ws(){
  local p="${PORT:-443}"
  [ -n "$DOMAIN" ] || die "vmess-ws-tls 需要 --domain your.domain.com"
  cat > "$CFG_DIR/config.json" <<EOF
{
  "log": { "level": "warn", "timestamp": true },
  "inbounds": [{
    "type": "vmess",
    "tag": "vmess-in",
    "listen": "::",
    "listen_port": $p,
    "users": [{ "uuid": "$UUID", "alterId": 0 }],
    "transport": { "type": "ws", "path": "/vpnkit", "max_early_data": 2048 },
    "tls": {
      "enabled": true,
      "server_name": "$DOMAIN",
      "alpn": ["http/1.1"],
      "certificate_path": "$CFG_DIR/cert.pem",
      "key_path": "$CFG_DIR/key.pem"
    }
  }],
  "outbounds": [{ "type": "direct", "tag": "direct" }]
}
EOF
}

gen_tuic(){
  local p="${PORT:-443}"
  cat > "$CFG_DIR/config.json" <<EOF
{
  "log": { "level": "warn", "timestamp": true },
  "inbounds": [{
    "type": "tuic",
    "tag": "tuic-in",
    "listen": "::",
    "listen_port": $p,
    "users": [{ "uuid": "$UUID", "password": "$HY2_PASS" }],
    "congestion_control": "bbr",
    "zero_rtt_handshake": true,
    "tls": {
      "enabled": true, "server_name": "$SNI", "alpn": ["h3"],
      "certificate_path": "$CFG_DIR/cert.pem", "key_path": "$CFG_DIR/key.pem"
    }
  }],
  "outbounds": [{ "type": "direct", "tag": "direct" }]
}
EOF
}

# 自签证书（hy2 / tuic / vmess-ws 用）
mkcert(){
  [[ -f "$CFG_DIR/cert.pem" ]] && return
  local cn="${DOMAIN:-$SNI}"
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -keyout "$CFG_DIR/key.pem" -out "$CFG_DIR/cert.pem" \
    -subj "/CN=$cn" -addext "subjectAltName=DNS:$cn" >/dev/null 2>&1
  chmod 600 "$CFG_DIR/key.pem"
}

info "协议: $PROTO"
case "$PROTO" in
  hysteria2|hy2)      mkcert; gen_hy2 ;;
  vless-reality|reality) gen_reality ;;
  vmess-ws-tls|vmess) mkcert; gen_vmess_ws ;;
  tuic|tuic-v5)       mkcert; gen_tuic ;;
  *) die "未知协议 $PROTO (hysteria2|vless-reality|vmess-ws-tls|tuic)" ;;
esac

sing-box check -c "$CFG_DIR/config.json" || die "配置校验失败"

# ---------------------------------------------------------------- 服务
cat > /etc/systemd/system/sing-box.service <<'EOF'
[Unit]
Description=sing-box service (vpnkit)
After=network.target nss-lookup.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/sing-box run -c /etc/sing-box/config.json
Restart=on-failure
RestartSec=3
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now sing-box >/dev/null 2>&1
sleep 2
systemctl is-active --quiet sing-box || { journalctl -u sing-box -n 20 --no-pager; die "服务启动失败"; }

# ---------------------------------------------------------------- 防火墙
info "放行端口"
if command -v ufw >/dev/null && ufw status | grep -q active; then
  ufw allow "${PORT:-443}"/tcp >/dev/null 2>&1 || true
  ufw allow "${PORT:-443}"/udp >/dev/null 2>&1 || true
fi
if command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then
  firewall-cmd --permanent --add-port="${PORT:-443}"/tcp >/dev/null 2>&1 || true
  firewall-cmd --permanent --add-port="${PORT:-443}"/udp >/dev/null 2>&1 || true
  firewall-cmd --reload >/dev/null 2>&1 || true
fi

# ---------------------------------------------------------------- 出口信息
IP=$(curl -fsS4 --max-time 8 https://api.ipify.org || curl -fsS4 --max-time 8 https://ifconfig.me || echo "YOUR_VPS_IP")
LOC=$(curl -fsS --max-time 8 "http://ip-api.com/line/${IP}?fields=country" 2>/dev/null | head -1 || echo "")
P="${PORT:-443}"

echo
echo "=================================================================="
echo " 部署完成  出口 $IP  $LOC"
echo "=================================================================="

case "$PROTO" in
  hysteria2|hy2)
    URI="hysteria2://${HY2_PASS}@${IP}:${P}/?sni=${SNI}&insecure=1#vpnkit-self-${IP}"
    cat > "$OUT/clash_self.yaml" <<EOF
proxies:
  - name: "SELF-HY2-${LOC}"
    type: hysteria2
    server: ${IP}
    port: ${P}
    password: ${HY2_PASS}
    sni: ${SNI}
    skip-cert-verify: true
    udp: true
EOF
    ;;
  vless-reality|reality)
    PUB=$(cat "$CFG_DIR/reality.pub"); SID=$(cat "$CFG_DIR/reality.sid")
    URI="vless://${UUID}@${IP}:${P}?encryption=none&flow=xtls-rprx-vision&security=reality&sni=${SNI}&fp=chrome&pbk=${PUB}&sid=${SID}&type=tcp#vpnkit-self-${IP}"
    cat > "$OUT/clash_self.yaml" <<EOF
proxies:
  - name: "SELF-REALITY-${LOC}"
    type: vless
    server: ${IP}
    port: ${P}
    uuid: ${UUID}
    flow: xtls-rprx-vision
    tls: true
    servername: ${SNI}
    client-fingerprint: chrome
    skip-cert-verify: true
    reality-opts:
      public-key: ${PUB}
      short-id: "${SID}"
    network: tcp
    udp: true
EOF
    ;;
  vmess-ws-tls|vmess)
    URI="vmess://$(printf '{"v":"2","ps":"vpnkit-self-%s","add":"%s","port":"%s","id":"%s","aid":"0","scy":"auto","net":"ws","type":"none","host":"%s","path":"/vpnkit","tls":"tls","sni":"%s"}' "$IP" "$IP" "$P" "$UUID" "$DOMAIN" "$DOMAIN" | base64 -w0)"
    cat > "$OUT/clash_self.yaml" <<EOF
proxies:
  - name: "SELF-VMESS-${LOC}"
    type: vmess
    server: ${DOMAIN}
    port: ${P}
    uuid: ${UUID}
    alterId: 0
    cipher: auto
    tls: true
    servername: ${DOMAIN}
    skip-cert-verify: true
    network: ws
    ws-opts:
      path: /vpnkit
      headers: { Host: ${DOMAIN} }
    udp: true
EOF
    ;;
  tuic|tuic-v5)
    URI="tuic://${UUID}:${HY2_PASS}@${IP}:${P}?sni=${SNI}&alpn=h3&congestion_control=bbr&allow_insecure=1#vpnkit-self-${IP}"
    cat > "$OUT/clash_self.yaml" <<EOF
proxies:
  - name: "SELF-TUIC-${LOC}"
    type: tuic
    server: ${IP}
    port: ${P}
    uuid: ${UUID}
    password: ${HY2_PASS}
    sni: ${SNI}
    alpn: [h3]
    congestion-controller: bbr
    skip-cert-verify: true
    udp: true
EOF
    ;;
esac

echo
echo "--- 分享链接（粘到任意客户端）---"
echo "$URI"

# 合并进 vpnkit 配置
MERGED="/root/vpnkit_out/vpnkit_plus_self.yaml"
if [[ -f /root/vpnkit_out/base.yaml ]]; then
  python3 - "$MERGED" <<'PYEOF'
import sys, yaml
out = sys.argv[1]
base = yaml.safe_load(open('/root/vpnkit_out/base.yaml'))
self_proxy = yaml.safe_load(open('/root/vpnkit_out/clash_self.yaml'))['proxies'][0]
base.setdefault('proxies', []).insert(0, self_proxy)
for g in base.get('proxy-groups', []):
    if g['name'] in ('PROXY', 'AUTO-FAST', 'AUTO-FALLBACK'):
        g['proxies'].insert(0, self_proxy['name'])
yaml.safe_dump(base, open(out, 'w'), allow_unicode=True, sort_keys=False)
print('merged ->', out)
PYEOF
fi

echo
echo "--- 常用命令 ---"
echo "  systemctl status sing-box"
echo "  journalctl -u sing-box -f"
echo "  改端口: 编辑 /etc/sing-box/config.json 后 systemctl restart sing-box"
echo
echo "--- 客户端侧（Clash Verge）---"
echo "  配置里加上 $CFG_DIR 对应的 clash_self.yaml 内容，或直接用上面的分享链接"
echo "  hysteria2/tuic 是 UDP，检查运营商/防火墙是否放行 UDP ${P}"
echo "=================================================================="
