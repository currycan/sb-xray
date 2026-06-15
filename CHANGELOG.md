# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

版本命名：`MAJOR.MINOR.PATCH`，其中 MAJOR/MINOR 与底层 Xray-core 版本对齐；PATCH 为本项目发布迭代号。

---

## [Unreleased]

### Added（新增）

- **OpenWrt 一键初始化整合**（`sources/openwrt/openwrt-init.sh`，原 `cn-exit-setup.sh` 更名扩展）：脚本职责从「回国出口配置」扩展为「OpenWrt 侧完整初始化」，所有手动操作归零为「填一份 config.env，跑一条命令」。本批变更仅涉及 OpenWrt 侧脚本与模板，不改 docker-compose env，无 watchtower 漂移契约（CLAUDE.md §2）影响。
  - **OpenClash 配置纳管**（`OPENCLASH_MANAGE=1` 默认开）：按架构选 `sources/openclash/op-amd|op-arm` 模板（脚本同目录优先，缺失自动下载），注入 dashboard 密码与订阅地址（`OPENCLASH_SUBS`，`"名=URL"` 空格分隔按 `option name` 匹配、address 注入块尾对齐 LuCI 保存顺序），未提供地址的订阅块（AllOne / 示例）整块裁剪；规范化 diff 无漂移即跳过，有差异先 `.bak.<时间戳>` 再整文件覆写，restart 统一由解耦步骤末尾的 `RELOAD_OPENCLASH` 逻辑触发（避免双重重启）。自检新增「配置无漂移」（软检查——真机实测 OpenClash 启动头 ~10s 会临时翻写 `redirect_dns`/`cachesize_dns` 等 DNS 交接字段后自行恢复，restart 后立即硬比对必然误报；真漂移由 apply 步骤捕获重写）与「密码非占位符」两项。已在生产路由器三轮真机验证：首轮应用配置（log_level 拉回 info）、后续轮幂等跳过、自检 19/0 全过。
  - **CDN IP 优选集成**（`CDN_DOMAIN` 非空启用）：原 `sources/hack/cdn-speedtest.sh` 整体并入主脚本（heredoc 内嵌），安装时写出 `/usr/bin/cdn-speedtest`（保留 run/install/status/clean 子命令）+ 按 `CDN_SUBDOMAINS` 生成 `/etc/subdomains.txt` + 每日 cron（`CDN_CRON_SCHEDULE`，默认 04:00）+ 预装 CloudflareST；自动清理旧版 `cdn-speedtest.sh` 手装产物与 cron 行。独立文件已从仓库删除。安装收尾时若无历史优选结果（`/etc/CloudflareST/last_best.txt` 门禁，保 init 重跑幂等）**前台同步首跑一次**（测速会暂停 OpenClash、测完自动恢复，前台执行让中断可见且自检在恢复后才跑；失败仅 warn 不阻断，次日 cron 重试），新鲜度此后由每日 cron 维持；自检新增「已生效（last_best.txt）」软检查。新增 `cdn` 子命令：`sh openwrt-init.sh cdn` 只跑 CDN 段+自检（仅需 `CDN_DOMAIN`，不依赖节点清单），`cdn run|status|clean` 自动注入域名与测速参数后透传内嵌工具。测速参数 `SPEED_TEST_*` 五项由内嵌硬编码提为 `${VAR:-默认}` 可覆盖并注入首跑与 cron 行——修复 README 此前已声称「环境变量可覆盖」但实际硬编码无效的文档漂移。内嵌 `install_cloudflarest` 新增 busybox tar 兼容回退 `extract_cfst_fallback`：上游 cfst tar.gz 历代均为无 ustar 魔数的老式 tar，busybox tar（`-z` 与流式管道皆然）报 `invalid tar magic`——回退按 512 字节块手工走档头抽出 `cfst` 二进制 + `ip.txt`/`ipv6.txt` 测速输入清单，零依赖、与上游版本无关；装有 GNU tar（如 `apk add tar`）的设备走正常路径、回退不触发。测速窗口新增双层 trap 兜底（HUP/INT/TERM/EXIT）：`run_speedtest` 经 `$()` 命令替换在子 shell 执行，单独信号父进程时子 shell 收不到信号（事后向已死父进程管道写日志还会 SIGPIPE 猝死、恢复代码永不可达，真机演练实测）——故子 shell 与父进程（main run 分支）各装一道 trap，恢复动作：resolv 按备份存在性还原；OpenClash 恢复经原子 mkdir 锁串行化 + 无条件 `restart`（进程探测方案两类真机实测假象而废弃：rc stop 返回后 core 拖尾退出致 pgrep 命中将死进程而误跳过，旁观进程 argv 含关键字假性命中；并发双 start 的 rc 竞态会把服务打成 inactive，故须锁）；两道 trap 幂等可交错；进程组信号（Ctrl-C/SSH 断线）即时恢复，单杀父进程则在测速自然结束后恢复（真机演练验证「测速父进程被中断」恢复链）。另：resolv.conf 备份加「已有备份不覆盖」守卫，防 kill -9 残局重跑把公共 DNS 写进备份导致原始配置永久丢失。kill -9/断电仍属物理极限，需手动 `/etc/init.d/openclash start`。另修「陈旧 result.csv 静默沿用」：cfst 在严苛参数（如收紧延迟上限）下可能整轮产不出合格 IP 而不写 csv，脚本会把上一轮残留旧榜当本轮结果（真机实测）——现在测速前先删旧 csv，无果则显式报错退出、不碰 hosts。

  - **LAN 网段迁移护栏**：自检新增「通告网段含本机 LAN 实际网段」检查（内核路由表取网段基址，比对 `TS_ADVERTISE_ROUTES`），改了路由器网段忘改 config.env 时直接 FAIL 而非静默通告旧网段；README §5.5 新增迁移 runbook（链路仅此一处依赖 LAN 网段——VPS 侧只认 Tailscale IP 与域名，OpenClash 模板无 LAN 硬编码，订阅模板内网直连用泛 RFC1918 段）。
  - **Tailscale 身份自恢复（设备重置零断点）**：设备重置后 Tailscale state 丢失会产生新身份新 IP，而全部 VPS 的 `CN_EXIT_SOCKS5_HOST` 写死本机固定 IP。新增 OAuth admin API 闭环（全部可选，未配维持旧行为）：未登录时用 OAuth client 现场铸短时效 preauthorized auth key 免交互登录（根治普通 auth key 90 天过期的灾备腐烂问题）→ 检测 `tailscale ip -4` ≠ `TS_EXPECTED_IP` 时 API 删除占用该 IP 的旧设备条目、把本机 IP 恢复为固定值（VPS 侧零改动）→ API 批准 subnet routes + exit node（消除后台手动点击）。所有 API 失败路径降级为 warn + 打印后台手动步骤，不阻塞其余安装；自检新增固定 IP 硬校验与 routes 批准软检查；config.env 含 OAuth secret 时自动收紧 600。新变量：`TS_OAUTH_CLIENT_ID/SECRET`、`TS_OAUTH_TAGS`（默认 `tag:openwrt`，OAuth 铸 key 平台要求带 tag，设备变 tagged——README §2.3 说明 policy 一次性准备与 ACL 影响）、`TS_EXPECTED_IP`、`TS_AUTH_KEY`。README 新增 §2.3（OAuth 一次性准备）与 §5.6（设备重置恢复 runbook：scp 三文件 + 跑一条命令）。

- **CN 出口整机宕机反向探活**（`sources/vps/cn-exit-watchdog.sh` + `vps-cn-exit-init.sh` §3.6）：补监控盲区——设备侧 `cn-bridge-monitor` 跑在 CN 出口设备自身上，整机宕机时监控随之失联，VPS 侧 balance 探活只做静默 failover 不告警。新增 VPS 侧反向探活脚本：cron 每分钟经 socks5 腿实测回国（默认 generate_204），连续 `WD_THRESHOLD`（默认 3）次失败发 Telegram 告警（bot 直连，不经 shoutrrr-forwarder；告警期去重，恢复发解除），`--test` 验证通道。init 集成为可选护栏段：`WD_TG_TOKEN`+`WD_TG_CHAT` 同时有值才装（自门控）、写 conf 600、装 `/etc/cron.d/cn-exit-watchdog` 并自动迁移清理早期手装 user-crontab 条目；建议仅 1-2 台节点启用互为冗余。纯 VPS 侧脚本，不涉及镜像/compose，无 watchtower 漂移契约（§2）影响。

### Changed（变更）

- **CDN IP 优选转为硬契约 + 服务就绪后执行**（`sources/openwrt/openwrt-init.sh`）：堵死灾备换机反复出现的「优选 IP 静默漏做」缺口（`/etc/hosts` 无优选条目、cron 缺失却 init 报完成、只有人工排查能发现）。三处加固：① `CDN_DOMAIN` 由可选升为**必填**——`validate_config` 缺则 `die`，并要求 `CDN_SUBDOMAINS` 或非空 `/etc/subdomains.txt`（否则优选无域名可用）；② `install_cdn_speedtest` 拆为 `install_cdn_tooling`（工具+cron+subdomains+预装 CloudflareST，**无服务依赖、cron 始终先在位**）与 `cdn_optimize_firstrun`（优选首跑），首跑**锁在服务自检 `verify` 通过之后**才做——优选需 OpenClash/Tailscale 正常运行（首跑临时停 OpenClash 跑 CloudflareST 再恢复），服务未过则跳过首跑并让 init `exit 1`；③ 首跑失败由 `warn` 升为**硬失败 `die`**，自检 `verify_cdn`→`verify_cdn_outcome`：去掉 `CDN_DOMAIN` 空值逃逸外壳（杜绝「空=零检查=静默全绿」），把「优选 IP 已写入 `/etc/hosts`」由 `check_soft` 升为**硬 `check`**（真相源对齐人工排查实际看的 `/etc/hosts` 映射）。`cdn` 子命令同步走拆分流程，可在 DR 服务恢复后单独补做（不碰 Tailscale）。`docs/12` 新增 §8b「服务确认正常后补做 CDN 优选」+ §16 两条硬判据；`config.env.example`/README 标 `CDN_DOMAIN` 必填。纯 OpenWrt 侧脚本/文档，不涉镜像/compose，无 watchtower 漂移契约（§2）影响。

- **`cn-exit-setup.sh` → `openwrt-init.sh` 更名**：与 `vps-cn-exit-init.sh` 命名对称。路由器上的运行时产物名（`cn-bridge`、`cn-bridge-monitor`、`/etc/cn-exit/`、`xray-bridge-<名>`）全部不变，已部署路由器无需迁移；仓库内全部文档/注释/测试引用同步更名（CHANGELOG 历史条目保留旧名）。迁移：下次重跑时按 README 下载新名脚本即可，旧脚本副本可删。

### Fixed（修复）

