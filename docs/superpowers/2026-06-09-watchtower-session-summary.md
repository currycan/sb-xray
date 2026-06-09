# Watchtower 自动更新 — 本轮工作总结

- 日期: 2026-06-09
- 分支: `feat/watchtower-auto-update` / PR #36
- 设计文档: [specs/2026-06-09-watchtower-auto-update-design.md](specs/2026-06-09-watchtower-auto-update-design.md)
- 本轮范围: 从设计评审 → dc99-3 PoC → compose schedule service 落地 → 通知质量优化

## 1. 完成的工作

| 阶段 | 产出 | 状态 |
|------|------|------|
| 设计闭环 | 方案 B（全自动 + 漂移契约）+ L1 canary 护栏 + 漂移契约 (a)(b) | ✅ 已评审 |
| dc99-3 PoC | `nickfedor/watchtower --run-once` 实测核心机制 | ✅ 通过 |
| compose schedule service | `docker-compose.yml` 加 watchtower 常驻 service + sb-xray label | ✅ 落地 dc99-3 |
| 通知质量优化 | hostname 节点标识 + 关 startup 噪音 | ✅ 落地 dc99-3 |

## 2. 关键发现 / 踩坑（影响后续实现）

1. **`containrrr/watchtower` 已停摆**：内置 Docker API client v1.25，被现代 daemon（≥1.44）拒绝后直接 panic（SIGSEGV）。**必须用 `nickfedor/watchtower`**（活跃 fork，drop-in，PoC 实测 1.17.2 using Docker API v1.53 正常）。
2. **`--run-once` 模式不发通知**（watchtower issue #603）：`updated=1` 却 `notify=no`。含义：手动 `sbx-update`（run-once）更新静默，可见性靠 §4.8 自检告警兜底；只有常驻 schedule service 才发更新通知。
3. **watchtower 重建容器 HostConfig 完整继承 compose**：host network / cap_add（NET_ADMIN+SYS_MODULE）/ mem_limit 460M / restart / tty 一个没丢。这是方案成立的根基，实测无丢失。
4. **schedule 时区**：watchtower 按容器时区解释 cron，须设 `TZ=Asia/Shanghai`，否则 `0 0 4 * * *` 会落在北京正午。实测 `Next scheduled run: 2026-06-10 03:00:00 CST` 正确。
5. **通知标题靠容器 hostname**：设 `hostname: ${domain}` 让标题显示 `Watchtower updates on dc99-3.ansandy.com` 而非容器 hash；`WATCHTOWER_NO_STARTUP_MESSAGE=true` 关掉每次重启的 startup 噪音。详细更新报告交 §4.8 自检告警，不上 watchtower Go template。

## 3. 验证矩阵（dc99-3 实测）

| 验证项 | 结果 |
|--------|------|
| 检测→pull→重建 | ✅ f515f7b5 → fdbfdf37 |
| 重建后 HostConfig 等价 | ✅ host net / caps / mem 460M / restart / tty 全继承 |
| 容器健康 | ✅ starting→healthy ≤15s |
| 443 tcp+udp / forwarder 18085 | ✅ 2 / 2 / 1 |
| 回国链路端到端（generate_204） | ✅ 204 |
| 断流时长 | ✅ ≈13s |
| watchtower 常驻 schedule | ✅ Next run 2026-06-10 03:00 CST |
| label-enable 隔离 | ✅ 仅 sb-xray 纳入，不碰其它容器 |
| 通知到达 Telegram | ✅ startup message 已到（顺带验证链路）|
| 通知标题节点标识 | ✅ hostname=dc99-3.ansandy.com |
| 关 startup 噪音 | ✅ WATCHTOWER_NO_STARTUP_MESSAGE 生效 |
| schedule 模式「真更新」通知 | ⏳ 留待首次真实更新自然验证 |

## 4. dc99-3 当前状态

- **已是武装的 canary**：watchtower 常驻 + 错峰 03:00 CST（`.env` 设 `WATCHTOWER_SCHEDULE=0 0 3 * * *`）+ 通知优化。
- sb-xray 跑最新镜像 `fdbfdf37`、healthy。
- 节点 `/root/sb-xray` **不是 git 仓库**，compose 靠 scp/init 部署；本轮 scp 了 feat 版 compose，原文件已备份为 `docker-compose.yml.bak-1780977966`。
- **缺件**：`sbx-canary-check` 自检脚本 + timer 尚未安装（§4.8 L1 护栏的核心，通知优化已把「详细报告」责任推给它）。
- 明天 03:00 dc99-3 首次自动检查：已是最新镜像，预期 no-op（除非夜间出新 `:latest`）。

## 5. TODO — 接下来该干嘛

按设计文档 §5 落地步骤，剩余：

- [ ] **步骤 3–4：写 `sbx-canary-check` 自检脚本 + 改 init 装 timer**（最关键）
  - 四项自检（容器 healthy / 443 listen / 回国 generate_204 / 镜像 digest 变更确认），每项重试 3× 间隔 10s
  - 失败 → 宿主 `curl 127.0.0.1:18085` 复用 shoutrrr-forwarder 推 Telegram，标题带 `[sb-xray:$domain]`，正文按角色附 runbook（canary=叫停其余 / 其余=本台回滚）
  - **全 16 台同构**，仅 schedule（canary 03:05 / 其余 04:05）+ 告警文案按角色不同
  - 脚本落 `sources/vps/`，init `vps-cn-exit-init.sh` 统一装 cron/systemd timer
- [ ] **步骤 5：CLAUDE.md / 发布流程文档加漂移契约 (a)(b)**
  - (a) 新增 compose env 必须有向后兼容默认；(b) 修复/安全变更必须镜像内默认生效，否则标 `requires-compose-sync` 不走自动分发
- [ ] **步骤 6：canary 实跑验证** —— dc99-3 装好自检 timer 后，人为制造各项失败，确认重试兜底 + forwarder 告警 + runbook 文案到位
- [ ] **步骤 7：全量 15 台上线** —— 各跑最后一次 `git pull && docker compose up -d` 装 watchtower + 自检 timer（默认 04:00），此后全自动
- [ ] **通知「真更新」自然验证** —— 等下次真实 daily-build 出新 `:latest`，dc99-3 03:00 自动更新推出首条真实通知（带节点域名标题）
- [ ] **合并 PR #36 到 main**（实现全部完成后）；合并后 dc99-3 等节点的 compose 来源与 main 一致

## 6. 注意事项

- **`:latest` 覆盖风险**（沿用既有认知）：daily-build merge job 无条件打 `:latest`，任何 `should_build=true` 的分支 run 都会覆盖生产 latest。
- dc99-3 当前 compose 是 feat 版（含 watchtower），PR 未合并前与 main 不一致；这是预期中间态。
- 相关 memory：`project_watchtower_poc`（踩坑速查）、`project_force_build_no_cache`（PoC 拉到的新镜像即其产物）。
