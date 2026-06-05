# Tailscale kernel TUN 模式迁移设计（OpenWrt 客户端）

## 背景与动机

`openwrt/install.sh` 此前以 `--tun=userspace-networking` 模式部署 tailscaled：无 tailscale0 网卡、零防火墙配置即可满足「VPS→OpenWrt:7891 SOCKS5 入站」这一 CN-exit 核心路径（tailscaled 用户态终结连接后以本机进程身份回环 dial，不经 netfilter zone 链）。

现在需要三个 userspace 模式给不了的能力：

1. **吞吐性能**——内核转发取代用户态 netstack
2. **subnet router / exit node**——tailnet 其他设备经 OpenWrt 访问 172.18.18.0/23 内网、以家宽为出口；exit node 流量**经 OpenClash 分流**（CN 直连家宽、非 CN 走代理节点）
3. **本机直连 tailnet**——OpenWrt 上进程可主动 dial 其他节点的 100.x IP

方案选择：**硬切换**（不留 userspace 配置开关），回滚靠 git 旧版脚本重跑。备选的「TS_TUN_MODE 双模式开关」因增加两条代码路径被否决；「userspace + --socks5-server」无法满足吞吐与 exit-node-经-OpenClash 目标。

顺带收益：docs/08 §1.1（userspace 启动参数）与 §1.2（TUN 模式防火墙配置）长期自相矛盾，迁移后 §1.2 名副其实。

## 目标架构

- tailscaled：保留 `--socket/--port=$TS_PORT`，删除 `--tun=userspace-networking` → 内核创建 tailscale0（依赖 kmod-tun）；state 路径移至 `/etc/tailscale/`（/var 是 tmpfs，重启丢登录态）；init.d 的 `START=95/STOP=10` 取消注释（否则 enable 无效，无开机自启）
- `tailscale up --reset --timeout=120s --accept-dns=false --advertise-routes=$TS_ADVERTISE_ROUTES --advertise-exit-node --hostname=$TS_HOSTNAME`
  - `--reset` 清掉旧 prefs（登录态不受影响）；`--timeout` 防 up 无限挂起
  - **不带 `--accept-routes`**：kernel 模式下它会把其他节点已批准的本 LAN 网段路由装进内核，LAN 回包进隧道黑洞、整机失联（实机三次"死机"的根因）。本机是 subnet router 本体，无需接受对端路由
  - 顺带修复现存 bug：`--tun` 不是 `tailscale up` 的合法 flag，此前被 `|| warn` 吞掉

### 四条数据流

| # | 流 | 路径 | 依赖 |
|---|---|---|---|
| 1 | VPS → OpenWrt:7891 SOCKS5 | tailscale0 → input 链 | tailscale zone input ACCEPT |
| 2 | 手机 exit node → 互联网 | forward 链 → OpenClash tproxy 分流 | tailscale→wan forwarding + masq |
| 3 | 手机 → 172.18.18.0/23 | forward 链 → lan | tailscale↔lan forwarding |
| 4 | OpenWrt 本机 → 100.x | 路由表直连 | tailscale0 存在 |

- VPS 侧零改动（SOCKS5 HOST/PORT、Tailscale IP 不变）
- 人工步骤：Tailscale 管理后台批准 routes + exit node

### OpenClash 交互

- **UDP 41641 bypass 钩子原样保留**——kernel 模式下 tailscaled 自身加密 UDP 仍可能被 mangle 劫持
- exit node 经 OpenClash 分流依赖 tproxy 拦截 tailscale0 forward 流量（源 IP 100.64.0.0/10）。若实测拦不到：后续在 OpenClash 代理网段加 100.64.0.0/10，不阻塞迁移（拦不到时流量经 wan masq 直出家宽，功能可用只是没分流）

## 改动清单

1. **`openwrt/install.sh`**：头部注释更新；新增 `ensure_tun()`（kmod-tun preflight）；init.d 模板删 userspace 行；新增幂等 `setup_tun_network()`（network 接口 + tailscale zone + forwarding tailscale↔lan/tailscale→wan，`uci add` 前先探测防叠加）；`tailscale up` 换新 flag；`verify()` 增 tailscale0/zone/路由通告检查；`main()` 接线
2. **`openwrt/config.env.example`**：新增 `TS_ADVERTISE_ROUTES`（默认 172.18.18.0/23，按实际 lan 网段改）
3. **`docs/08-cn-exit-guide.md`**：§1.1 模板与脚本对齐；§1.2 补 tailscale→wan forwarding 与幂等提示
4. **`openwrt/README.md`**：模式描述同步；管理后台批准步骤；回滚命令

## 错误处理

- kmod-tun 装不上 → die（硬切换语义，不静默降级）
- uci 操作失败 → die（半套防火墙比没有更糟）
- `tailscale up` 失败 → `|| warn` 容忍（首次授权场景）

## 验证

脚本级：`sh -n`/`ash -n` 语法检查；实机连跑两遍确认幂等（zone 不叠加）。

端到端（按序）：tailscale0 存在 → VPS `curl --socks5` 回国出口仍家宽 IP（不回归）→ 本机直连 VPS 100.x（流 4）→ 手机 exit node 访问 CN 站显示家宽 IP、`ip.sb` 显示代理节点 IP（流 2 + 分流判定）→ 手机访问内网（流 3）→ 可选 iperf3 量化吞吐。

迁移窗口：tailscaled 重启 + 重新打洞 < 1 分钟；OpenClash、xray bridge 不受影响。

## 回滚

`git checkout 10ce8f9 -- openwrt/install.sh && sh install.sh` 回 userspace 模式；遗留 tailscale zone 无害（恢复为无效配置），README 提供手动清理命令。
