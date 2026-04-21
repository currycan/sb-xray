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

---

## 1. 系统控制面板导航

SB-Xray 集成了多个可视化管理面板，全部通过 CDN 域名的子路径访问。

### 1.1 控制面板一览

| 面板 | 入口路径 | 功能 | 默认凭据来源 |
|:---|:---|:---|:---|
| **X-UI (3x-ui)** | `https://${CDNDOMAIN}/${XUI_WEBBASEPATH}` | Xray 协议与用户管理 | `/.env/secret` 中 `PUBLIC_USER/PASSWORD` |
| **S-UI** | `https://${CDNDOMAIN}/${SUI_WEBBASEPATH}` | Sing-box 入站与出站监控 | 同上 |
| **Sub-Store** | `https://${CDNDOMAIN}/sub-store` | 订阅源管理与节点清洗 | 无需认证 |
| **Dufs** | `https://${CDNDOMAIN}/${DUFS_PATH_PREFIX}` | 文件上传/下载网盘 | HTTP Basic 认证（同上凭据） |
| **Yacd/Zashboard** | `https://${CDNDOMAIN}:9090/ui` | 实时流量与策略组监控 | Secret: `yyds666` |

> **安全提醒**：所有面板路径均通过 Nginx 的 `location` 指令保护，建议首次登录后立即修改默认 WebBasePath。

### 1.2 环境变量配置示例

```yaml
environment:
  # X-UI
  - XUI_WEBBASEPATH=3xadmin      # 自定义面板路径

  # S-UI
  - SUI_WEBBASEPATH=sui           # 自定义面板路径

  # Dufs 文件服务
  - DUFS_PATH_PREFIX=/myfiles     # 文件网盘的 URL 前缀
  - DUFS_SERVE_PATH=/data         # 文件存储路径
```

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

> **实践说明**：各层通常包含不同的变量，实际覆盖冲突极少。`ensure_var` 的三分支逻辑确保自动生成的变量首次计算后即永久缓存，不会被重复生成。

### 2.2 用户配置变量（在 docker-compose 设置）

#### 必填（无默认值，缺少则容器退出）

| 变量 | 说明 |
|:---|:---|
| `DOMAIN` | 主域名（Reality TLS 伪装目标所在域） |
| `CDNDOMAIN` | CDN 域名（Cloudflare 代理，Nginx Web 层监听此 SNI） |
| `DECODE` | 远端密钥库解密密钥（由 `crypctl` 使用） |

#### 核心可选

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
| `ACMESH_SERVER_NAME` | `zerossl` | ACME CA：`zerossl` / `google` |
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

| 变量 | Dockerfile 默认 | 说明 |
|:---|:---|:---|
| `SUI_WEBBASEPATH` | `sui` | 面板访问路径 |
| `SUI_SUB_PATH` | `sub` | 订阅子路径 |
| `SUI_PORT` | `3095` | S-UI 内部端口 |
| `SUI_SUB_PORT` | `3096` | S-UI 订阅内部端口 |
| `SUI_LOG_LEVEL` | `info` | 日志级别 |

#### Dufs 文件服务

| 变量 | Dockerfile 默认 | 说明 |
|:---|:---|:---|
| `DUFS_PATH_PREFIX` | `/dufs` | URL 前缀 |
| `DUFS_SERVE_PATH` | `/data` | 文件存储根目录 |
| `DUFS_ALLOW_UPLOAD` | `true` | 允许上传 |
| `DUFS_ALLOW_DELETE` | `true` | 允许删除 |

### 2.3 远端密钥变量（`/.env/secret`，由 `DECODE` 解密注入）

这些变量从加密的远端密钥库中读取，**不应出现在 `docker-compose.yml`** 中：

| 变量 | 说明 |
|:---|:---|
| `PUBLIC_USER` | 统一用户名（X-UI / S-UI / HTTP Basic Auth 共用） |
| `PUBLIC_PASSWORD` | 统一密码 |
| `<PREFIX>_ISP_IP` | ISP 落地节点 IP（如 `LA_ISP_IP`） |
| `<PREFIX>_ISP_PORT` | ISP 落地节点端口 |
| `<PREFIX>_ISP_USER` | ISP 落地节点用户名 |
| `<PREFIX>_ISP_SECRET` | ISP 落地节点密码 |

