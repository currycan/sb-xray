# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## 项目概览

SB-Xray 是一个 Docker 容器化的代理聚合网关。容器以 `scripts/entrypoint.py`（Python，PID 1 守护进程）为核心：它在启动时渲染 Nginx / Xray / Sing-box / supervisord 配置、签发证书、实测 ISP 落地节点、注册 cron，最后 `exec supervisord` 拉起所有进程。镜像本身由四阶段 `Dockerfile` 把多个上游二进制（Xray、Sing-box、Mihomo、Sub-Store、Dufs、Cloudflared 等）装进一个最终 Nginx 基镜像。

业务逻辑（值得改动的代码）几乎全部是 `scripts/sb_xray/` 下的 Python 包，配置则是 `templates/` 下的 Jinja/占位符模板。运行时行为由 `docker-compose.yml` 的环境变量驱动，文档以编号 `docs/00–11` 为权威。

## 常用命令

开发环境没有预置 venv，dev 依赖需先装：`python3 -m pip install -e ".[dev]"`（项目用 `pyproject.toml`，命令一律 `python3`）。

```bash
# 测试（pytest，testpaths=tests，pythonpath=scripts，asyncio_mode=auto）
python3 -m pytest                                  # 全量（~48 个测试文件）
python3 -m pytest tests/test_config_builder.py     # 单文件
python3 -m pytest tests/test_isp_retest.py -k retest  # 单用例（-k 过滤）
python3 -m pytest --cov                            # 覆盖率（fail_under=85）

# 静态检查
ruff check scripts/sb_xray tests                   # lint（line-length 100，E/F/W/I/B/UP/SIM/RUF）
mypy                                               # 类型检查（strict，仅 scripts/sb_xray）

# 冒烟测试（容器级持续验证基线，每个 PR 必过）
./scripts/test_smoke.sh                             # 针对当前 compose 部署
SKIP_COMPOSE=1 ./scripts/test_smoke.sh             # 只做离线静态校验（适合 CI）

# 构建镜像
./build.sh                                          # 离线模式（默认）：读 versions.json，不触网
./build.sh refresh                                  # 刷新模式：GitHub API → 写回 versions.json → 构建
./build.sh --local                                  # 单架构 amd64，--load 本地，不 push

# 运行 / 排障
docker compose up -d && docker logs -f sb-xray      # 启动并看生成的账户 / UUID / 订阅 token
docker exec sb-xray bash                            # 进容器
python3 scripts/entrypoint.py show                  # 打印订阅链接横幅 + TLS 诊断
python3 scripts/entrypoint.py run --dry-run         # 跑完流水线但不 exec supervisord
python3 scripts/entrypoint.py run --skip-stage <id> # 跳过某个 stage（可重复）
```

注意 `pyproject.toml` 把 pytest `filterwarnings` 设为 `error`——新代码触发的 warning 会让测试失败。

## 架构

**entrypoint.py — 多 stage 启动流水线。** `run` 子命令按固定顺序执行 `scripts/sb_xray/stages/` 下的各 stage（keys / dhparam / geoip / nginx_auth / panels / supervisord / cron / xray_run 等），每个 stage 幂等且可用 `--skip-stage` 跳过。`entrypoint.py` 还提供一组非 `run` 子命令，多数是 supervisord 进程或 cron 入口：`show`、`trim`、`geo-update`、`isp-retest`、`substore-check`、`shoutrrr-forward`、`xray-run`、`xray-exit-listener`。

**scripts/sb_xray/ — 业务模块。** 关键单元：
- `config_builder.py` — 渲染 xray / sing-box / nginx / supervisord 模板（模板里的 `${...}` 占位符在此填充）。
- `routing/` — `isp.py`（isp-auto balancer + 服务分桶）、`media.py`（流媒体/AI 可达性探针）、`service_spec.py`（服务→probe URL 单一真相源）、`providers.py`。
- `speed_test.py` — ISP 带宽实测 + TTL 冷启动缓存；`cert.py` — acme.sh 封装 + 续签判定；`events.py` / `shoutrrr.py` — 结构化事件总线（stdout JSON + 可选 shoutrrr 推送）。
- `geo.py`、`subscription.py`、`substore_check.py`、`network.py`、`secrets.py`、`node_meta.py` 等承载各自领域逻辑。

**templates/ — 配置即模板。** `xray/`、`sing-box/`、`nginx/`、`supervisord/`、`dufs/`、`client_template/`、`providers/`。xr.json / sb.json 的路由规则不是手写死的，而是由 Python 在运行时按 env 生成拼接。改路由行为应改生成代码，不是改最终产物。