- **更新远端密钥库后运行中的服务永不感知（始终用缓存 secret）**（`scripts/sb_xray/secrets.py` + `scripts/entrypoint.py` + 新增 `scripts/sb_xray/stages/secrets_refresh.py` + `scripts/sb_xray/stages/cron.py`）：更新加密库 `tmp.bin` 后，运行中的节点始终沿用旧凭据。三层缓存叠加所致：① `decrypt_remote_secrets()`「文件已存在即跳过」——`/.env/secret` 经宿主机卷持久化，连容器重启都不重解密；② 全部下游（`routing/isp.py`、`speed_test.py`）从 `os.environ` 读 `*_ISP_*`，而该 env 在 boot `source /.env/secret` 后即冻结，cron 子进程继承冻结快照；③ 无任何 secret 刷新触发器。修复（双触发、共用同一刷新函数，均镜像内默认生效、新 env 带默认值、不需 compose 同步，符合 §2）：新增 `refresh_remote_secrets()`——每次下载比对解密后明文，仅在凭据变化时原子替换 `/.env/secret` 并算出变更/删除的 key 集；上游不可达且本地有缓存时降级用旧值、不抛错。① **每次 boot 复检**：secrets stage 改用 `refresh_remote_secrets`，使 `docker compose up -d --force-recreate`（及 watchtower 镜像跳变重建）顺带刷新——无需再手删 `.envs/secret`；离线时启动不失败。② **周期 cron `secrets-refresh`**（默认每小时，`SECRET_REFRESH_INTERVAL_HOURS`，hostname 打散分钟位）：检测到变化后强制覆盖冻结 env 的变更 key、`pop` 删除 key（绕开 `_load_env_file` 的 setdefault 冻结），重测选路、重渲染并热重启 xray/sing-box，发 `secret.refresh.completed` 事件——使密钥轮换有上界生效延迟（默认 ≤1h）且与镜像发布节奏解耦。立即下发：`docker exec sb-xray /scripts/entrypoint.py secrets-refresh`。`isp_retest` 与 `secrets_refresh` 复用抽出的 `stages/reload_util.py`（`restart_daemons` / `restore_media_routing`）。新增回归测试覆盖刷新七分支（冷解密 / 无变化 / 有变化算 key 差 / 离线保缓存 / 离线冷启动抛错 / 无 DECODE 保缓存 / 无 DECODE 冷启动抛错）与 cron 编排（noop / completed / disabled / env 覆盖）。docs/04 §2.3 与 cron 表、docs/06 事件登记同步更新。

- **残留测速缓存致删节点后 `dependency[proxy-X] not found` 启动崩溃**（`scripts/sb_xray/speed_test.py` + `scripts/sb_xray/routing/isp.py`）：运维从 `SECRET_FILE` 删除某 ISP 节点后未清 `STATUS_FILE`，启动时 sing-box/xray 报 `dependency[proxy-<slug>] not found for outbound[isp-auto]`。根因：`isp-auto` urltest / xray balancer 的成员来自测速缓存 `_ISP_SPEEDS_JSON`，而 `proxy-*` 出站只由当前 env 的 `*_ISP_IP` 生成——两者不同源；冷启动 TTL 缓存路径 `_try_speed_cache_hit()` 原样复用缓存、**不校验 tag 是否仍是当前节点**（其兄弟函数 `_try_cache_hit()` 早有此校验，加固不一致）。双层修复（均镜像内默认生效、不新增 env，符合 §2）：① **源头校验**——`_try_speed_cache_hit()` 与 `_try_cache_hit()` 对齐，缓存任一 tag 不再有 `*_ISP_IP` 后端即判 cache miss、落到实测重生成干净 speeds 并重选赢家（系统自愈，运维无需手动清 `STATUS_FILE`）；两路径共用新抽出的 `_current_isp_tags()` 消除漂移。② **配置层兜底**——`build_client_and_server_configs()` 生成成员前把 `speeds` 与当前节点求交集，从结构上保证「urltest/balancer 成员 ⊆ 已生成出站」，对 sing-box 与 xray 一处覆盖；全员失配时 speeds 收敛为空、urltest/balancer 优雅置空。新增回归测试覆盖两层（含「删节点不清缓存」「全员失配」「健康缓存不误杀」）。

- **CDN 优选根因：解包丢失 `ip.txt` 致测速每轮白跑**（`sources/openwrt/openwrt-init.sh`）：真机验证发现「优选 IP 反复漏做」的最深层根因——早期为绕开 busybox tar 自写了 512 字节块解析器，只抽 `cfst` 二进制、丢弃了上游 tar 内随附的 `ip.txt`（CloudflareST 测速的 IP 段输入清单），而 `cdn-speedtest run` 的 `./cfst` 调用不带 `-f`、默认读 cwd（`/etc/CloudflareST`）的 `ip.txt`。无 GNU tar 的设备 → `ip.txt` 缺失 → cfst 报 `open ip.txt: no such file or directory` → 每轮（含每日 cron）测速白跑、`/etc/hosts` 恒无优选条目；此前仅静默 warn 而 init 报完成。修复：**删除手写解析器**（按文件枚举抽取是 bug 面本身），改为 `tar -xzf` 失败时 `ensure_gnu_tar` 自动 `apk add tar`/`opkg install tar` 后重试——GNU tar 一次解出全部条目（`cfst`+`ip.txt`+`ipv6.txt`），结构上消除「漏抽某文件」类 bug；二者皆无或仍失败则硬失败、提示手动装 tar 后重跑。配套 `install_cloudflarest` 幂等门由 `[ -x cfst ]` 收紧为 `[ -x cfst ] && [ -s ip.txt ]`，使「已装 cfst 但缺 ip.txt」的存量设备下次 init 走重装路径、由 `tar -xzf` 解全量自愈补回（镜像内默认生效，符合 §2）。真机验证（ImmortalWrt/apk amd64，2026-06-16）：busybox tar 报 `invalid tar magic` → `apk add tar` 装出 GNU tar → `tar -xzf` 全量解出 cfst + ip.txt + ipv6.txt；ip.txt 在场后实跑 `cdn-speedtest run`，`/etc/hosts` 优选条目从无到有（覆盖全部 CDN 子域）、`last_best.txt` 写入、OpenClash 恢复 running。纯 OpenWrt 侧脚本，不涉镜像/compose。

- **cdn-speedtest 测速诊断改写 stderr**（`sources/openwrt/openwrt-init.sh` 内嵌工具）：`run_speedtest()` 经 `best_ip=$(run_speedtest)` 命令替换调用,而 `log()` 同时写 stdout——OpenClash 停/启与测速进度的诊断全被 `$()` 捕获吞掉(前台 `cdn-speedtest run`/首跑看不到停启过程,仅靠 `tail -1` 从混合 stdout 捞返回 IP,脆弱)。`log()` 诊断改写 **stderr**:前台诊断照常可见、`$()` 仅捕获末尾 `echo "$BEST_IP"`、`tail -1` 降为防御兜底;`log()` 写 `/var/log/cdn-speedtest.log` 不变。OpenClash 停/启行为本就正确(无条件 `stop`→测速→`restore_proxy_env` `restart`,真机实测跑后 running),本条仅修可见性与健壮性。busybox `sh -n` 通过。

### Security（安全）

- **op-amd / op-arm 模板 dashboard 密码脱敏**：真实生产密码以明文存在于公共仓库模板（`option dashboard_password`），改为 `<OPENCLASH_DASHBOARD_PASSWORD>` 占位符，由 `openwrt-init.sh` 从 config.env（`OPENCLASH_DASHBOARD_PASSWORD`，gitignored）注入。注意：git 历史中仍有旧值，建议轮换该密码。

## [26.6.11] — 2026-06-11 · watchtower 全自动更新体系 + ISP 测速选路优化 + 风险分类媒体分流

> 汇总自 v26.6.7 以来的全部变更：主线是镜像分发从「手动滚动升级」演进到 **watchtower 全自动更新 + canary 自检**（配套 `YY.M.D-<短 sha>` 镜像版本号方案，本版起 Git Release tag 与 Docker 镜像 tag 一一对应），伴随 ISP 测速/选路系统性优化、媒体分流改为账号风险分类模型与多项稳定性修复。

### Added（新增）

- **Watchtower 全自动镜像更新体系**：生产节点经 watchtower 自动跟进 `:latest`，人工滚动升级退役为兜底手段。
  - `docker-compose.yml` 新增 watchtower service：schedule 定时拉取；`hostname=${domain}` 使通知标题可辨节点；`WATCHTOWER_NO_STARTUP_MESSAGE=true` 关闭容器重启时的 startup 噪音，只在真更新/失败时通知。
  - **canary 自检**：`sources/vps/sbx-canary-check.sh` 在自动更新后做业务层自检并推送 Telegram 通知（updated/failed 两类事件，中文 formatter 入 `scripts/sb_xray/shoutrrr.py`）；`vps-cn-exit-init.sh` 按角色安装自检 cron（canary 节点错峰先行、worker 随后），并安装 `sbx-update`（run-once 手动更新）helper。
  - **漂移缓解契约**（CLAUDE.md §2）：watchtower 从现有容器 env 重建新镜像、不读 compose——新增 env 必须镜像内默认兜底；修复必须镜像内默认生效，否则发布说明标 `requires-compose-sync`、不走自动分发。
- **风险分类媒体分流（risk-class media routing）**：以「账号风险 A/B 二分类」取代原先三组互相矛盾的媒体探测组：账号敏感类（ChatGPT / Gemini / Claude / social / TikTok）不做探测——家宽直连、否则恒走 `isp-auto`（封号风险探测不出来，保持最大保守）；流媒体解锁类（Netflix / Disney / YouTube）改为读正文 GET + 内容签名，仅 REAL 解锁判定才直连（HEAD 200 可能掩盖验证码/地区墙页面）。`is_restricted_region()` 提升为住宅短路之上的顶层安全网（受限地区家宽节点也走 fallback 不直出）；旧 IP 信誉 / `ipapi.json` 层裁撤（新模型下无消费者）。
- **OpenWrt `cn-exit-setup` 初始化自动注入 GLOBAL 组**：OpenClash global 模式下把内置 DIRECT/REJECT 置底（纯 UI 顺序，不影响路由），照 skip-auth 同款幂等手法注入官方覆写钩子；成员动态取现有 proxy-groups 组名、不硬编码节点名。同时删除天生覆盖不到 proxy-providers 的失效脚本 `sources/hack/anytls_overwrite.sh`。

### Changed（变更）