> `<PREFIX>` 可自定义（如 `LA`、`KR`），需在 `DEFAULT_ISP` 中指定全局兜底前缀。

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
| `PORT_XHTTP_H3` | `4443`（Dockerfile ENV 固定值） | XHTTP/3 UDP 端口（永久启用） |
| `PORT_XICMP_ID` | `12345` | XICMP 紧急通道 ICMP id（默认关，见 `docs/07-new-features-guide.md §6`） |
| `PORT_XDNS` | `5353` | XDNS 紧急通道 UDP 端口（默认关，见 `docs/07-new-features-guide.md §7`） |
| `ENABLE_XICMP` / `ENABLE_XDNS` / `ENABLE_ECH` / `ENABLE_REVERSE` | `false` | 实验性 feature flag；开启条件与效果详见 [新特性使用指南](./07-new-features-guide.md) |
| `XDNS_DOMAIN` | _空_ | XDNS 紧急通道的 NS 域名（`ENABLE_XDNS=true` 时必填） |
| `REVERSE_DOMAINS` | _空_ | VLESS Reverse 需要穿透的域名列表（逗号分隔，`ENABLE_REVERSE=true` 时生效） |
| `SHOUTRRR_URLS` / `SHOUTRRR_FORWARDER_PORT` / `SHOUTRRR_TITLE_PREFIX` | `"" / 18085 / [sb-xray]` | 事件总线推送通道（留空 = dry-run 仅本地日志） |
| `XUI_LOCAL_PORT` | 随机端口 | X-UI 实际监听端口 |
| `DUFS_PORT` | 随机高位端口 | Dufs 内部监听端口 |
| `SUB_STORE_FRONTEND_BACKEND_PATH` | 随机 32 位路径 | Sub-Store 后端 API 路径（每次部署唯一，防扫描） |
| `STRATEGY` | API 检测 | 双栈 / 纯 IPv4 / 纯 IPv6 |
| `GEOIP_INFO` | API 检测 | GeoIP 归属字符串 |
| `IS_BRUTAL` | 内核探测 | BBR/Brutal 支持状态 |
| `IP_TYPE` | API 检测 | `isp` / `hosting` 等 |

### 2.5 自动检测变量（`/.env/status`，可清除重新检测）

删除 `/.env/status` 并重启容器即可强制重新探测所有网络状态（含测速选路）：

```bash
docker exec sb-xray rm -f /.env/status
docker compose restart
```

| 变量 | 含义 |
|:---|:---|
| `ISP_TAG` | 最快 ISP 代理 tag（测速选路结果）；`direct` 表示无代理回退直连。用于 IS_8K_SMOOTH 计算和节点标签生成 |
| `IS_8K_SMOOTH` | `true`/`false`，实际出口速度是否 ≥ 100 Mbps；驱动 show-config.sh 生成 `✈ good`（代理出口）或 `✈ super`（住宅直出）节点标签 |
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

---

## 3. 订阅端点安全体系

订阅配置文件（`/sb-xray/`）包含所有代理连接信息，安全性至关重要。系统对此端点设计了多层防御策略。

### 3.1 安全体系总览

```mermaid
graph TD
    A["访问请求"] --> B{"携带 Token?"}
    B -- "是" --> C{"Token 正确?"}
    C -- "是" --> D["允许访问"]
    C -- "否" --> E["要求 HTTP 基础认证"]
    B -- "否" --> E
    E -- "凭据正确" --> D
    E -- "凭据错误" --> F["返回 404"]

    style D fill:#55efc4,stroke:#00b894,stroke-width:2px
    style F fill:#ff7675,stroke:#d63031,stroke-width:2px
```

### 3.2 认证方式一：Token 认证（推荐）

Token 在容器首次启动时**自动生成**。

**查看当前 Token**：

```bash
docker exec sb-xray grep SUBSCRIBE_TOKEN /.env/sb-xray
```

**使用方法**：在订阅 URL 后添加 `?token=YOUR_TOKEN` 参数：

```
https://your-domain.com/sb-xray/MihomoPro.yaml?token=a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6
```

**自定义 Token**（可选）：

```yaml
environment:
  - SUBSCRIBE_TOKEN=your_custom_secure_token_32_chars
```

### 3.3 认证方式二：HTTP 基础认证（备用）

使用与 X-UI / S-UI 相同的用户名和密码（来自 `/.env/secret`）：

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

`scripts/geo_update.sh` 负责更新 Xray 和 Sing-box 使用的地理信息数据库。

### 5.1 自动更新

系统通过 Supervisor 定时任务自动执行更新。

### 5.2 手动更新

```bash
# 手动触发更新
docker exec sb-xray /scripts/geo_update.sh

# 查看当前 GeoIP 数据版本
docker exec sb-xray ls -la /usr/local/bin/bin/
```

### 5.3 数据源

