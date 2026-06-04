# 09. 外网访问家里 NAS：小白手把手教程

> 跟着本文一步步操作，最终效果：人在外面，掏出手机（或打开电脑），浏览器输入 `http://nas.lan/`，直接打开家里 NAS 的管理页面——家里**不需要公网 IP、不需要在路由器上开任何端口**。
>
> 本文是手把手教学版；想了解原理细节、故障告警、安全边界，请看进阶篇 [06. VLESS Reverse Proxy 部署指南](./06-reverse-proxy-guide.md)。

---

## 0. 开始前的检查清单

请确认你已经具备以下条件（缺一不可）：

| # | 条件 | 怎么确认 |
|---|------|---------|
| 1 | 一台**已经跑起来的 sb-xray 公网节点**（VPS + Docker） | 手机客户端平时能正常翻墙上网 |
| 2 | 家里有一台 **OpenWrt 软路由**，能 SSH 登录 | `ssh root@192.168.1.1` 能进去 |
| 3 | 家里有一台 **NAS**（群晖 / 威联通 / 自建均可），知道它的内网 IP 和网页端口 | 在家时浏览器能打开，例如 `http://192.168.1.10:5000` |
| 4 | 手机或电脑上装好了代理客户端，并已导入 sb-xray 的订阅 | 不会的话先看 [03. 客户端接入](./03-routing-and-clients.md) |

整个方案只有三个角色，先混个脸熟：

```
[你的手机/电脑]  --①走代理-->  [公网 sb-xray (VPS)]
                                      ^
                                      | ②反向隧道（由家里主动连出去，全程复用 443）
                                      |
                              [家里 OpenWrt 软路由]
                                      |
                                      v ③内网直连
                              [NAS 192.168.1.10]
```

为什么家里不用开端口？因为隧道（②）是**家里的路由器主动连出去**建立的——就像你在家能打开网页一样，只用到"出门"的流量，外面的人永远不需要"敲家里的门"。你的访问请求（①）先到 VPS，VPS 再顺着已经建好的隧道（②）把请求"递"回家。

下面三步，每步结束都有检查点，确认通过再进行下一步。

---

## 1. 第一步：在公网 VPS 上打开穿透开关

### 1.1 改两行配置

SSH 登录你的 VPS，编辑 sb-xray 的 `docker-compose.yml`，在 `environment:` 段落里加两行：

```yaml
services:
  sb-xray:
    environment:
      # ... 已有变量保持不动 ...
      - ENABLE_REVERSE=true
      - REVERSE_DOMAINS=domain:nas.lan
```

- `ENABLE_REVERSE=true`：打开内网穿透功能
- `REVERSE_DOMAINS=domain:nas.lan`：告诉 VPS"凡是访问 `nas.lan` 的流量，都送进回家的隧道"。以后想多暴露几个设备，用英文逗号追加即可，例如 `domain:nas.lan,domain:router.lan`

### 1.2 重启容器

```bash
docker compose up -d --force-recreate
```

重启后 sb-xray 会自动生成一个专用的隧道身份证（`XRAY_REVERSE_UUID`）并写好全部服务端配置，你不需要手动改任何 JSON。

### 1.3 抄下 6 个参数

运行下面的命令，把输出**完整抄下来**，第二步要用：

```bash
docker exec sb-xray bash -c '. /.env/sb-xray; cat <<EOF
DOMAIN=$DOMAIN
LISTENING_PORT=$LISTENING_PORT
DEST_HOST=$DEST_HOST
XRAY_REVERSE_UUID=$XRAY_REVERSE_UUID
XRAY_REALITY_PUBLIC_KEY=$XRAY_REALITY_PUBLIC_KEY
XRAY_REALITY_SHORTID=$XRAY_REALITY_SHORTID
EOF'
```

| 参数 | 它是什么 |
|------|---------|
| `DOMAIN` | 你 VPS 的域名，例如 `vpn.example.com` |
| `LISTENING_PORT` | VPS 对外端口，一般是 `443` |
| `DEST_HOST` | Reality 伪装目标网站（照抄即可，不用懂） |
| `XRAY_REVERSE_UUID` | 隧道专用身份证，**只给隧道用**，和你平时翻墙的 UUID 不是同一个 |
| `XRAY_REALITY_PUBLIC_KEY` | Reality 公钥（照抄即可） |
| `XRAY_REALITY_SHORTID` | Reality Short ID（照抄即可） |

