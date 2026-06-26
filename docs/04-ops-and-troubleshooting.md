# 04. 运维管理与故障排查手册

> 覆盖系统管理面板导航、环境变量完整参考、订阅端点安全防护、证书运维、GeoIP 数据更新与常见故障排查。

---

## 目录

1. [系统控制面板导航](#1-系统控制面板导航)
2. [环境变量完整参考](#2-环境变量完整参考)
3. [订阅端点安全体系](#3-订阅端点安全体系)
4. [证书管理运维](#4-证书管理运维)
5. [GeoIP/GeoSite 数据更新](#5-geoipgeosite-数据更新)
6. [故障排查实战手册](#6-故障排查实战手册)
7. [小内存节点部署指引（内存不超过 512 MB）](#7-小内存节点部署指引内存不超过-512-mb)

---

## 1. 系统控制面板导航

SB-Xray 集成了多个可视化管理面板，全部通过 CDN 域名的子路径访问。

### 1.1 控制面板一览

| 面板 | 入口路径 | 功能 | 默认凭据来源 |
|:---|:---|:---|:---|
| **X-UI (3x-ui)** | `https://${CDNDOMAIN}/${XUI_WEBBASEPATH}` | Xray 协议与用户管理（开箱默认不启用，需用时见 §7.2 将 `ENABLE_XUI` 设为 `true`） | `/.env/secret` 中 `PUBLIC_USER/PASSWORD` |
| **S-UI** | `https://${CDNDOMAIN}/${SUI_WEBBASEPATH}` | Sing-box 入站与出站监控（已不再内置，仅作历史参考） | 同上 |
| **Sub-Store** | `https://${CDNDOMAIN}/sub-store` | 订阅源管理与节点清洗 | 无需认证 |
| **Dufs** | `https://${CDNDOMAIN}/${DUFS_PATH_PREFIX}` | 文件上传/下载网盘 | HTTP Basic 认证（同上凭据） |
| **Yacd/Zashboard** | `https://${CDNDOMAIN}:9090/ui` | 实时流量与策略组监控 | mihomo external-controller secret（见下方注） |

> **安全提醒**：所有面板路径均通过 Nginx 的 `location` 指令保护，建议首次登录后立即修改默认 WebBasePath。

> 📘 **Yacd/Zashboard 的 `Secret` 说明**：客户端配置（`templates/client_template/*.yaml`）里的 `secret: yyds666` 是 **mihomo（clash 内核）`external-controller` 的 RESTful API secret**，Yacd/Zashboard 面板用它去连接客户端本地（`0.0.0.0:9090`）的 mihomo 控制 API。它**不是服务端 SB-Xray 面板的登录凭据**，也与 `/.env/secret` 中的 `PUBLIC_USER/PASSWORD` 无关——这是面板↔本地内核之间的认证，运行在用户客户端侧。

### 1.2 环境变量配置示例

```yaml
environment:
  # X-UI
  - XUI_WEBBASEPATH=3xadmin      # 自定义面板路径

  # S-UI（已不再内置，以下变量保留仅作历史参考）
  - SUI_WEBBASEPATH=sui           # 自定义面板路径

  # Dufs 文件服务
  - DUFS_PATH_PREFIX=/myfiles     # 文件网盘的 URL 前缀
  - DUFS_SERVE_PATH=/data         # 文件存储路径
```

### 1.3 挂载卷与持久化目录

🔧 `docker-compose.yml` 把宿主机目录挂进容器，用于持久化证书、订阅文件、面板数据库与规则缓存。容器重建（`docker compose up -d` 拉新镜像）不丢这些数据。

| 挂载路径          | 容器路径                  | 用途                                  |
| :---------------- | :------------------------ | :------------------------------------ |
| `./pki`           | `/pki`                    | TLS 证书存储                          |
| `./acmecerts`     | `/acmecerts`              | ACME 账户与中间证书                   |
| `./.envs`         | `/.env`                   | 运行时环境变量缓存（含 UUID、密钥等） |
| `./sb-xray`       | `/sb-xray`                | 生成的订阅文件与客户端配置            |
| `./data`          | `/data`                   | Dufs 文件服务的数据存储               |
| `./x-ui`          | `/etc/x-ui/` + `/x-ui/db` | X-UI 数据库（持久化面板数据）         |
| `./s-ui`          | `/s-ui/db`                | S-UI 数据库（s-ui 不再内置，卷保留仅兼容历史部署） |
| `./sub-store`     | `/sub-store/data`         | Sub-Store 数据库                      |
| `./nginx/http`    | `/etc/nginx/conf.d`       | 自定义 Nginx HTTP 配置                |
| `./nginx/tcp`     | `/etc/nginx/stream.d`     | 自定义 Nginx Stream 配置              |
| `./nginx-dhparam` | `/etc/nginx/dhparam`      | DH 密钥参数（首次生成后缓存，见 §4.3） |
| `./geo`           | `/geo`                    | GeoIP / GeoSite 规则缓存（避免重启重下 ~100 MB，见 §5.4） |
| `./logs`          | `/var/log`                | 所有日志文件                          |

> 📘 `./geo:/geo` 卷持久化 GeoIP / GeoSite 规则库，避免每次重启重下 6 个 ~10–30 MB 文件；持久化卷要求详见 §5.4。

### 1.4 常用运维命令速查

🔧 容器名固定为 `sb-xray`。下列命令覆盖日常启停、查看配置、按核重启与进容器排障。

```bash
# 一键启动 / 拉取最新镜像后重建
docker compose up -d

# 查看 entrypoint 启动日志（测速、选路、证书、配置渲染全流程）
docker logs -f sb-xray

# 查看生成的配置、节点 UUID 与订阅链接
docker exec sb-xray show

# 重启所有服务
docker compose restart

# 仅重启某个核心（不重建容器）
docker exec sb-xray supervisorctl restart xray
docker exec sb-xray supervisorctl restart sing-box

# 查看进程状态
docker exec sb-xray supervisorctl status

# 进入容器终端排障
docker exec -it sb-xray bash
```

> 📘 更细的分场景命令（证书续期见 §4、GeoIP 更新见 §5、订阅端点监控见 §3.5、故障排查见 §6）在各对应章节就地给出。

---

## 2. 环境变量完整参考

### 2.1 优先级模型

容器启动时，变量按以下顺序加载，**序号越小优先级越高**（后加载的覆盖先设的值）：

| 优先级 | 来源 | 加载时机 | 典型内容 |
|:---|:---|:---|:---|
| **1（最高）** | `/.env/status` | `main_init` 步骤 3，最后 source | 流媒体/AI 检测结果（`*_OUT` 变量）；当 ISP_TAG 需要重新评估时，所有 `*_OUT` 行会被联动清除后重新写入 |
| **2** | `/.env/sb-xray` | `main_init` 步骤 3 | UUID、端口、密钥、`ISP_TAG` 等 |
| **3** | `/.env/secret` | `main_init` 步骤 2 | 面板凭据、ISP 节点凭据 |
| **4** | `docker-compose environment` | 容器启动时注入 | 用户显式配置 |
| **5（最低）** | `Dockerfile ENV` | 镜像构建时烘焙 | 安全默认值 |

> **实践说明**：各层通常包含不同的变量，实际覆盖冲突极少。`ensure_var` 的三分支逻辑确保自动生成的变量首次计算后即永久缓存，不会被重复生成；唯一例外是探测类变量 `GEOIP_INFO`——其持久化值为空（探测曾失败）时下次启动会重新探测，避免空值被永久缓存后压制后续探测（见 §2.4）。

### 2.2 用户配置变量（在 docker-compose 设置）

#### 必填（无默认值，缺少则容器退出）

| 变量 | 说明 |
|:---|:---|
| `DOMAIN` | 主域名（Reality TLS 伪装目标所在域） |
| `CDNDOMAIN` | CDN 域名（Cloudflare 代理，Nginx Web 层监听此 SNI） |
| `DECODE` | 远端密钥库解密密钥（由 `crypctl` 使用） |

#### 核心可选

> 📘 **列头说明**：下表及 ACME / X-UI / S-UI 面板各节使用「Dockerfile 默认」列——这些值通过 `Dockerfile ENV` 指令固化在镜像内，无论 compose 如何设置均作为兜底。`#### Supervisord 控制凭据` 及 `#### Dufs 文件服务` 等后续节改用「镜像内默认」——这些值的兜底由 Python 入口的 `os.environ.get(key, 默认值)` 提供，而非 `Dockerfile ENV`；行为等价，来源不同。

| 变量 | Dockerfile 默认 | 说明 |
|:---|:---|:---|
| `LISTENING_PORT` | `443` | Nginx 主监听端口 |
| `DEST_HOST` | `www.microsoft.com` | Reality SNI 伪装目标（建议改为 `speed.cloudflare.com`） |
| `DEFAULT_ISP` | `LA_ISP` | ISP 出口模式：非空=锁定到指定前缀出口（跳过测速）；**显式置空=启用测速自动选路**。Dockerfile 默认 `LA_ISP`，不覆盖则永远锁定 LA 出口 |
| `GEMINI_DIRECT` | `""` | Gemini 路由：`true`=强制直连，`false`=代理，空=自动判断 |
| `NODE_SUFFIX` | `""` | 订阅节点名称后缀（如 ` ✈ 高速`） |
| `PROVIDERS` | `""` | 外部订阅源，多行格式 |
| `TZ` | `Asia/Singapore` | 容器时区 |

#### ACME 证书

| 变量 | Dockerfile 默认 | 说明 |
|:---|:---|:---|
| `ACMESH_SERVER_NAME` | `letsencrypt` | ACME CA：`letsencrypt`（默认，无需 EAB，通配符即开即用）/ `zerossl` / `google`（需 EAB）。CA 切换行为见 [02. 协议与安全](./02-protocols-and-security.md) §5 |
| `ACMESH_REGISTER_EMAIL` | `""` | ACME 注册邮箱 |
| `ACMESH_DEBUG` | `2` | ACME 调试级别（生产可改为 `1`） |
| `ACMESH_EAB_KID` | — | Google CA 专用 EAB Key ID |
| `ACMESH_EAB_HMAC_KEY` | — | Google CA 专用 EAB HMAC Key |
| `SSL_PATH` | `/pki` | 证书存储路径 |

#### X-UI 面板

| 变量 | Dockerfile 默认 | 说明 |
|:---|:---|:---|
| `XUI_WEBBASEPATH` | `xui` | 面板访问路径 |
| `XUI_LOG_LEVEL` | `info` | 日志级别 |
| `XUI_DEBUG` | `false` | 调试模式 |

#### S-UI 面板

> ⚠️ S-UI 已不再内置，当前镜像不含 s-ui 二进制；下表与 `ENABLE_SUI` 仅作历史参考，设置后无任何效果。

| 变量 | Dockerfile 默认 | 说明 |
|:---|:---|:---|
| `SUI_WEBBASEPATH` | `sui` | 面板访问路径 |
| `SUI_SUB_PATH` | `sub` | 订阅子路径 |
| `SUI_PORT` | `3095` | S-UI 内部端口 |
| `SUI_SUB_PORT` | `3096` | S-UI 订阅内部端口 |
| `SUI_LOG_LEVEL` | `info` | 日志级别 |

#### Supervisord 控制凭据

| 变量 | 镜像内默认 | 说明 |
|:---|:---|:---|
| `SUPERVISOR_USER` | `sb-xray` | supervisord XML-RPC 控制接口用户名；**与 `PUBLIC_USER` 独立**，避免面板凭据泄漏同时交出进程控制权 |
| `SUPERVISOR_PASSWORD` | *(派生)* | 未显式设置时，由 `PUBLIC_PASSWORD` 经固定 salt 做 SHA-256 确定性派生（取前 32 位十六进制字符）；**与 PUBLIC_PASSWORD 不同值**。派生逻辑在 `config_builder._resolve_supervisor_credentials`，salt 冻结（`sb-xray-supervisor::`），改 salt 会轮转所有存量部署的派生密码 |

> 🔬 **盐值冻结不变量**：salt 字符串 `sb-xray-supervisor::` 硬编码在 `config_builder.py` 注释中，禁止修改——修改会导致所有未显式设置 `SUPERVISOR_PASSWORD` 的节点在镜像升级后生成不同密码，中断 supervisorctl 远程控制会话。如需强制轮换，显式在 compose 设置 `SUPERVISOR_PASSWORD`。

#### Nginx HTTP Basic Auth 凭据文件

🔬 **`.htpasswd` 文件权限**：启动阶段 `nginx_auth` 将 `/etc/nginx/.htpasswd`（apr1 哈希凭据文件）写入后立即设置权限 `0640`（owner rw / group r / other 无读权限）。Nginx worker 进程通过 group 成员身份读取该文件；容器内其他进程（非同组）无法读取哈希值。排障提示：首次启动出现 `403` 时，先确认 `nginx_auth` stage 已正常完成（容器日志含 `nginx_auth: ok`），再核查文件存在且权限为 `0640`（`docker exec sb-xray stat -c '%a %n' /etc/nginx/.htpasswd` 期望输出 `640 /etc/nginx/.htpasswd`）。

#### Dufs 文件服务

| 变量 | 镜像内默认 | 说明 |
|:---|:---|:---|
| `DUFS_PATH_PREFIX` | `/dufs` | URL 前缀 |
| `DUFS_SERVE_PATH` | `/data` | 文件存储根目录 |
| `DUFS_ALLOW_UPLOAD` | `false` | 允许上传文件；**镜像内默认关闭**（Nginx 内网 ACL 限制访问面，但建议保持默认以最小化写权限） |
| `DUFS_ALLOW_DELETE` | `false` | 允许删除文件；**镜像内默认关闭** |
| `DUFS_ALLOW_SYMLINK` | `false` | 允许跟随符号链接；**镜像内默认关闭**（防止 serve-path 内的 symlink 越界读取宿主文件） |
| `DUFS_ALLOW_ARCHIVE` | `false` | 允许打包下载目录；**镜像内默认关闭** |
| `DUFS_ENABLE_CORS` | `false` | 放开跨域（CORS）；**镜像内默认关闭**（开启后任意网页可发起跨源请求） |

🔬 **深挖：Dufs 配置来源与 `DUFS_*` env 优先级**

Dufs 进程由 supervisord 用 `dufs -c ${WORKDIR}/dufs/conf.yml -a ${PUBLIC_USER}:${PUBLIC_PASSWORD}@/:rw` 启动（`templates/supervisord/daemon.ini`），既加载 `templates/dufs/conf.yml` 配置文件，又（dufs 二进制原生）读取 `DUFS_` 前缀环境变量。`conf.yml` 里若干开关**硬编码为 `true`**，有明确安全含义：

| `conf.yml` 字段 | 值 | 安全含义 |
|:---|:---|:---|
| `allow-all` | `true` | 同时放开上传/删除/搜索/打包等全部操作 |
| `allow-symlink` | `true` | 允许跟随符号链接 —— 若 `serve-path` 内存在指向 `serve-path` 之外的 symlink，访问者可越界读取宿主文件 |
| `enable-cors` | `true` | 放开跨域，任意网页可发起跨源请求访问该文件服务 |
| `render-spa` | `true` | 以单页应用方式渲染目录 |

> ⚠️ **优先级实测结论(生产容器内 dufs 0.46.0,受控 A/B + 控制组 + bind 双向交叉验证):dufs 取 `命令行 > 环境变量 > config 文件 > 内置默认`,`DUFS_` 环境变量严格优先于 `conf.yml`。** 用 `bind` 字段可干净判决:`conf=0.0.0.0 + env DUFS_BIND=127.0.0.1` → 实际监听 `127.0.0.1`;反向 `conf=127.0.0.1 + env=0.0.0.0` → 监听 `0.0.0.0`——两向均 **env 赢、conf 让位**。
>
> **但 `allow-all` 这个布尔 flag 是例外表象**:在 clap 里 `false` 等同「未提供该 flag」,无法与「显式 false」区分,于是 `false` 一方自动让位给 `true` 一方,表现得像「任一为 `true` 即放开」。实测:`env=false + conf=true` 放开、`env=true + conf=false` 放开、`env=false + conf=false`(及均缺省)拒绝(`403` 基线)。**底层仍是 env 优先,只是布尔 false 不算「提供值」**。
>
> 因此镜像 `Dockerfile` 的 `ENV DUFS_ALLOW_ALL="false"` **关不掉 allow-all**——它给的 `false` 被当「未提供」,`conf.yml` 硬编码的 `allow-all: true` 生效,effective 值恒为 `true`。**生产 dufs 的 `allow-all` 实际处于开启状态**,前述「保守假设」由实测坐实为既成事实。
>
> `allow-all` 只管**操作权限**(上传/删除/搜索/打包),不放开**身份认证**:启动行的 `-a ${PUBLIC_USER}:${PUBLIC_PASSWORD}@/:rw` 仍强制 HTTP Basic 认证——实测对运行实例发未认证请求,落在 `DUFS_PATH_PREFIX` 下返回 `401`。真正兜底暴露面的是「随机前缀 + Basic 认证」(见 §1.1),而非 `DUFS_ALLOW_ALL=false`。**运维结论**:① 不要依赖 `DUFS_ALLOW_ALL=false` 收紧权限(它被当「未提供」、被 conf 的 `true` 盖过),要收紧须改 `conf.yml` 的 `allow-all` 并保留 `-a` 认证 + 随机前缀;② 留意 `DUFS_BIND` env 严格压过 `conf.yml bind`——host 网络模式下若 env 设 `0.0.0.0` 会让文件服务监听全网卡(实测如此),暴露面以「随机端口 + 随机前缀 + Basic 认证」三重兜底。
>
> <details><summary>实测方法(可复现)</summary>
>
> 在跑着 sb-xray 容器的节点上,用容器内同一 dufs 二进制 `docker exec` 起隔离实例(绑 `127.0.0.1` 测试端口、唯一探针文件、`env -i` 精确控制变量),实验后清理:
>
> `allow-all`(未认证 `PUT`/`DELETE` 响应码):
> - `env=false + conf=true` → `201 / 204`(放开);`env 未设 + conf=true` → `201`(放开,证 conf 被读)
> - `env=true + conf=false` → `201 / 204`(放开);`env 未设 + conf=false` → `403`(拒绝)
> - `env=false + conf=false`(及均缺省)→ `403 / 403`(拒绝,基线,证 `403` 是真实拒绝而非「总是允许」)
>
> `bind`(实际监听地址):
> - `conf=0.0.0.0 + env=127.0.0.1` → 监听 `127.0.0.1`;`conf=127.0.0.1 + env=0.0.0.0` → 监听 `0.0.0.0`(两向 env 赢)
>
> 判读:`bind` 双向证明 **env 严格优先**;`allow-all` 的「任一 true 即放开」是布尔 `false ≡ 未提供` 的假象,非真正的并集语义。
> </details>

#### Entrypoint 日志（Python stdlib logging）

| 变量 | 默认值 | 说明 |
|:---|:---|:---|
| `SB_LOG_LEVEL` | `INFO` | Python entrypoint 日志级别：`DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL`（大小写不敏感，`WARN` 作为 `WARNING` 的别名）。**与给 xray/sing-box 用的 `LOG_LEVEL` 分离**，避免 xray 的 `warning` 字符串意外屏蔽阶段进度 INFO 日志。 |
| `NO_COLOR` | *(空)* | 设为任意非空值 → 关闭 entrypoint 日志的 ANSI 彩色。容器 stdout 非 TTY 时自动关闭，无需手动配置。 |

**格式**：`[ISO-8601 时区时间戳] [LEVEL] [模块名] 消息`

```
[2026-04-22T13:59:33.123+08:00] [INFO] [sb_xray.entrypoint] ▶ Stage 5/17 speed: ISP 测速与选路
[2026-04-22T13:59:33.876+08:00] [INFO] [sb_xray.routing.isp] 注入出站: proxy-us-isp (999.00 Mbps)
[2026-04-22T13:59:33.891+08:00] [INFO] [sb_xray.entrypoint] ✓ Stage 5/17 speed ok in 768ms
```

- 每个阶段用 `▶ / ✓ / ⋯ / ✗` 标识 start / ok / skipped / failed，带毫秒级耗时。
- 日志走 **stderr**；`SYSTEM STRATEGY SUMMARY` 方框与订阅 banner 是 **stdout** 的一次性报告，不属于日志流。
- 日志流末尾会留一行锚点 `handing over to supervisord; subsequent lines come from supervisord / xray / nginx`，之后的 supervisord / xray / nginx 日志保留其原生格式。
- 排查启动问题时推荐 `SB_LOG_LEVEL=DEBUG`；稳定后改回 `INFO`。

### 2.3 远端密钥变量（`/.env/secret`，由 `DECODE` 解密注入）

这些变量从加密的远端密钥库中读取，**不应出现在 `docker-compose.yml`** 中：

| 变量 | 说明 |
|:---|:---|
| `PUBLIC_USER` | 统一用户名（X-UI / HTTP Basic Auth 共用） |
| `PUBLIC_PASSWORD` | 统一密码 |
| `<PREFIX>_ISP_IP` | ISP 落地节点 IP（如 `LA_ISP_IP`） |
| `<PREFIX>_ISP_PORT` | ISP 落地节点端口 |
| `<PREFIX>_ISP_USER` | ISP 落地节点用户名 |
| `<PREFIX>_ISP_SECRET` | ISP 落地节点密码 |

> `<PREFIX>` 可自定义（如 `LA`、`KR`），需在 `DEFAULT_ISP` 中指定全局兜底前缀。

> 📘 **密钥轮换如何下发到运行中的节点**：`/.env/secret` 是远端加密库 `tmp.bin` 解密后的本地缓存，经宿主机卷持久化。更新 `tmp.bin` 后，运行中的服务通过两条路径感知变更，均**镜像内默认生效**：
> - **周期 cron**（`secrets-refresh`，默认每小时，见 §6）：下载比对 `tmp.bin`，凭据有变化才重解密 `/.env/secret`、覆盖运行期 env、重测选路并热重启 xray/sing-box；重启后执行 `nginx -s reload`，使重渲的 nginx 配置即时生效（无需重启容器）。生效延迟有上界（默认 ≤1h），与镜像发布节奏解耦。
> - **每次 boot 复检**：容器启动时复查上游，内容变化即原子替换缓存，故 `docker compose up -d --force-recreate`（或 watchtower 镜像跳变重建）会顺带刷新——**无需再手删 `.envs/secret`**。上游不可达且本地已有缓存时降级用旧值，启动不失败。三类远端拉取（secret 加密库、geo `.dat` 规则库、ipapi 地理信息）遭遇瞬态失败时均执行一次有界重试（共 2 次尝试，无 sleep），仍失败则降级；ipapi 地理信息仅在响应携带有效 `country_code` 时才写入 `/tmp` 缓存，局部响应不污染缓存。
>
> 🔧 **立即下发某次轮换**（不等周期 cron）：`docker exec sb-xray /scripts/entrypoint.py secrets-refresh`；期望日志含 `secrets-refresh: completed`（凭据无变化则 `secrets-refresh: noop`）。

### 2.4 自动生成变量（`/.env/sb-xray`，首次启动后永久缓存）

> ⚠️ **禁止在 docker-compose 中手动设置**，否则会锁死随机值，重建容器无法刷新。

| 变量 | 生成方式 | 说明 |
|:---|:---|:---|
| `XRAY_UUID` | `uuidgen` | Xray VLESS 用户 ID |
| `SB_UUID` | `uuidgen` | Sing-box 用户 ID |
| `PASSWORD` | 随机 16 位 | 通用密码 |
| `SUBSCRIBE_TOKEN` | 随机 32 位 | 订阅 URL 鉴权 Token |
| `XRAY_REALITY_SHORTID` | `openssl rand -hex 8` | Reality ShortId |
| `XRAY_REALITY_PRIVATE_KEY` | `xray x25519` | Reality 私钥（配对生成） |
| `XRAY_REALITY_PUBLIC_KEY` | `xray x25519` | Reality 公钥 |
| `XRAY_MLKEM768_SEED` | `xray mlkem768` | ML-KEM 种子（配对生成） |
| `XRAY_MLKEM768_CLIENT` | `xray mlkem768` | ML-KEM 客户端密钥 |
| `XRAY_URL_PATH` | 随机 32 位 | XHTTP 路径 |
| `PORT_HYSTERIA2` | `6443`（Dockerfile ENV 固定值） | Hysteria2 UDP 端口（Xray 承载） |
| `PORT_TUIC` | `8443`（Dockerfile ENV 固定值） | TUIC UDP 端口（Sing-box 承载） |
| `PORT_ANYTLS` | `4433`（Dockerfile ENV 固定值） | AnyTLS TCP 端口（Sing-box 承载） |
| `PORT_XHTTP_H3` | `4443`（Dockerfile ENV 固定值） | XHTTP/3 UDP 端口 |
| `PORT_XICMP_ID` | `12345` | XICMP 紧急通道 ICMP id（默认关，见 `docs/09-feature-flags-and-capabilities.md §6`） |
| `PORT_XDNS` | `5353` | XDNS 紧急通道 UDP 端口（默认关，见 `docs/09-feature-flags-and-capabilities.md §7`） |
| `ENABLE_XICMP` / `ENABLE_XDNS` / `ENABLE_ECH` | `false` | 实验性 feature flag；开启条件与效果详见 [特性开关与可选能力指南](./09-feature-flags-and-capabilities.md) |
| `ENABLE_REVERSE` | 镜像默认 `false`；标准 compose 部署默认 **`true`** | VLESS reverse bridge 总开关。**权威口径见 §2.7「ENABLE_REVERSE 默认姿态」**（本篇为该口径 owner，05/09 引用此处）；效果详见 [特性开关与可选能力指南](./09-feature-flags-and-capabilities.md) |
| `ENABLE_SUBSTORE` / `ENABLE_XUI` / `ENABLE_SUI` / `ENABLE_SHOUTRRR` | `true`（Dockerfile ENV 注册；`ENABLE_XUI` 在 `docker-compose.yml` 默认 `false`，需用时改 compose 为 `true`） | **小内存节点降载开关**；设 `false` 在 `createConfig` 后由 `python3 /scripts/entrypoint.py trim` 过滤对应 `[program:*]` 段；详见 §7（注：`ENABLE_SUI` 已废弃，s-ui 不再内置） |
| `GOMEMLIMIT` / `GOGC` | _未设置_ | Go GC 硬上限 + 回收激进度；推荐小内存节点 `GOMEMLIMIT=320MiB` + `GOGC=50` |
| `XDNS_DOMAIN` | _空_ | XDNS 紧急通道的 NS 域名（`ENABLE_XDNS=true` 时必填） |
| `REVERSE_DOMAINS` | _空_ | VLESS Reverse 需要穿透的域名列表（逗号分隔，`ENABLE_REVERSE=true` 时生效） |
| `SHOUTRRR_URLS` / `SHOUTRRR_FORWARDER_PORT` / `SHOUTRRR_TITLE_PREFIX` | `"" / 18085 / [sb-xray]` | 事件总线推送通道（留空 = dry-run 仅本地日志） |
| `XUI_LOCAL_PORT` | 随机端口 | X-UI 实际监听端口 |
| `DUFS_PORT` | 随机高位端口 | Dufs 内部监听端口 |
| `SUB_STORE_FRONTEND_BACKEND_PATH` | 随机 32 位路径 | Sub-Store 后端 API 路径（每次部署唯一，防扫描） |
| `STRATEGY` | API 检测 | 双栈 / 纯 IPv4 / 纯 IPv6 |
| `GEOIP_INFO` | ipapi.is | 落地归属串 `<国>\|<ip>`（国家级中文名） |
| `GEOIP_CC` | ipapi.is | 落地 ISO 国家码（如 `US`）；国旗与受限地区判定的主真相源 |
| `IS_BRUTAL` | 内核探测 | BBR/Brutal 支持状态 |
| `IP_TYPE` | ipapi.is | `isp` / `hosting` 等 |

> 📘 **落地探测复用一次抓取 + 二级回退**：`GEOIP_INFO` / `GEOIP_CC` / `IP_TYPE` 同源于一次 `api.ipapi.is` 抓取（结果缓存在 `/tmp/ipapi.json`，一次启动只请求一次）。国旗由 ISO 码 `GEOIP_CC` 经码点直接生成。`api.ipapi.is` 拿不到落地国时，geo（`GEOIP_INFO`/`GEOIP_CC`）二级回退 `ip-api.com`（缓存 `/tmp/ip-api.json`，仅取国家级、无城市）；`IP_TYPE` 仅 ipapi.is 提供、无回退。

> 📘 **`GEOIP_INFO` / `GEOIP_CC` 为空时自动重探**：表中变量一旦计算成功即永久缓存，唯这两个探测类变量例外。它们由 `api.ipapi.is` 探测落地国，可能因网络抖动等临时原因失败而得到空值；为避免空值被永久缓存后压制后续探测，**持久化值为空时每次启动都会重新探测，且空结果不写入缓存**——探测成功即正常缓存并保持稳定。实践含义：某节点若曾因探测失败导致节点名无国旗（`FLAG_PREFIX` 为空），重启容器即可自愈，无需手动删 `/.env/sb-xray`。

### 2.5 自动检测变量（`/.env/status`，可清除重新检测）

删除 `/.env/status` 并重启容器即可强制重新探测所有网络状态（含测速选路）：

```bash
docker exec sb-xray rm -f /.env/status
docker compose restart
```

| 变量 | 含义 |
|:---|:---|
| `ISP_TAG` | 最快 ISP 代理 tag（测速选路结果）；`direct` 表示无代理回退直连。用于 IS_8K_SMOOTH 计算和节点标签生成 |
| `IS_8K_SMOOTH` | `true`/`false`，实际出口速度是否 ≥ 阈值（默认 60 Mbps，`ISP_8K_SMOOTH_MBPS` 可调）；驱动 `show` 子命令生成 `✈ good`（代理出口）或 `✈ super`（住宅直出）节点标签 |
| `HAS_ISP_NODES` | `true`/空，标识是否存在可用 ISP 节点。有节点时路由函数返回 `isp-auto`（健康选优），无节点返回 `direct` |
| `CHATGPT_OUT` | ChatGPT 出口策略 tag |
| `NETFLIX_OUT` | Netflix 出口策略 tag |
| `DISNEY_OUT` | Disney+ 出口策略 tag |
| `YOUTUBE_OUT` | YouTube 出口策略 tag |
| `GEMINI_OUT` | Gemini 出口策略 tag |
| `CLAUDE_OUT` | Claude 出口策略 tag |
| `SOCIAL_MEDIA_OUT` | 社交媒体出口策略 tag |
| `TIKTOK_OUT` | TikTok 出口策略 tag |
| `ISP_OUT` | ISP 首选策略 tag |
| `ISP_LAST_RETEST_TS` | 上次周期重测 Unix 时间戳（用于冷启动缓存 TTL 判定） |
| `ISP_LAST_RETEST_DELTA_PCT` | 上次重测触发 reload 的最大速率变化百分比 |
| `ISP_LAST_RETEST_TOP_TAG` | 上次重测后选中的最快 tag |

### 2.6 `isp-auto` 优化控制变量（可选）

所有变量都有保守默认值,**不设置任何一个**也能正常工作；需要调优时再逐项开启,随时可通过取消覆盖单变量回滚。

| 变量 | 默认 | 作用 |
|:---|:---|:---|
| `ISP_PROBE_URL` | `https://speed.cloudflare.com/__down?bytes=1048576` | urltest / observatory 探测 URL；默认携带带宽信号,可切 `https://www.gstatic.com/generate_204` 仅测 RTT |
| `ISP_PROBE_INTERVAL` | `1m` | 探测周期；小内存节点建议 `5m` |
| `ISP_PROBE_TOLERANCE_MS` | `300` | sing-box `urltest` 切换最低 RTT 差阈值（毫秒） |
| `ISP_EVENTS_ENABLED` | `true` | 结构化事件 `event=... payload=...` 是否写 stdout + POST 到 shoutrrr |
| `ISP_RETEST_INTERVAL_HOURS` | `6` | 周期性带宽重测 cron 间隔；`0` 禁用 |
| `ISP_RETEST_JITTER` | `true` | 按 `sha1(hostname) % 60` 给 retest cron 分钟位打散（如 `52 */6`），避免全队同一秒压同一组共享上游代理（惊群导致集体测速偏低误报）；`false` 回到 `0 */6` |
| `ISP_RETEST_ENABLED` | `true` | 即使 cron 已安装,也可以通过此开关让子命令 no-op |
| `ISP_RETEST_DELTA_PCT` | —（已废弃） | **不再被读取，设置无任何效果**。重测仅在「已配置线路集变化（增删节点）」或「路由类别 direct↔proxy 切换」时才 rebuild+restart；纯带宽排名波动及单线 0↔alive 横跳交给运行期 `leastPing` 实时处理。带宽变化幅度仍会算出并写入 `ISP_LAST_RETEST_DELTA_PCT` + 事件 payload 作遥测（可观测，但不触发重启） |
| `ISP_LEADER_HYSTERESIS` | `1.15` | 上报主选（`FASTEST_PROXY_TAG`）的跨轮滞回余量：挑战者需超过上轮 leader 该倍数才换主，抑制抖动翻转告警 |
| `ISP_8K_SMOOTH_MBPS` | `60` | `IS_8K_SMOOTH` 判定阈值（Mbps），与内部评级梯子 8K 档对齐；旧值 100 对跨境单连接几乎不可达 |
| `SUBSTORE_CHECK_CRON` | `30 4 * * *` | 每日产出全部 remote 订阅做拉取自检的 cron 表达式；任一订阅失败（HTTP 非 2xx 或 0 节点）即发 `substore.sub_fetch.failed` 告警；置空 `""` 禁用 |
| `CERT_RENEW_CRON` | 每日 `03:xx`（分钟按 hostname jitter） | TLS 证书每日续签 cron 开关。**镜像内默认开**：不设置此变量即按默认每日运行，无需配 compose env。值为完整 5 字段 cron 表达式时按该表达式调度；置空 `""` 禁用。续签逻辑：调用 `entrypoint.py cert-renew`，实际向 CA 申请仅当证书剩余有效期 <7 天时触发；申请成功后自动执行 `nginx -s reload` 使新证书立即生效（无需重启容器）；跳过时零操作。日志写 `/var/log/cert_renew.log`，并发 `cert.renew.completed` 事件（见 §4.6） |
| `SECRET_REFRESH_INTERVAL_HOURS` | `1` | `secrets-refresh` cron 间隔（小时）：周期下载比对远端 `tmp.bin`，凭据变化才重解密 `/.env/secret` 并热重配；`0` 禁用。与镜像发布解耦，使密钥轮换有上界生效延迟（见 §2.3） |
| `SECRET_REFRESH_ENABLED` | `true` | 即使 cron 已安装，也可通过此开关让 `secrets-refresh` 子命令 no-op |
| `ISP_PER_SERVICE_SB` | `false` | 开启后 sing-box 为 Netflix / OpenAI / Claude / Gemini / Disney / YouTube 生成独立 `isp-auto-<service>` urltest balancer,各自用该服务的真实域名探测;xray 因 observatory 单例不受影响 |
| `ISP_FALLBACK_STRATEGY` | `direct` | `direct`(静默直连) / `block`(fail-closed,CN / HK / RU 建议) |
| `ISP_SPEED_CACHE_TTL_MIN` | `60` | 冷启动缓存 TTL（分钟）；`0` 禁用,每次 boot 强制实测 |
| `ISP_SPEED_CACHE_ASYNC` | `true` | 缓存命中时是否后台线程异步刷新速度;`false` 仅用于调试 |
| `ISP_SPEED_BOOT_BUDGET_SEC` | `45` | 启动测速墙钟预算（秒）；冷缓存实测超过该上界时放弃等待，主进程改用 STATUS_FILE 中 last-known `ISP_TAG` 渲染初始配置继续启动，测速线程在后台跑完并原子写回结果。`0` 禁用预算（同步等待，退回不设上界的行为） |

> **冷启动延迟包络与降级**：首次启动（无缓存或缓存过期 TTL）时，ISP 测速串行实测每个节点（单节点耗时约 `ISP_SPEED_TIMEOUT_SEC` × `ISP_SPEED_SAMPLES`）。为防止该阶段阻塞后续证书 / 配置 / supervisord 拉起，启动测速受 `ISP_SPEED_BOOT_BUDGET_SEC`（默认 45 s）墙钟预算约束：超界即放弃等待，改用 STATUS_FILE 中 last-known 路由键继续 boot；若为无历史数据的真冷启动，路由键本轮保持未定，待下一次 `isp-retest` cron 补全。此外，`probe`（基础落地变量）/ `speed`（测速选路）/ `media`（流媒体可达性）三个 stage 均为**非致命**——任一抛异常只标记 `DEGRADED` 并继续启动，不像证书 / 密钥 / DH 参数那样 fail-fast 中止容器；启动汇总行的 `degraded=N` 计数即来源于此。缓存命中（TTL 内）时测速移入后台异步刷新，boot 几乎无额外延迟。

#### v2 带宽采样器（流式 + 预热丢弃 + 时间窗 + 诊断）

v1 单次 GET + 1 MiB 文件 + 5s 超时的采样在跨境 SOCKS5 链路上受 TCP slow-start、TLS/SOCKS5 握手开销和小文件管道填不满三重因素叠加影响，系统性低估节点带宽 5–20 倍。v2 采样器改用 `httpx.stream()` 流式读取 + 时间窗 + 首字节后计时 + 结构化失败码。

| 变量 | 默认 | 作用 |
|:---|:---|:---|
| `ISP_SPEED_WINDOW_SEC` | `8.0` | 稳态测速窗口秒数（加长可减少噪声,建议 ≥ 5） |
| `ISP_SPEED_WARMUP_SEC` | `1.5` | TCP 慢启动预热丢弃秒数；高延迟链路可拉长到 2–3 |
| `ISP_SPEED_MAX_BYTES` | `268435456` | 单样本字节封顶（256 MiB 默认足够覆盖 1 Gbps 链路在 8s 窗口内跑满） |
| `ISP_SPEED_CHUNK_BYTES` | `65536` | `iter_bytes` 分块大小；默认 64 KiB 兼顾内存与精度 |
| `ISP_SPEED_SAMPLES` | `3` | 每个节点外层采样次数；`n≥3` 取中位数抗离群（旧别名 `SPEED_SAMPLES` 仍兼容） |
| `ISP_SPEED_SAMPLE_RETRIES` | `1` | 单样本遇 `connect_fail`/`timeout` 时的即时重试次数，避免一次抖动把整节点判死 |
| `ISP_SPEED_TIMEOUT_SEC` | `20` | 单样本硬超时（应 > warmup + window + 网络缓冲） |
| `ISP_SPEED_URL_MAP` | `""` | JSON `{"proxy-kr-isp":"https://kr-speed.example/100mb"}`,按 tag 覆盖探针 URL；缺项回退到 `ISP_PROBE_URL` |
| `ISP_SPEED_DIAG_ENABLED` | `true` | 是否把 `_ISP_SPEEDS_DIAG_JSON`（每 tag 诊断）写入 STATUS_FILE 和 event payload |
| `ISP_SPEED_LEGACY` | `false` | **Kill switch** — 置 `true` 回退到 v1 单次 GET 采样器（保留至少一个 release 以防新算法在某台机器上水土不服） |
| `ISP_SPEED_RTT_ADAPTIVE` | `false` | Opt-in：测速前发一次 HEAD 探 RTT，按 `max(warmup, 10 × RTT)` 拉长预热窗口（封顶 5s）；RTT 高的跨境链路可避开更长的 TCP slow-start |

**读懂 `_ISP_SPEEDS_DIAG_JSON`**

每次测速后，`/.env/status` 多写一行：

```
export _ISP_SPEEDS_DIAG_JSON='{"proxy-la-isp":{"status":"ok","ok":3,"total":3,"statuses":["ok","ok","ok"],"bytes":100663296,"window_sec":24.0},"proxy-kr-isp":{"status":"connect_fail","ok":0,"total":3,"statuses":["connect_fail","connect_fail","connect_fail"],"bytes":0,"window_sec":0.0}}'
```

字段含义：

| 字段 | 含义 |
|:---|:---|
| `status` | 整体分类：`ok` = 全部采样成功；`mixed` = 部分成功；单一失败码（`connect_fail` / `timeout` / `low_speed` / `zero_body` / `proxy_dep_missing`）= 全部同类失败 |
| `ok` | 成功采样数（status=="ok"） |
| `total` | 总采样数 |
| `statuses` | 每次采样的原始状态列表，便于排查偶发抖动 |
| `bytes` | 所有采样的 metered 字节总和（排除 warmup） |
| `window_sec` | 所有采样的 metered 时长总和（排除 warmup） |

失败码对照：

| 状态码 | 含义 | 排查方向 |
|:---|:---|:---|
| `ok` | 成功且速率 ≥ 1 KiB/s | 正常 |
| `connect_fail` | SOCKS5 / TLS / HTTP 握手失败 或 4xx/5xx | 节点 IP:PORT 不通 / 凭据错 / 探针 URL 被节点屏蔽 |
| `timeout` | httpx 读取超时 | RTT 过高 / 节点严重拥塞；尝试拉长 `ISP_SPEED_TIMEOUT_SEC` |
| `low_speed` | 测得速率 < 1 KiB/s | 节点真的很慢 / 链路被限速 |
| `zero_body` | 流开启但零字节 | 节点接受连接后立即关闭 — 检查认证或节点状态 |
| `proxy_dep_missing` | 镜像缺 `socksio` | 升级镜像或 `pip install httpx[socks]` |
| `mixed` | 采样间结果不一致 | 节点状态抖动，观察 `statuses` 找规律 |

> **典型组合**
> - **小内存节点（OOM 敏感）**: `ISP_PROBE_INTERVAL=5m`, `ISP_PER_SERVICE_SB=false`, `ISP_RETEST_INTERVAL_HOURS=12`
> - **CN / HK / RU 受限地区 fail-closed**: `ISP_FALLBACK_STRATEGY=block`
> - **追求极致解锁命中率**: `ISP_PER_SERVICE_SB=true` + 保持 `ISP_PROBE_URL` 默认

**升级后验证周期性重测已注册**（从旧镜像升级过来的部署,首次启动会注入 `isp-retest` cron entry）:

```bash
docker exec sb-xray crontab -l | grep isp-retest
# 期望: 0 */6 * * * /scripts/entrypoint.py isp-retest >> /var/log/isp_retest.log 2>&1
```

若无输出,说明 pipeline stage 16 未跑到(容器未完成 boot) — 等待或查 `docker logs sb-xray | grep 'Cron 定时任务'`。禁用后确认: `ISP_RETEST_INTERVAL_HOURS=0` 重启,该 entry 应消失。

**Entrypoint 事件流**（`SHOUTRRR_URLS` 未设置时仅 stdout）:
- `isp.speed_test.result` — 每次速度测试完成
- `isp.speed_test.cache_hit` — 冷启动缓存命中
- `isp.retest.completed` — 周期重测触发了 daemon restart
- `isp.retest.noop` — 周期重测结果无变化
- `isp.retest.error` / `isp.speed_test.error` — 失败路径

### 2.7 回国出站（`CN_EXIT_MODE` 家族，可选）

海外节点除了承担「国内用户经节点访问外网」（出境，由 `isp-auto` 分流），还可把**判定为国内的流量回送到国内出站**，让海外环境正常访问 Bilibili / 网易云 / 各银行等地区限定应用。这两条方向正交：出境走 `isp-auto` / `direct`，回国由本节的 `CN_EXIT_MODE` 控制。

**四档模式**（`CN_EXIT_MODE`，值落在集合内时直接生效；留空则按 `ENABLE_SOCKS5_PROXY` / `REVERSE_CN_EXIT` 派生）：

| 取值 | 行为 | 就绪条件 | 国内流量出站 |
|:---|:---|:---|:---|
| `socks5` | 国内流量经 SOCKS5 回国（Tailscale / OpenClash 家宽） | `CN_EXIT_SOCKS5_HOST` 非空 | `cn-exit` |
| `reverse` | 国内流量经 `r-tunnel` 反向隧道回国（家宽 bridge 挂载） | `ENABLE_REVERSE=true` | `r-tunnel` |
| `balance` | SOCKS5 + `r-tunnel` 主备，`leastPing` 探测自动故障转移 | 两条均就绪 | `cn-exit-balance` |
| `off` | 不回国，国内流量直接封禁 | — | `block` |

> `docker-compose.yml` 生产默认 `CN_EXIT_MODE=balance`（两条回国链路互为主备，任一失效自动切换，全断则 fallback 直连）。

**相关环境变量**：

| 变量 | 默认 | 作用 |
|:---|:---|:---|
| `CN_EXIT_MODE` | `balance`（compose 默认；显式留空则按既有变量派生） | 回国模式总开关；取值 `socks5` / `reverse` / `balance` / `off` |
| `CN_EXIT_SOCKS5_HOST` | _空_ | 回国 SOCKS5 地址（通常为 Tailscale IP）；`socks5` / `balance` 必填 |
| `CN_EXIT_SOCKS5_PORT` | `7891` | 回国 SOCKS5 端口 |
| `CN_EXIT_PROBE_URL` | `http://connect.rom.miui.com/generate_204` | `balance` 模式健康探测 URL（国内可达的 204 端点） |
| `CN_EXIT_PROBE_INTERVAL` | `30s` | `balance` 模式探测周期；`CN_EXIT_PROBE_*` 仅在 `balance` 下生效 |
| `ENABLE_SOCKS5_PROXY` | `true` | **`CN_EXIT_MODE` 留空时的派生开关**：`true` 且 `CN_EXIT_SOCKS5_HOST` 有值 → 派生 `socks5` 模式（`config_builder.py:306`）。`CN_EXIT_MODE` 显式取值时本变量不参与判定 |
| `REVERSE_CN_EXIT` | `false` | **`CN_EXIT_MODE` 留空时的派生开关**：socks5 不就绪且本变量 `true` → 派生 `reverse` 模式（`config_builder.py:310`）。`CN_EXIT_MODE` 显式取值时不参与判定 |
| `ENABLE_REVERSE` | 镜像 `false` / compose `true` | 启用 `r-tunnel` 反向隧道（`reverse` / `balance` 前置开关）；双口径见下方权威说明 |
| `REVERSE_DOMAINS` | _空_（镜像默认；纯回国/无内网穿透无需设置） | 经 `r-tunnel` 穿透的域名列表（逗号分隔）；与 §2.4 VLESS Reverse 为**同一变量**，`reverse` / `balance` 复用；需内网穿透时由运维注入，例如 `domain:host.example.lan,domain:nas.example.lan` |

> 📘 **`ENABLE_REVERSE` 默认姿态（权威口径 · 本篇为 owner，05/09 引用此处）**
>
> `ENABLE_REVERSE` 有两个语境下不同的默认值，二者都正确、有意为之：
>
> | 语境 | 默认值 | 出处 | 含义 |
> |:---|:---|:---|:---|
> | **镜像内默认** | `false` | `Dockerfile` `ENV ENABLE_REVERSE="false"` | watchtower 兜底值。watchtower 不读 `docker-compose.yml`，仅从旧容器 inspect 出的 env 重建镜像，故镜像内必须有向后兼容的安全默认 |
> | **标准 compose 部署** | **`true`** | `docker-compose.yml` `ENABLE_REVERSE=${ENABLE_REVERSE:-true}` | 运维层有意覆盖。`CN_EXIT_MODE=balance`（compose 默认回国模式）的 `reverse` 半边依赖它，故标准部署默认开启 |
>
> 即：用 `docker-compose.yml` 拉起的标准节点，reverse bridge **默认开启**；只有在缺失 compose env 的 watchtower 重建场景下才回落到镜像内 `false`。两层默认不是 bug，是 watchtower 自动更新纪律下的设计——见 `CLAUDE.md §2`。

**回国路由分层**（自上而下首条命中，由 `config_builder.py` 的 `_rewire_cn_rules` 注入）：

| 顺序 | ruleTag | 匹配 | 出站 | 用途 |
|:---:|:---|:---|:---|:---|
| 1 | `cn-exit-probe-bypass` | `full:www.gstatic.com` | `direct` | 放行客户端健康探测域名，不卷进隧道 |
| 2 | `cn-exit-overseas` | `geosite:geolocation-!cn` | `direct` | **海外护栏**：已知海外服务（Google Play 等）始终走海外出口，不被回国规则误吞 |
| 3 | `cn-geosite` | `geosite:cn` | 回国出站 | 国内域名回国 |
| 4 | `cn-ip` | `geoip:cn` | 回国出站 | 国内 IP 兜底回国 |

> 第 2 层护栏配合 §5.5 的 MetaCubeX `geosite.dat`（其 `geosite:cn` 不含 `@cn` 标记的海外 CDN）双重确保 `dl.google.com` / `*.gvt1.com` 等不会被误送回国。架构原理与流量图解见 [08. Xray Reverse Bridge](./08-xray-reverse-bridge.md)；Tailscale 半边（`socks5` 链路）见 [07. Tailscale 代理架构](./07-tailscale-proxy-architecture.md)。

---

## 3. 订阅端点安全体系

订阅配置文件（`/sb-xray/`）包含所有代理连接信息，安全性至关重要。系统对此端点设计了多层防御策略。

### 3.1 安全体系总览

```mermaid
flowchart TD
    A["访问请求"] --> T{"SUBSCRIBE_TOKEN<br/>已配置且字符合法?"}
    T -- "否（空/非法字符）" --> E
    T -- "是" --> B{"携带 ?token=?"}
    B -- "是，值匹配" --> D["允许访问（免 Basic Auth）"]
    B -- "否或值不匹配" --> E["要求 HTTP 基础认证"]
    E -- "凭据正确" --> D
    E -- "凭据错误" --> F["返回 404"]

    class D process
    class F block
    class T,B decision
    class E gateway
    classDef process  fill:#00b894,stroke:#009577,stroke-width:2px,color:#fff
    classDef block    fill:#ff7675,stroke:#d63031,stroke-width:2px,color:#fff
    classDef decision fill:#fdcb6e,stroke:#e0a33e,stroke-width:2px,color:#333
    classDef gateway  fill:#dfe6e9,stroke:#636e72,stroke-width:2px,color:#333
```

> 📘 **Fail-closed 不变量**：`SUBSCRIBE_TOKEN` 为空、或含 nginx 语法破坏字符（双引号、换行、空白、分号、花括号、反斜杠等）时，nginx `map` 块仅保留 `default "Restricted"`——任何 `?token=` 参数（含空串）都**不能**绕过 Basic Auth。订阅端点永远不会在无凭据的状态下开放。

### 3.2 认证方式一：Token 认证（推荐）

Token 在容器首次启动时**自动生成**（`[a-z0-9]` 32 位字符，见 §2.4）。

**查看当前 Token**：

```bash
docker exec sb-xray grep SUBSCRIBE_TOKEN /.env/sb-xray
```

**使用方法**：在订阅 URL 后添加 `?token=YOUR_TOKEN` 参数：

```
https://your-domain.com/sb-xray/MihomoPro.yaml?token=a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6
```

**自定义 Token**（可选，允许字符集 `[a-zA-Z0-9_-]`，最长 256 字符）：

```yaml
environment:
  - SUBSCRIBE_TOKEN=your_custom_secure_token_32_chars
```

> ⚠️ 自定义 Token 含空格、双引号、分号等字符时，`config_builder` 会检测到非法字符并 fail-closed——该 Token 不生效，订阅端点强制 Basic Auth，容器日志打 WARNING。

### 3.3 认证方式二：HTTP 基础认证（备用）

使用与 X-UI 相同的用户名和密码（来自 `/.env/secret`）：

**使用方法**：

* 浏览器：访问链接时会弹出认证对话框
* 客户端：`https://admin:password@your-domain.com/sb-xray/MihomoPro.yaml`

### 3.4 安全加固措施

| 措施 | 实现方式 | 效果 |
|:---|:---|:---|
| **防扫描设计** | 所有非法请求统一返回 `404` | 攻击者无法判断端点是否存在 |
| **严格路径验证** | 仅允许字母数字+`.yaml`后缀 | 阻止目录遍历攻击 (`../etc/passwd`) |
| **禁止目录浏览** | Nginx `autoindex off` | 防止列出全部订阅文件 |
| **速率限制** | 每 IP 每分钟 10 次 | 超限也返回 `404`（防暴力破解） |
| **安全响应头** | `X-Content-Type-Options: nosniff` | 防 MIME 嗅探、点击劫持 |
| **禁止缓存** | `Cache-Control: no-store` | 防敏感配置被中间节点缓存 |

### 3.5 监控与日志

```bash
# 查看成功的订阅访问
docker exec sb-xray tail -f /var/log/nginx/subscribe_access.log

# 实时查看扫描尝试
docker exec sb-xray tail -f /var/log/nginx/subscribe_scan.log

# 统计前 10 个攻击者 IP
docker exec sb-xray awk '{print $1}' /var/log/nginx/subscribe_scan.log | sort | uniq -c | sort -rn | head -10
```

---

## 4. 证书管理运维

### 4.1 日常运维命令

```bash
# 查看证书状态
docker exec sb-xray openssl x509 -in /pki/sb_xray_bundle.crt -text -noout | grep -E "Not (Before|After)"

# 强制重新签发（删除旧证书后重启）
rm -rf ./pki/* ./acmecerts/*
docker compose restart

# 手动续期
docker exec sb-xray /acme.sh/acme.sh --renew -d ${DOMAIN} -d ${CDNDOMAIN} --force
```

### 4.2 多域名证书策略

系统默认申请**泛域名 + 主域名**双SAN证书：

```
SAN[0]: *.example.com     (泛域名，覆盖所有子域名)
SAN[1]: example.com       (主域名)
```

### 4.3 DH 参数安全加固

首次启动时，系统自动生成 2048-bit DH 密钥参数：

```bash
# 存储路径：挂载卷 ./nginx/dhparam/dhparam.pem
# 耗时约 30 秒至 2 分钟（取决于 CPU）
# 生成后缓存，后续重启直接复用
```

---

## 5. GeoIP/GeoSite 数据更新

`scripts/sb_xray/geo.py` 负责下载 Xray 和 Sing-box 使用的规则库,并通过 `entrypoint.py geo-update` 子命令暴露给 cron。规则库落在持久化卷 `./geo:/geo`,容器重启不会重新下载。

### 5.1 自动更新

- **启动阶段**: entrypoint `geoip` 段（17 段坐标系第 11 段，`stages/geoip.py`）调用 `geo.refresh(on_startup=True)`。文件 <7 天视为新鲜,直接跳过下载;仅维护 `/usr/local/bin/*.dat` 符号链接。
- **每日任务**: cron 在 03:00 时段（分钟受 `ISP_RETEST_JITTER` 打散，默认 `true`；设 `false` 回退到分钟 0）执行 `/scripts/entrypoint.py geo-update`，强制刷新并通过 `supervisorctl` 重启 xray 让新规则生效。
- **日志**: cron 输出重定向到 `/var/log/geo_update.log`;启动阶段输出走 entrypoint 的 stderr。

### 5.2 手动更新

```bash
# 手动触发更新 (与 cron 等价:强制刷新 + 重启 xray)
docker exec sb-xray /scripts/entrypoint.py geo-update

# 查看缓存的规则库
docker exec sb-xray ls -lh /geo/
docker exec sb-xray ls -l /usr/local/bin/ | grep dat   # 符号链接 → /geo/*.dat
```

### 5.3 清空并重新下载

```bash
# 宿主机删除缓存,重启容器触发全量下载
rm -rf ./geo/*.dat
docker compose restart sb-xray
```

### 5.4 持久化卷要求

`docker-compose.yml` 需挂载 `./geo:/geo`。早期版本(无此卷)仍可运行,但每次 `docker compose down/up` 都会丢失缓存并重新下载 6 个 ~10-30 MB 的文件。升级现网节点只需在 compose 里追加一行然后 `docker compose up -d` 即可。

### 5.5 数据源

| 文件 | 用途 | 来源 |
|:---|:---|:---|
| `geosite.dat` | 域名分类库 (Xray, Sing-box) | MetaCubeX/meta-rules-dat |
| `geoip.dat` | IP 分类库 (Xray, Sing-box) | Loyalsoldier/v2ray-rules-dat |
| `geoip_IR.dat` / `geosite_IR.dat` | 伊朗区域规则 | chocolate4u/Iran-v2ray-rules |
| `geoip_RU.dat` / `geosite_RU.dat` | 俄罗斯区域规则 | runetfreedom/russia-v2ray-rules-dat |

> `geosite.dat` 取自 **MetaCubeX**：其 `geosite:cn` 不含被上游 `@cn` 标记的海外 CDN（`dl.google.com` / `*.gvt1.com` / `*.googleapis.com` 等），避免回国规则（见 §2.7）把 Google Play 等地区敏感服务误送回国。`geoip.dat` 仍取自 Loyalsoldier（IP 库无此问题，独立文件可混用）。

### 5.6 定时任务（cron）总表

系统运行期共有 **7 个定时任务**，分属两个调度来源：前 6 个由容器内 root crontab 安装（`scripts/sb_xray/stages/cron.py`，幂等重装、随 env 收敛），第 7 个由 Sub-Store 后端进程自身按其原生 env 调度（不进 root crontab）。

| # | 任务 | 默认表达式 | 控制变量 | 调度来源 | 用途 |
|:---:|:---|:---|:---|:---|:---|
| 1 | `geo-update` | `<m> 3 * * *`（分钟按 hostname 派生 0–59；`ISP_RETEST_JITTER=false` 时为 0） | `ISP_RETEST_JITTER`（默认 `true`，`false` 回退分钟 0） | root crontab | 强制刷新 GeoIP/GeoSite 规则库并重启 xray（见 §5.1） |
| 2 | `isp-retest` | `0 */6 * * *`（按 hostname 打散分钟位） | `ISP_RETEST_INTERVAL_HOURS`（默认 `6`，`0` 禁用） | root crontab | 周期性带宽重测，线路集/类别变化时热重配 balancer 并重启 xray/sing-box，重启后执行 `nginx -s reload` 使重渲 nginx 配置即时生效（见 §2.6） |
| 3 | `substore-check` | `30 4 * * *` | `SUBSTORE_CHECK_CRON`（空串禁用） | root crontab | 拉取自检全部 remote 订阅，失败发 `substore.sub_fetch.failed` 告警（见 §2.6） |
| 4 | `secrets-refresh` | `0 */1 * * *`（按 hostname 打散分钟位） | `SECRET_REFRESH_INTERVAL_HOURS`（默认 `1`，`0` 禁用） | root crontab | 周期下载比对远端 `tmp.bin`，凭据变化时重解密 `/.env/secret`、热重配并重启 xray/sing-box，重启后执行 `nginx -s reload` 使重渲 nginx 配置即时生效，发 `secret.refresh.completed` 事件（见 §2.3） |
| 5 | `log-rotate` | `0 * * * *` | `LOG_ROTATE_CRON`（空串禁用） | root crontab | 按大小轮转 `/var/log` 下日志（logrotate），防止 nginx/xray/sing-box 日志撑满磁盘（见 §6.7） |
| 6 | `cert-renew` | `xx 3 * * *`（分钟按 hostname jitter） | `CERT_RENEW_CRON`（**镜像内默认开**，空串禁用） | root crontab | 每日检查 TLS 证书有效期；剩余 <7 天时通过 acme.sh 向 CA 申请续签并执行 `nginx -s reload` 使新证书立即生效；有效期充足时零操作。日志写 `/var/log/cert_renew.log`，发 `cert.renew.completed` 事件 |
| 7 | Sub-Store 后端定时同步 | `0 4 * * *` | `SUB_STORE_BACKEND_SYNC_CRON` | Sub-Store 进程原生 | Sub-Store 后端自身的订阅后端同步任务；由 sub-store node 进程读取该 env 调度，**不在 root crontab 内**。默认排在 `substore-check`（04:30）前 30 分钟，使自检验证的是当天刚同步的数据 |

> 📘 任务 1–6 用 `docker exec sb-xray crontab -l` 可见；任务 7 是 Sub-Store 应用层调度，不出现在 crontab，仅作为 env 透传给 sub-store 进程（`docker-compose.yml` 不显式设时用镜像内默认 `0 4 * * *`；显式设过该 env 的部署不受默认值变更影响）。
>
> 🔬 **托管行识别机制**：重装/收敛时，`cron.py` 以正则 `/scripts/entrypoint\.py <子命令>(?:\s|$)` 识别需剥除的托管行——子命令名后须接空白或行尾（token 边界），而非裸子串匹配。不含该模式的自定义 crontab 行在收敛过程中原样保留，重装操作幂等（不产生重复行）。

---

## 6. 故障排查实战手册

### 6.1 快速诊断流程

```mermaid
flowchart TD
    Start(("连接出问题")) --> Q1{"能 ping 通服务器 IP 吗?"}
    Q1 -- "不能" --> A1["检查服务器防火墙和网络"]
    Q1 -- "能" --> Q2{"浏览器打开域名能出页面吗?"}
    Q2 -- "出 SSL 报错 / 无响应" --> A2["检查 Nginx 和证书配置"]
    Q2 -- "出伪装页面 / 404" --> Q3{"Proxy 客户端连接超时?"}
    Q3 -- "Vision 超时" --> A3["检查 UUID、Reality 参数"]
    Q3 -- "VMess/XHTTP 超时" --> A4["检查 Path、CDN域名匹配"]
    Q3 -- "Hysteria2 超时" --> A5["检查 UDP 端口、防火墙"]
```

### 6.2 常见故障排查表

#### ❌ 故障一：客户端连接 502 Bad Gateway

**现象**：浏览器或客户端返回 502 错误

**排查步骤**：

```bash
# 1. 检查 Xray 核心是否存活
docker exec sb-xray supervisorctl status xray

# 2. 查看 Xray 错误日志
docker exec sb-xray tail -50 /var/log/xray/access.log

# 3. 检查 Nginx 连接 UDS 是否正常
docker exec sb-xray ls -la /dev/shm/*.sock

# 4. 检查 Nginx 错误日志
docker exec sb-xray tail -50 /var/log/nginx/error.log
```

**常见原因**：
| 原因 | 解决方案 |
|:---|:---|
| Xray 配置 JSON 语法错误导致崩溃 | 检查日志中的 JSON parse error |
| UDS Socket 文件不存在 | 重启容器：`docker compose restart` |
| 内存不足导致 OOM | 检查 `dmesg` |

#### ❌ 故障二：订阅文件返回 404

**排查顺序**：

1. **Token 错误**：检查 URL 中的 Token 与 `/.env/sb-xray` 中的是否一致
2. **认证失败**：未携带 Token 时，输入的用户名/密码可能错误
3. **文件名大小写**：文件名大小写敏感
4. **触发限流**：请求过于频繁，等待一分钟

#### ❌ 故障三：证书申请失败

**排查步骤**：

```bash
# 检查 DNS 解析
nslookup ${DOMAIN}

# 检查 443 端口是否开放
nc -zv ${SERVER_IP} 443

# 检查 acme.sh 日志
docker exec sb-xray cat /var/log/acme.sh.log
```

**常见原因**：
| 原因 | 解决方案 |
|:---|:---|
| DNS A 记录未指向服务器 | 修正 DNS 记录并等待传播 |
| Cloudflare 小黄云未关闭 | DNS-only 模式下申请后再开启 |
| CA 频率限制 | 切换 CA 或等待后重试 |
| EAB 凭据过期 (Google CA) | 重新生成 EAB 凭据 |

#### ❌ 故障四：Hysteria2/TUIC 连接失败

**排查步骤**：

```bash
# 检查 Sing-box 状态
docker exec sb-xray supervisorctl status sing-box

# 检查 UDP 端口是否监听
docker exec sb-xray ss -ulnp | grep ${PORT_HYSTERIA2}

# 外部测试 UDP 连通性
nc -zuv ${SERVER_IP} ${PORT_HYSTERIA2}
```

**常见原因**：
| 原因 | 解决方案 |
|:---|:---|
| 服务器防火墙未放行 UDP | `ufw allow ${PORT}/udp` 或安全组放行 |
| ISP 封锁 UDP | 切换其他协议 |
| 客户端 UUID 错误 | 注意 Sing-box 使用 `SB_UUID` 而非 `XRAY_UUID` |

### 6.3 日志检查速查表

| 日志位置 | 用途 |
|:---|:---|
| `docker logs sb-xray` | **entrypoint 启动日志**（测速、选路、证书、配置渲染全流程） |
| `/var/log/xray/access.log` | Xray 访问日志 |
| `/var/log/xray/error.log` | Xray 错误日志 |
| `/var/log/sing-box/sing-box.log` | Sing-box 日志 |
| `/var/log/nginx/error.log` | Nginx 错误日志（stderr，经 supervisord 进 `docker logs`） |
| `/var/log/nginx/http-access.log` | Nginx HTTP 访问日志（默认 `minimal` 档仅记非 2xx/3xx，见 §6.7） |
| `/var/log/nginx/tcp-access.log` | Nginx TCP/SNI 访问日志（同档位策略） |
| `/var/log/nginx/subscribe_access.log` | 订阅成功访问 |
| `/var/log/nginx/subscribe_scan.log` | 扫描/攻击记录 |
| `/var/log/supervisord.log` | Supervisor 主日志（自带 `SUPERVISOR_LOG_MAX_BYTES` 轮转） |
| `/var/log/logrotate.log` | logrotate 每次轮转的输出（见 §6.7） |
| `/var/log/acme.sh.log` | 证书申请/续期日志 |

> 📘 除 supervisord 主日志外，上表所有 `/var/log` 下日志由 logrotate 按大小轮转封顶（见 §6.7），轮转后历史份为 `*.log.1`、`*.log.2.gz` …

### 6.4 快速运维命令汇总

```bash
# 容器状态
docker compose ps
docker exec sb-xray supervisorctl status

# 重启所有服务
docker compose restart

# 仅重启某个核心
docker exec sb-xray supervisorctl restart xray
docker exec sb-xray supervisorctl restart sing-box
docker exec sb-xray supervisorctl restart nginx

# 查看实时配置
docker exec sb-xray show

# 进入容器终端
docker exec -it sb-xray bash
```

### 6.5 解读测速选路启动日志

entrypoint 启动时会在 `docker logs sb-xray` 中打印完整的测速与选路决策链路，方便运维人员判断选路是否符合预期。

#### Stage 5/17 speed（测速与选路）日志结构

每行实际还有 `[<ISO-8601 时间戳>] [INFO] [<logger名>]` 前缀（格式见 §2「Entrypoint 日志」小节），下面只列消息体；测速行的 logger 名为 `sb_xray.speed_test`，段头/段尾为 `sb_xray.entrypoint`：

```
▶ Stage 5/17 speed: ISP 测速与选路
IP_TYPE=<类型> | 地区=<国家> | DEFAULT_ISP=<值或"未设置">       ← 决策上下文
开始: 节点 | 测速源: <URL> | 采样: <N>次 | sampler=v2           ← 直连基准测速（标签固定为"节点"）
节点 | 第 1/<N> 轮: XXXX KB/s → XX.XX Mbps                     ← 每轮单次结果
节点: <M>/<N> 有效样本，截断均值 XX.XX Mbps，标准差 X.XX Mbps [稳定]  ← 单次 < 1 KB/s 不计入
  或
节点: 全部 <N> 次采样失败，返回 0                               ← 全部采样低于 1 KB/s 阈值
直连基准: XX.XX Mbps（不参与选路；无代理时用于 IS_8K_SMOOTH 判定）
发现 ISP 节点 <N> 个，逐节点采样 <N> 次 | sampler=v2

开始(diag): <节点前缀> | 代理: socks5h://<IP>:<端口> | 测速源: <URL> | 采样: <N>次   ← v2 逐节点测速，无逐轮明细
<tag>: XX.XX Mbps → 新最优                                     ← 速度超过当前最优
  或
<tag>: XX.XX Mbps (最优仍: <tag> XX.XX Mbps)                   ← 未超过最优

ISP_TAG=<tag> IS_8K_SMOOTH=<true/false>                        ← 最终选路决策（单行）
✓ Stage 5/17 speed ok in <N>ms
```

每次直连/节点测速后还会向 stderr 直写一个无前缀的 `8K 测速报告` 框（`════` 边框 + 速度 + 评级），不属于结构化日志流。

#### Stage 8/17 outbounds（健康检测配置生成）日志结构

```
▶ Stage 8/17 outbounds: 生成客户端/服务端配置片段
注入出站: proxy-kr-isp (76.56 Mbps)                            ← 按速度降序注入全部 ISP（logger: sb_xray.routing.isp）
注入出站: proxy-jp-isp (70.00 Mbps)
balancer configured: probe=<URL> interval=1m tolerance=300ms nodes=2 per_service_sb=False
                                                               ← sing-box urltest + xray observatory/balancer 构建完成的汇总行
✓ Stage 8/17 outbounds ok in <N>ms
```

#### 常见选路场景对照

| 日志关键字 | 含义 |
|:---|:---|
| `DEFAULT_ISP=未设置` | 未设置该变量（compose 显式置空时显示为 `DEFAULT_ISP=`），测速自动决策 |
| `DEFAULT_ISP=LA_ISP` | 手动锁定 LA 出口（测速仍运行，但选路决策无条件锁定该出口） |
| `未发现 ISP 节点（无 *_ISP_IP 环境变量），将回退直连` | `/.env/secret` 中无 `*_ISP_IP` 变量，检查密钥库 |
| `命中缓存 ISP_TAG=proxy-xx，跳过测速` | `/.env/status` 存有旧结果，删除后重启可重新测速 |
| `speed cache hit (age=… ttl=…) — deferring live test to background` | TTL 缓存命中（`ISP_SPEED_CACHE_TTL_MIN`，默认 60 分钟），后台线程异步刷新 |
| `缓存 ISP_TAG=… 在当前 *_ISP_IP 环境里已不存在 …，清缓存后重新测速` | 缓存的出口已被运维从密钥库移除，自动失效并重测；同时静默联动清除所有 `*_OUT` 旧缓存（该清除无独立日志行） |
| `speed cache 含已失效节点 …，清缓存后重新测速` | TTL 缓存里残留了已从密钥库移除的节点出口，自动判缓存失效并实测重选——无需手动清 `/.env/status`。此守卫防止残留出口落进 `isp-auto` 成员却无对应出站，导致 `dependency[proxy-xx] not found` 启动失败 |
| `→ 新最优` | 该节点速度超过当前最优，成为新的 FASTEST_PROXY_TAG |
| `最优仍: proxy-xx` | 该节点速度未超过当前最优 |
| `注入出站: proxy-xx (XX Mbps)` | 全部 ISP 节点按速度降序注入出站配置 |
| `balancer configured: probe=… nodes=N …` | sing-box urltest（含所有 ISP + direct 回退）与 xray observatory + balancer（fallbackTag: direct）均已构建，此为汇总行 |
| `ISP_TAG=proxy-xx IS_8K_SMOOTH=true` | 参考速度 > 60 Mbps（`ISP_8K_SMOOTH_MBPS` 可调），节点将附加 good 标签 |
| `ISP_TAG=… IS_8K_SMOOTH=false` | 速度不足阈值，节点无 good/super 标签 |

#### ❌ 故障五：ISP 测速结果不符预期 / 选路错误

**现象**：选路走了不期望的出口，或 IS_8K_SMOOTH 结果与实际网速不符

**排查步骤**：

```bash
# 查看完整启动日志，重点看 Stage 5/17 speed 段（logger 名 sb_xray.speed_test）
docker logs sb-xray 2>&1 | grep -E "Stage 5/17|sb_xray\.speed_test|ISP_TAG="

# 强制重新测速：清空运行时状态后重启
docker exec sb-xray rm /.env/status
docker compose restart
```

**常见原因**：

| 原因 | 日志特征 | 解决方案 |
|:---|:---|:---|
| `DEFAULT_ISP` 非空导致选路被锁定 | `IP_TYPE=… \| 地区=… \| DEFAULT_ISP=xxx` 且最终 `ISP_TAG` 恒为该出口 | docker-compose 中显式设置 `DEFAULT_ISP=` |
| 缓存未清除 | `命中缓存 ISP_TAG=proxy-xxx，跳过测速` | `rm /.env/status` 后重启 |
| 无 ISP 节点注入 | `未发现 ISP 节点（无 *_ISP_IP 环境变量），将回退直连` | 检查 `/.env/secret` 中的 `*_ISP_IP` 变量 |
| 节点速度均低于阈值（默认 60 Mbps） | `ISP_TAG=… IS_8K_SMOOTH=false` | 正常现象，速度不足则无 good 标签 |
| 全部采样显示 0 Mbps | `全部 N 次采样失败，返回 0` | curl 下载速度低于 1 KB/s 阈值（连接失败）；检查节点连通性：`curl -x socks5h://IP:PORT https://speed.cloudflare.com/__down?bytes=1000 -o /dev/null -w '%{speed_download}'` |

#### ❌ 故障六：ISP 代理运行中突然失效，流媒体/AI 不可用

**现象**：容器已正常运行一段时间后，ChatGPT/Netflix 等突然无法访问

**运行时保障**：系统内置健康检测（Sing-box `urltest` / Xray `observatory`），按 `ISP_PROBE_INTERVAL`（默认 1 分钟）探测所有 ISP 节点。ISP 故障后：
- **Sing-box**：urltest 在下次探测后自动切换到存活节点，全部故障时回退 `direct`
- **Xray**：observatory 标记不健康节点，balancer 选择存活节点或回退 `direct`（`fallbackTag`）

**排查步骤**：

```bash
# 查看 Sing-box 日志确认 urltest 切换行为
docker exec sb-xray tail -100 /var/log/sing-box/sing-box.log | grep -i "urltest\|outbound"

# 查看 Xray 日志确认 observatory 探测结果
docker exec sb-xray tail -100 /var/log/xray/access.log | grep -i "observatory\|balancer"

# 手动测试 ISP 节点连通性
docker exec sb-xray curl -x socks5h://ISP_IP:PORT -o /dev/null -w '%{http_code}' https://www.gstatic.com/generate_204
```

**常见原因**：

| 原因 | 解决方案 |
|:---|:---|
| ISP 提供商临时维护 | 等待恢复，系统自动回退 direct 兜底 |
| ISP 凭据过期 | 更新 `/.env/secret` 中对应 `*_ISP_*` 变量后重启 |
| 回退 direct 也无法访问 | VPS 本身 IP 被目标服务封锁，需更换 ISP 节点 |

> **关键特性**：健康检测是系统内置能力，ISP 故障后最多一个探测周期（默认 1 分钟）即可完成切换，无需手动干预。

---

#### ❌ 故障七：ISP_TAG 已更新但流媒体/AI 路由仍走旧出口

**现象**：删除 `/.env/status` 重启后，Stage 5/17 speed 段的 `ISP_TAG=…` 行显示已选出新的最优 ISP（如 `proxy-jp-isp`），但实际 ChatGPT、Netflix 等流量仍走上次的旧代理（如 `proxy-us-isp`）

**根本原因**：早期版本中，`*_OUT` 服务路由缓存（`CHATGPT_OUT`、`NETFLIX_OUT` 等）写入 `/.env/status` 后不会随 ISP_TAG 重置而联动清除。下次启动时 `analyze_ai_routing_env()` 检测到这些变量非空，直接复用旧值，导致路由不一致。

此问题已在 Bug #025 中修复：测速编排（`scripts/sb_xray/speed_test.py` 的 `run_isp_speed_tests()`）在 ISP_TAG 未命中缓存、进入全新测速前，会先清除 `/.env/status` 中所有 `*_OUT` 行及对应进程内环境变量，确保后续流媒体/AI 路由基于新 ISP_TAG 重新评估。该清除是静默执行的（**无独立日志行**，仅失败时打 `status: purge failed …` WARNING）；验证方式是直接看状态文件：

```bash
# 全新测速运行后，旧的 *_OUT 行应已被清掉、随 Stage 6/17 media 重新写入
docker exec sb-xray grep '_OUT=' /.env/status
```

**排查步骤**（仅针对未升级到修复版本的环境）：

```bash
# 同时清除 ISP_TAG 与所有 *_OUT 缓存
docker exec sb-xray sed -i '/^export ISP_TAG=/d; /^export ISP_OUT=/d; /^export CHATGPT_OUT=/d; /^export NETFLIX_OUT=/d; /^export DISNEY_OUT=/d; /^export YOUTUBE_OUT=/d; /^export GEMINI_OUT=/d; /^export CLAUDE_OUT=/d; /^export SOCIAL_MEDIA_OUT=/d; /^export TIKTOK_OUT=/d' /.env/status
docker compose restart
```

> **注意**：升级到修复版本后，仅删除 `/.env/status` 并重启即可触发完整的缓存联动清除，无需手动执行上述命令。

#### ❌ 故障八：本机节点名无国旗（`FLAG_PREFIX` 为空）

**现象**：分享链接导入客户端后，本机节点名只有裸协议名（`Hysteria2`、`TUIC`、`XTLS-Reality`…），缺少 `🇺🇸` / `🇯🇵` 等国旗前缀。

**根本原因**：本机节点的国旗由 `GEOIP_CC`（`api.ipapi.is` 探测的落地 ISO 国家码）经 `node_meta.derive_and_export` 派生为 `FLAG_PREFIX`。`GEOIP_CC` / `GEOIP_INFO` 一旦探测落空（探测站点临时不可达等），空值会被持久化进卷 `/.env/sb-xray`，并随卷跨镜像升级保留——即便后续探测能力已恢复，节点名仍旧无旗。

**自愈**：`GEOIP_CC` / `GEOIP_INFO` 的空持久化值会在每次启动重新探测（见 §2.4），因此**重启容器即可自愈**：

```bash
docker compose restart
# 期望：GEOIP_CC 已填充为 ISO 码（如 US）、GEOIP_INFO 为「<国>|<ip>」，节点名带上国旗前缀
docker exec sb-xray grep -E 'GEOIP_CC|GEOIP_INFO' /.env/sb-xray
```

若重启后仍为空，则是该节点到探测站点的出网受限（网络层问题，非缓存），手动验证可达性：

```bash
# 期望 HTTP=200；非 200 说明该节点访问探测站点受限
docker exec sb-xray curl -s -o /dev/null -w 'HTTP=%{http_code}\n' --max-time 12 https://api.ipapi.is/
```

---

### 6.6 用 `xray_exit_listener` 定位 xray 崩溃

supervisord 拓扑里有一个专门的崩溃诊断器 `xray_exit_listener`（eventlistener，架构与进程拓扑见 [01. 架构与流量](./01-architecture-and-traffic.md) §6）。它订阅 supervisord 的 `PROCESS_STATE_EXITED` 事件，**每当 xray 进程异常退出就向容器日志打一行结构化诊断**，让运维把 xray 的 autorestart 循环和底层崩溃根因对应起来。

**怎么用**：xray 反复重启（502 频发、`supervisorctl status xray` 显示频繁 RESTARTING）时，直接在容器日志里捞退出行：

```bash
docker logs sb-xray 2>&1 | grep "\[xray-exit\]"
# 期望输出形如:
# [xray-exit] processname=xray from_state=RUNNING pid=1234 expected=0
```

**读懂字段**：

| 字段 | 含义 | 排查方向 |
|:---|:---|:---|
| `from_state` | 退出前状态（`RUNNING` = 正常运行中崩溃） | `RUNNING` 突崩多为配置/资源问题 |
| `pid` | 退出进程 PID | 与 `docker logs` 时间线对齐定位崩溃时刻 |
| `expected` | 退出码是否在 supervisord 预期集内（`0` = 非预期退出） | `expected=0` 表示意外崩溃，需查根因 |

> 📘 典型场景：小内存节点上 xray 被内核 OOM-killer 以 `SIGKILL` 杀掉（退出码 `-9`），表现为 `expected=0` 的非预期退出 + autorestart 循环。配合 `dmesg | grep -i "killed process"` 与 §7 小内存降载开关定位并缓解。诊断器本身永不因事件解析失败而退出（始终回 `OK`），不会给 supervisord 添乱。

---

### 6.7 日志体积治理与爆盘清理

长时间运行的节点日志会持续累积——尤其 nginx 的 JSON 访问日志高频写入。系统用**两层机制**把 `/var/log` 总量封顶，全部镜像内默认生效，无需运维设置 compose env（watchtower 用旧 env 重建镜像也能止血）。

#### 📘 两层治理

| 层 | 机制 | 控制变量 | 作用 |
|:---|:---|:---|:---|
| 源头精简 | nginx access 日志按状态码条件记录 | `NGINX_ACCESS_LOG`（默认 `minimal`） | `minimal` 仅记非 2xx/3xx 异常请求，大幅压制高频访问日志产生；`full` 全量；`off` 关闭。`subscribe_*` 安全审计日志不受影响，始终全量 |
| 轮转兜底 | logrotate 按大小轮转 `/var/log` 全部日志 | `LOG_ROTATE_SIZE` / `LOG_ROTATE_KEEP` / `LOG_ROTATE_CRON` | 单文件超 `LOG_ROTATE_SIZE`（默认 `50M`）即轮转，保留 `LOG_ROTATE_KEEP`（默认 `3`）份压缩历史；由 cron 每 `LOG_ROTATE_CRON`（默认每小时）触发（任务表见 §5.6 #5） |

> 📘 单文件稳态上限 ≈ `LOG_ROTATE_SIZE × (LOG_ROTATE_KEEP + 1)`，默认约 200 MB/文件。logrotate 用 `copytruncate`（先复制再清空原文件），nginx/xray/sing-box 无需重开 fd、进程无感。supervisord 主日志由 `SUPERVISOR_LOG_MAX_BYTES` 自管，不重复纳入。

#### 🔧 调整与验证

```bash
# 查看当前 access 日志档位（容器内 env）
docker exec sb-xray printenv NGINX_ACCESS_LOG    # 期望: minimal

# dry-run 预演 logrotate 规则解析（不实际轮转）
docker exec sb-xray logrotate -d /etc/logrotate.d/sb-xray
# 期望: 无 error，逐条列出 /var/log/nginx/*.log 等纳管路径

# 临时改为全量访问日志（排查具体请求时）：在 compose 设 NGINX_ACCESS_LOG=full 后重建容器
#   environment: [ NGINX_ACCESS_LOG=full ]
```

#### 🔧 磁盘被日志占满时的一次性清理

拉取含本能力的镜像后，logrotate 会在下一次 cron（≤1 小时）自动把存量大日志轮转压缩。需要**立即止血**时手动强制轮转一次：

```bash
# 强制轮转所有纳管日志（无视大小阈值），立即压缩历史、清空原文件
docker exec sb-xray logrotate -f /etc/logrotate.d/sb-xray

# 或对单个超大日志直接清空（不留历史）
docker exec sb-xray truncate -s 0 /var/log/nginx/http-access.log

# 复核磁盘占用（按大小倒序）
docker exec sb-xray du -sh /var/log/* | sort -rh | head
```

> 📘 日志经 `./logs:/var/log` 持久化到宿主机（见 §1.3），容器重启不会清空；务必用上述命令在容器内清理。直接在宿主机删除运行中的日志文件可能因进程仍持 fd 而不释放空间。

---

## 7. 小内存节点部署指引（内存不超过 512 MB）

镜像默认启用 9 个 supervisord program（xray / sing-box / nginx / x-ui / sub-store / http-meta / shoutrrr-forwarder / dufs / cron）+ 1 个 eventlistener（xray_exit_listener，进程拓扑见 [01. 架构 §6](./01-architecture-and-traffic.md)），常驻 RSS ~390–650 MB，启动峰值可达 650–870 MB。低于 1 GB 物理内存的节点需通过以下开关组合降载至 ~300–430 MB 常驻。

**作用机制**：`scripts/sb_xray/config_builder.py:create_config` 用 `string.Template` envsubst 语义渲染出完整 `/etc/supervisor.d/daemon.ini` 后，`scripts/entrypoint.py:run_pipeline` 在 `trim` 段（17 段坐标系第 14 段，见 [01. 架构 §5](./01-architecture-and-traffic.md)）直接调用同模块的 `trim_runtime_configs` 按环境变量 `ENABLE_*` 对 `daemon.ini` 做幂等 in-place 过滤（也可通过 `docker exec sb-xray python3 /scripts/entrypoint.py trim` 重跑一次）。所有开关都是 opt-out：仅显式设 `false` 生效，未设置或为空保持默认启用。

### 7.1 docker-compose.yml（宿主层硬约束）

```yaml
services:
  sb-xray:
    mem_limit: 460m
    mem_reservation: 380m
    memswap_limit: 900m
    oom_kill_disable: false
    environment:
      # Go 三件套（xray / sing-box / x-ui）共享上限（s-ui 已不再内置）
      GOMEMLIMIT: "320MiB"
      GOGC: "50"
      # 容器级进程精简
      ENABLE_SUBSTORE: "false"       # 关掉 sub-store + http-meta（省 ~130–200 MB）
      ENABLE_XUI: "false"            # 关掉 x-ui 面板（省 ~35–55 MB）
      ENABLE_SUI: "false"            # s-ui 已不再内置，此项无效（保留仅作历史参考）
      ENABLE_SHOUTRRR: "false"       # 关掉 shoutrrr-forwarder（省 ~20–30 MB）
```

### 7.2 开关语义

| 变量 | 默认 | 设 `false` 的副作用 |
|------|-----|--------------------|
| `ENABLE_SUBSTORE` | true（保留） | 本节点无法对外托管订阅聚合/转换，客户端改从主节点订阅 |
| `ENABLE_XUI` | **`false`**（`docker-compose.yml` 默认不启用；镜像 ENV 为 `true`，需用时改 compose 为 `true`） | 无 X-UI 面板；xray 仍正常运行，改为直接编辑 JSON 配置 |
| `ENABLE_SUI` | —（已废弃，s-ui 不再内置，此开关无效） | s-ui 二进制已从镜像移除；此行仅作历史参考 |
| `ENABLE_SHOUTRRR` | true（保留） | xray `rules.webhook` 事件不再转发到 Telegram/Discord |

> Opt-out 语义：以上开关**仅**在显式设为字符串 `"false"` 时才禁用功能；未设置、空字符串、`"0"` 等均保持默认启用。

### 7.3 VPS 宿主层建议

- 确认 `swapon --show` 显示 ≥ 512 MB swap；无 swap 时 `fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile`
- `sysctl vm.swappiness=60`（激进换入 swap 保物理内存）
- `sysctl vm.overcommit_memory=1`（允许 VSZ 超分，避免 xray 启动瞬间 1.4 GB VSZ 被内核拒绝）

### 7.4 验证

部署后 30 分钟内观察：

```bash
docker stats --no-stream sb-xray   # MEM USAGE 应稳定 < 400 MB
free -h                            # available 稳定 > 80 MB
dmesg | grep -i "killed process"   # 无新 OOM kill 事件
```

若仍触发 OOM，进一步措施：

- 主节点接管订阅服务，小内存节点仅作纯转发
- 保留上一个已知稳定版镜像标签作为紧急回滚兜底：`docker pull <镜像>:<上一稳定标签>`

---

## 上游组件出处

本文管理面板与运维涉及的上游：

- Xray-core — <https://github.com/XTLS/Xray-core>
- Sing-box — <https://github.com/SagerNet/sing-box>
- Mihomo（external-controller / 面板内核）— <https://github.com/MetaCubeX/mihomo>
- Zashboard（面板 UI）— <https://github.com/Zephyruso/zashboard>
- Sub-Store — <https://github.com/sub-store-org/Sub-Store>
- Dufs — <https://github.com/sigoden/dufs>
- acme.sh — <https://github.com/acmesh-official/acme.sh>
- Tailscale — <https://github.com/tailscale/tailscale>
- OpenClash — <https://github.com/vernesong/OpenClash>（仓库内配置见 [`../sources/openclash/`](../sources/openclash/readme.md)）
- geoip / geosite 规则数据 — <https://github.com/MetaCubeX/meta-rules-dat>
