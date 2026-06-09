# Watchtower 全自动镜像更新 — 设计文档

- 日期: 2026-06-09
- 状态: 待评审
- 方案: B（全自动更新 + 漂移兜底纪律）

## 1. 目标

让 16 台生产节点在 `currycan/sb-xray:latest` 更新后**无人值守自动跟进**,免去逐台手动 `docker compose pull && up -d`,同时把"镜像更新但 compose 未同步"的配置漂移风险压到可接受范围。

## 2. 背景与约束

- 镜像 `currycan/sb-xray:latest` 由 `.github/workflows/daily-build.yml` 维护,每天 UTC 02:00(北京 10:00)cron 产出,亦可 `workflow_dispatch` 手动触发。
- 节点统一用 `docker-compose.yml`(单 service `sb-xray`)部署,大量 env 走 `${VAR:-默认}` 引用宿主 `.env`(由 `sources/vps/vps-cn-exit-init.sh` 注入)。
- `docker-compose.yml` 历史被改 26 次,近期几乎每次发布都**新增 env**(`CN_EXIT_MODE` / `CN_EXIT_PROBE_URL` / `REVERSE_DOMAINS` / `ENABLE_SOCKS5_PROXY` …)。即"镜像+compose 同时变"是常态。
- 节点仅对外暴露 443(tcp/udp),不希望为运维新增管理端口/攻击面。

## 3. 关键机制认知

watchtower **不读 `docker-compose.yml`**。它监控运行容器的镜像 digest,发现 `:latest` 更新后,从**现有容器** inspect 出 env/volumes/ports/labels,用新镜像停旧建新。
→ 后果:compose 里的 `${VAR:-默认}` 兜底属 **compose 解析层**,watchtower 不经过,只继承旧容器**已实例化**的 env 集。新发布引入的 compose env,在运维 `git pull` 同步前,watchtower 重建时拿不到。

## 4. 方案 B 设计

### 4.1 部署形态
watchtower 作为 `docker-compose.yml` 的**第二个 service**,与 `sb-xray` 并存;init 脚本现有的 `docker compose up -d` 自动带起,不新增部署步骤。

### 4.2 监控范围(安全隔离)
- `sb-xray` service 增加 label `com.centurylinklabs.watchtower.enable=true`。
- watchtower 配 `WATCHTOWER_LABEL_ENABLE=true`,**只更新带该 label 的容器**,绝不触碰节点上其它容器。

### 4.3 更新时机(方案 A)
- `WATCHTOWER_SCHEDULE="0 0 4 * * *"`(6 段 cron,容器时区,北京 04:00)。
- daily-build 当天 10:00 产出,次日 04:00 低峰窗口更新,断流可预期、几乎无感。

### 4.4 自动更新通知(B 的可见性补强)
- `WATCHTOWER_NOTIFICATIONS=shoutrrr`
- `WATCHTOWER_NOTIFICATION_URL=${shoutrrr_urls}`(与 sb-xray 同源,复用现有 Telegram)
- 每次自动更新推一条「哪台/何时/更新到哪个镜像」,运维事后可核对。

### 4.5 手动触发(run-once,零新增端口)
- 节点内 helper `sbx-update`,本质:
  `docker run --rm -v /var/run/docker.sock:/var/run/docker.sock containrrr/watchtower --run-once sb-xray`
- 立即检查并更新本台,跑完即退,不影响常驻 schedule 容器(digest 未变即 no-op,幂等)。
- 全量立即更新:本地 wrapper 遍历节点清单 SSH 跑 `sbx-update`(清单由运维维护,不入库)。
- 放弃 HTTP API(`WATCHTOWER_HTTP_API_UPDATE`):需逐台开端口+管 token,攻击面成本不划算。

### 4.6 漂移缓解契约(B 的安全底座)
写入 `CLAUDE.md` + 发布流程文档一条硬约束:

> 凡新增 `docker-compose.yml` env,必须在 `entrypoint.py` / `sb_xray` 内对应有 `os.environ.get(key, 合理默认)` 兜底,且默认值保持向后兼容。

→ watchtower 用旧 env 集重建新镜像时**不崩**,新功能暂用镜像内默认值,直至运维 `git pull` 同步 compose。把"漂移=事故"降级为"漂移=新功能延迟生效"。

### 4.7 回滚
- `WATCHTOWER_CLEANUP=true` 删旧镜像省盘。
- 回滚依赖 registry 快照 tag(沿用既有 `before-m2` 习惯):重大发布前打 tag,需要时节点 `docker compose` 切回该 tag。

## 5. 落地步骤(交付 = 实现计划输入)

1. **PoC(dc99-3,镜像已 ready)**:`--run-once` 触发一轮 → 验证 检测新 `:latest` → pull → 重建 sb-xray → 容器 healthy + forwarder 正常 + 中文卡片改动生效 + 收到 watchtower Telegram 通知。
2. **改 `docker-compose.yml`**:加 watchtower service(schedule/label-enable/notify/cleanup),给 sb-xray 加 watchtower label。
3. **改 `sources/vps/vps-cn-exit-init.sh`**:写入 `sbx-update` helper,自检纳入 watchtower 容器状态。
4. **CLAUDE.md / 发布流程文档**:加漂移缓解契约条目。
5. **全量上线**:其余 15 台各跑**最后一次** `git pull && docker compose up -d` 装上 watchtower,此后全自动。

## 6. 测试与验证

- PoC 在 dc99-3 端到端通过(上述 5 项)。
- watchtower 误伤面验证:确认 `WATCHTOWER_LABEL_ENABLE` 下不更新无 label 的容器。
- 通知链路验证:自动更新与 `--run-once` 均推 Telegram。
- 漂移演练(可选):故意让旧容器缺一个新 env,确认重建后 entrypoint 默认值兜底、服务不崩。

## 7. 已知风险与接受

| 风险 | 缓解 | 残留 |
|------|------|------|
| compose env 漂移 | 4.6 兜底契约 | 新功能延迟到 `git pull` 才生效(可接受) |
| 凌晨自动断流 | 04:00 低峰 + schedule 可预期 | 重连秒级(可接受) |
| watchtower 误伤其它容器 | label-enable 白名单 | 无 |
| 回滚镜像被 cleanup 删除 | registry 快照 tag | 依赖发布纪律打 tag |

## 8. 范围外(YAGNI)

- 不做 HTTP API 触发。
- 不做多容器 rolling-restart(每节点单 sb-xray 容器)。
- 不引入 CI 自动部署/灰度编排,保持"构建产镜像 + watchtower 拉取"两段解耦。
