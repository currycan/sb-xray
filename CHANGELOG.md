# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

版本命名：`MAJOR.MINOR.PATCH`，其中 MAJOR/MINOR 与底层 Xray-core 版本对齐；PATCH 为本项目发布迭代号。

---

## [Unreleased]

（占位）

---

## [26.4.17] — 2026-04-21 · 2026-04 大升级（底层 Xray v26.4.17）

> 本次升级覆盖 **可观测化 / 抗审查 / 内网穿透 / 单核收敛** 四条产品主线。配套 52 条静态规约（`scripts/test_smoke.sh`）+ 生产环境 E2E 验证通过。
>
> 客户端订阅 URL **全部保持不变**，升级无感。所有实验性能力默认关闭，按需通过 `ENABLE_*` 开关启用。

### Added（新增功能）

**可观测与稳固**
- **事件总线化**：引入 `scripts/shoutrrr-forwarder.py` 作为 HTTP sidecar 接收器，监听 `127.0.0.1:18085`，把 Xray `rules.webhook` 推送的 JSON 事件转发到 `shoutrrr` CLI（Telegram / Discord / Slack / 等 20+ 通道）。
- **Ban-rule webhook**：`templates/xray/xr.json` 的 `ban_bt` / `ban_geoip_cn` / `ban_ads` / `private-ip` 四条路由规则均接入 webhook，命中即推送带元数据（protocol / source / destination / email / inboundTag / outboundTag / ts）的告警。
- **TLS 诊断命令**：`show-config.sh` 集成 `tls_ping_diagnose` 函数，`DEBUG=1` 时打印 `${CDNDOMAIN}:443` 与 `${DOMAIN}:443` 的 leaf 证书指纹 / ALPN / 加密套件。

**抗审查（adv 已于 2026-04 并入主轨，三轨→两轨）**
- **XHTTP obfuscation 新字段 + Finalmask fragment**：`xPaddingQueryParam` / `xPaddingPlacement` / `UplinkDataPlacement` + `finalmask.tcp.fragment` 已**直接合进 `02_xhttp_inbounds.json` 主轨**。Xray-core 26.3.27+ 客户端自动获得全套能力；低版 Xray-core 与 mihomo / sing-box 客户端降级到 `v2rayn-compat`。
- **原 `v2rayn-adv` 独立订阅轨已退役**。原独立 `02_xhttp_adv_inbounds.json` 入站、nginx `/xxx-xhttp-adv` location、`V2RAYN_ADV_SUBSCRIBE` 订阅产物、`show_info_links` 的 adv 入口全部移除。最终客户端订阅结构简化为 **两轨**：`v2rayn`（全能力）+ `v2rayn-compat`（无 VLESS 加密，TCP xhttp）。

**VLESS Reverse Proxy 内网穿透**
- **feature flag `ENABLE_REVERSE`**：默认 `false`。启用后 entrypoint 用 `jq` 往 `01_reality_inbounds.json.clients` 追加一个带 `reverse.tag=r-tunnel` 标记的 UUID，并按 `REVERSE_DOMAINS`（逗号分隔域名列表）往 `xr.json.routing.rules` 前置插入 `outboundTag=r-tunnel` 规则。
- **双 UUID 独立**：`XRAY_REVERSE_UUID` 与 `XRAY_UUID` 由 entrypoint 分别生成，不冲突；reverse 身份禁止用于正向代理（Xray v25.12.8 commit a83253f 起的安全边界）。
- **落地机配套模板 `templates/reverse_bridge/client.json`**：扁平化 simplified outbound 格式，通过 REALITY 回连 VPS portal，家宽无需公网 IP 即反向挂载。
- **部署文档 `docs/06-reverse-proxy-guide.md`**：含 portal + bridge 两端步骤、故障排查、撤销流程。

