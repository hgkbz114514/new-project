# vpnkit

**自己给自己续命的电脑端代理。** 采集公开节点 → 逐个真实压测 → 只留下真能用的 → 定时自动重建。
不需要订阅，不改任何客户端二进制。

```text
抓 4 万个节点  →  TCP 探活  →  代理真连通  →  并发 TLS 门禁  →  实测吞吐  →  生成配置  →  自动部署
   41279          2252          126            两道关             65 个          vpnkit.yaml   每 4 小时
```

实测数据（2026-10-25，一台普通 Windows 机器）：

| 指标 | 数字 |
|---|---|
| 去重后节点 | 41 279 |
| TCP 探活通过 | 2 252（5.5%） |
| 真能转发流量 | 126（0.3%） |
| 通过并发 TLS 门禁 | 65 |
| 实测 ≥500 KB/s | 19 个，最快 985 KB/s |
| 出口国家 | 13 个（US / DE / NL / RO / FR / IT / SG / HK …） |
| 全流水线耗时 | 约 6 分钟 |

走系统代理的真实结果：youtube 200/3.4s、github 200/1.3s、netflix 200/2.2s、
claude.ai 403/5.6KB（Cloudflare 正常拦截，说明链路通）、baidu 200/0.3s（走直连规则）。

> **先读这段**：免费公共节点的带宽是物理上限，0～1 MB/s 是常态，晚高峰更差。
> 想要稳定看 4K 或长期挂机，看 [第 5 节自建独享节点](#5-想要永远不掉自建独享节点) —— 一个月 5 美元的 VPS 就够。
> 本项目的价值不是"提供节点"，而是**把一堆七零八落的公开节点自动筛成一份能用的配置**。

---

## 0. "破解 VPN"到底卡在哪

任何 VPN 软件都是三层授权，**只破第一层等于没破**：

| 层 | 是什么 | 破了能怎样 | 破不了会怎样 |
|---|---|---|---|
| ① 本地许可 | 客户端自己的 license / 试用计数 | 界面显示"高级会员" | 弹试用到期 |
| ② 服务端授权 | 登录后服务端签发 Entitlement | 显示有权限 | 服务端不认，登录即掉 |
| ③ **节点授权** | 服务端下发的服务器列表与凭据 | —— | **拿不到节点 = 连不上任何服务器** |

第 ③ 层是死结：节点凭据由服务端加密下发并绑定账号，本地伪造没有意义。
所以真正的"长期可用"不是改客户端，而是**让节点这一层自给自足**。

本项目走的是前两条：

- **方案 A（默认）**：开源客户端 + 自动采集并逐个真实验证的节点池 → 免费、自动续命
- **方案 B（推荐加装）**：你自己 VPS 上的独享节点 → 速度/稳定性拉满

---

## 1. 装起来

### 前置条件

| 需要 | 说明 |
|---|---|
| Windows 10/11 | 走系统代理用的是 WinINET API，Windows 专属 |
| Python 3.8+ | 只用标准库 + `requests`、`PyYAML` |
| [Clash Verge Rev](https://github.com/clash-verge-rev/clash-verge-rev/releases) | 提供 `verge-mihomo.exe` 内核。装好即可，**不用在里面加订阅** |

```powershell
git clone https://github.com/<you>/vpnkit.git
cd vpnkit

# 一条命令搞定：装依赖、找内核、跑第一遍流水线
setup.bat
```

`setup.bat` 会做完四件事：找 Python → 装 `requests`/`pyyaml` → 定位 `verge-mihomo.exe` → 跑一次完整采集压测（约 6 分钟）。
中间任何一步失败都会明确告诉你缺什么。

想手动来也行：

```powershell
pip install -r requirements.txt
python bin\vpn.py doctor     # 先体检：内核在哪、配置能不能吃、端口空不空
python bin\vk.py all         # 采集 → 探活 → 压测 → 生成 → 部署
python bin\vpn.py start      # 拉起内核 + 打开系统代理
```

`vpn.py start` 做三件事：拉起内核 → 等 7897 端口就绪 → 打开 Windows 系统代理。
之后浏览器、微信、Steam 立刻走代理。

### 常用命令

```powershell
python bin\vpn.py doctor     # 体检
python bin\vpn.py status     # 内核 pid / 端口 / 节点数 / 当前分组 / 当前出口
python bin\vpn.py test       # 测出口 IP + 关键站点连通性
python bin\vpn.py stop       # 关闭内核 + 关系统代理
python bin\vpn.py refresh    # 重新跑一遍采集压测流水线
python bin\vpn.py autostart  # 装开机自启 + 每 4 小时自动刷新
```

也可以双击 `vpnkit.bat` 用菜单操作（13 项）。想放桌面快捷方式：

```powershell
python bin\make_shortcuts.py    # 在桌面建「控制台」和「秒开」两个快捷方式
python bin\make_icon.py         # 想换图标就重新生成 vpnkit.ico
```

改过 `.bat` 之后跑一下这个静态检查（`cmd.exe` 有几个专坑批处理的语法陷阱，
这个脚本会把它们全抓出来，退出码非 0 就是有问题）：

```powershell
python bin\lint_bat.py          # 检查仓库里所有 .bat
python bin\lint_bat.py 某个目录   # 也可以只查指定目录
```

### 路径探测

内核和 geo 数据都不写死路径。`bin/vkcore.py` 按下面顺序找：

1. `VK_CORE` 环境变量（显式指定）
2. 项目自带的 `bin/core/`
3. `PATH` 里的 `verge-mihomo` / `mihomo` / `clash-meta`
4. 各盘符下的 `Clash Verge`、`Program Files\Clash Verge` 等常见目录

想指定位置：

```powershell
set VK_CORE=D:\Tools\mihomo\mihomo.exe
set VK_GEO=D:\Tools\mihomo\resources
```

项目自身的路径全部由 `Path(__file__).resolve().parent.parent` 推出，
**克隆到任何盘符、任何目录都能直接跑**。`profiles/`、`09_logs/` 首次运行时自动创建。

---

## 2. 长期可用是怎么实现的

```
         ┌────────────── 每 4 小时自动跑一轮（guard.py）──────────────┐
         │                                                          │
 130 个候选订阅源                                                    │
         ↓ discover_sources.py   只留"200 且真能抠出节点"的源        │
   存活源清单 sources_live.txt                                       │
         ↓ harvest.py            ss/vmess/vless/trojan/hysteria2/    │
                                 tuic/anytls/ssr，去重               │
 4~8 万节点                                                          │
         ↓ probe.py              TCP 并发探活（约 3 分钟）           │
   ~2500 节点                                                        │
         ↓ test_live2.py         起真内核做多目标 delay 测活          │
   ~65~130 节点真正能转发                                            │
         ↓ verify_exit.py        逐节点取真实出口 IP / 国家 / ISP     │
   ~65 节点（带国家标签）                                            │
         ↓ build_config.py       先出一版，让下一步有节点名可用       │
         ↓ speed_probe.py        临时内核 + 每节点独立 listeners 入口 │
                                 并行 curl 实测吞吐（全量 50 秒）     │
         ↓ ghscan.py             同样并行，真 GET github 验证         │
         ↓ build_config.py       按实测吞吐分层 + 生成 vpnkit.yaml    │
         ↓ vk.py deploy          注册进 Clash Verge profiles         │
         └──────────────────────────────────────────────────────────┘
```

关键设计：**每一层都是真流量验证**。

- TCP 通 ≠ 代理通：2537 个 TCP 存活节点里只有 126 个真能转发（**95% 是废节点**）。
- 延迟低 ≠ 能打开网页：url-test 只测 TTFB，实测过 208ms 的节点带宽 **8.2 KB/s**，
  表现就是"显示已连接但网页一直转圈"。所以自动组按**实测吞吐**筛，不按延迟筛。
- `/delay` 接口不校验证书，会把"TLS 被中间盒替换"的节点算成可用 ——
  github 必须真 GET 一次拿到 50KB+ 内容才算过。
- 握手能过 ≠ 扛得住并发：2026-10-25 实测一个 TW vless 节点，单流 1259 KB/s、
  连续 4 次握手全过，但用它跑 62 站并发只到 **33/62**；同组的 http 节点是
  **51/62**。免费中继按连接限速，单连接成功不构成证据。
- **并发门禁不能跟在并发里一起跑**。第一版把 6 路握手塞进 24 线程的 worker，
  等于 120 路同时打进一个临时内核 —— 结果是探针自己把内核压垮，20 个节点里
  15 个被判"TLS 断裂"。用 `gate_audit.py` 串行复查这 5 个"断裂"节点：
  **4/4 全通、实测 180~1973 KB/s**，全是误杀。所以现在分两阶段：

  1. **阶段一**（24 线程）：串行 TLS 4 次 + 单流吞吐采样。TLS 真死的在这里就出局
     （实测拦下 17 个，报错是 `ConnectionError@1/4` / `ReadTimeout@4/4`）。
  2. **串行复测**：阶段一里 TLS 通过但采到 0 字节的，单独重测一次。24 路并发下
     慢节点会输掉采样竞争，`kbps=0.0` 是采样事故不是节点坏了 —— 钉死的 Claude
     出口独立测是 370~6300 KB/s，在并发采样里却是 0.0。
  3. **阶段二**（4 线程）：只有已经跑出字节的节点才进 6 路并发门禁。降到 4 路
     并行后，同样的 49 个活节点里只拦下 **1 个**（`013 FR` 5/6），误杀归零。

  修好之后：**19 个 >=500 KB/s**（修前 14 个）、27 个 >=100 KB/s（修前 17 个）。

所以 `speed_probe.py` 的两道关是**先后串行**的：先串行 TLS 4 次，再阶段二的 6 路
并发握手。任一道不过，`tls_ok=false`，不进 SPEED-TOP / AUTO-*，只留在全量列表里
供手动应急。

### 装成开机自启（推荐）

```
vpnkit 控制台 → [6] 安装开机自启
```

做两件事：

1. 计划任务 `vpnkit-guard` 每 4 小时跑一轮刷新（后台无窗口）
2. 启动文件夹放 `vpnkit.lnk`，登录自动 `vpn.py start`

> 启动项 `vpnkit.lnk` 用的是 **pythonw.exe**（无控制台）。这正是 `vkstdio.harden()`
> 存在的原因：pythonw 下 `sys.stdout` 是 `None`，任何 `print` 或
> `sys.stdout.reconfigure()` 都会立刻抛 `AttributeError`，进程启动即死 ——
> 表现为"自启了但端口是黑的"。

---

## 3. 客户端里怎么用（可选，图形界面）

`vk.py deploy` 已经把配置注册进 Clash Verge 了：

- 打开 `<Clash Verge 安装目录>\clash-verge.exe`
- 「配置」页能看到 `vpnkit-xxxx` 这一条，处于选中状态
- 打开「系统代理」开关，或开「TUN 模式」（全局，需管理员）

想让它**永久自动更新**，用本地订阅服务：

```
vpnkit 控制台 → [8] 启动本地订阅服务
```

然后在 Clash Verge 里新建订阅：`http://127.0.0.1:7777/sub`
这个地址永远返回"当下最新、刚验证过"的配置，客户端只配这一次。

订阅服务是**纯读取方**：它监视 `profiles\vpnkit.yaml` 的 mtime，文件一变就换内存里的字节。
刷新只由 `guard.py` 一个写者负责 —— 两个进程各自跑刷新链会抢临时内核端口，也会一轮重建两遍配置。

```powershell
# 只想看服务状态
curl http://127.0.0.1:7777/status
```

---

## 4. 节点分组说明

| 分组 | 作用 |
|---|---|
| `PROXY` | 总出口，默认指向 `SPEED-TOP` |
| `SPEED-TOP` | **实测 ≥500 KB/s** 的节点（默认用这个，最快） |
| `AUTO-FAST` | 实测 ≥300 KB/s 里延迟最低的（url-test，300s 一轮） |
| `AUTO-FALLBACK` | 主节点挂了自动切下一个（fallback，最稳） |
| `LOAD-BALANCE` | 多节点分摊带宽（下载大文件用） |
| `GIT` | github 全系域名专用（真 GET 验证过的节点） |
| `CLAUDE` | **钉死单一出口**，给 Claude / Anthropic / Persona 用，见下节 |
| `MEDIA` | YouTube / Netflix / Disney+ / Spotify |
| `AI` | OpenAI / Gemini（不含 Anthropic） |
| `TELEGRAM` | Telegram |
| `AUTO-XX` | 按国家分（US / NL / DE / RO / FR / IT / SG / HK…） |

国内站点（B 站、微信、淘宝、百度…）走 `GEOSITE,cn,DIRECT` 直连，不浪费节点带宽。
实测 baidu 115ms、bilibili 304ms，确认直连没被代理掺一脚。

### 4.1 为什么 Claude 要单独一个 `CLAUDE` 组

Anthropic 的封号理由里明写着 **"从不支持的位置创建账户"**，而 KYC 过程中出口 IP
一跳国家，Persona 会直接判验证失败。所以 Claude 这条链上的域名不能走
`url-test` —— 那种组每隔几分钟按延迟重选一次，会在 US/NL/RO 之间来回跳。

`CLAUDE` 组是 **select 型，第一个成员永远是被钉住的那个节点**：

```bash
python bin/claude_pin.py --top 70 --threads 14 --force   # 重新选一个钉子
python bin/vpn.py start                                   # 启动时会强制钉回去
python bin/vpn.py status                                  # 第七行显示钉子状态
```

`claude_pin.py` 的判定比普通测活严得多，两个门禁都要过：

| 门禁 | 判定 |
|---|---|
| `api.anthropic.com/v1/models` | 无 key 应返回 **401**；2xx~4xx 才算 TLS 通了 |
| `claude.ai/login` | Cloudflare 会回 **403**；把 403 当通过，因为它证明真到了边缘节点 |

命中后再做 **3 轮稳定性复测**，至少 2 轮通过才允许当钉子 —— 只过 1 次的节点
（例如 `172.64.68.89:443` 这种 Cloudflare 边缘 IP 的 `http` 型代理）会先给你
`api=401` 再突然 `dial tcp ... i/o timeout`，固定到它等于让验证中途断线。

出口国家必须在 Anthropic 支持清单里（US/GB/CA/DE/NL/FR/IT/IE/SG/JP/AU… 共 23 个），
RU/HK/TW/CN 一律不选。

`claude_pin.json` 里同时存了 `candidates`（实测同时通 api 与 web、且地区合格的
备选，按稳定性排序）。重建配置时这些会排在 `CLAUDE` 组成员的前面，钉子万一失效，
你手动切到第二个也是干净的出口。

**两个坑（都实测踩过）**：

1. **curl 测不出来**。Windows 版 curl 用 Schannel 后端，默认做证书吊销检查；
   Anthropic 证书链里的 CRL 分发点在免费节点上不可达，Schannel 直接放弃并返回
   `000`，看着像"节点全废"。实测对比：

   ```
   curl  https://claude.ai/login                 -> 000  (0.6s)
   curl  https://claude.ai/login --ssl-no-revoke -> 403  (2.2s, 5346 bytes)
   requests                                      -> 403  (0.8s)
   ```

   所以 `claude_pin.py` 一律走 `requests`（OpenSSL，不查吊销）。

2. **mihomo 会把上次的手动选择记在 `cache.db` 里**，重启后静默恢复成那个，
   于是钉好的 `001 US` 会变回 `000 US`。`vpn.py start` 里的 `enforce_pin()`
   每次启动后都会把组选择写回 `claude_pin.json` 里的节点。


---

## 5. 想要"永远不掉"→ 自建独享节点

免费公共节点的现实：单节点存活 2 小时 ~ 3 天，到期连不上是常态，
而且几百人共享带宽，高峰期慢。**这是免费方案的物理上限，不是配置问题。**

要彻底解决只有一条路：自己的节点。

```bash
# 在任意 VPS 上（root 执行），一条命令搞定
bash deploy_node.sh --proto hysteria2 --port 443
# 或者最抗封锁的 REALITY（不需要域名）
bash deploy_node.sh --proto vless-reality
```

脚本做完：安装 sing-box 1.11.15 → BBR 调优 → 自签证书 → systemd 常驻
→ 防火墙放行 → 输出分享链接 + 可直接合并的 clash 配置。

产出物 `/root/vpnkit_out/clash_self.yaml`，粘进 `vpnkit.yaml` 的 `proxies:` 段即可。
建议插到列表最前面，它会成为各自动组的首选。

VPS 成本参考：1 核 1G 境外小鸡年付 ¥100~200，独享带宽，比任何订阅都稳。

---

## 6. 目录结构

```
<安装目录>\
├─ vpnkit.bat                  ← 控制台启动器（纯 ASCII，避开 cmd 的 OEM 编码坑）
├─ vpnkit.ico                  ← 图标（7 个尺寸 16~256，纯标准库生成）
├─ bin\
│  ├─ menu.py                  控制台菜单本体（中文全在 Python 里）
│  ├─ vpn.py                   启动/停止/状态/测速/刷新/自启
│  ├─ vk.py                    全流水线 all/quick/build/deploy/status
│  ├─ guard.py                 4 小时守护（唯一刷新写者，--once 给计划任务）
│  ├─ subserver.py             本地订阅服务 :7777（纯读取方）
│  ├─ discover_sources.py      源自愈发现
│  ├─ harvest.py               多协议 URI 解析 + 去重
│  ├─ probe.py                 TCP 并发探活
│  ├─ test_live2.py            真内核多目标 delay 测活 + 二分校验
│  ├─ filter_exit.py           出境三目标交叉验证
│  ├─ geo_tag.py               服务器 IP 国家标注
│  ├─ verify_exit.py           逐节点真实出口 IP
│  ├─ speed_probe.py           并行实测吞吐（listeners 绑定）
│  ├─ ghscan.py                并行真实 github 可达性
│  ├─ claude_pin.py            给 Claude 钉死单一出口（双门禁 + 稳定性复测）
│  ├─ build_config.py          生成 clash 配置（分层分组）
│  ├─ vkstdio.py               pythonw 下的 stdio 兜底（无控制台启动必备）
│  ├─ report.py                交付报告：分组 / 最快节点 / 国家覆盖
│  ├─ diag.py                  单节点失败原因诊断
│  ├─ sources*.txt             订阅源清单
│  ├─ _archive\                一次性诊断脚本（historical）
│  └─ deploy_node.sh           VPS 自建节点一键脚本
├─ profiles\                   ← 运行后自动生成，已在 .gitignore
│  ├─ vpnkit.yaml              最终配置（内核可直接吃）
│  ├─ claude_pin.json          Claude 钉住的出口与备选清单
│  ├─ nodes_*.json             各阶段节点数据
│  └─ geoip.dat / GeoSite.dat / Country.mmdb / ASN.mmdb
├─ 09_logs\                    ← 运行日志（*.log 结构化 / *.console.log 崩溃兜底）
└─ 10_backup\                  ← hosts 备份等
```

---

## 7. 排错

**启动后上不了网**

```powershell
python vpn.py status       # 看端口是否 LISTEN、系统代理是否开
Get-Content <你的安装目录>\09_logs\mihomo.out.log -Tail 30
```

> 坑：`port_listening()` 最初写成 `":7897 " in netstat输出 and "LISTENING" in netstat输出`，
> 后半段是**全文件匹配** —— 只要有任意一个端口在监听，就判定所有端口都在监听。
> 内核崩了却报 LISTEN，查了半天才定位。现改为逐行解析 `Proto/Local/Foreign/State/PID`。
> 第二个版本又错在取 `parts[-1]`（那是 PID），正确的状态列是 `parts[3]`。

日志里看 `match GeoSite(...) using XXX` 就知道每个域名走了哪条规则。

**某个站点连不上，但别的都正常**

先确认不是系统 hosts 被别的工具改了：

```powershell
Resolve-DnsName github.com -Type A     # 正常应返回真实 IP，不是 127.0.0.1
Select-String -Path C:\Windows\System32\drivers\etc\hosts -Pattern '127\.0\.0\.1'
```

> 踩过的坑（最贵的一个）：Steam++ / Watt Toolkit 这类加速工具会往系统 hosts 写
> ~70 条 `127.0.0.1 <域名>` 屏蔽条目，包含 `github.com`、`api.github.com`、
> `raw.githubusercontent.com`、`huggingface.co`、`img.youtube.com` 等。
> mihomo 默认 `use-hosts: true` 会读系统 hosts，于是这些域名永远解析到 127.0.0.1，
> **走任何节点都必然 TLS 失败**。
> 本方案的 `dns` 段已显式设 `use-hosts: false` + `use-system-hosts: false` 绕开，
> hosts 文件本身没动（仍可逆）。
> 副作用：Steam++ 的 hosts 级加速/屏蔽对代理流量失效 —— 这正是想要的效果。

**节点全挂了**

```powershell
python vpn.py refresh      # 重跑一整轮（8~15 分钟，含实测吞吐）
```

**某站点被判成直连**

说明它命中了 `GEOSITE,cn`。在 `build_config.py` 的 `rules` 里把对应
`GEOSITE,xxx,PROXY` 提到 `GEOSITE,cn,DIRECT` **前面**即可。

> 另一个坑：`GEOIP,LAN,DIRECT,no-resolve` 会把 fake-ip 段的 `198.18.x.x` 全部命中 LAN，
> 导致所有域名被判成 lan 直连（github 曾经因此连不上）。这条规则已移除。

**配置校验失败**

```powershell
& "<Clash Verge 安装目录>\verge-mihomo.exe" -t -f <你的安装目录>\profiles\vpnkit.yaml -d <你的安装目录>\profiles
```

内核校验极严，任一节点非法（比如 REALITY `short_id` 长度不对）整份配置就挂 ——
这就是 `test_live2.py` 要做二分剔除的原因。

**临时内核端口冲突**

`speed_probe.py` / `ghscan.py` 用私有端口段（17900 / 19097 / 17910+），
检测方式是逐个 `socket.bind` 试，不是读 netstat 文本（早期用
`if f":{p} " in txt and "LISTENING" in txt` 会全局匹配，一条监听就把所有端口判成占用）。

**订阅服务起不来 / 起来又立刻死**

`pythonw.exe` 下 `sys.stdout` 和 `sys.stderr` 都是 `None`。任何
`sys.stdout.reconfigure(...)` 或 `print(...)` 都会抛 `AttributeError`，
进程在第一行日志就死了 —— 表现是端口 7777 闪一下就没了。

三个常驻脚本（`vpn.py` / `guard.py` / `subserver.py`）现在统一在 `main()` 第一行调用
`vkstdio.harden("<tag>")`，它把缺失的流接到 `09_logs\<tag>.console.log`，再做 UTF-8 兜底。

```powershell
Get-Content <你的安装目录>\09_logs\subserver.console.log -Tail 20
```

> 另一个附带坑：tee 的文件名一开始也叫 `<tag>.log`，而每个脚本自己的 `log()`
> 已经在写同名文件，于是每行日志都出现两次，看起来像流水线跑了两遍。
> 现在 tee 走 `.console.log`，结构化日志走 `.log`，互不干扰。

**双击 vpnkit.bat 菜单乱码 / 报 `'更新）' 不是内部或外部命令`**

不要把中文写进 `.bat` 的 `echo` 里。cmd.exe 按 OEM 代码页（本机 936/GBK）
**逐行解析**批处理文件，UTF-8 编码的全角字符（比如 `，` = `EF BC 8C`）
会被当成 GBK 双字节，连带吞掉后面一个 ASCII 字节，字节流从此错位 ——
于是 `echo [8] 启动本地订阅服务（…永久更新）` 这一行会被从中间劈开，
cmd 真去执行 `更新）` 这个"命令"。`chcp 65001` 救不了，错位在定位行时已经发生。

现在 `vpnkit.bat` 只有十几行纯 ASCII，菜单正文全在 `bin\menu.py`。

> 同一个家族还剩两个更阴的坑，都在 `setup.bat` 上真实踩过：
>
> - **`echo ... -> ...` 里的 `>` 是重定向符**。原作者写
>   `echo   [1/4] Python %PYVER%  ->  %PY%`，cmd 实际执行的是
>   `echo   [1/4] Python 3.13.15  - 1>"C:\...\Python313\python.exe"`，
>   于是报 `The process cannot access the file because it is being used by
>   another process`，那一行永远打不出来，**并且有把正在运行的 python.exe
>   截断的风险**。`echo` 里要么别写 `>` `<`，要么写成 `^>` `^<`。
> - **`set /p VAR=<file` 不能写在括号块里**。cmd 在解析整个 `if (...)` 块时
>   就把重定向绑定了，那时文件还没生成 → `The system cannot find the file
>   specified`，`VAR` 留空。要么用单行 `if exist X set /p VAR=<X`，
>   要么先 `call :label` 出去。
> - **`for /f "delims=" %%P in ('"C:\a b\py.exe" -c "..."')` 引号嵌套**。
>   cmd 把 `in ('...')` 交给 `cmd /c` 执行，内层再出现成对引号就在中途断开，
>   报 `'C:\...\python.exe" -c "import' 不是内部或外部命令`。
>   改成先重定向到临时文件、再 `set /p` 读回来。

这三类坑现在由 `python bin\lint_bat.py` 静态扫描（见上文「常用命令」）。

**桌面图标丢了 / 想重建快捷方式**

```powershell
cd <你的安装目录>\bin
python make_icon.py         # 重新生成 vpnkit.ico（7 尺寸）
python make_shortcuts.py    # 重新在桌面放两个快捷方式
```

> 两个都踩过编码坑，所以写法固定下来：
> - **图标不用 PowerShell 画**。`System.Drawing.Drawing2D.GraphicsPath` 的多参数
>   构造在 PowerShell 5.1 里跨行传参会解析失败（`Unexpected token '}'`）；
>   改成 Python 纯标准库手写 PNG（`zlib` + `struct`）+ ICO 容器，不依赖 PIL/GDI+。
> - **快捷方式不用命令行传中文**。`WScript.Shell` 只能通过 COM 调用，而中文文件名
>   经 cmd/powershell 命令行传递时会按 GBK 重新编码，非 GBK 字符变 `?`。
>   改成把整段脚本以 **UTF-16LE + base64**（`-EncodedCommand`）传入，全程 Unicode。

**`[3] 查看状态` 里出现 `出口查询 失败 ... Read timed out`**

旧版 `status()` 单一依赖 `ip-api.com`（HTTP、海外、节点慢时经常要 8 秒以上），
超时就把整条 `requests` 异常打印出来，而那正是用户天天看的一行。
现在 `exit_ip()` 先走 `https://www.cloudflare.com/cdn-cgi/trace`（HTTPS、响应几百字节、
内核自己也在用同一个端点取延迟），失败才回退 ip-api，再失败就输出
`查询超时（节点慢, 不影响使用）` —— 永远是一行干净的中文。

**Claude 打不开 / 一直转圈，但 github、youtube 都正常**

先跑 `python bin\vpn.py status`，看最后一行 `Claude 出口`：

| 显示 | 含义 | 处理 |
|---|---|---|
| `001 US ... 稳定性 3/3` | 正常 | 无需处理 |
| `未钉死` | 从没跑过扫描 | `python bin\claude_pin.py --force` |
| `!! 当前组选的是 XXX` | 被 `cache.db` 恢复成了旧选择 | `python bin\vpn.py start`（会执行 `enforce_pin`） |
| `pin 文件损坏` | JSON 坏了 | 删掉 `profiles\claude_pin.json` 重扫 |

如果钉子节点本身挂了，`CLAUDE` 组里紧跟着的 `candidates` 都是实测能同时通
`api.anthropic.com` 与 `claude.ai`、且地区合格的备选，在 Clash Verge 里手动点第二个即可。

**`claude_pin.py` 报 `!! still busy: [...]`**

上一次扫描被 Ctrl-C 或崩溃中断，留下一个临时内核占着 `17900-17980` 段。
现在脚本会先按命令行里的 `vkcl_` 目录名识别并杀掉这些孤儿（**不会碰主内核**，
主内核的 `-d` 指向 `<你的安装目录>\profiles`）。如果还报，说明占用者不是它，手动查：

```powershell
netstat -ano | Select-String ':179\d\d.*LISTENING'
```

---

## 8. 关键参数速查

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `VK_TIMEOUT` | 5000 | 单节点 delay 测活超时 ms |
| `VK_CONC` | 128 | 测活并发 |
| `VK_ROUNDS` | 2 | 测活轮数（≥2 更稳） |
| `VK_INTERVAL_H` | 4 | 守护刷新间隔小时 |
| `VK_MIN_NODES` | 20 | 低于此数保留旧配置不部署 |
| `VK_TOPN` | 80 | 参与实测吞吐 / Claude 扫描的节点数上限 |
| `VK_REFRESH_MIN` | 180 | 订阅 `Profile-Update-Interval` 头 |
| `VK_SKIP_EXIT` | 0 | 设 1 跳过出口验证（更快） |
| `VK_SUB_AUTOREFRESH` | 0 | 设 1 让订阅服务自己刷新（此时别跑 guard） |

---

## 9. 已知边界

- 免费节点带宽上限普遍在 0~1.7 MB/s，实测最快 `SG vless 1718 KB/s`，
  3 个节点超 1000 KB/s。想要稳定千兆只能自建（方案 B）。
- 节点存活期短，兜底靠守护每 4 小时重采。守护有阈值门禁：
  新池子小于 `VK_MIN_NODES` 就保留旧配置，不会把可用配置刷成空。
- `chatgpt.com` / `claude.ai` 用脚本请求会返回 403，那是 Cloudflare 对非浏览器 UA 的例行拦截，
  真实浏览器里正常。
- 系统代理只覆盖遵循 WinINET 的程序。要覆盖 Steam、部分游戏、UWP，开 Clash Verge 的 TUN 模式（需管理员）。
- **`CLAUDE` 组钉死的出口住在一台免费节点的 IP 上，而 Anthropic 认的是"这个账号从哪个
  IP 来"。** 钉住只保证"每次是你自己不变"，保证不了"这个 IP 干净"——免费节点的 IP
  是几百人共享的机房地址（Cloudflare/DataCamp/M247），别人拿它注册过什么你控制不了。
  真要过 KYC，独享 IP 比任何客户端技巧都重要（方案 B 自建节点，`deploy_node.sh` 里
  选 `hysteria2` 模式，一台 $5/月 的 VPS 就够）。

---

## 10. 免责与说明

- 本项目**不分发节点、不提供服务器**。`bin/harvest.py` 抓的是互联网上公开分享的
  订阅链接，去留由各自的发布者决定。使用者需自行判断所在地法律与网络服务条款。
- 不做任何绕过付费墙、破解商业服务、盗用他人账号的事。第 0 节讲的三层授权是为了
  说明"为什么破解客户端拿不到节点"，不是教程。
- 公共节点的隐私性无法保证 —— 流量经过陌生人控制的中转。**不要用它登录网银、
  支付、处理公司机密**。有这类需求请用方案 B 自建。
- 代码以 MIT 发布，不附带任何担保。因使用本项目产生的后果由使用者承担。

## 11. 致谢

- [Clash Verge Rev](https://github.com/clash-verge-rev/clash-verge-rev) —— 提供 `verge-mihomo` 内核与 geo 数据
- [Mihomo](https://github.com/MetaCubeX/mihomo) —— 内核本体
- 所有坚持公开分享订阅的维护者