# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

版本命名：`MAJOR.MINOR.PATCH`，其中 MAJOR/MINOR 与底层 Xray-core 版本对齐；PATCH 为本项目发布迭代号。

---

## [Unreleased]

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

[Unreleased]: https://github.com/currycan/sb-xray/compare/v26.3.27...HEAD
[26.3.27]: https://github.com/currycan/sb-xray/compare/v26.4.17...v26.3.27
[26.4.17]: https://github.com/currycan/sb-xray/compare/v26.4.14...v26.4.17