**Xray 单后端收敛与实验性入站**
- **Xray 原生 Hysteria2 入站**（永久替换，无开关）：`templates/xray/04_hy2_inbounds.json` 永久取代 `templates/sing-box/01_hysteria2_inbounds.json`。端口 / 密码 / obfs / ALPN 与 sing-box 版本**完全等价**，客户端订阅 URL 不变（`hysteria2://${SB_UUID}@${DOMAIN}:6443/?sni=...&obfs=salamander&obfs-password=...&alpn=h3`）。**无 feature flag**：Hy2 永久由 xray 承载，替换原有 sing-box 方案，降低引擎维护面。
- **XHTTP/3 + BBR 入站**（永久启用，无开关）：`templates/xray/02_xhttp_h3_inbounds.json` 直接监听 UDP `${PORT_XHTTP_H3:-4443}` + HTTP/3 + BBR 拥塞控制，绕开 nginx 直连内核。模板内置 adv 字段（`xPaddingQueryParam` / `xPaddingPlacement` / `UplinkDataPlacement`）。对应节点 `Xhttp-H3+BBR` **进入 `v2rayn` 主轨（排第一位，性能优先）**；`v2rayn-compat` **不含 H3**（sing-box / mihomo 的 xhttp transport 是 TCP-only，不支持 QUIC / H3）。与 `02_xhttp_inbounds.json` **互补不替换**：02_xhttp 走 TCP/443 经 nginx，兼容 CDN（Cloudflare 到源站不支持 H3 upstream）+ 兼容 UDP-受限网络 + 兼容 Xray-core <26.3.27 的低版本客户端（这类客户端命中 H3 节点延迟 -1 会自动跳过）；02_xhttp_h3 仅适用 Xray-core 26.3.27+ 且直连（非 CDN）场景。
- **XICMP 紧急通道**（抗封锁备选）：`templates/xray/05_xicmp_emergency_inbounds.json`，`ENABLE_XICMP=false` 默认关闭。**仅在常规 TCP/443 + UDP/443 + UDP/4443 都被封锁的极端场景下启用**；ICMP echo 载荷承载代理流量（mKCP transport），需 `docker-compose.yml` 打开 `cap_add: [NET_RAW]`。
- **XDNS 紧急通道**（抗封锁备选）：`templates/xray/06_xdns_emergency_inbounds.json`，`ENABLE_XDNS=false` 默认关闭。**仅在 XICMP 也不可达但 DNS 可用的极端场景下启用**；DNS 查询载荷承载代理流量（类似 DNSTT），需用户控制的 NS 域名 `XDNS_DOMAIN=ns.example.com`。文件命名带 `_emergency_` 特征区分常规通道。
- **feature flag `ENABLE_ECH`**（占位）：env 开关已注册，**TLS 层接入尚未实现**，启用暂无效果，预留给下次 release。
- **entrypoint feature-flag 过滤器**：`createConfig()` 遍历 `/templates/xray/*.json` 前按文件名查 `ENABLE_*`，关闭时 `rm -f` WORKDIR 残留（避免升级后关掉 flag 但老文件继续生效的情况）。

**跨横切**
- **`scripts/test_smoke.sh` 规约体系**：按特性分组共 52 项静态规约；`SKIP_COMPOSE=1` 支持 CI 纯静态运行。
- **Dockerfile env 新增**：`ENABLE_XICMP` / `ENABLE_XDNS` / `ENABLE_ECH` / `ENABLE_REVERSE` / `REVERSE_DOMAINS` / `PORT_XHTTP_H3` / `PORT_XICMP_ID` / `PORT_XDNS` / `XDNS_DOMAIN` / `SHOUTRRR_URLS` / `SHOUTRRR_FORWARDER_PORT` / `SHOUTRRR_TITLE_PREFIX` / `LOG_LEVEL`。（**Hy2 / XHTTP-H3 无开关，永久启用**；仅 emergency 通道 + ECH 占位 + Reverse 需要 flag）

### Changed（行为变更）