- **镜像版本号方案 `YY.M.D-<7 位短 sha>`**：daily-build 增加 push 触发（合入 main 即构建、覆盖 `:latest`），镜像同时打 `:<version>` + `:latest` 双 tag，并以 OCI label（version/revision/source）写入镜像便于节点反查来源；`build.sh` / `release.sh` 对齐同格式——Git Release tag 与 Docker 镜像 tag 一一对应。组件版本仍以 `versions.json` 为单一事实源，不再与 Xray 上游版本号绑定。
- **Sub-Store 后端订阅同步默认时间 23:55 → 04:00**（`SUB_STORE_BACKEND_SYNC_CRON` 镜像内默认值，Dockerfile）：原 `55 23 * * *` 晚于次日 04:30 的 `substore-check` 拉取自检 4.5 小时，自检验证的是隔夜旧数据、因果顺序颠倒。改为 `0 4 * * *` 使同步先行 30 分钟，自检验证当天刚同步的结果。镜像内默认生效（watchtower 自动分发，无需 compose 同步）；在 compose 显式设过该 env 的部署不受影响。
- **ISP 测速与选路优化（消除全队"不流畅"误报 + 杜绝无谓重启）**：电报里全队节点集体报「8K ⚠️ 不流畅」（proxy-us-isp ~5 Mbps）经实测是**测量假象**——同一上游代理裸 curl 单连接达 95–146 Mbps，生产函数与 curl 同时刻吻合。根因是全队 `0 */6` cron 在整点同一秒压同两个共享上游代理，自造惊群。本次系统性优化：
  - **错峰重测**：retest cron 分钟位按 `sha1(hostname) % 60` 打散（如 `52 */6` 而非 `0 */6`），全队散布全小时、互不踩踏。`ISP_RETEST_JITTER=false` 可关。
  - **测量更稳健**：默认采样 3 次（`ISP_SPEED_SAMPLES` 此前被代码忽略，现已生效）、瞬时 `connect_fail`/`timeout` 单样本重试一次（`ISP_SPEED_SAMPLE_RETRIES`）、`n≥3` 取中位数抗离群。
  - **选路解耦排序与重启**：xray `leastPing` 本就按实时 RTT 选线、无视 selector 顺序，故纯带宽排名波动不再触发重启——仅「已配置节点增删」或「路由类别 direct↔proxy 切换」才 rebuild+重启。flaky 线 `0↔alive` 横跳**不触发重启**：死/慢线保留在 selector（含全部已配置节点），交给运行期 `leastPing` + `fallbackTag/direct` 尾部成员实时绕开、全死时优雅退 direct——比剔除它们更稳（剔除会让横跳 churn 重启、且全死时 `isp-auto` 消失致引用悬空）。主选加跨轮滞回（`ISP_LEADER_HYSTERESIS`，默认 1.15）。
  - **告警边沿触发**：仅在首次/评级翻转/可用成员变化/选线变化时推 Telegram，纯抖动静默，终结每 6h 刷屏。
  - **8K 评级校准**：判定阈值 100→60 Mbps 对齐内部评级梯子（`ISP_8K_SMOOTH_MBPS` 可覆盖）；Telegram 不再显示二元「⚠️ 不流畅」，改显评级梯子标签（如「评级: 流畅 4K，8K 可能卡顿」）。
  - 废弃：`ISP_RETEST_DELTA_PCT` 不再被读取，设置无任何效果（重启触发改为成员/路由类别变化）。带宽变化幅度仍写入 `ISP_LAST_RETEST_DELTA_PCT` + 事件 payload 作遥测，不触发重启。
- **ISP 重测通知合并为单条**：原先一个重测周期发两条（测速结果卡 + 切换决策，且 noop 走 key:value 兜底格式），现测速摘要折入决策卡——「🔄 ISP 重测 · 线路已切换」/「🔁 ISP 重测 · 线路不变」合并卡，单条读完即闭环；重测路径抑制独立测速推送（仍记日志）。
- **sing-box rule_set 数据源 SagerNet → MetaCubeX**：`sb.json` 全部 14 个远程 rule_set（13 geosite + geoip-cn）切到 MetaCubeX/meta-rules-dat（sing 分支），与 xray 引擎 geo 源对齐、根除两引擎数据源分裂；仅换 URL，tag / download_detour / 更新间隔不变。
- **`TS_ADVERTISE_ROUTES` 去默认改必填**（OpenWrt / VPS 脚本侧）：删除内置默认网段（合规脱敏 §4），`cn-exit-setup.sh` 在 socks5/balance 模式未设置时启动即报错退出、不再静默使用默认网段；`config.env.example` 改为留空必填注释。
- **CI 工程加固**：traffic-stats 增加数据新鲜度可观测性（clones.csv 行数/最新日期入 step summary，一眼区分「GitHub Traffic API 固有 2-3 天上报延迟」与「抓取异常」）+ 被弃用 action 升级 Node24 版本；`force_build=true` 时禁用 buildx 缓存，强制构建真正从头构建。

### Fixed（修复）

- **经 anytls/tuic 的媒体流量全断（Google/OpenAI/Netflix/YouTube/Claude/TikTok…）**：`isp-retest` 触发配置重渲染时，sing-box `sb.json` 的媒体路由占位符 `${GEMINI_OUT}`/`${CHATGPT_OUT}`/`${ISP_OUT}` 等未被替换、原样写入运行配置 → sing-box `outbound not found` 丢包。根因：`*_OUT` 媒体路由变量仅在启动期媒体探针阶段写入 `os.environ`，而 cron retest 是全新进程从不恢复它们。修复两层：
  - **正确性**：`isp_retest.run()` 在重渲染前补跑 media 探针恢复 `*_OUT`，并按 boot 同样的 `HAS_ISP_NODES` 推导补回 `ISP_OUT`（电商 amazon/paypal/ebay 及 social/tiktok 规则用）——它不是 media 探针项，不补则两核都退成 direct、电商走数据中心 IP 触发风控（`scripts/sb_xray/stages/isp_retest.py:_restore_media_routing`）。
  - **纵深防御**：sb.json 渲染后对任何残留 `${*_OUT}` 一律兜底为 `direct` 并告警（`config_builder.py:_patch_unresolved_service_outs`），杜绝任何未来重渲染路径再退化成字面量——与 xray 服务路由 `outbounds.get(name) or "direct"` 的优雅回退对齐。
- **`isp-retest` 每 6 小时无条件重启 xray/sing-box**：`_ISP_SPEEDS_JSON` 从不持久化到 STATUS_FILE，cron retest（全新进程）只能与**启动期冻结的环境快照**比对 → 永远 `delta=100%` → 每次重测都重启守护进程、掐断全部连接。修复：测速结果持久化进 STATUS_FILE，重载判据改为「已配置成员/路由类别变化」（见上「选路解耦」）。
- **ISP 住宅代理冷启动竞态根治**：async 测速刷新路径污染 `HAS_ISP_NODES` 进程环境，导致冷启动期选路推导错乱。修复：`is_restricted_region` 改显式传参、不再变更 env；STATUS_FILE 写入原子化 + flock 串行；async 刷新只持久化、从不回写 env；新增回归测试钉死冷启动 `ISP_OUT` 竞态。
- **TikTok 分流规则被遮蔽（双引擎死规则）**：`geosite:tiktok` 原排在 `geosite:category-social-media-!cn` 之后、永远不命中。两引擎统一前置：xray `_SERVICE_SPEC` 与 sing-box `sb.json` 调整规则顺序，并按服务拼接 `${TIKTOK_OUT}` / `${SOCIAL_MEDIA_OUT}`。
- **watchtower canary 通知「镜像构建: 未知」**：formatter 读 `payload.built` 但 canary 脚本未发该字段、两端错位，digest 被忽略。镜像侧 formatter 补字段回退（updated→new / failed→image）默认止血；canary 脚本改读 OCI `image.version` label，通知显示友好版本号（label 缺失回退 digest 末段）。
- **mihomo amd64 改用 `-compatible` 构建（兼容 ≤x86-64-v2 老 CPU）**：通用 amd64 mihomo 二进制按 x86-64-v3 微架构优化，在 SSE4.2 等老 CPU 的 VPS 上触发非法指令崩溃，容器内 `http-meta` 进程起不来、Sub-Store 机场订阅拉取失效。统一改用 `mihomo-linux-amd64-compatible-v${VER}.gz`：
  - `Dockerfile` / `build.sh`（refresh 模式 `get_asset_digest`）/ `versions.json` 切到 compatible 资源与对应 SHA256（PR #21）。
  - **CI digest 漏改修复**：`.github/workflows/daily-build.yml` 的 check job 仍以通用 `mihomo-linux-amd64-v${VER}.gz` 计算 digest 并覆盖 `versions.json`，与 Dockerfile 实下的 compatible 文件 SHA 不符，导致 amd64 构建在 `sha256sum -c` 阶段失败（run 27128783528）。check job 的 amd64 digest 资源名同步为 compatible（PR #24）。arm64 无 compatible 变体，三处统一保留通用 `mihomo-linux-arm64-v${VER}.gz`。
  - 构建坑固化进 `docs/00 §6 Q8`（Dockerfile / build.sh / daily-build.yml 三处资源名一致表 + `grep` 自检 + `http-meta -v` 目标 CPU 验证，PR #25）。
  - 验证：强制重建成功（linux/amd64 + linux/arm64 双架构）；全量生产节点滚动升级后全部 healthy，`docker exec sb-xray /sub-store/http-meta/http-meta -v` 全队退出码 0。

### Docs（文档）

- **全量文档重构（#48）**：01–09 + readme 审计裁决落地——架构坐标系重建、S-UI 死引用清理、跨文档锚点与编号收口、机制描述向单一真相源转引；readme 精炼为导航门面。配套合规脱敏：`sources/nikki` 凭据占位化、脚本注释去环境特定信息；删除无引用死变量 `ISP_RETEST_DELTA_PCT`（Dockerfile）。
- **docs/00 新增 §8「GitHub Actions CI 自动构建与发布流水线」（#47）**：四 job 职责表、push / 每日 cron / 手动三触发与门控真值表、原生 arm64 runner / push-by-digest + manifest 合并 / 缓存策略等架构决策，以及 CI 与本地构建、生产 watchtower 闭环的关系（接漂移契约与 `requires-compose-sync` 例外）。
- **dufs 配置优先级机制实测修正**（docs/04 §2.2）：双向实测判决 env 严格优先于 conf（CLI > env > config > default），撤回早先「取并集/无单向覆盖」的不准确表述；补 `DUFS_BIND` env 压过 conf bind 的安全提示（host 网络下 `0.0.0.0` 监听全网卡）。
- **媒体分流文档同步至账号风险分类模型**（#41，docs/01 §5.3 / docs/03 §1.2）；**watchtower canary 通知卡片与 `built` 字段语义**（#43，docs/06 §9.1 + `sources/vps/README`）；**PR 提交前文档记录纪律**入 CLAUDE.md §5。

## [26.6.7] — 2026-06-07 · 回国出口多公网高可用（CN_EXIT_MODE balance + Tailscale + reverse bridge）

> 汇总自 v26.4.22 以来的全部变更：主线是「海外回国」从单链路演进到**多公网双腿主备高可用**，伴随 S-UI 面板移除、文档体系重构与若干稳定性修复。

### Added（新增）