> ✅ **检查点**：6 个参数全部有值、没有空行。如果 `XRAY_REVERSE_UUID` 是空的，说明容器还没重启完成，等半分钟再跑一次。

---

## 2. 第二步：在家里的 OpenWrt 上部署"隧道客户端"

这一步在**家里的 OpenWrt 软路由**上操作（SSH 登录它）。

### 2.1 安装 xray 程序

先看路由器是什么架构：

```sh
uname -m
```

| 输出 | 该下载的文件 |
|------|-------------|
| `x86_64` | `Xray-linux-64.zip` |
| `aarch64` | `Xray-linux-arm64-v8a.zip` |

以 `x86_64` 为例（aarch64 把文件名换掉即可），下载本项目锁定的 Xray v26.3.27：

```sh
opkg update && opkg install unzip ca-bundle
cd /tmp
wget https://github.com/XTLS/Xray-core/releases/download/v26.3.27/Xray-linux-64.zip
unzip Xray-linux-64.zip -d xray-dist
cp xray-dist/xray /usr/bin/xray
chmod +x /usr/bin/xray
xray version
```

> ✅ **检查点**：`xray version` 输出 `Xray 26.3.27 ...` 即成功。
>
> 路由器访问不了 GitHub 的话，先在电脑上下载好，再 `scp Xray-linux-64.zip root@192.168.1.1:/tmp/` 传过去。

### 2.2 下载配置模板并填参数

```sh
mkdir -p /etc/xray
wget https://raw.githubusercontent.com/currycan/sb-xray/main/templates/reverse_bridge/client.json \
     -O /etc/xray/client.json
```

把第一步抄下的参数填进去——下面的命令**每一行的右边都要换成你自己的值**：

```sh
sed -i \
  -e "s|\${DOMAIN}|vpn.example.com|g" \
  -e "s|\${XRAY_REVERSE_UUID}|<你的 XRAY_REVERSE_UUID>|g" \
  -e "s|\${XRAY_REALITY_PUBLIC_KEY}|<你的 XRAY_REALITY_PUBLIC_KEY>|g" \
  -e "s|\${XRAY_REALITY_SHORTID}|<你的 XRAY_REALITY_SHORTID>|g" \
  -e "s|\${DEST_HOST}|<你的 DEST_HOST>|g" \
  /etc/xray/client.json
```

> 模板里出站端口固定写的 `443`。如果你的 `LISTENING_PORT` 不是 443，再手动编辑 `/etc/xray/client.json`，把 `"port": 443` 改成你的端口。

确认没有漏填：

```sh
grep '\${' /etc/xray/client.json
```

> ✅ **检查点**：上面的 grep **没有任何输出**（还有输出说明有占位符没替换掉）。

### 2.3 给 NAS 起个名字

第一步里我们约定外网用 `nas.lan` 访问 NAS，现在要让路由器知道 `nas.lan` 指的是谁。把下面命令里的 `192.168.1.10` 换成你 NAS 的真实内网 IP：

```sh
echo "192.168.1.10 nas.lan" >> /etc/hosts
```

（喜欢图形界面的话，LuCI → 网络 → 主机名 → 添加，效果相同。）

```sh
ping -c 1 nas.lan
```

> ✅ **检查点**：ping 通，且显示的是 NAS 的内网 IP。

### 2.4 注册为开机自启服务

OpenWrt 用 procd 管理服务。新建 `/etc/init.d/xray-bridge`，内容如下：

```sh
#!/bin/sh /etc/rc.common
# Xray reverse bridge (sb-xray 内网穿透家庭端)
START=99
USE_PROCD=1

start_service() {
    procd_open_instance
    procd_set_param command /usr/bin/xray run -c /etc/xray/client.json
    procd_set_param respawn
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_set_param limits nofile="65535 65535"
    procd_close_instance
}
```

启用并启动：

```sh
chmod +x /etc/init.d/xray-bridge
/etc/init.d/xray-bridge enable
/etc/init.d/xray-bridge start
logread -e xray | tail -20
```

> ✅ **检查点**：日志里没有 `failed` / `rejected` 字样。同时在 **VPS 上**运行：
>
> ```bash
> docker exec sb-xray tail -f /var/log/xray/access.log | grep -i reverse
> ```
>
> 看到类似 `accepted reverse [REALITY_IN -> r-tunnel] email: reverse@portal.bridge` 的日志，说明隧道已经建立。🎉

### 2.5 路由器上跑了 OpenClash？看这里

