# 05. 构建部署与版本发布指南

> 本文档详细解析 SB-Xray Docker 镜像的完整构建流程，包括环境准备、自动化构建脚本、四阶段 Dockerfile 架构、常见构建问题，以及 Git Release 自动化版本发布机制。

---

## 目录

1. [构建环境准备](#1-构建环境准备)
2. [自动构建脚本](#2-自动构建脚本buildsh)
3. [四阶段 Dockerfile 架构](#3-四阶段-dockerfile-架构)
4. [组件版本管理](#4-组件版本管理)
5. [手动精细构建](#5-手动精细构建)
6. [常见构建问题 FAQ](#6-常见构建问题-faq)
7. [Git Release 版本发布](#7-git-release-版本发布releasesh)

---

## 1. 构建环境准备

### 1.1 必备工具

| 工具 | 最低版本 | 用途 |
|:---|:---|:---|
| **Docker** | 24.0+ | 容器运行时 |
| **Docker Buildx** | 0.11+ | 多架构构建 |
| **jq** | 1.6+ | 解析 GitHub API 返回的 JSON |
| **curl** | 7.68+ | 调用 GitHub API 获取版本 |
| **Git** | 2.30+ | 源码管理 |

### 1.2 多架构构建器配置

```bash
# 检查是否存在 buildx builder
docker buildx ls

# 如不存在，创建一个支持多架构的 builder
docker buildx create --name multiarch --use --bootstrap

# 验证支持的架构
docker buildx inspect --bootstrap
```

### 1.3 GitHub API Token（可选，推荐）

GitHub API 对匿名请求有严格限流（60 次/小时）。推荐设置 Token：

```bash
export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
```

---

## 2. 自动构建脚本（build.sh）

`build.sh` 是推荐的构建入口。**单一真相源是仓库根目录的 `versions.json`**，`build.sh` 既不硬编码任何组件版本，也不在本地构建期间与 CI 产生漂移：

- 日常构建直接读 `versions.json`（已由 CI 每天刷新并提交），纯离线、可复现
- 需要跟最新上游版本时用 `./build.sh refresh`，会像 CI 一样调用 GitHub API 拉取 versions + digests，写回 `versions.json`，然后构建

### 2.1 基本用法

一条命令完成构建并同时推送 `:版本号` 和 `:latest` 两个 tag，两者始终指向同一 Image ID。

```bash
./build.sh              # 离线模式（默认）：读 versions.json 构建，不触网
./build.sh refresh      # 刷新模式：GitHub API 拉最新 → 写回 versions.json → 构建
./build.sh --local      # 单架构 linux/amd64，--load 到本地，不 push；可与上面两种组合
```

`./build.sh default` / `./build.sh offline` 作为旧名称保留为离线模式别名。

### 2.2 工作流程

```mermaid
flowchart TD
    classDef entry    fill:#0984e3,stroke:#0566b3,stroke-width:2px,color:#fff
    classDef process  fill:#00b894,stroke:#009577,stroke-width:2px,color:#fff
    classDef decision fill:#fdcb6e,stroke:#e0a33e,stroke-width:2px,color:#333
    classDef data     fill:#a29bfe,stroke:#6c5ce7,stroke-width:2px,color:#fff
    classDef terminal fill:#2d3436,stroke:#636e72,stroke-width:3px,color:#fff

    Start(["./build.sh [refresh] [--local]"]):::entry --> Mode{"模式?"}:::decision

    Mode -- "offline (默认)" --> Read["读 versions.json<br/>versions + digests"]:::data
    Mode -- "refresh" --> API["GitHub API<br/>拉 versions + digests"]:::process
    API --> Write["写回 versions.json"]:::data
    Write --> Read

    Read --> Validate["校验每个字段<br/>(semver + 非空)"]:::process
    Validate --> Build["docker buildx build<br/>--tag :VERSION<br/>--tag :latest"]:::process
    Build --> Done(["构建完成"]):::terminal
```

### 2.3 版本获取策略（仅 refresh 模式用到）

| 策略 | API 端点 | 适用组件 |
|:---|:---|:---|
| **Latest Release** | `/repos/{owner}/{repo}/releases/latest` | Shoutrrr、Mihomo、Http-Meta、Sub-Store、S-UI |
| **Latest Stable Tag** | `/repos/{owner}/{repo}/tags?per_page=100`（过滤 `rc/beta/alpha`） | Xray、Sing-box、3x-ui、Dufs、Cloudflared |

`.github/workflows/daily-build.yml` 每日跑相同逻辑：调用 API → 计算 digests → 提交 `versions.json`。所以本地 `./build.sh` 和 CI 的最新产物始终位级一致。

---

## 3. 四阶段 Dockerfile 架构

整个构建过程分为四个精心设计的阶段（Multi-Stage Build），最大程度地减小最终镜像体积。

```mermaid
flowchart TD
    classDef stage1 fill:#74b9ff,stroke:#0984e3,stroke-width:2px,color:white
    classDef stage2 fill:#fab1a0,stroke:#e17055,stroke-width:2px,color:white
    classDef stage3 fill:#a29bfe,stroke:#6c5ce7,stroke-width:2px,color:white
    classDef stage4 fill:#55efc4,stroke:#00b894,stroke-width:2px,color:black

    S1["阶段一: Sub-Store 构建层\nnode:alpine"]:::stage1
    S2["阶段二: S-UI 前端构建层\nnode:alpine"]:::stage2
    S3["阶段三: Golang 主构建层\ngolang:1-alpine"]:::stage3
    S4["阶段四: 最终镜像层\ncurrycan/nginx:1.29.4"]:::stage4

    S1 -->|"Sub-Store 前后端\n+ Shoutrrr\n+ Http-Meta\n+ Mihomo"| S4
    S2 -->|"S-UI 前端静态资源"| S3
    S3 -->|"x-ui 二进制\n+ s-ui 二进制\n+ sing-box\n+ xray\n+ dufs\n+ cloudflared\n+ crypctl"| S4

    S4 --> Final(("最终镜像\ncurrycan/sb-xray:latest"))
```

### 3.1 阶段一：Sub-Store 构建层

**基础镜像**: `node:alpine`

**构建产物**:

| 组件 | 来源 | 构建方式 |
|:---|:---|:---|
| **Shoutrrr** | containrrr/shoutrrr | 预编译二进制下载 |
| **Http-Meta** | xream/http-meta | 预编译 JS Bundle 下载 |
| **Mihomo** | MetaCubeX/mihomo | 预编译二进制下载 |
| **Sub-Store 后端** | sub-store-org/Sub-Store | 预编译 JS Bundle 下载 |
| **Sub-Store 前端** | sub-store-org/Sub-Store-Front-End | **从源码构建** (pnpm build) |

### 3.2 阶段二：S-UI 前端构建层

**基础镜像**: `node:alpine`

**构建过程**:
1. 克隆 S-UI 后端仓库（含 Go 源码）
2. 克隆 S-UI-Frontend 仓库
3. `npm install && npm run build`
4. 将编译后的 `dist/` 移入后端的 `web/html` 目录

### 3.3 阶段三：Golang 主构建层

**基础镜像**: `golang:1-alpine`

**环境特性**: `CGO_ENABLED=1`（启用 CGO 以支持 SQLite）

**构建产物**:

| 组件 | 构建方式 | 压缩 |
|:---|:---|:---|
| **crypctl** | `go build` + UPX | ✅ |
| **Dufs** | 预编译下载 + UPX | ✅ |
| **Cloudflared** | 预编译下载 + UPX | ✅ |
| **X-UI (3x-ui)** | 从源码 `go build` + UPX | ✅ |
| **S-UI** | 从源码 `go build` (含 QUIC/gRPC/ACME tags) + UPX | ✅ |
| **Sing-box** | 预编译下载 + UPX | ✅ |
| **Xray** | 预编译 ZIP 下载 + UPX | ✅ |

> **UPX 压缩**：所有二进制文件使用 `upx --lzma --best` 极限压缩，减小 50-70% 体积。

### 3.4 阶段四：最终镜像层

**基础镜像**: `currycan/nginx:1.29.4`（基于 Alpine 的自定义 Nginx）

**运行时安装**:

```
curl bash iproute2 net-tools tzdata ca-certificates python3 pip
gettext libc6-compat vim libqrencode-tools jq sqlite nodejs
supervisor dumb-init fail2ban acme.sh
```

**关键配置**:

| 配置项 | 值 | 说明 |
|:---|:---|:---|
| `ENTRYPOINT` | `dumb-init -- python3 /scripts/entrypoint.py run` | dumb-init 作为 PID 1，Python `entrypoint.py` 提供 `run` / `show` / `trim` 三个子命令；`run` 用纯 Python 一次性跑完 15 段初始化流水线（探测 → 选路 → 证书 → 模板渲染 → 面板初始化 → cron → `os.execvp` supervisord） |
| `CMD` | `supervisord` | Supervisor 管理所有子进程 |
| `HEALTHCHECK` | `supervisorctl status xray` | 每 30 秒检查 Xray 存活 |
| `EXPOSE` | `80 443` | 默认暴露端口 |
| `TZ` | `Asia/Singapore` | 时区 |

---

## 4. 组件版本管理

### 4.1 当前组件版本

所有版本由仓库根目录 `versions.json` 统一声明，CI 每日从 GitHub API 刷新并提交。`build.sh` 离线模式直接读取该文件，因此**本地构建与 CI 产物始终一致**。查阅当前组件版本：

```bash
jq -r 'to_entries[] | select(.key != "digests") | "\(.key): \(.value)"' versions.json
```

对应的 Docker build-arg 名称：

| 组件（`versions.json` 字段） | Build Arg |
|:---|:---|
| `shoutrrr` | `SHOUTRRR_VERSION` |
| `mihomo` | `MIHOMO_VERSION` |
| `http_meta` | `HTTP_META_VERSION` |
| `sub_store_frontend` | `SUB_STORE_FRONTEND_VERSION` |
| `sub_store_backend` | `SUB_STORE_BACKEND_VERSION` |
| `s_ui` | `SUI_VERSION` |
| `dufs` | `DUFS_VERSION` |
| `cloudflared` | `CLOUDFLARED_VERSION` |
| `x_ui` | `XUI_VERSION` |
| `sing_box` | `SING_BOX_VERSION` |
| `xray` | `XRAY_VERSION` |

配套的二进制 SHA256 校验值存于 `versions.json` 的 `digests` 字段（由 CI 同步刷新），Dockerfile 通过 build-arg 传入并在下载阶段强制校验。任何 digest 缺失都会导致 `build.sh` 直接退出。

### 4.2 版本覆盖

可通过环境变量强制指定版本：

```bash
# 指定特定 Xray 版本
XRAY_VERSION=25.12.15 docker buildx build \
  --build-arg XRAY_VERSION=25.12.15 \
  --tag currycan/sb-xray:custom \
  .
```

---

## 5. 手动精细构建

### 5.1 仅构建单架构（本地测试）

```bash
# 仅构建 amd64（不推送）
docker buildx build \
  --platform linux/amd64 \
  --build-arg XRAY_VERSION=26.2.6 \
  --build-arg SING_BOX_VERSION=1.12.21 \
  --tag currycan/sb-xray:test \
  --load .
```

### 5.2 多架构构建并推送

推荐直接使用 `build.sh`（自动获取版本 + 同时推送两个 tag）：

```bash
./build.sh
```

如需手动指定版本构建：

```bash
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --build-arg XRAY_VERSION=26.2.6 \
  --build-arg SING_BOX_VERSION=1.13.1 \
  --tag currycan/sb-xray:26.2.6 \
  --tag currycan/sb-xray:latest \
  --push .
```

### 5.3 构建缓存优化

```bash
# 使用 registry 缓存加速重复构建
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --cache-from type=registry,ref=currycan/sb-xray:cache \
  --cache-to type=registry,ref=currycan/sb-xray:cache,mode=max \
  --push .
```

---

## 6. 常见构建问题 FAQ

### Q1: GitHub API 返回 403 / Rate Limit

**原因**: 未配置 GitHub Token 导致匿名限流（60 次/小时）。注意只有 `./build.sh refresh` 会调 GitHub API —— 默认 `./build.sh` 完全离线。

**解决**:
```bash
# 离线构建（直接用仓库 versions.json，无需 API）
./build.sh

# 若确实需要刷新到最新上游版本
export GITHUB_TOKEN=<your-personal-access-token>
./build.sh refresh
```

### Q2: Sub-Store 前端构建失败

**现象**: `pnpm install` 或 `pnpm run build` 失败

**常见原因**:
* 网络问题导致 npm 包下载失败
* Node.js 版本不兼容

**解决**: 重试或检查 Sub-Store 前端仓库的 `engines` 字段要求

### Q3: UPX 压缩失败

**现象**: `upx: CantPackException`

**常见原因**: 某些二进制已被压缩或具有特殊段

**解决**: 跳过该文件的 UPX 或降级 UPX 版本

### Q4: ARM64 构建报错

**现象**: 在 x86 机器上构建 ARM64 失败

**解决**:
```bash
# 安装 QEMU 用户态模拟
docker run --rm --privileged multiarch/qemu-user-static --reset -p yes
```

### Q5: 如何查看镜像中所有组件版本？

```bash
docker run --rm currycan/sb-xray:latest bash -c "
  echo '=== Xray ===' && xray version
  echo '=== Sing-box ===' && sing-box version
  echo '=== Nginx ===' && nginx -v
  echo '=== Node ===' && node --version
  echo '=== Python ===' && python3 --version
"
```

### Q6: 版本 tag 和 `:latest` 的 Image ID 不一致？

**原因**: 在 `build.sh` 之外又单独执行了一次 `docker buildx build`，导致其中一个 tag 被新构建覆盖。

**解决**: 只用 `./build.sh` 构建，它在同一次 `docker buildx build` 中同时传入 `--tag :VERSION --tag :latest`，两个 tag 天然指向同一 Image ID。

### Q7: 构建后镜像体积过大？

**预期体积**: 约 300-400 MB (compressed)

**优化要点**:
1. 确认 UPX 压缩正常执行
2. 确认使用 `--no-cache` 的 `apk add`
3. 多阶段构建已自动丢弃中间层

---

## 7. Git Release 版本发布（release.sh）

`release.sh` 负责将项目的 Git Release 版本号与 Docker 镜像版本（即 Xray 版本号）保持**自动同步**。

### 7.1 版本同步策略

本项目的版本号直接对齐 **Xray-core 最新 Tag**，确保三者一致：

| 标识 | 格式 | 示例 |
|:---|:---|:---|
| Xray-core Tag | `vX.Y.Z` | `v26.2.6` |
| Docker 镜像 Tag | `X.Y.Z` (无 `v` 前缀) | `26.2.6` |
| Git Release Tag | `vX.Y.Z` | `v26.2.6` |

### 7.2 基本用法

```bash
# 自动获取最新 Xray 版本，创建对应 Git Tag 和 GitHub Release
./release.sh

# 推荐配置 GitHub Token 以避免 API 限流
export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
./release.sh
```

### 7.3 工作流程

```mermaid
flowchart TD
    Start(["./release.sh"]) --> Fetch["调用 GitHub API<br/>获取 Xray 最新 Tag"]
    Fetch --> Valid{"版本获取成功?"}
    Valid -- 否 --> Abort(["❌ 取消发布"])
    Valid -- 是 --> CalcTag["计算版本号<br/>v26.2.6 → Git Tag: v26.2.6<br/>Docker Tag: 26.2.6"]

    CalcTag --> LocalTag{"本地 Tag<br/>已存在?"}
    LocalTag -- 是 --> SkipTag["跳过本地 Tag 创建"]
    LocalTag -- 否 --> CreateTag["git tag -a vX.Y.Z"]

    SkipTag --> GhCli
    CreateTag --> GhCli{"gh CLI<br/>可用?"}

    GhCli -- 是 --> RemoteCheck{"远端 Release<br/>已存在?"}
    RemoteCheck -- 是 --> Done(["✅ 无需操作"])
    RemoteCheck -- 否 --> Push1["git push origin tag"] --> CreateRelease["gh release create"]
    CreateRelease --> Done2(["✅ Release 创建成功"])

    GhCli -- 否 --> Push2["git push origin tag"] --> Manual(["⚠️ 请手动创建 Release"])

    style Abort fill:#d63031,stroke:#b71c1c,color:#fff
    style Done fill:#55efc4,stroke:#00b894,color:#333
    style Done2 fill:#00b894,stroke:#009577,color:#fff
    style Manual fill:#fdcb6e,stroke:#e0a33e,color:#333
```

### 7.4 详细流程说明

| 步骤 | 动作 | 说明 |
|:---:|:---|:---|
| 1 | 获取 Xray 最新版本 | 通过 GitHub API `/repos/XTLS/Xray-core/tags` 获取最新 Tag |
| 2 | 计算 Release Tag | 去除/添加 `v` 前缀以匹配 Docker 与 Git 两种命名规范 |
| 3 | 创建本地 Git Tag | 使用 `git tag -a` 创建附注标签（annotated tag） |
| 4a | 推送 + 创建 Release | 若检测到 `gh` CLI → 自动推送标签并创建 GitHub Release |
| 4b | 仅推送标签 | 若无 `gh` CLI → 推送标签后提示用户手动创建 Release |

### 7.5 幂等性保障

`release.sh` 设计为**可重复执行**，不会产生副作用：

* **本地 Tag 已存在** → 跳过创建，不报错
* **远端 Release 已存在** → 跳过创建，不报错
* **获取版本失败** → 立即中止，不执行任何 Git 操作

### 7.6 与 build.sh 的关系

```mermaid
flowchart LR
    API(("GitHub API<br/>Xray 版本")) --> Build
    API --> Release

    Build["build.sh"] -- ":VERSION + :latest\n同一 Image ID" --> Registry["Docker Hub"]
    Release["release.sh<br/>发布 Git Release"] -- "Git Tag: v26.2.6" --> GitHub["GitHub Releases"]

    style Build fill:#00b894,stroke:#009577,color:#fff
    style Release fill:#a29bfe,stroke:#6c5ce7,color:#fff
    style API fill:#fdcb6e,stroke:#e0a33e,color:#333
```

推荐执行顺序：`./build.sh` → `./release.sh`。两个脚本共享同一版本源，确保 Docker 镜像版本与 Git Release 版本始终一致。