- **回国出口总开关 `CN_EXIT_MODE`（socks5 / reverse / balance / off）**：取代旧的「按 `CN_EXIT_SOCKS5_HOST`/`REVERSE_CN_EXIT` 隐式派生」。`balance` 把 SOCKS5（Tailscale/OpenClash）与 `r-tunnel`（VLESS reverse）两条回国腿挂 `leastPing` balancer + observatory 探活，自动故障转移、全断 fallback direct。路由改写见 `scripts/sb_xray/config_builder.py:_rewire_cn_rules`。
- **多公网回国高可用（OpenWrt 侧）**：`sources/openwrt/cn-bridge` 拨号工具（list/up/down/status，per-node 独立 `xray-bridge-<名>` 进程、api 端口错开）+ `cn-bridge-monitor`（cron 探活热备 r-tunnel / Tailscale peer，去抖 telegram 告警）。节点池 `nodes.list`（`名 FQDN token`），`BRIDGE_HOT` 指定常驻热备、其余冷备靠 socks5 腿兜底（不黑洞）。
- **VPS 侧一键初始化 `sources/vps/vps-cn-exit-init.sh`**：写回国 `.env`（docker-compose 以 `${VAR}` 引用）+ 装 Tailscale 入网 + 同步最新 `docker-compose.yml` + `compose pull/up` + 自检（硬失败非 0 退出、socks5 回国实测）。
- **OpenWrt 一键 `sources/openwrt/cn-exit-setup.sh`**：按 `CN_EXIT_MODE` 分派，装 Tailscale（kernel TUN：subnet router + exit node + UDP GRO 转发优化）/ xray reverse bridge / OpenClash 解耦（VPS 域名 DIRECT + `IN-PORT,7891` 强制直出 + SOCKS skip-auth）+ keepalive 保活。
- **VLESS Reverse Bridge（`ENABLE_REVERSE` + `REVERSE_DOMAINS`）**：零公网 IP 家宽落地机经 `r-tunnel` 反向挂载到 VPS，做内网穿透或回国出口；双 UUID 隔离（`XRAY_REVERSE_UUID` 禁作正向代理）。落地机模板 `templates/reverse_bridge/client.json`，`show` 渲染带 token 下载链接。
- **事件总线 shoutrrr 多通道告警**：Xray `rules.webhook` → forwarder → shoutrrr CLI → Telegram/Discord/Slack/Gotify；`ban_bt` / `ban_geoip_cn` / `ban_ads` / `ban_private_ip` 四类事件，`SHOUTRRR_URLS` 空走 dry-run。详见 `docs/06`。

### Changed（变更）

- **默认 `CN_EXIT_MODE=balance`**：`docker-compose.yml` / `vps-cn-exit-init.sh` / `cn-exit-setup.sh` / `config.env.example` / `docs/04 §2.7` 统一为 balance 默认。
- **移除 S-UI 面板**：构建 / 配置 / nginx / CI 全面剔除，Dockerfile 由四阶段收敛为三阶段，组件 11→10。
- **客户端模板统一拦截 QUIC（UDP 443）**：防止 HTTP/3 绕过分流出口。
- **geosite 数据源换 MetaCubeX**：根除 `geosite:cn` 的 `@cn` 海外 CDN（`dl.google.com` 等）污染导致 Google Play 等地区敏感应用从国内 IP 访问失效；并前置 `geosite:geolocation-!cn → direct` 海外直出护栏。
- **文档体系重构**：05 VLESS Reverse 指南、06 事件总线、08 Reverse Bridge 回国架构重写为图文并茂（mermaid）；构建文档前移为 `00` 并补齐 S-UI 移除后的编号空缺；脚本目录迁入 `sources/{openwrt,vps}/` 并各配新手向 README；修正多处死链与显示编号。
- **VPS/OpenWrt 脚本可靠性加固**：自检硬失败以非 0 退出码收尾（批量编排可筛坏节点）、时序敏感项（DERP 打洞 / OpenClash 重启）改重试软告警、`docker-compose.yml` 自动同步（修旧部署 `.env` 不生效）、`tailscale up` 限时防挂死、新增 `TS_AUTHKEY_FILE` / `SKIP_PULL` / `SKIP_COMPOSE_UPDATE` 开关。
- **`common` 订阅轨移除 AnyTLS 节点**：mihomo / OpenClash / Karing 等通用客户端的 anytls outbound 在 mihomo `1.19.24` 及附近版本上出现 url-test 持续返回 `-1` 的回归（上游 commit `9613f02` 重构 22 个 outbound 的 `Base` 初始化路径，对 anytls 字段映射存在静默副作用）。`common` 从 7 条裁剪为 6 条，保留 Hysteria2 / VMess / XTLS-Reality / Xhttp+Reality直连 / 上行 Xhttp+TLS+CDN 下行 Xhttp+Reality / Xhttp+TLS+CDN 上下行不分离。AnyTLS 仍保留在 `/v2rayn` 主轨（v2rayN / sing-box 客户端实现可靠）。同步更新 `show` 输出（"6 协议"）、订阅单测与协议文档（§1.1 表格、§1.10 TUIC 备注、§1.11 AnyTLS 订阅轨标注）。
- **兼容订阅轨重命名为 `common`**：原 `/v2rayn-compat` 分享链接改为 `/common`，`write_subscriptions()` 只生成 `v2rayn` + `common` 两个 base64 订阅文件。`common` 从原 9 条裁剪为 7 条：保留 Hysteria2 / AnyTLS / VMess / XTLS-Reality / Xhttp+Reality直连 / 上行 Xhttp+TLS+CDN 下行 Xhttp+Reality / Xhttp+TLS+CDN 上下行不分离，移除 TUIC 与 `上行Xhttp+Reality下行Xhttp+TLS+CDN`。同步更新 `show` 输出、订阅单测与协议文档。
- **ISP 测速采样器重构（v2）**：跨境 SOCKS5 链路上，v1 的「单次 GET + 1 MiB 文件 + 5s 超时」系统性低估节点带宽 5–20 倍（生产观察：直连 463 Mbps，节点测得 0–21 Mbps）。根因是 TCP slow-start、TLS/SOCKS5 握手、小文件管道填不满三重叠加。
  - v2 改用 `httpx.stream()` 流式读取：丢弃 `ISP_SPEED_WARMUP_SEC`（默认 1.5s）的 TCP 慢启动段 → 从**首字节之后**开始计时 → 在 `ISP_SPEED_WINDOW_SEC`（默认 8s）或 `ISP_SPEED_MAX_BYTES`（默认 256 MiB）封顶时停止 → 返回结构化 `SampleResult`。
  - 失败不再静默 `0.0`：输出 `ok / connect_fail / timeout / low_speed / zero_body / proxy_dep_missing` 状态码。整批聚合成 `_ISP_SPEEDS_DIAG_JSON` 并附加到 `isp.speed_test.result` 事件。
  - 新增 `ISP_SPEED_URL_MAP`（JSON `{tag: url}`）按 tag 覆盖探针 URL；`ISP_SPEED_LEGACY=true` 一键回退 v1。
  - **兼容性**：`_ISP_SPEEDS_JSON` schema 仍是 `{tag: float}`，首次 cron 重测会因新旧值差距触发一次 `delta_exceeded` 重启 xray + sing-box（一次性事件）。

### Fixed（修复）

- **Tailscale kernel TUN 迁移三处致命缺陷**：路由黑洞、state 丢失、重启后 daemon 停在 `Stopped` 不自恢复（`init.d` 开机自动 `tailscale up`）。
- **隧道断导致 OpenClash 全节点健康检查失败**：`geosite:cn` 收录了 mihomo/OpenClash 默认探测域名 `full:www.gstatic.com`，隧道一断健康检查全挂、客户端节点集体掉线 —— 前置 `full:www.gstatic.com → direct` 豁免规避。
- **reverse bridge 客户端 routing `inboundTag` 应为 `r-tunnel`**：原误写导致反向隧道流量无规则匹配被丢弃。
- **既有文档死链与编号缺口**：修复多处跨文档死链、S-UI 文档删除后的编号顺延。
- **xray supervisord autorestart 路径下因 stale UDS socket 死循环** —— 部分小内存节点观察到 xray 进程异常退出后,supervisord 自动拉起时 inbound 报 `bind: address already in use /dev/shm/udsxhttp-compat.sock`,容器进入 "进程 RUNNING 但 inbound 全挂" 的隐性故障(`supervisorctl status` 显示正常,订阅链路对外不可用)。手动 `docker restart` 可恢复(tmpfs 被销毁),但过段时间复发。
  - 根因:xray 启动时通过 `/dev/shm/uds*.sock` 监听 4 路 UDS inbound(XHTTP / XHTTP-compat / Reality / VMess+WS),崩溃时 sock 文件残留;supervisord `autorestart=true` 只是 `exec xray run`,不清理 stale socket;ISP switching 路径(`geo.py:_restart_xray_if_running`)虽显式 `glob unlink` 清理,但 autorestart 路径完全绕过。
  - 修复:新增 Python 启动器 `scripts/sb_xray/stages/xray_run.py`,在 `os.execvp("xray", …)` 之前清理 `/dev/shm/uds*.sock`;`templates/supervisord/daemon.ini` 的 `[program:xray]` 改为 `command=python3 /scripts/entrypoint.py xray-run`,使三条路径(autorestart / cron geo-update / ISP switching)统一从干净状态启动。`geo.py` 同步去重(改为单次 `supervisorctl restart xray`,清理交给启动器)。
  - 配套:新增 supervisord eventlistener `xray_exit_listener`,捕获 `PROCESS_STATE_EXITED` 写入容器日志(`[xray-exit] processname=xray from_state=… pid=… expected=…`),便于后续定位崩溃根因(SIGKILL=-9 → OOM 嫌疑)。
  - 文件改动:`scripts/sb_xray/stages/xray_run.py`(新),`scripts/sb_xray/stages/xray_exit_listener.py`(新),`scripts/entrypoint.py`(注册 `xray-run` / `xray-exit-listener` 子命令),`templates/supervisord/daemon.ini`(改 `[program:xray]` + 新增 `[eventlistener:xray_exit_listener]`),`scripts/sb_xray/geo.py`(去重)。
  - 测试:新增 `tests/test_stages_xray_run.py`(6 用例)、`tests/test_stages_xray_exit_listener.py`(7 用例)。全量 430 通过。

### Changed（变更）

- **`common` 订阅轨移除 AnyTLS 节点**：mihomo / OpenClash / Karing 等通用客户端的 anytls outbound 在 mihomo `1.19.24` 及附近版本上出现 url-test 持续返回 `-1` 的回归（上游 commit `9613f02` 重构 22 个 outbound 的 `Base` 初始化路径，对 anytls 字段映射存在静默副作用）。`common` 从 7 条裁剪为 6 条，保留 Hysteria2 / VMess / XTLS-Reality / Xhttp+Reality直连 / 上行 Xhttp+TLS+CDN 下行 Xhttp+Reality / Xhttp+TLS+CDN 上下行不分离。AnyTLS 仍保留在 `/v2rayn` 主轨（v2rayN / sing-box 客户端实现可靠）。同步更新 `show` 输出（"6 协议"）、订阅单测与协议文档（§1.1 表格、§1.10 TUIC 备注、§1.11 AnyTLS 订阅轨标注）。

- **兼容订阅轨重命名为 `common`**：原 `/v2rayn-compat` 分享链接改为 `/common`，`write_subscriptions()` 只生成 `v2rayn` + `common` 两个 base64 订阅文件。`common` 从原 9 条裁剪为 7 条：保留 Hysteria2 / AnyTLS / VMess / XTLS-Reality / Xhttp+Reality直连 / 上行 Xhttp+TLS+CDN 下行 Xhttp+Reality / Xhttp+TLS+CDN 上下行不分离，移除 TUIC 与 `上行Xhttp+Reality下行Xhttp+TLS+CDN`。同步更新 `show` 输出、订阅单测与协议文档。