隧道客户端是路由器**自己发出**的流量（目标是你的 VPS 443 端口）。OpenClash 默认只接管局域网设备的流量、不动路由器自身的连接，所以两者通常相安无事。

只有当你在 OpenClash 里开启了「代理路由器本机流量」之类的选项时，隧道流量可能被它截走、出现反复重连。处理办法任选其一：

- 在 OpenClash 的绕过列表（访问控制/黑名单）中加入你的 VPS 域名或 IP；
- 关闭「代理路由器本机流量」选项。

---

## 3. 第三步：在外面用手机 / 电脑访问

回到你的手机或电脑（连手机流量或其他网络，**不要连家里 WiFi**，否则测不出效果）。

### 3.1 核心概念：`nas.lan` 必须"走代理"

平时我们让国内流量直连、国外流量走代理；而 `nas.lan` 恰恰相反——它**必须走 sb-xray 节点**，因为只有 VPS 知道怎么把它送进回家的隧道。

好消息是：sb-xray 自带的订阅模板里，所有没被规则匹配的"陌生域名"默认都交给 **`兜底流量`** 这个策略组，`nas.lan` 正属于此类。所以你只需要做一件事：**确保 `兜底流量` 组选中的是 sb-xray 节点**（而不是 DIRECT 或别家机场的节点）。

### 3.2 手机（ClashMi / Karing）

1. 打开 App，确认 sb-xray 的订阅是当前生效配置，启动代理；
2. 在策略组列表里找到 **`兜底流量`**，把它切换到你的 sb-xray 节点（或指向 sb-xray 的自动选择组）;
3. 浏览器打开 `http://nas.lan/`（NAS 网页端口不是 80 的话带上端口，如 `http://nas.lan:5000/`）。

### 3.3 电脑（Clash Verge / mihomo）

操作与手机相同：导入订阅 → 开启系统代理 → `兜底流量` 组选中 sb-xray 节点 → 浏览器访问 `http://nas.lan/`。

想要"一劳永逸"、不依赖兜底组的选择，可以在客户端的覆写/自定义规则里**置顶**加一条（`节点选择` 换成你实际指向 sb-xray 的策略组名）：

```yaml
rules:
  - DOMAIN-SUFFIX,nas.lan,节点选择
```

> ✅ **检查点**：浏览器成功打开 NAS 登录页。到此全部完成！

---

## 4. 打不开？对照下面排查

| 现象 | 最可能的原因 | 怎么办 |
|------|-------------|--------|
| 路由器日志见 `REALITY: processed invalid connection` | UUID 或 ShortID 抄错 | 重做 2.2，逐字核对 6 个参数 |
| 路由器日志见 `tls: handshake failure` | 公钥抄错，或 `DEST_HOST` 与 VPS 端不一致 | 在 VPS 重跑 1.3 的命令，重新核对 |
| 路由器日志见 `i/o timeout` | 路由器连不上 VPS | 在路由器上 `curl -v https://<你的域名>:443` 测试；跑了 OpenClash 看 2.5 |
| VPS 日志一切正常，但手机打不开 `nas.lan` | `兜底流量` 组没选 sb-xray 节点 | 重看 3.1，或直接加 3.3 的置顶规则 |
| 手机能连到 NAS 但奇慢/断流 | 走的是别家节点而非 sb-xray | 同上：检查 `nas.lan` 实际命中的节点 |
| 在家测试一切正常，出门就不行 | 手机还挂着家里 WiFi 的"回忆" | 关 WiFi，用蜂窝流量重试 |

快速自检命令（在任何一台外网 Linux 机器上，验证整条链路通不通）：

```bash
curl -x socks5h://<sb-xray-socks5地址>:1080 http://nas.lan/ -I
```

返回 HTTP 响应头即链路正常，问题出在客户端配置。

---

## 5. 进阶与收尾

- **多暴露几台设备**：第一步的 `REVERSE_DOMAINS` 加域名 + 第二步 2.3 在路由器 `/etc/hosts` 加对应解析，VPS 重启容器即可生效；
- **隧道断线告警、安全边界（为什么穿透 UUID 不能拿来翻墙）、彻底关闭穿透**：见 [06. VLESS Reverse Proxy 部署指南](./06-reverse-proxy-guide.md)；
- **bridge 不想跑在 OpenWrt 上**：任何家里常开的 Linux 设备（NAS 的虚拟机、树莓派等）都可以，部署方式（systemd 版）同样见 06 篇第 3 节。
