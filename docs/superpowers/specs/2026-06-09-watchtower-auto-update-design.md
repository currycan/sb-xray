# Watchtower 全自动镜像更新 — 设计文档

- 日期: 2026-06-09
- 状态: 已评审 / dc99-3 PoC 已验证(核心机制)/ 待全量实现
- 方案: B（全自动更新 + 漂移兜底纪律）+ L1 canary 护栏（dc99-3 错峰先行 + 业务层自检告警）

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

- **镜像源:`nickfedor/watchtower`(不是 `containrrr/watchtower`)**。`containrrr/watchtower` 上游已停止维护,内置 Docker API client v1.25 被现代 daemon(要求 ≥1.44)拒绝并直接 panic(dc99-3 PoC 实测崩溃)。`nickfedor/watchtower` 是社区活跃维护的 **drop-in 替代**(配置/CLI 完全兼容),2026-04 起官方引用已迁移至该 fork;PoC 实测 1.17.2 using Docker API v1.53 正常工作。

### 4.2 监控范围(安全隔离)
- `sb-xray` service 增加 label `com.centurylinklabs.watchtower.enable=true`。
- watchtower 配 `WATCHTOWER_LABEL_ENABLE=true`,**只更新带该 label 的容器**,绝不触碰节点上其它容器。

### 4.3 更新时机(错峰 canary)
- compose 写 `WATCHTOWER_SCHEDULE=${WATCHTOWER_SCHEDULE:-0 0 4 * * *}`(6 段 cron),并设 `TZ=${TZ:-Asia/Shanghai}` 让 schedule 按北京时间解释(否则容器默认 UTC,04:00 会落在北京正午);默认档北京 04:00。dc99-3 PoC 实测 `Next scheduled run: 2026-06-10 03:00:00 CST` 正确。
- **dc99-3(canary)** 宿主 `.env` 覆盖 `WATCHTOWER_SCHEDULE="0 0 3 * * *"`(北京 03:00,提前 1h);其余 15 台不设,走默认 04:00。
- 共用同一份 compose,靠宿主 `.env` 区分角色——与现有 `CN_EXIT_MODE`/`tsip` 等全套 env 注入模式一致,零特例。
- daily-build 当天 10:00 产出,canary 次日 03:00 先行、其余 04:00 跟进,均落在低峰窗口,断流可预期、几乎无感。中间 1h 是 canary 自检的拦截窗口(见 4.8)。

### 4.4 通知策略(统一中文,PoC 实测后定)
- **watchtower 自带通知已关闭**:其文本为英文且不可定制(仅 Go template,维护成本不值),`docker-compose.yml` 的 watchtower service **不配** `WATCHTOWER_NOTIFICATIONS`。
- **所有 Telegram 报警统一由 §4.8 的 `sbx-canary-check` 用中文经 shoutrrr-forwarder 发出**:更新成功(✅ 卡片)、自检失败(🔴 卡片 + 处置 runbook)。复用容器内 `[sb-xray:$domain]` 标题前缀,节点可辨。中文卡片 formatter 落在 `scripts/sb_xray/shoutrrr.py`(`_format_canary_failed` / `_format_canary_updated`),随镜像发布。
- **镜像信息用「构建时间」而非 sha256**:卡片显示 `镜像构建: 2026-06-09 10:43`(容器 `.Created` 转北京时区),运维看得懂;sha256 digest 仅脚本内部用于「是否刚更新」检测。
- 取舍:放弃 watchtower 即时通知的「即时性」(更新瞬间不推),换全中文 + 零模板维护 + 通知来源统一;更新可见性延后到自检窗口(canary 03:05 / 其余 04:05),自动更新场景可接受。注:现有 `HEALTHCHECK`(`Dockerfile`:`supervisorctl status xray`)只确认 xray 进程在跑,不覆盖 443 服务面与回国链路,故须 §4.8 业务层自检。

### 4.5 手动触发(run-once,零新增端口)
- 节点内 helper `sbx-update`,本质:
  `docker run --rm -v /var/run/docker.sock:/var/run/docker.sock nickfedor/watchtower --run-once sb-xray`