- **ISP 测速采样器重构（v2）**：跨境 SOCKS5 链路上，v1 的「单次 GET + 1 MiB 文件 + 5s 超时」系统性低估节点带宽 5–20 倍（生产观察：直连 463 Mbps，节点测得 0–21 Mbps）。根因是 TCP slow-start、TLS/SOCKS5 握手、小文件管道填不满三重叠加。
  - v2 改用 `httpx.stream()` 流式读取：丢弃 `ISP_SPEED_WARMUP_SEC`（默认 1.5s）的 TCP 慢启动段 → 从**首字节之后**开始计时 → 在 `ISP_SPEED_WINDOW_SEC`（默认 8s）或 `ISP_SPEED_MAX_BYTES`（默认 256 MiB）封顶时停止 → 返回结构化 `SampleResult`。
  - 失败不再静默 `0.0`：输出 `ok / connect_fail / timeout / low_speed / zero_body / proxy_dep_missing` 状态码。整批聚合成 `_ISP_SPEEDS_DIAG_JSON`（`{tag: {status, ok, total, statuses, bytes, window_sec}}`）并附加到 `isp.speed_test.result` 事件。
  - 新增 `ISP_SPEED_URL_MAP`（JSON `{tag: url}`）按 tag 覆盖探针 URL；默认 Cloudflare 对所有地理位置并非最优。
  - **Kill switch**：`ISP_SPEED_LEGACY=true` 一键回退 v1 路径（保留至少一个 release）。
  - **兼容性**：`_ISP_SPEEDS_JSON` schema 仍是 `{tag: float}`，`stages/isp_retest.py:_max_delta_pct` 零改动。首次 cron 重测会因为新旧值差距触发一次 `delta_exceeded` 重启 xray + sing-box（一次性事件，CHANGELOG 在此显式提醒）。
  - 文件改动：`scripts/sb_xray/speed_test.py`（+500 LoC），`Dockerfile` 暴露 9 个新 ENV，`docker-compose.yml` 示例行，`docs/01-architecture-and-traffic.md` / `docs/04-ops-and-troubleshooting.md` §2.6 新增 v2 小节 + 诊断 runbook。
  - 测试：新增 `tests/test_speed_test_sampler.py`（13 用例）、`tests/test_speed_test_diagnostics.py`（10 用例）、`tests/test_speed_test_url_map.py`（8 用例）；现有 `tests/test_speed_test.py` + `tests/test_run_isp_speed_tests.py` 扩展兼容层。全量 413 通过。

## [26.4.22] — 2026-04-22 · Polish: 构建工具链单一真相源 + 预览版过滤修复

### Fixed（修复）

- **Pre-release 识别改为依赖 GitHub API 元数据而非字符串匹配** —— 上一版用 `tags?per_page=100` + 正则排除 `rc|beta|alpha` 做 stable 过滤，当上游（如 XTLS）把 Pre-release 打成纯数字型 tag（`v26.4.17` 标记 `prerelease: true` 但 tag 名不含 rc/beta/alpha）时，字符串过滤会漏掉，CI 再次把 xray pin 回退为预览版。现 `build.sh:get_latest_stable_tag` 与 `daily-build.yml:get_stable_tag` 改为查 `releases?per_page=100` 并按 `prerelease == false` 布尔过滤 —— 这是 GitHub UI 判定 Pre-release 徽章的唯一真相源。两处注释保留了 v26.4.17 的历史教训，防后续维护者再次换回字符串过滤。同步把 CI 错误写回 `versions.json` 的 `xray: 26.4.17` 手动回退为 `26.3.27`。

### Changed（变更）

- **`build.sh`：`versions.json` 成为 versions + digests 的单一真相源**。原实现存在三方割裂:离线模式 `./build.sh default` 只读 digests、versions 用硬编码 fallback;无参模式每次打 GitHub API;Dockerfile ARG 又是第四套默认值 —— 三方可能漂移。现重构为两模式对称:
  - `./build.sh`（默认离线）— versions + digests 都从 `versions.json` 读，完全不触网，与 CI 每日提交的产物位级一致
  - `./build.sh refresh` — 拉 GitHub API → 写回 `versions.json` → 构建（等价 CI 动作）
  - `./build.sh --local` — 单架构 linux/amd64 + `--load`,不推 registry;可与两模式组合
  - `./build.sh default` / `./build.sh offline` 保留为离线模式别名
  删除 `update_script_default()` 与 `VERSIONS_UPDATED` 机制（不再有需要回写的硬编码默认版本）；`check_version` 从 4 参数精简为 3 参数，版本缺失即 `exit 1`，消除隐式 fallback。
- **`release.sh` 同步切到 stable release selector**。`docs/05-build-release.md §2 §4.1` 同步改写为两模式 CLI 说明 + 「`versions.json` 是单一真相源」导航。

### Docs（文档）

- **readme.md 精修**（净减 33 行）:
  - §4 业务级智能路由分发 改写为三点，突出 `isp-auto` 健康选优闭环（带宽信号 probe URL / 按服务分桶 / 周期重测 / 策略驱动 fallback），链接到 01 §6.4 架构图
  - §环境变量全集 从 6 张 `<details>` 子表（含若干已不存在的 ghost env）压缩为 11 行速查表 + 指向 `docs/04 §2` 的完整参考
  - §目录结构 + §挂载卷补齐新增文件（`events.py` / `service_spec.py` / `stages/isp_retest.py` / `versions.json` / `CONTRIBUTING-diagrams.md`）与 `/geo` 持久化卷
  - §开发与跨平台构建 CLI 更新为 `./build.sh` / `./build.sh refresh` 两模式
  - §证书默认值纠错 `ACMESH_SERVER_NAME=letsencrypt`（与 Dockerfile 实际 ENV 一致，原表错标为 `zerossl`）
  - §1.1 Sing-box 引擎描述与鸣谢章节里 "Hysteria2 于 2026-04 永久迁至 Xray" 改为中性表述

## [26.3.27] — 2026-04-22 · Hotfix: 稳定版回滚

### Fixed（修复）

- **版本选择器意外放行 Pre-release 导致 Xray pin 在 v26.4.17 (XTLS 官方 Pre-release)**：`build.sh` 对 Xray 使用无过滤的 `get_latest_tag()`(底层 `/tags?per_page=1`,取首个 tag),未与 sing-box / x-ui / dufs / cloudflared 等组件一致走 `get_latest_stable_tag()`(过滤 `rc|beta|alpha`)。同样的 bug 也存在于 `.github/workflows/daily-build.yml` 的 `get_tag` 调用路径。现 Xray 统一切到 `get_latest_stable_tag` / `get_stable_tag`,fallback 默认值同步为 `26.3.27`。
- **Xray-core pin 从 v26.4.17 回退到稳定版 v26.3.27**：同步更新 `Dockerfile` / `pyproject.toml` / `sb_xray.__version__` / `versions.json`。本项目全部功能(后量子 MLKEM768、Xray-native Hysteria2、marktag webhook、XHTTP/3、ECH 等)的兼容性调研本就基于 v26.3.27 稳定版,无功能回退。

> 注：本版本号较上一版 `26.4.17` 在 SemVer 数字上倒退,是因 `v26.4.17` 系 XTLS 官方 Pre-release,不符合本项目「所有组件使用稳定版」策略。项目版本号与 Xray 稳定主版本对齐的约定不变。

### Added（新增功能）

- **`isp-auto` 健康选优系统升级（5 阶段闭环）**——把原本仅"启动时测一次"的 ISP 选路,改造为「冷启动缓存 → 速度实测 → 配置渲染 → 内核健康选优 → 周期重测」的完整闭环,所有行为由 12 个 env flag 控制,默认值在 Dockerfile 注册,不改 docker-compose 即可开箱运行。
  - **探测 URL 带宽信号化**:硬编码的 `gstatic.com/generate_204`(0 字节)替换为 `ISP_PROBE_URL`,默认 `https://speed.cloudflare.com/__down?bytes=1048576`(1 MiB)。被限速但仍能 ping 通的 ISP 节点现在会自然下沉而非继续被选中。新增 `ISP_PROBE_INTERVAL` / `ISP_PROBE_TOLERANCE_MS`。
  - **结构化事件总线**:新增 `sb_xray.events.emit_event(name, payload)`,向 stdout 输出 `event=... payload={...}` 一行 + 当 `SHOUTRRR_URLS` 设置时 POST 到本地 forwarder。6 类事件:`isp.speed_test.result` / `.cache_hit` / `.error`,`isp.retest.completed` / `.noop` / `.error`。env 开关 `ISP_EVENTS_ENABLED=true`。
  - **周期性带宽重测**:新增 `/scripts/entrypoint.py isp-retest` 子命令 + `scripts/sb_xray/stages/isp_retest.py` 编排器。cron 每 `ISP_RETEST_INTERVAL_HOURS`(默认 6h)触发,仅当节点组成 / top-1 tag 变化 或任意节点速度变化 > `ISP_RETEST_DELTA_PCT`(默认 15%)时重渲染配置并 `supervisorctl restart xray sing-box`,纯 RTT 波动留给 urltest / leastPing 在线处理。`ISP_RETEST_INTERVAL_HOURS=0` 或 `ISP_RETEST_ENABLED=false` 完全禁用。
  - **sing-box 按服务分桶 balancer**:`ISP_PER_SERVICE_SB=true`(默认关闭)开启后,在保留 legacy `isp-auto` 的同时额外生成 `isp-auto-netflix` / `isp-auto-openai` / `isp-auto-claude` / `isp-auto-gemini` / `isp-auto-disney` / `isp-auto-youtube` 共 6 个独立 urltest 出站,各自用该服务的真实域名做 probe。通过 `config_builder` 的 env 快照/复位,sb.json 看到分桶 tag,xr.json 保持 legacy `isp-auto`(xray observatory 全局单例结构约束,无法在单实例内做同等分桶)。新增 `scripts/sb_xray/routing/service_spec.py` 作为「服务 → probe URL」的单一事实来源。
  - **策略驱动 Fallback 链**:`ISP_FALLBACK_STRATEGY` ∈ `{direct(默认), block}`。`block` 为 CN/HK/RU 受限地区提供 fail-closed 语义,拒绝 ISP 全挂时静默走 direct。`network.get_fallback_proxy()` 委托到统一解析器 `_resolve_fallback_tags()`,避免 media 探测与 balancer 渲染漂移。
  - **冷启动 TTL 缓存**:`ISP_SPEED_CACHE_TTL_MIN`(默认 60 分钟)窗口内启动不跑实测,直接读取上次 STATUS_FILE 中的 `_ISP_SPEEDS_JSON`(冷启动从 ~30s 降到 <1s);同时 daemon 线程异步跑一次实测刷新结果。`ISP_SPEED_CACHE_ASYNC=false` 可关闭异步刷新仅用于调试。
  - **测试与文档**:新增 60 个 pytest 用例(`test_probe_resolver` / `test_events` / `test_stages_cron` 扩展 / `test_isp_retest` / `test_build_sb_urltest_set` / `test_sb_service_outs_override` / `test_fallback_resolver` / `test_speed_cache_coldboot`);`tests/test_routing_isp.py` 字面量断言泛化为 env-driven 参数化。所有 12 个 env flag 在 `docs/04-ops-and-troubleshooting.md §2.6` 有完整表格和典型组合(低内存节点 / 受限地区 fail-closed / 极致解锁命中率)。
  - **运行时闭环架构图**:`docs/01-architecture-and-traffic.md §6.4` 新增完整 mermaid 图,串起启动 → 缓存判定 → 实测 → 渲染 → 内核健康选优 → 周期重测 → 事件 6 个子图。
  - **文档视觉体系重构**:产品文档的 37 张 mermaid 图统一到商业级调色板(`entry / process / decision / data / external / warning / terminal` 七语义色,固定 hex),清除 `#f96 / #61dafb / #b19cd9 / #98fb98 / #ff6b6b / #4ecdc4 / #95e1d3 / #f9f / #6f9` 等 ad-hoc 色;`graph LR/TD/TB` → `flowchart LR/TD/TB`;新增仓库根 `CONTRIBUTING-diagrams.md` 作为维护者风格指引(不纳入 `docs/`)。