| 文件 | 用途 | 来源 |
|:---|:---|:---|
| `geoip.dat` | IP 地理归属库 (Xray) | Loyalsoldier/v2ray-rules-dat |
| `geosite.dat` | 域名分类库 (Xray) | Loyalsoldier/v2ray-rules-dat |
| `geoip.db` | IP 地理归属库 (Sing-box) | SagerNet/sing-geoip |
| `geosite.db` | 域名分类库 (Sing-box) | SagerNet/sing-geosite |

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
| `/var/log/nginx/error.log` | Nginx 错误日志 |
| `/var/log/nginx/access.log` | Nginx 访问日志 |
| `/var/log/nginx/subscribe_access.log` | 订阅成功访问 |
| `/var/log/nginx/subscribe_scan.log` | 扫描/攻击记录 |
| `/var/log/supervisord.log` | Supervisor 主日志 |
| `/var/log/acme.sh.log` | 证书申请/续期日志 |

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
docker exec sb-xray /scripts/show-config.sh

# 进入容器终端
docker exec -it sb-xray bash
```

### 6.5 解读测速选路启动日志

entrypoint 启动时会在 `docker logs sb-xray` 中打印完整的测速与选路决策链路，方便运维人员判断选路是否符合预期。

#### 阶段 2 日志结构

```
[阶段 2] 测速与选路...
[阶段 2] 环境: IP_TYPE=<类型> | 地区=<国家> | DEFAULT_ISP=<值>    ← 决策上下文
[阶段 2] 直连基准: XX.XX Mbps（不参与选路；无代理时用于 IS_8K_SMOOTH 判定）
[阶段 2] 发现 ISP 节点: N 个，开始逐节点测速（采样=2次）...

[测速] <节点> | 第 1/2 轮: XXXX KB/s → XX.XX Mbps    ← 每轮单次结果
[测速] <节点> | 第 2/2 轮: ...
[测速] <节点>: 2/2 有效样本，截断均值 XX.XX Mbps      ← 有效样本均值（单次 < 1 KB/s 不计入）
  或
[测速] <节点>: 全部 2 次采样失败，返回 0               ← 全部采样低于 1 KB/s 阈值

[测速] <tag>: XX.XX Mbps → 新最优                     ← 速度超过当前最优
  或
[测速] <tag>: XX.XX Mbps (最优仍: <tag> XX.XX Mbps)   ← 未超过最优

[选路] ════════════════════════════════════════════
[选路] 决策输入:
[选路]   IP_TYPE       = <值> (<描述>)
[选路]   地区          = <国家>
[选路]   DEFAULT_ISP   = <值>
[选路]   直连速度      = XX.XX Mbps（不参与选路）
[选路]   最优 ISP 代理 = <tag> (XX.XX Mbps)
[选路] <路由判断结果>
[选路] IS_8K_SMOOTH: 出口=<tag> | 参考速度=XX.XX Mbps | 阈值=100 Mbps → <true/false>  → <标签提示>
[选路] ✓ 最终决策: ISP_TAG=<tag> | IS_8K_SMOOTH=<true/false>
[选路] ════════════════════════════════════════════
```

#### 阶段 4 日志结构（健康检测配置生成）

```
[阶段 4] 生成客户端/服务端配置片段...
[ISP] 注入出站: proxy-kr-isp (76.56 Mbps)              ← 按速度降序注入全部 ISP
[ISP] 注入出站: proxy-jp-isp (70.00 Mbps)
[ISP] Sing-box urltest 已生成: outbounds=[...]          ← urltest 包含所有 ISP + direct
[ISP] Xray observatory + balancer 已生成: selector=[...]← balancer 精确匹配 ISP tags
[阶段 4] 完成
```

#### 常见选路场景对照

| 日志关键字 | 含义 |
|:---|:---|
| `DEFAULT_ISP=未设置（自动选路）` | docker-compose 已置空，测速自动决策 |
| `DEFAULT_ISP=LA_ISP（锁定模式）` | 强制使用 LA 出口，测速跳过 |
| `未发现 ISP 节点` | `/.env/secret` 中无 `*_ISP_IP` 变量，检查密钥库 |
| `ISP_TAG 已缓存` | `/.env/status` 存有旧结果，删除后重启可重新测速 |
| `清除服务路由缓存（与 ISP_TAG 同步刷新）` | ISP_TAG 未命中缓存，触发重新测速；同时联动清除所有 `*_OUT` 旧缓存，确保流媒体/AI 路由基于新 ISP_TAG 重新评估 |
| `受限地区 (中国\|香港)，强制使用代理` | GeoIP 检测到受限地区，无论速度必须走代理 |
| `→ 新最优` | 该节点速度超过当前最优，成为新的 FASTEST_PROXY_TAG |
| `最优仍: proxy-xx` | 该节点速度未超过当前最优 |
| `注入出站: proxy-xx (XX Mbps)` | 全部 ISP 节点按速度降序注入出站配置 |
| `Sing-box urltest 已生成` | urltest 健康选优出站已构建（含所有 ISP + direct 回退） |
| `Xray observatory + balancer 已生成` | observatory 探测 + balancer 选优已构建（fallbackTag: direct） |
| `IS_8K_SMOOTH → true → ✈ good 标签` | 代理均值 > 100 Mbps，节点将附加 good 标签 |
| `IS_8K_SMOOTH → false → 无质量标签` | 速度不足 100 Mbps，节点无 good/super 标签 |

#### ❌ 故障五：ISP 测速结果不符预期 / 选路错误

**现象**：选路走了不期望的出口，或 IS_8K_SMOOTH 结果与实际网速不符

**排查步骤**：

```bash
# 查看完整启动日志，重点看 [阶段 2] 和 [选路] 段
docker logs sb-xray 2>&1 | grep -E "\[阶段 2\]|\[测速\]|\[选路\]"