- **源 IP 真实化**：`02_xhttp_inbounds.json` / `02_xhttp_compat_inbounds.json` / `03_vmess_ws_inbounds.json` 的 sockopt 添加 `"trustedXForwardedFor": ["X-Forwarded-For"]`（[Xray #5331](https://github.com/XTLS/Xray-core/pull/5331)）。nginx 前置写入的真实客户端 IP 不再被 xray 忽略，access log / webhook 事件的 source 准确。
- **DNS 抗故障**：`xr.json` 的 DNS 段启用 `enableParallelQuery: true` + `serveStale` 乐观缓存（[#5237](https://github.com/XTLS/Xray-core/pull/5237) / [#5239](https://github.com/XTLS/Xray-core/pull/5239)）。单 DNS 服务器故障不再硬等 4 秒，首次访问延迟显著降低。
- **隐私合规**：`xr.json` log 段添加 `"maskAddress": "/16+/64"`（[#5570](https://github.com/XTLS/Xray-core/pull/5570)）。access log 里 IPv4 自动掩码前 16 bit、IPv6 前 64 bit。
- **模板编号重排（最终形态）**：xhttp 家族全部归入 `02_` 前缀；emergency 后移保持紧凑：
  - `hy2` 独立占 04（与 sing-box 家族对齐）
  - `xhttp_h3` 归入 02_xhttp_* 家族
  - `xicmp` / `xdns` 加 `_emergency_` 特征 + 滑位到 05/06
- **02_xhttp_h3 内置 adv 字段**：H3 客户端必然 Xray-core 26.3.27+，模板直接合并 `xPaddingQueryParam` / `xPaddingPlacement` / `UplinkDataPlacement`（等价 xhttp obfuscation 字段集）。不另建 `_h3_adv` 变体。`finalmask.fragment` 是 TCP-only，H3 用 `finalmask.quicParams.congestion=bbr` 做 QUIC 级整形。
- **v2rayn 主轨订阅序**：`Xhttp-H3+BBR` 节点排在第一位（性能优先原则；客户端按实测 RTT 重排，显示序保留 H3 优先）。
- **sing-box 职责收窄**：从 "Hy2 + TUIC + AnyTLS" 三协议缩减为仅 "TUIC + AnyTLS"；Hy2 由 Xray 接管。`templates/sing-box/` 剩 `01_tuic_inbounds.json` + `02_anytls_inbounds.json` + `sb.json`。
- **entrypoint 启动日志**：`[阶段 1]` 输出 `hy2=${PORT_HYSTERIA2}(xray)`，永久标注 Hy2 后端为 xray（与永久迁移一致，不再动态分支）。

### Fixed（问题修复）

- **容器重启崩溃循环**（[598dfc8](https://github.com/currycan/sb-xray/commit/598dfc8)）：stale supervisor socket 导致 supervisord 启动失败 → 容器 restart loop。`templates/supervisord/daemon.ini` 与 entrypoint 增加启动前清理。
- **Vmess-Adv 节点导入失败**：v2rayN 导入订阅时 Vmess-Adv 节点延迟显示 -1。根本原因是 VMess URL 标准不承载 Finalmask 字段，v2rayN UI 也未暴露 Finalmask 手动配置入口，客户端不发 SSH banner 与服务端握手对不上。**决策**：彻底删除 `templates/xray/03_vmess_ws_adv_inbounds.json` + nginx `/xxx-vmessws-adv` location；`V2RAYN_ADV_SUBSCRIBE` 不再包含 Vmess-Adv URL。触发未来重启的条件：XTLS/BBS discussions/716 把 `fm=` 字段推进到 vmess URL，或主流客户端 UI 暴露 Finalmask 手动配置。
- **smoke test 路径错误**：`docs/08-reverse-proxy-guide.md` → `docs/06-reverse-proxy-guide.md`。

### Removed（移除）

- `templates/sing-box/01_hysteria2_inbounds.json`（Hy2 已迁至 Xray）
- `templates/xray/03_vmess_ws_adv_inbounds.json`（见 Fixed 的 Vmess-Adv 决策）
- `templates/xray/02_xhttp_adv_inbounds.json`（2026-04 合并进 `02_xhttp_inbounds.json` 主轨）
- nginx `/${XRAY_URL_PATH}-xhttp-adv` location + `udsxhttp-adv.sock`（同上合并）
- `V2RAYN_ADV_SUBSCRIBE` 订阅产物 + `WORKDIR/subscribe/v2rayn-adv` 输出（三轨→两轨）
- `buildMphCache` CLI 调用 + `XRAY_MPH_CACHE` env 规划：PR #5505 被 upstream PR #5814 revert（2026-04-13），新方案是运行时自动生效的 matcher group 优化，无需 CLI / env。

### Security（安全）

- `allowInsecure` 规避：本项目主订阅走 REALITY / XHTTP，不使用 `allowInsecure`，不受 [Xray 2026-06-01 自动禁用截止日期](https://github.com/XTLS/Xray-core/pull/5624)影响。
- REALITY 入站继续使用 `mlkem768x25519plus.native.<ttl>.${XRAY_MLKEM768_SEED}` 后量子加密（PQ-safe）。
- VLESS Reverse UUID 默认禁止用于正向代理（Xray commit a83253f）。

### Deprecated（废弃）

- `ENABLE_ECH` env 目前仅占位，启用后无实际效果。下次 release 完成 TLS 层接入前，不建议在生产环境期待其行为。

### Migration notes（迁移说明）

- **v2rayn-adv 订阅轨退役**：如果你之前订阅了 `https://${CDNDOMAIN}/sb-xray/v2rayn-adv`，**改订阅为 `/v2rayn`**（主轨已吸收所有 adv 能力 + H3 主轨节点）。老 `/v2rayn-adv` URL 将返回 404（订阅文件已不再生成）。
- **低版 Xray-core（<26.3.27）客户端**：主轨 `02_xhttp` 现已包含 xhttp obfs 新字段，这类低版客户端命中会握手失败。**改订阅 `/v2rayn-compat`** 使用无 ML-KEM 的 TCP xhttp 节点。
- **Hy2 客户端**：**无需任何操作**。服务端升级后 Xray 永久接管 6443/UDP（无回退开关），参数完全等价于原 sing-box 版本，客户端订阅 URL 不变。
- **XHTTP/3 启用**：**服务端默认自动启用**，无需开关。客户端要求 v2rayN 26.3.27+ / Xray CLI 26.3.27+；宿主机防火墙需放行 UDP `${PORT_XHTTP_H3:-4443}`（未放行时 H3 节点显示超时，其他节点不受影响）。`v2rayn` 主轨订阅含 H3 节点（排第一位）；`v2rayn-compat` 不含（sing-box / mihomo 不支持 xhttp-h3）。
- **VLESS Reverse 启用**：见 `docs/06-reverse-proxy-guide.md`。
- **XICMP 启用**：`docker-compose.yml` 取消注释 `cap_add: [NET_RAW]`；无标准化客户端 URL，需手动拼链接。
- **XDNS 启用**：需用户持有 NS 域名并把 `XDNS_DOMAIN` 指向 VPS 的 NS 记录；需防火墙放行 UDP `${PORT_XDNS:-5353}`。

### 配套使用文档

- **新特性使用指南**：[`docs/07-new-features-guide.md`](./docs/07-new-features-guide.md) —— 按特性列出"做什么 / 何时用 / 怎么开 / 如何验证 / 故障排查"。
- **反向代理部署指南**：[`docs/06-reverse-proxy-guide.md`](./docs/06-reverse-proxy-guide.md)

### 验证

- **静态规约**：`SKIP_COMPOSE=1 bash scripts/test_smoke.sh` → **52 通过 / 0 失败**
- **生产 E2E**（2026-04-21）：
  - 10 个 supervisord program 全部 RUNNING（健康检查通过）
  - `xray -test -confdir /sb-xray/xray/` → `Configuration OK`
  - Hy2 端口 6443/UDP 被 xray pid 绑定；sing-box 只剩 tuic+anytls
  - sing-box 作 Hy2 client 端到端握手 → `http=200 time=0.033s`，远端出口 IP 匹配 VPS 公网 IP
  - 订阅 URL 保持完全不变（客户端无感迁移）

### 回滚（如遇问题）

```bash
# 本次 release 部署前保留了回滚 tag
docker tag currycan/sb-xray:before-2026-04 currycan/sb-xray:latest
cd /root/sb-xray && docker compose up -d
# 30 秒内回滚到上一版镜像
```

---

## [先前版本]

> 历史版本未维护此 CHANGELOG；提交历史见 `git log` 或 GitHub Releases。

- `e82a9dc` — init: sb-xray proxy platform v26.4.14
- `aa9e77f` — feat: replace logo with SVG
- `2bd95cf` — feat: add LOG_LEVEL env var
- `ac1e942` — feat: dual-track subscription (mihomo/sing-box compat)
- `598dfc8` — fix: prevent docker restart crash loop

---

[Unreleased]: https://github.com/currycan/sb-xray/compare/v26.4.17...HEAD
[26.4.17]: https://github.com/currycan/sb-xray/compare/v26.4.14...v26.4.17