### Changed（变更）

- **Entrypoint 日志彻底 stdlib 化 + StageTimer 计时 + 架构轻量整理**:
  - **日志基础设施**：新增 `scripts/sb_xray/log_config.py`（stdlib `logging` + `dictConfig` + 自定义 `SbFormatter`，ISO-8601 带时区时间戳 + `%(name)s` 自动模块名）和 `scripts/sb_xray/stage.py`（`StageTimer` 上下文管理器 + `PipelineSummary` + `render_summary_box`）。删除旧 `scripts/sb_xray/logging.py`（手写 stderr writer）。
  - **环境变量契约**：`SB_LOG_LEVEL`（默认 `INFO`，`DEBUG/INFO/WARNING/ERROR/CRITICAL`，兼容 `WARN` 别名）控制 Python 日志级别；与给 xray/sing-box 用的 `LOG_LEVEL=warning` 刻意分离，避免 xray 的字符串值屏蔽 INFO 阶段进度。`NO_COLOR`（https://no-color.org/）或非 TTY stdout → 自动关闭 ANSI 彩色。
  - **迁移点**：14 个模块（entrypoint + stages/* + routing/isp + geo + config_builder + speed_test + display + shoutrrr + cert + ...）全部从 `sblog.log(LEVEL, "[模块] msg")` 迁到 `logger = logging.getLogger(__name__)` + `logger.info/warning/error/...`；手写 `[module]` 前缀全部删除（由 formatter 的 `%(name)s` 自动补齐，消除 `[选路]` vs `[ISP]` vs `[路由]` 这类拼写漂移）。`display.py` 的 7 处 `print()`（tls-ping / qr 诊断）全部走 logger；`shoutrrr.py` 的自定义 `_log()` 包装移除。
  - **StageTimer**：每个 pipeline 阶段用 `▶ / ✓ / ⋯ / ✗` 三符号表示 start / ok / skipped / failed，自动带毫秒级 duration；失败时 `logger.exception` 输出完整 traceback；所有结果累积到 `PipelineSummary`，`run_pipeline()` 末尾一次性输出 `Pipeline summary: N stages total in Xms — ok= skipped= degraded= failed=` 聚合行。
  - **SUMMARY 方框去重**：原 `entrypoint.py:603` 和 `:626` 两处 `sblog.log_summary_box()` 导致 SYSTEM STRATEGY SUMMARY 方框被打两次、字段不同但样式相同。现保留末尾一次（通过新 `render_summary_box()` 写 stdout，与订阅 banner 同步为一次性运维报告），原阶段 6 的重复方框改为结构化一行 `logger.info("media routing: ...")`。
  - **订阅 banner 保留 stdout**：`display.render_info_links` 面向终端用户的订阅链接/QR 不属于日志流，仍然写 `sys.stdout`；日志流末尾留一行锚点 `handing over to supervisord; subsequent lines come from supervisord / xray / nginx` 明确格式切换点。
  - **架构附带整理**（范围 B）：
    - `speed_test.run_isp_speed_tests()` 137 行 → 拆为 `_resolve_sample_count / _try_cache_hit / _reset_caches_for_fresh_run / _log_routing_inputs / _measure_direct_baseline / _measure_isp_nodes / _persist_routing_decision` 7 个子函数（均 <40 行），主函数保留签名。
    - `cert.ensure_certificate()` 122 行 → 拆为 `_bundle_paths / _existing_bundle_is_fresh / _issue_with_acme / _purge_nginx_dynamic_dirs / _install_and_cleanup` 5 个子函数。
  - **Pipeline 阶段编号**：原 15 阶段（`_step` 标签）→ 17 阶段（StageTimer 细粒度 index），每个可 skip 子阶段一个独立 timer；`--skip-stage` 的 16 个 ID 保持不变。
  - **测试**：`tests/test_logging.py` 重写（13 个用例覆盖 formatter、LOG_LEVEL 过滤、NO_COLOR、非 TTY、idempotent、traceback、StageTimer start/end/skipped/failed、PipelineSummary、render_summary_box）。`tests/test_display.py` / `tests/test_shoutrrr.py` 从 `capsys` 迁到 `caplog`（log 去 stderr 不再进 capsys.out）。共 319 pytest 全绿 + 49 smoke 全绿。
  - **文档**：`docker-compose.yml` 加 `SB_LOG_LEVEL=INFO` 示例；`docs/04-ops-and-troubleshooting.md §2.2` 新增"Entrypoint 日志" 小节（格式/级别/锚点/排查建议）；`docs/features/0421-new-features-guide.md` 参考表加 `SB_LOG_LEVEL` + `NO_COLOR`。
- **Shoutrrr forwarder:迁入 `sb_xray` 包 + 吞错 bug 修复 + 产品文档重写**:
  - **架构**:新增 `scripts/sb_xray/shoutrrr.py`,与 `geo.py` / `display.py` 统一形态,`run(port, urls, title_prefix)` 参数化入口,`Handler` 从模块全局改为闭包工厂,杜绝跨测试污染。
  - **入口**:`scripts/entrypoint.py` argparse 新增 `shoutrrr-forward` 子命令;`templates/supervisord/daemon.ini` 的 `[program:shoutrrr-forwarder]` 改为 `command=python3 /scripts/entrypoint.py shoutrrr-forward`。program block 名保持 `shoutrrr-forwarder` 不动,`config_builder.py` 的 `ENABLE_SHOUTRRR` trim 映射和 `test_smoke.sh:M1-5` 的 grep 断言均无需更新。
  - **Bug 修复 — 吞错再无**:原实现 `subprocess.run(check=False, capture_output=True)` 把 shoutrrr 子进程的 stdout/stderr/exit code 全吞掉,导致"forwarder 返回 204 但通道里没消息"完全无迹可循(本次用户真实踩坑:bot 非频道管理员,shoutrrr 返回 exit=69 + "need administrator rights" 但 forwarder 日志一片干净)。修复后每条推送都会记 `send ok scheme=telegram event=...` 或 `send failed scheme=telegram exit=69 stderr='Bad Request: ...'`,token 不泄露(只记 URL scheme);subprocess 崩溃单独记 `send crashed`。
  - **配置**:`docker-compose.yml` 第 46–50 行新增 `SHOUTRRR_URLS=` / `SHOUTRRR_TITLE_PREFIX=[sb-xray]` / `SHOUTRRR_FORWARDER_PORT=18085` 三行(默认 dry-run,不外推),运维改两个字符就能接上 Telegram/Discord。
  - **清理**:`scripts/shoutrrr-forwarder.py` 91 行物理删除;`test_smoke.sh` 必需文件清单同步为 `scripts/sb_xray/shoutrrr.py`。
  - **测试**:新增 `tests/test_shoutrrr.py` 10 条(`_parse_urls` 4 种输入 / dry-run 不 spawn subprocess / 多 URL 成功路径 + 日志脱敏 / 非零 exit + stderr 透传回归 / subprocess 崩溃 / healthz 200 / 404 / POST 204 / POST 400 / `run()` env 回退),从 303 升至 **313 全绿**。
  - **产品文档**:新增独立文档 `docs/07-event-bus-shoutrrr.md`(11 节 ~450 行),含 ASCII 架构图 + Mermaid 序列图 + 环境变量对照表 + Telegram 5 分钟快速开始(含 bot 提管理员/chat.id/409 冲突/`:` URL-encode 等所有真实踩坑)+ URL 语法速查 + 故障排查对照表 + payload 字段说明 + 降噪/多通道/trim 进阶 + 诊断命令集。`docs/features/0421-new-features-guide.md` §1 缩为引流段。
  - **行为零变化**:对外 env var、监听端口 18085、webhook payload 格式、supervisord log 路径、`ENABLE_SHOUTRRR=false` trim 行为全部保持不变;升级只需 `docker compose up -d sb-xray --force-recreate` 重建容器让新 env 生效。

- **GeoIP/GeoSite 规则库:Python 重写 + 持久化 (`geo_update.sh` 退役)**:
  - 新增 `scripts/sb_xray/geo.py`:httpx + `ThreadPoolExecutor(6)` 并行下载 6 个 `.dat`(Loyalsoldier/chocolate4u/runetfreedom),`os.replace` 原子写入,失败不污染旧缓存。
  - 规则库落盘目录从镜像内的临时路径 `/usr/local/bin/bin/` 迁移到持久化卷 `/geo`(`docker-compose.yml` 新增 `./geo:/geo`;Dockerfile 追加 `VOLUME /geo`)。容器重启不再重下 ~100 MB,首次冷启动 `/geo` 空目录时才全量下载。
  - 启动阶段 (`sb_xray.stages.geoip`) 调用 `geo.refresh(on_startup=True)`:文件 <7 天视为新鲜直接跳过,不触发 xray 重启;cron 场景 (`/scripts/entrypoint.py geo-update`) 强制刷新并在 supervisord socket 存在时 `supervisorctl stop/start xray`。
  - 符号链接在 `/usr/local/bin/bin/`(xray asset dir)**和** `/usr/local/bin/`(sing-box asset dir)双写,修复 xray `open /usr/local/bin/bin/geoip.dat: no such file` 启动失败。
  - `scripts/entrypoint.py` argparse 新增 `geo-update` 子命令供 cron 调用;`sb_xray.stages.cron._GEO_ENTRY` 同步替换,并自动清理老部署残留的 `/scripts/geo_update.sh` 行(幂等迁移)。
  - `scripts/geo_update.sh` 60 行 bash 物理删除;`scripts/sb_xray/geo.py` docstring 接管 PR #5505 / PR #5814 revert 的历史注记以满足 `test_smoke.sh` M1-4 校验。
  - pytest 从 297 升至 303 条全绿,新增 `tests/test_geo.py` 覆盖首启全量下载 / 7 天新鲜度跳过 / 下载失败回滚 / cron 强刷 + xray 重启 / socket 缺失时跳过重启 / 双 link_dir 并行写入 6 种路径。
  - **迁移**:升级现网只需 `docker compose pull && docker compose up -d`;未升级 `docker-compose.yml` 的节点仍能运行但每次 down/up 都会重下。

- **`scripts/show` 入口修复**:Dockerfile 新增 `ln -sf /scripts/show /usr/local/bin/show`,让 `docker exec sb-xray show` 可正常触发 `entrypoint.py show` shim(此前镜像构建从未创建该符号链接,`show` 命令始终 `command not found`)。

- **`show` 横幅扩展 Sub-Store 面板链接**:新增 `🗂 Sub-Store 面板` + `🔑 Sub-Store 后端 API` 两行块(格式与订阅条目一致,Web UI 黄色 / 后端青色);后端 URL 取 `$SUB_STORE_FRONTEND_BACKEND_PATH`(32 位随机路径),用户首次进面板复制粘贴到"设置 → 后端地址"即可。`ENABLE_SUBSTORE=false` 自动隐藏。

- **`scripts/check_ip_type.sh` 归档**:xykt IPQuality 体检脚本(561 行,从未被 runtime 调用)移入 `sources/hack/`;同名的 Python 运行时函数 `sb_xray.network.check_ip_type`(ipapi.is + 本地缓存)继续服务 ISP 判定。

- **Entrypoint Python 重写 · Phase 8 — `entrypoint.sh` 彻底退役（100% Python 编排）**：
  - `scripts/entrypoint.sh`（1475 行 bash）**物理删除**；`scripts/entrypoint.py:run_pipeline` 在容器内顺序执行 15 段 Python 启动流水线（`_init_dirs` → 解密远端密钥库 + source → bootstrap ENV/STATUS → `probe_base_env` → `run_isp_speed_tests` → 媒体探针 → `ensure_all_keys`（Reality + MLKEM768）→ `build_client_and_server_configs` → `issue_bundle_certificate`（fail-fast）→ `ensure_dhparam` → `update_geo_data` → `create_config` + `generate_and_export` + `trim_runtime_configs` → `init_panels` → `setup_basic_auth` → `install_crontab` → banner → `os.execvp` supervisord）。
  - 新增 `sb_xray/stages/` 子包封装 7 个 subprocess-only 阶段（`dhparam/geoip/panels/nginx_auth/cron/supervisord/keys`）。
  - `sb_xray.speed_test.measure` 升级为截断均值 + 标准差 + CV `[稳定]/[轻微波动]/[波动较大]` 标签（bash §9 逐行对齐）；`IspSpeedContext.tolerance` 默认回到 `1.0` 匹配 bash `>` 比较。
  - `_load_env_file` 委托给 `bash -c 'set -a; source "$1"; env -0'` 子进程而非自写解析器 —— 兼容 bash source 的全部语法（quoted / bareword / `export` 前缀 / `$(...)` 命令替换 / heredoc / CRLF / BOM / 注释），并在 SECRET_FILE 里的 7 个 ACME 凭据能覆盖 Dockerfile 里的空字符串占位符（`setdefault` 仅保护非空父值）。
  - `config_builder._envsubst` 修正为"未定义 `$VAR` 保留原文"（对齐 GNU envsubst），防止 nginx.conf 里的 `$http_*` / `$arg_*` / `$client_ip` 等运行时变量被错误替换为空导致 `invalid number of arguments in "map" directive`。
  - `routing/isp.py`:`build_xray_balancer` 用 `_unwrap_outer_braces` 精确剥一对 `{}`（老版 `.strip('{}')` 会连带删掉 JSON 内层闭括号）；`build_sb_urltest` 返回值追加尾逗号与 sb.json 模板约定一致。
  - `sb_xray.cert`:`ensure_certificate` 改为**总是** `--register-account + --issue`（acme.sh 自身幂等，去除易污染的 `_acme_already_has` `--list` 短路）；`--issue` / `--install-cert` 全部检查返回码；`_acme_env()` 将 UPPER_CASE SECRET 凭据翻译为 acme.sh DNS 插件期望的 mixed-case（`Ali_Key`/`Ali_Secret`/`CF_Token`/`CF_Zone_ID`/`CF_Account_ID`）；新 `_issue_failure_hint` 按日志 pattern 给出运维级提示（rate-limit / 凭据缺失 / DNS 传播 / 配额）；`ssl_path` 默认读 `$SSL_PATH`（= `/pki`）与模板一致。
  - 证书阶段引入 `CertStageError` 快速失败语义：`DOMAIN/CDNDOMAIN` 缺失 / acme.sh 非 0 返回码 / 安装后 `/pki/sb_xray_bundle.{crt,key,-ca.crt}` 文件缺失任一情况都中止 pipeline，Python 退出非 0，docker-compose restart 给运维明确信号，避免下游 nginx/xray 进入 FATAL restart 循环。
  - `run_isp_speed_tests` cache-hit 路径做环境漂移检测：缓存的 `ISP_TAG` 对应节点已从 `*_ISP_IP` 中移除时，清缓存走完整测速，避免 xray 启动时报 "outbound tag not found"。
  - `Dockerfile` pip 安装 `socksio` 以支持 `httpx` 的 socks5h:// 代理测速；`pyproject.toml` dep 改为 `httpx[socks]`；speed-test subprocess ImportError 时优雅降级为 0 Mbps（bash parity）。
  - CA 默认 `ACMESH_SERVER_NAME=letsencrypt`（无需 EAB，支持 DNS-01 wildcard，速率限制宽松）；docker-compose.yml / Dockerfile ENV / docs/02 §4 CA 对照表全部对齐；新增 "Buypass 不支持通配符" 告警。
  - 启动日志格式简化：去掉 `[步骤 N]` / `[N/15]` 魔法数字，改为 `▸ 阶段描述` + 子阶段域前缀 `[env]/[选路]/[测速]/[ISP]/[media]/[keys]/[cert]/[dhparam]/[geoip]/[nginx-auth]/[panels]/[cron]/[supervisord]/[secrets]/[skip]`。
  - `scripts/test_smoke.sh` 里所有 `grep entrypoint.sh` 断言重定向到对应的 Python 模块（`sb_xray/config_builder.py` 等）；Dockerfile `ENTRYPOINT` / readme 文件树 / `docs/01/04/05` / nginx 模板注释全部同步。
  - **pytest 从 249 升至 297 条全绿**，新增 `test_stages_*` / `test_run_isp_speed_tests` / `test_build_isp_outbounds` / `test_cert` 回归 + `test_entrypoint_py` 的 bash-source 委托测试共 30+ 条覆盖新增路径（包含 生产 VPS 7 轮攻坚定位出的每一类根因）。

### Added（新增功能）

- **小内存节点降载开关**:新增 4 个 opt-out 环境变量让内存不超过 512 MB 的节点常驻 RSS 从 ~520 MB 降到 ~300–430 MB,避免 xray 启动期 VSZ 1.4 GB 触发内核 OOM kill。开关均为 opt-out 语义,**仅**在显式设为字符串 `"false"` 时生效,未设置时保持完整启动。
  - `ENABLE_SUBSTORE=false` 过滤 supervisord 的 `sub-store` + `http-meta` 段(省 ~130–200 MB)
  - `ENABLE_XUI=false` 过滤 `x-ui` 面板段(省 ~35–55 MB)
  - `ENABLE_SUI=false` 过滤 `s-ui` 面板段(省 ~35–55 MB)
  - `ENABLE_SHOUTRRR=false` 过滤 shoutrrr-forwarder 段(省 ~20–30 MB)
  - **作用机制**:`scripts/entrypoint.py` 新增 `trim` 子命令 → `scripts/sb_xray/config_builder.py:trim_runtime_configs()` 实现 regex 分段过滤(保留 supervisor `%(ENV_*)s` 插值语法);`scripts/entrypoint.sh:createConfig` 后立即 `python3 /scripts/entrypoint.py trim`(幂等、失败不阻塞主流程)。
  - **配置锚点**:Dockerfile 注册 4 个 ENV 默认值(全部 `true`,全启用语义);`docker-compose.yml` `environment:` 段显式列出供用户快速覆写;`GOMEMLIMIT=320MiB` + `GOGC=50` 配合使用可进一步约束 Go 进程 GC。
  - **文档**:`docs/04-ops-and-troubleshooting.md` §2 环境变量表新增降载开关行;§7 新增 "小内存节点部署指引",含 `docker-compose.yml` 片段(`mem_limit: 460m`)、开关语义表、宿主层 `swap`/`vm.overcommit_memory=1` 建议、30 分钟验证命令。`docs/01-architecture-and-traffic.md` §5 启动流水线补充 §13b trim 阶段。
  - pytest 新增 8 条用例覆盖 opt-out 默认行为、显式 `"false"` 分支、supervisor 插值保留、`trim_runtime_configs` 两条路径、`trim` 子命令不回落 legacy bash。

- **Entrypoint Python 重写 · Phase 0 骨架**:引入 `pyproject.toml` 定义 Python 包 `sb_xray`(路径 `scripts/sb_xray/`),声明运行时依赖(jinja2 / httpx / pydantic / pyyaml)与开发依赖(pytest / pytest-asyncio / pytest-cov / pytest-httpx / respx / ruff / mypy)。Dockerfile 运行时层补 `py3-jinja2 py3-httpx py3-yaml py3-pydantic` 四个 apk 包。新建 `tests/` 目录含 `conftest.py` 共享 fixture(`tmp_env_file` / `isolated_workdir`)。`scripts/test_smoke.sh` 新增 "Python 包健康检查" section(sb_xray 包导入 + pytest + ruff check),smoke 基线从 52 条扩充到 55 条。
- **Entrypoint Python 重写 · Phase 1 工具层 + ENV 管理**:迁移 entrypoint.sh §1-6。新增模块 `sb_xray.logging`(log/log_summary_box/show_progress,honor `NO_COLOR`)、`sb_xray.http`(httpx 同步/异步 probe + trace_url,替代 curl)、`sb_xray.random_gen`(secrets 驱动的 port/uuid/password/path/hex 生成)、`sb_xray.templates`(Jinja2 StrictUndefined,`${VAR}` 转 `{{ VAR }}`,`.json` 目标自动校验 + 重格式化)、`sb_xray.env.EnvManager`(三优先级 `ensure_var` + `ensure_key_pair` 原子写入 + `check_required`)。新建 `scripts/entrypoint.py` 薄壳入口(`--dry-run` / `--env-file` / `--skip-stage`),bootstrap 加载持久化 ENV 后 `subprocess` 调原 `entrypoint.sh` 继续剩余阶段。新增 62 条 pytest 单测全绿,覆盖 `ensure_var` 三分支、`ensure_key_pair` 原子性、模板缺变量抛错、JSON 失败抛错等。smoke 基线 55 → 56 条。
- **Entrypoint Python 重写 · Phase 2 网络探测 + 测速**:迁移 entrypoint.sh §7-9。新增模块 `sb_xray.network`(`detect_ip_strategy` / `check_ip_type` ipapi.is 缓存 / `get_geo_info` ip111.cn / `is_restricted_region` CN/HK/MO/RU 正则 / `check_brutal_status` 检测 `/sys/module/brutal` / `get_fallback_proxy` / `get_isp_preferred_strategy`)、`sb_xray.speed_test`(`measure` httpx 采样返回 Mbps、`rate` 五级分档 8K-HDR/8K/4K/1080P/slow、`show_report` stderr 框、`IspSpeedContext` 带 1.15 tolerance 的最快节点追踪)。`entrypoint.py` 新增 `--python-stage probe` 开关(默认关闭),启用后在 bootstrap 后调用 `probe_base_env` 预填 `GEOIP_INFO` / `IP_TYPE` / `BRUTAL_STATUS` 持久化到 ENV_FILE,Bash 下游阶段直接继承。新增 38 条 pytest 单测(Phase 1/2 合计 106 条),`pyproject.toml` ruff 忽略中文标点误报(RUF001/002/003)。
- **Entrypoint Python 重写 · Phase 3 路由决策**:迁移 entrypoint.sh §10-11。新增 `sb_xray.routing.isp`(`RoutingContext` / `IspDecision` 不可变数据类、`process_single_isp` 生成 Xray+Sing-box socks 出站 JSON、`build_sb_urltest` Sing-box urltest 按速度降序、`build_xray_balancer` observatory+balancer JSON 片段、`build_xray_service_rules` geosite 规则带 marktag/balancerTag/outboundTag、`apply_isp_routing_logic` 综合选路纯函数含 DEFAULT_ISP 锁定 + 受限地区 + 非住宅 IP + IS_8K_SMOOTH 阈值 100 Mbps)、`sb_xray.routing.media`(8 个 `check_*` 函数 Netflix/Disney/YouTube/Social/TikTok/ChatGPT/Claude/Gemini,表驱动 restricted-region 短路 + probe + trace_url Claude 重定向识别 + GEMINI_DIRECT 覆盖,`check_all` 聚合成 `{NETFLIX_OUT/…/GEMINI_OUT}` 8 键映射)。新增 41 条 pytest 单测(含 6 分支路由 + 3 分支 IS_8K_SMOOTH + 8 个媒体 restricted/hosting/residential 全路径)。累计 147 条 pytest 全绿。
- **Entrypoint Python 重写 · Phase 4 证书 + 订阅 + 展示**:迁移 entrypoint.sh §12/§14 + 整个 `show-config.sh`。新增 `sb_xray.cert.ensure_certificate`(subprocess 包装 openssl 7 天有效期阈值判断 + acme.sh 注册/签发/安装,Google CA EAB 校验);`sb_xray.secrets.decrypt_remote_secrets`(httpx 下载 + crypctl `--key-env DECODE`);`sb_xray.subscription`(10+ 协议 URL 构造器 Hy2/TUIC/AnyTLS/VMess/XHTTP-H3 + `write_subscriptions` 产出 `v2rayn` / `v2rayn-compat` 两轨 base64 文件,compat 轨自动剔除 mlkem768);`sb_xray.display`(`get_flag_emoji` 19 地区查表、`tls_ping_diagnose` xray 子进程、`show_qrcode` qrencode 子进程、`show_info_links` stdout 订阅页)。`scripts/entrypoint.py` 升级为 argparse subparsers(`run` / `show`),新增 `scripts/show` 1 行 shim `exec python3 /scripts/entrypoint.py show "$@"` 替代 `show-config.sh` 作为 `/usr/local/bin/show` 软链目标。新增 35 条 pytest 单测(累计 182),cert/secrets/subscription/display 全覆盖。
- **Entrypoint Python 重写 · Phase 5 切换 Dockerfile ENTRYPOINT**:Dockerfile L417 `ENTRYPOINT` 从 `/scripts/entrypoint.sh` 切换为 `python3 /scripts/entrypoint.py run`(保留 `dumb-init` PID 1);`/usr/local/bin/show` 运行时软链目标从 `show-config.sh` 改为 `/scripts/show` Python shim,`docker exec sb-xray show` 走 Python 路径。entrypoint.sh 本身仍保留供 Python 内部 subprocess 调用处理未迁移的配置渲染片段,Phase 6 会彻底移除。冷启动链路:dumb-init → python3 entrypoint.py run → subprocess bash entrypoint.sh → exec supervisord。生产 VPS(vpn.example.com)灰度观察 72 小时后进入 Phase 6。
- **Entrypoint Python 重写 · Phase 6 收尾清理 (a)**:删除 `scripts/test_entrypoint.sh`(426 行,由 pytest 套件完全替代,182 条测试覆盖所有原 T1-T11 分支);更新 `.dockerignore` 去除已删除文件、补 `tests/` 排除入镜像;更新 `readme.md` 文件树把 `test_entrypoint.sh` 改为 `scripts/show` shim 说明。
- **Entrypoint Python 重写 · Phase 6 收尾清理 (b) + Phase 7 orchestration wiring**:(1) 正式删除 `scripts/show-config.sh`(267 行)—— 由 `scripts/show` shim + Python `scripts/entrypoint.py show` 子命令完全替代,覆盖率 ~98% 含字节对齐的 10+4 协议订阅 URL、`FLAG_PREFIX`/`NODE_SUFFIX` 派生、ANSI 彩色 banner + 去色归档;(2) 删除 `scripts/stop-supervisor.sh`(19 行死代码,仅被 supervisord.conf 注释块 `[eventlistener:exit]` 引用,本轮一并清理该注释块);(3) 新模块 `sb_xray.routing.providers.generate_and_export`(`generateProxyProvidersConfig` port,导出 `CLASH_PROXY_PROVIDERS` / `SURGE_PROXY_PROVIDERS` / `SURGE_PROVIDER_NAMES` / `STASH_PROVIDER_NAMES` 四个 env);(4) 新模块 `sb_xray.config_builder.create_config`(`createConfig` orchestrator,含 13 个 Xray/Sing-box JSON 模板 envsubst 渲染 + 孤儿 JSON 清理 + `ENABLE_XICMP`/`ENABLE_XDNS` feature-flag 过滤 + `ENABLE_REVERSE` VLESS Reverse-Proxy client/route JSON 注入);(5) 新模块 `sb_xray.node_meta.derive_and_export`(`NODE_SUFFIX` 派生 4 条规则:`dmit|dc|jp` 前缀 → ✈ 高速、`ISP_TAG!=direct + IS_8K_SMOOTH=true` → ✈ good、`IP_TYPE=isp + IS_8K_SMOOTH=true` → ✈ super、`IP_TYPE` 自身后缀);(6) `entrypoint.py` 新增 `--python-stage {probe,cert,providers,config,media}` 接线开关,opt-in 逐阶段灰度;`probe_base_env` 从 3 vars 扩到 **16 vars**(XUI_LOCAL_PORT / DUFS_PORT / PASSWORD / XRAY_UUID / XRAY_REVERSE_UUID / SB_UUID / XRAY_REALITY_SHORTID_{,2,3} / XRAY_URL_PATH / SUBSCRIBE_TOKEN / STRATEGY / GEOIP_INFO / IS_BRUTAL / SUB_STORE_FRONTEND_BACKEND_PATH / IP_TYPE);新增 `issue_bundle_certificate` / `run_media_probes` 入口。

### Fixed(修复)

- **acme.sh `integer expected` 警告消除**:Dockerfile `LOG_LEVEL="warning"`(给 xray/sing-box 使用的字符串日志级别)与 acme.sh 内部数值 `LOG_LEVEL`(1/2/3)命名冲突,触发 `[ "${LOG_LEVEL:-$DEFAULT_LOG_LEVEL}" -ge "$LOG_LEVEL_1" ]` 的整数比较告警(L347/381/414)。`scripts/entrypoint.sh:issueCertificate` 所有 4 处 `acme.sh` 调用改用 `env -u LOG_LEVEL acme.sh` 包装;`scripts/sb_xray/cert.py` 新增 `_acme_env()` helper 剥离该 env,所有 `subprocess.run(["acme.sh", ...])` 传 `env=_acme_env()`。
- **nginx 证书安装前清目录 + 关旧 nginx + 清 PID**:`cert.py:ensure_certificate` 在 `acme.sh --install-cert` 前后补齐 bash 原副作用—— 清空 `/etc/nginx/conf.d/*` 与 `/etc/nginx/stream.d/*`(避免 acme 的 `--reloadcmd` 启动 nginx 时加载残留 orphan upstream)、`--install-cert` 之后 `nginx -s quit` + `rm -f /var/run/nginx/nginx.pid`(让 supervisord 后续能 fork 干净 nginx)。
- **nginx catch-all 的 IPv6 噪声消除**:容器默认 docker-bridge 无 IPv6 出口,`location / { proxy_pass https://${DEST_HOST}; }` 被扫描器命中时,DNS 解析到 Cloudflare IPv6 `[2606:4700:…]` 会 `ENETUNREACH` 产生 "Network unreachable" 噪声。`templates/nginx/http.conf` 在 server 块加 `resolver 127.0.0.11 8.8.8.8 1.1.1.1 ipv6=off valid=300s` + `resolver_timeout 5s`,强制仅选 A 记录。
- **`bootstrap()` / `main()` summary box `N/A` 修复**:`scripts/entrypoint.py:bootstrap` 现在把 env_file 路径写回 `os.environ["ENV_FILE"]`(bash `source` 自带此语义,Python 之前缺),`main()` 启动期与 `run_show_pipeline` 都额外 source `STATUS_FILE` + `SECRET_FILE`,恢复 `ISP_TAG` / `IS_8K_SMOOTH` / 远端密钥对下游的可见性,同时修 `NODE_SUFFIX` 缺 ✈ super/good 标签。
- **`show_qrcode` 参数对齐 Bash**:`scripts/sb_xray/display.py:show_qrcode` 补齐 `-v 10 -d 300 -k 2` + `-f 0 -b 255`,与 show-config.sh L111 字节一致(死代码补齐,Bash 本身也未调用该函数)。

### Changed(变更)

- `scripts/entrypoint.py:bootstrap()` 把 `ENV_FILE` 路径写回 `os.environ`;`main()` 启动期 source STATUS_FILE + SECRET_FILE(对齐 Bash `main_init` L1351-1352)。
- `scripts/test_smoke.sh` 3 处 grep 目标从 `show-config.sh` 迁移到 Python 模块:M1-7 `tls_ping_diagnose` → `scripts/sb_xray/display.py`;M2-Adv-Retired 和 M4-订阅 XHTTP-H3 → `scripts/sb_xray/subscription.py`。smoke 基线 54 → 52(对应移除 adv/show-config.sh 目标的 grep;0 失败)。
- `scripts/entrypoint.sh` 3 处注释里的 `show-config.sh` 改为 `show 子命令`。
- `readme.md` / `docs/02` / `docs/03` / `docs/04` 用户文档 `docker exec sb-xray /scripts/show-config.sh` 全部改为 `docker exec sb-xray show`;相应 mermaid 子图标签、IS_8K_SMOOTH/协议命名注释同步更新。

### Removed(移除)

- 删除 `scripts/show-config.sh`(267 行 Bash)。
- 删除 `scripts/stop-supervisor.sh`(19 行 Bash,死代码)。
- 删除 `templates/supervisord/supervisord.conf` 的 `[eventlistener:exit]` 注释块(6 行,引用被删脚本)。

### Tests(测试)

- pytest 从 183 条扩到 **240 条**(新增覆盖 providers 10 条、config_builder 14 条、node_meta 7 条、show 子命令 pipeline 2 条、cert env/purge/quit 3 条、entrypoint.py --python-stage cert/media/providers/config 4 条、STATUS_FILE 加载回归 1 条、QR 参数 - skip、show-config STATUS_FILE 回归 1 条等)。
- `SKIP_COMPOSE=1 bash scripts/test_smoke.sh` → 52/0。

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

- **新特性使用指南**：[`docs/features/0421-new-features-guide.md`](./docs/features/0421-new-features-guide.md) —— 按特性列出"做什么 / 何时用 / 怎么开 / 如何验证 / 故障排查"。
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

[Unreleased]: https://github.com/currycan/sb-xray/compare/v26.4.22...HEAD
[26.4.22]: https://github.com/currycan/sb-xray/compare/v26.3.27...v26.4.22
[26.3.27]: https://github.com/currycan/sb-xray/compare/v26.4.17...v26.3.27
[26.4.17]: https://github.com/currycan/sb-xray/compare/v26.4.14...v26.4.17