- 立即检查并更新本台,跑完即退,不影响常驻 schedule 容器(digest 未变即 no-op,幂等)。
- 全量立即更新:本地 wrapper 遍历节点清单 SSH 跑 `sbx-update`(清单由运维维护,不入库)。
- **注:`--run-once` 模式不发 watchtower 通知**(watchtower 已知行为,issue #603):手动 `sbx-update` 触发的更新静默,可见性由 §4.8 独立 canary 自检告警兜底(走 forwarder,不依赖 watchtower 通知);常驻 schedule service(§4.1)的自动通知不受影响。
- 放弃 HTTP API(`WATCHTOWER_HTTP_API_UPDATE`):需逐台开端口+管 token,攻击面成本不划算。

### 4.6 漂移缓解契约(B 的安全底座)
写入 `CLAUDE.md` + 发布流程文档两条硬约束:

> **(a) 默认向后兼容**:凡新增 `docker-compose.yml` env,必须在 `entrypoint.py` / `sb_xray` 内对应有 `os.environ.get(key, 合理默认)` 兜底,且默认值保持向后兼容。
>
> **(b) 修复必须镜像内默认生效**:任何修复/安全类变更**必须落在镜像内默认行为里**,不得以「运维设新 compose env」为前提。若某发布确实必须靠新 env 才能正确运行,标记 `requires-compose-sync`,**该发布不走 watchtower 自动分发**,强制全量 `git pull`。

→ 约束 (a) 保证 watchtower 用旧 env 集重建新镜像时**不崩**,新功能暂用镜像内默认值,直至运维 `git pull` 同步 compose,把"漂移=事故"降级为"漂移=新功能延迟生效"。
→ 约束 (b) 堵住缺口:否则修复版镜像被 watchtower 自动拉下来、却因缺新 env 而**修复不生效**,运维误以为已修好(虚假安全感)。(b) 让 watchtower 自动更新即可让修复生效,或显式退出自动分发。

### 4.7 回滚
- `WATCHTOWER_CLEANUP=true` 删旧镜像省盘。
- 回滚依赖 registry 快照 tag(沿用既有 `before-m2` 习惯):重大发布前打 tag,需要时节点 `docker compose` 切回该 tag。

### 4.8 canary 自检护栏（L1）

**问题**:`:latest` 是 CI 当天产出、未经任何生产验证就被 16 台凌晨同步吃下。watchtower 的「重建成功」通知与容器 `HEALTHCHECK` 都只到进程层,搞坏回国链路/服务面也照样报「成功」。若不拦,坏镜像凌晨铺满 16 台、等人看通知才介入。

**机制**:用 4.3 的错峰让 dc99-3 提前 1h(03:00)先行更新,在其余 15 台 04:00 跟进前的窗口里跑一轮**业务层自检**,失败则主动告警,运维有 1h 窗口手动叫停其余。

- **触发**:**全部 16 台**统一装一个 cron / systemd timer 跑 `sbx-canary-check`(留 5min 给容器重建 + `HEALTHCHECK` `start-period` 15s 稳定),各自跟在本台 watchtower 更新窗口之后:canary(dc99-3)**03:05**、其余 15 台 **04:05**。同一套脚本、同一套自检四项,角色只决定 schedule 时间与告警文案(见下「告警」)。canary 自检承担**错峰拦截决策**,15 台自检承担**本台个体故障可见性**。
- **自检项**(每项**重试 3× 间隔 10s**,全失败才判失败,防国内网络凌晨抖动误报):
  1. **容器 Health = healthy** — `docker inspect --format '{{.State.Health.Status}}' sb-xray`,复用现有 `HEALTHCHECK`。
  2. **443 tcp+udp 在 listen** — `ss -tlnp` / `ss -ulnp` 确认对外唯一端口起来了。
  3. **回国链路端到端** — 经本机出站 `curl` 探测 `${CN_EXIT_PROBE_URL:-http://connect.rom.miui.com/generate_204}`(复用 compose 同款目标)通,真正验证这次更新没把 `CN_EXIT_MODE=balance` 链路搞坏。信号最强、也最易误报,故重试兜底。
  4. **镜像 digest 变更确认** — 比对 sb-xray 容器当前镜像 digest 与「上次已知 digest」(落盘),确保在验**新版**而非旧版;digest 未变说明 watchtower 没更新成功,直接判失败告警(否则会在旧版上跳假阳性通过)。
- **告警(零新增凭据)**:任一项失败 → 自检脚本在宿主 `curl 127.0.0.1:18085`(shoutrrr-forwarder,`network_mode: host` 宿主直达)推 Telegram,复用现有事件总线、不碰 telegram 凭据。标题醒目前缀,正文**按角色附不同 runbook**;成功则静默(避免凌晨噪音)。
  - **canary(dc99-3)失败** → 正文附**叫停 runbook**:复用 4.5 本地 wrapper(运维本地 SSH key)遍历其余 15 台 `docker compose stop watchtower`,手动确认坏镜像后执行,阻止 04:00 的 15 台跟进。
  - **其余 15 台某台失败** → 正文附**本台回滚 runbook**:该节点 `docker compose` 切回上一个 digest tag(个体故障可见性,不影响其它台;自动回滚仍 YAGNI,见 §8)。
- **谁跑自检**:独立 timer,**不用** watchtower lifecycle hook——更解耦、运维可读,不依赖 hook 失败时 watchtower 的细节行为。

**残留**:仍依赖人在 1h 窗口内响应高优告警;若凌晨无人响应,15 台仍会在 04:00 跟进坏镜像(这是 L1 与「CI 驱动 stable-tag 闸门」L2 的分界,L2 攻击面跳一档,本次不做,见 §8)。

## 5. 落地步骤(交付 = 实现计划输入)

1. **PoC(dc99-3)— ✅ 已完成(2026-06-09)**:`nickfedor/watchtower --run-once sb-xray` 实测:检测新 `:latest`(f515f7b5→fdbfdf37)→ pull → 重建 → 容器 healthy(15s)→ **HostConfig 完整继承**(host network / caps NET_ADMIN+SYS_MODULE / mem 460M / restart / tty)→ 443 tcp+udp + forwarder 18085 + 回国 probe 204 均正常 → 断流 ≈13s。两处踩坑已固化:`containrrr/watchtower` 停摆崩溃(见 4.1),改用 `nickfedor/watchtower`;run-once 不发通知(见 4.5,留 schedule service 阶段验)。
2. **改 `docker-compose.yml`**:加 watchtower service(`WATCHTOWER_SCHEDULE` 走 `${VAR:-0 0 4 * * *}` / label-enable / notify / cleanup),给 sb-xray 加 watchtower label。
3. **写 `sbx-canary-check`(L1)**:自检脚本(4.8 四项 + 重试 + forwarder 告警 + digest 落盘)落到 `sources/vps/`,**全 16 台同构**,角色只决定 schedule 时间与失败 runbook 文案。
4. **改 `sources/vps/vps-cn-exit-init.sh`**:写入 `sbx-update` helper;**全部 16 台**统一装 `sbx-canary-check` timer(canary 03:05 / 其余 04:05,按 `.env` 角色决定 schedule 与告警文案);自检纳入 watchtower 容器状态。
5. **CLAUDE.md / 发布流程文档**:加漂移缓解契约条目。
6. **canary 先上(dc99-3)**:`.env` 设 `WATCHTOWER_SCHEDULE="0 0 3 * * *"` + canary 角色开关,`git pull && docker compose up -d` 装 watchtower + 自检 timer,实跑一轮验证 4.8 告警链路。
7. **全量上线**:其余 15 台各跑**最后一次** `git pull && docker compose up -d` 装上 watchtower + 04:05 自检 timer(默认 04:00 更新),此后全自动。

## 6. 测试与验证

- PoC 在 dc99-3 端到端通过 — ✅ 2026-06-09 实测(检测/重建/HostConfig 等价/健康/回国链路,见 §5 步骤1)。
- watchtower 误伤面验证:确认 `WATCHTOWER_LABEL_ENABLE` 下不更新无 label 的容器。
- 通知链路验证:**仅常驻 schedule service 推 watchtower Telegram 通知**;`--run-once`(含 `sbx-update`)已知不发(issue #603)。canary 自检告警走独立 forwarder,两种模式下都发,不受影响。
- **canary 自检验证(L1)**:`sbx-canary-check` 四项全绿时静默;人为制造失败(如临时 stop xray / 阻断回国探测目标 / 不更新即跑)各触发一次,确认重试兜底 + forwarder 告警 + runbook 正文到位。
- 漂移演练(可选):故意让旧容器缺一个新 env,确认重建后 entrypoint 默认值兜底、服务不崩。

## 7. 已知风险与接受

| 风险 | 缓解 | 残留 |
|------|------|------|
| compose env 漂移 | 4.6 兜底契约 | 新功能延迟到 `git pull` 才生效(可接受) |
| 凌晨自动断流 | 04:00 低峰 + schedule 可预期 | 重连秒级(可接受) |
| watchtower 误伤其它容器 | label-enable 白名单 | 无 |
| 回滚镜像被 cleanup 删除 | registry 快照 tag | 依赖发布纪律打 tag |
| 坏镜像凌晨铺满 16 台 | 4.8 canary 错峰 + 业务自检告警 + 1h 叫停窗口 | 凌晨无人响应时 15 台仍跟进(L2 才能消,本次不做) |
| watchtower 报「成功」但业务挂 | 4.8 业务层端到端自检(443+回国链路),不只信进程层 | 自检覆盖面之外的局部功能回退 |
| 某台个体故障(OOM/磁盘/链路差异)重建后挂 | 4.8 自检**全 16 台**覆盖,本台失败各自告警 + 回滚 runbook | 事后告警非实时拦截;自动回滚 YAGNI |
| 修复类发布依赖新 env → 拉到却不生效(虚假安全感) | 4.6 约束 (b):修复须镜像内默认生效,否则标 `requires-compose-sync` 不走自动分发 | 依赖发布时判断纪律 |
| canary 自检误报(国内网络抖动) | 每项重试 3× | 极端持续抖动仍可能假告警(噪音,非事故) |

## 8. 范围外(YAGNI)

- 不做 HTTP API 触发。
- 不做多容器 rolling-restart(每节点单 sb-xray 容器)。
- 不引入 CI 自动部署/灰度编排,保持"构建产镜像 + watchtower 拉取"两段解耦。
- **不做 L2「CI 驱动 stable-tag 闸门」**:即生产 15 台监控 `:stable`、由 CI SSH canary 跑健康检查通过后 `imagetools` 重打 `:stable`。L2 能在凌晨无人响应时也挡住坏镜像(真无人值守),但需把 SSH key 下放进 GitHub Secrets、CI 新建到生产节点的通道,攻击面跳一档。当前以 L1(错峰自检 + 1h 人工叫停窗口)的性价比拐点为准;若后续凌晨故障容忍度收紧,再评估升级 L2。