# 强制重新测速：清空运行时状态后重启
docker exec sb-xray rm /.env/status
docker compose restart
```

**常见原因**：

| 原因 | 日志特征 | 解决方案 |
|:---|:---|:---|
| `DEFAULT_ISP` 非空导致跳过测速 | `ISP_TAG 已缓存` 或 `手动覆盖 DEFAULT_ISP=xxx` | docker-compose 中显式设置 `DEFAULT_ISP=` |
| 缓存未清除 | `ISP_TAG 已缓存 (proxy-xxx)，跳过测速` | `rm /.env/status` 后重启 |
| 无 ISP 节点注入 | `未发现 ISP 节点` | 检查 `/.env/secret` 中的 `*_ISP_IP` 变量 |
| 节点速度均低于 100 Mbps | `IS_8K_SMOOTH → false` | 正常现象，速度不足则无 good 标签 |
| 全部采样显示 0 Mbps | `全部 N 次采样失败，返回 0` | curl 下载速度低于 1 KB/s 阈值（连接失败）；检查节点连通性：`curl -x socks5h://IP:PORT https://speed.cloudflare.com/__down?bytes=1000 -o /dev/null -w '%{speed_download}'` |

#### ❌ 故障六：ISP 代理运行中突然失效，流媒体/AI 不可用

**现象**：容器已正常运行一段时间后，ChatGPT/Netflix 等突然无法访问

**新版行为**：系统内置运行时健康检测（Sing-box `urltest` / Xray `observatory`），每 1 分钟探测所有 ISP 节点。ISP 故障后：
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

> **与旧版对比**：旧版无运行时检测，ISP 故障后流量直接黑洞，需手动重启容器。新版自动检测并回退，最多 1 分钟恢复服务。

---

#### ❌ 故障七：ISP_TAG 已更新但流媒体/AI 路由仍走旧出口

**现象**：删除 `/.env/status` 重启后，`[选路]` 段显示已选出新的最优 ISP（如 `proxy-jp-isp`），但实际 ChatGPT、Netflix 等流量仍走上次的旧代理（如 `proxy-us-isp`）

**根本原因**：早期版本中，`*_OUT` 服务路由缓存（`CHATGPT_OUT`、`NETFLIX_OUT` 等）写入 `/.env/status` 后不会随 ISP_TAG 重置而联动清除。下次启动时 `analyze_ai_routing_env()` 检测到这些变量非空，直接复用旧值，导致路由不一致。

此问题已在 Bug #025 中修复：`run_speed_tests_if_needed()` 现在在 ISP_TAG 未命中缓存时，会立即清除所有 `*_OUT` 行及对应 shell 变量，确保后续流媒体/AI 路由基于新 ISP_TAG 重新评估。启动日志中会出现：

```
[阶段 2] 清除服务路由缓存（与 ISP_TAG 同步刷新）...
```

**排查步骤**（仅针对未升级到修复版本的环境）：

```bash
# 同时清除 ISP_TAG 与所有 *_OUT 缓存
docker exec sb-xray sed -i '/^export ISP_TAG=/d; /^export ISP_OUT=/d; /^export CHATGPT_OUT=/d; /^export NETFLIX_OUT=/d; /^export DISNEY_OUT=/d; /^export YOUTUBE_OUT=/d; /^export GEMINI_OUT=/d; /^export CLAUDE_OUT=/d; /^export SOCIAL_MEDIA_OUT=/d; /^export TIKTOK_OUT=/d' /.env/status
docker compose restart
```

> **注意**：升级到修复版本后，仅删除 `/.env/status` 并重启即可触发完整的缓存联动清除，无需手动执行上述命令。
