# hack 工具集

存放与 sb-xray 配套使用的辅助脚本，运行在客户端侧（如 OpenWrt 路由器）。

## cdn-speedtest.sh — Cloudflare CDN IP 优选

自动测速 Cloudflare IP 段，将最优 IP 写入 `/etc/hosts`，使所有 CDN 域名的流量走最快的边缘节点。

### 原理

所有 CDN 域名都经过 Cloudflare，Cloudflare 根据 TLS SNI / HTTP Host 头路由到正确的源站。因此**一个优选 IP 即可覆盖所有域名**，无需逐个对应。

```
客户端 → 优选IP → Cloudflare 边缘节点 ─┬─ SNI: jp.example.com   → 日本源站
                                        ├─ SNI: cn2.example.com  → CN2源站
                                        └─ SNI: big.example.com  → big源站
```

### 支持架构

| 架构 | 说明 |
|------|------|
| amd64 (x86_64) | 软路由、虚拟机 |
| arm64 (aarch64) | 树莓派、ARM 路由器 |
| armv7 | 32 位 ARM 设备 |

脚本自动检测架构并下载对应版本的 [CloudflareST](https://github.com/XIU2/CloudflareSpeedTest)。

### 安装

```bash
# 上传到路由器
scp sources/hack/cdn-speedtest.sh root@openwrt:/root/cdn-speedtest.sh

# 赋予执行权限
ssh root@openwrt “chmod +x /root/cdn-speedtest.sh”
```

### 使用

首先创建子域名前缀配置文件（每行一个前缀，`#` 开头为注释）：

```bash
cat > /etc/subdomains.txt << 'EOF'
# 每行一个子域名前缀
# 运行时与 CDNDOMAIN 拼接，如 node1.example.com
node1
node2
EOF
```

然后设置 `CDNDOMAIN` 环境变量运行：

```bash
# 执行测速并更新 hosts（首次运行会自动安装 CloudflareST）
CDNDOMAIN=example.com ./cdn-speedtest.sh run

# 仅安装 CloudflareST（不测速）
./cdn-speedtest.sh install

# 查看当前优选状态
CDNDOMAIN=example.com ./cdn-speedtest.sh status

# 清除优选记录，恢复 DNS 正常解析
CDNDOMAIN=example.com ./cdn-speedtest.sh clean
```

也可以 export 后直接使用：
```bash
export CDNDOMAIN=example.com
./cdn-speedtest.sh run
```

### 配置定时任务

```bash
# 每天凌晨 4 点自动优选
echo "0 4 * * * CDNDOMAIN=example.com /usr/bin/cdn-speedtest.sh run  # optimize CDN IP" >> /etc/crontabs/root
/etc/init.d/cron restart
```

### 域名配置

子域名前缀存放在配置文件中（默认 `/etc/subdomains.txt`），与 `CDNDOMAIN` 拼接生成完整域名。可通过 `CDN_SUBDOMAINS_FILE` 环境变量指定其他路径。

### 自定义测速参数

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `SPEED_TEST_THREADS` | 500 | 延迟测速线程数 |
| `SPEED_TEST_TIME` | 4 | 下载测速时间（秒） |
| `SPEED_TEST_COUNT` | 5 | 下载测速数量 |
| `SPEED_TEST_LATENCY_MAX` | 200 | 延迟上限（ms） |
| `SPEED_TEST_MIN_SPEED` | 5 | 最低下载速度（MB/s） |

### 日志

测速日志写入 `/var/log/cdn-speedtest.log`，包含时间戳、优选 IP、延迟和速度信息。

---

## anytls_overwrite.sh — OpenClash AnyTLS 覆写

[Giveupmoon/OpenClash_Overwrite: OpenClash覆写模块相关文件](https://github.com/Giveupmoon/OpenClash_Overwrite/tree/main)

```
https://hk.gh-proxy.org/https://raw.githubusercontent.com/currycan/sb-xray/main/sources/hack/anytls_overwrite.sh
```

## rename.js — 节点重命名

基于 [Keywos/rule](https://github.com/Keywos/rule) 的节点重命名脚本。

```
https://hk.gh-proxy.org/https://raw.githubusercontent.com/currycan/sb-xray/main/sources/hack/rename.js
```

过滤正则：`._(距离|套餐|国内|剩余|到期)._`

使用示例：
```
https://raw.githubusercontent.com/Keywos/rule/main/rename.js#name=Nexitally&fgf=|&blkey=Emby+GPT>OpenAI&nm
```