**versions.json + Dockerfile。** `versions.json` 是所有组件版本 + 二进制 SHA256 的单一真相源，由 `daily-build.yml` CI 每日刷新提交；离线构建与 CI 产物位级一致。Dockerfile 是多阶段（sub-store-builder / golang builder / 最终 nginx 基镜像）。

**文档权威在 docs/。** 编号 `00–11` 是正式项目文档（house style 见 project-docs 规范）：00 构建发布、01 架构与流量、02 协议与安全、03 路由与客户端、04 运维与排障（含 env 全集 / `CN_EXIT_MODE` 回国）、05 Reverse Proxy、06 事件总线、07 Tailscale、08 Xray Reverse Bridge、09 特性开关、10 多 WAN 防泄漏、11 OpenWrt 重建与割接。改了行为要同步对应文档（见下方 §5）。

`sources/` 含可独立部署的运维脚本（`vps/`、`openwrt/`、`openclash/` 等），各自带 README。

---

## 项目纪律（committed contract — 必须遵守）

### 1. Verified, Not Imagined

**Everything you state must be grounded in evidence you actually checked — never in assumption.** 区分观察与推断，只断言验证过的。陈述为真前先确认：跑命令、读文件、查源。无法验证就标注为未验证，而非断言。新证据与旧结论冲突时，在同一轮显式撤回。判据：每条断言都能指出你检查了什么来支撑？不能就去验证，或者别断言。

### 2. Watchtower 自动更新发布纪律（漂移缓解契约）

生产节点经 watchtower 自动跟进已发布镜像的 `:latest`。watchtower **不读 docker-compose.yml**——它从现有容器 inspect 出已实例化的 env 重建新镜像。因此在运维 `git pull` 同步 compose 之前，新发布引入的 compose env 不会生效。两条硬约束：

- **(a) 新增 env 必须镜像内默认兜底。** 凡新增 `docker-compose.yml` 的 env，必须在 `scripts/entrypoint.py` / `scripts/sb_xray` 内有对应 `os.environ.get(key, 合理默认)`，且默认值向后兼容。保证 watchtower 用旧 env 集重建新镜像时不崩。
- **(b) 修复必须镜像内默认生效。** 任何修复/安全类变更必须落在镜像内默认行为里，不得以「运维设新 compose env」为前提。若某发布确实必须靠新 env 才能正确运行，在发布说明标记 `requires-compose-sync`——该发布不走 watchtower 自动分发，强制全量 `git pull && docker compose up -d`。

### 3. superpowers 开发过程文件归置

所有 superpowers 工作流产物（设计 spec、实现 plan、brainstorm、会话 summary/handoff）一律放项目根 `.superpowers/` 下按类型归子目录（`specs/`、`plans/`、`brainstorm/`）。**不要放进 `docs/`**（那里只存编号正式文档）。`.superpowers/` 已在 `.gitignore`，**不入库**。入库文件（如 `docker-compose.yml`、`sources/vps/*`）若需引用 superpowers 设计文档，注意该路径在 clone 出的仓库中不存在——仅作本地开发参考。

### 4. 不写入环境特定信息

入库文档只描述**项目级、可移植的约束与约定**，不写部署环境或个人/组织特定信息——节点数量、主机名/域名、IP、账号、凭据、服务商、规模数字一律不得出现。这类信息留在 `.credentials/`、私有运维手册或不入库的部署清单中。约束以**机制与不变量**表述（写「生产节点经 watchtower 自动跟进 `:latest`」），而非以**当前部署现状**表述（不写「N 台 VPS……」）。

### 5. PR 提交前文档记录纪律

**改了东西就要留下文字记录。** 每个 PR 提交前同步：架构/设计决策（为什么这么改、权衡了什么）、代码行为变化（对外可见行为/契约/默认值变化）、热更新/bug 修复（根因、修复落点、是否镜像内默认生效见 §2）、运维处理过程（部署/回滚/脚本获取安装步骤）。落点分流：面向用户/运维能力→编号 `docs/`；随脚本走的操作说明→该脚本同目录 README；开发过程产物→`.superpowers/`（不入库）。判据：这个 PR 合并后，没参与的人能否只读入库文档就明白「系统现在做什么、怎么部署/更新、为什么这么设计」？不能就补文档再提。

---

## 凭据与文档查找

- **生产运维 / 登录 VPS 或 OpenWrt / 滚动更新 / 配置生成同步 / 需要任何凭据 secret token SSH 节点列表 域名** → 用 `project-operator` skill。它管凭据查找(`.credentials/`)、四档安全边界、操作留痕(`.ops/`)、code→CI→canary→worker 验证。**任何生产操作都要留痕**,不要问用户或猜凭据。
- **写/改 `docs/` 下任何文件** → 先读 `project-docs` skill，匹配本仓 house style（编号标题、图标 legend、Mermaid 调色板、五段式特性布局、no-dev-phase-language）。
