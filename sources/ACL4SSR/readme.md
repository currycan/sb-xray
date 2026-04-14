# 说明

[Google Emojis & Text | ⌕ ⋆.˚ 💻🔍🌐… | Copy & Paste](https://emojicombos.com/google)

[ACL4SSR 在线订阅转换](https://acl4ssr-sub.github.io/)

[zsokami/ACL4SSR: 自定义 订阅转换 配置转换 规则转换 的远程配置。正则匹配大小写、简繁体，更好地匹配中转、IPLC节点。自带旗帜 emoji 添加逻辑，原名不包含旗帜 emoji 才添加，原名已包含旗帜 emoji 则不添加。添加某些影视/动漫 APP 广告拦截规则（附 hosts 文件）。附无 DNS 泄漏配置。修改自 ACL4SSR_Online_Full.ini](https://github.com/zsokami/ACL4SSR)

[Loyalsoldier/clash-rules: 🦄️ 🎃 👻 Clash Premium 规则集(RULE-SET)，兼容 ClashX Pro、Clash for Windows 等基于 Clash Premium 内核的客户端。](https://github.com/Loyalsoldier/clash-rules)

[Aethersailor/Custom_OpenClash_Rules: 分流完善的 OpenClash 订阅转换模板，搭配保姆级 OpenClash 设置教程，无需套娃其他插件即可实现完美分流、DNS无污染无泄漏，且快速的国内外上网体验，配套自动化域名规则提交机器人](https://github.com/Aethersailor/Custom_OpenClash_Rules)

[Giveupmoon/OpenClash_Overwrite: OpenClash覆写模块相关文件](https://github.com/Giveupmoon/OpenClash_Overwrite)

[ios_rule_script/rule/Clash at master · blackmatrix7/ios_rule_script](https://github.com/blackmatrix7/ios_rule_script/tree/master/rule/Clash)

[全国DNS服务器IP地址大全 公共DNS地址大全 - ToolB](https://toolb.cn/publicdns)

[MetaCubeX/meta-rules-dat: rules-dat for mihomo](https://github.com/MetaCubeX/meta-rules-dat)

https://mylink.ansandy.top/sub?
http://172.18.18.254:25500/sub?

https://raw.githubusercontent.com/currycan/sb-xray/main/sources/ACL4SSR/Blacklist-Full.ini
https://raw.githubusercontent.com/currycan/sb-xray/main/sources/ACL4SSR/Blacklist-Lite.ini

https://raw.githubusercontent.com/currycan/sb-xray/main/sources/ACL4SSR/ACL4SSR_Online_Full.ini
https://raw.githubusercontent.com/currycan/sb-xray/main/sources/ACL4SSR/ACL4SSR_Online_Lite.ini
https://raw.githubusercontent.com/currycan/sb-xray/main/sources/ACL4SSR/ACL4SSR_Online_MT3000.ini

## Sub-store

```bash
# http://172.18.18.254:3010/sDFye2FvHNJwheeCkXQpGaGRQBtupYGS
docker run -it -d \
--restart=always \
-e "SUB_STORE_CRON=55 23 ** *" \
-e SUB_STORE_FRONTEND_BACKEND_PATH=/sDFye2FvHNJwheeCkXQpGaGRQBtupYGS \
-p 3010:3001 \
-v /etc/sub-store:/opt/app/data \
--name sub-store \
xream/sub-store
```

## DNS

[内置 DNS 服务 – Stash 用户文档](https://stash.wiki/features/dns-server)

由于部分地区开始劫持公共dns的ip，优化dns部分如下：

https://dns.alidns.com/dns-query
https://doh.pub/dns-query
223.5.5.5
119.29.29.29
114.114.114.114

## Tailscale 设置

[openwrt软路由安装tailscale - DEV Community](https://dev.to/dragon72463399/openwrtruan-lu-you-an-zhuang-tailscale-a7j)

[pkgs.tailscale.com/stable/](https://pkgs.tailscale.com/stable/)

> https://pkgs.tailscale.com/stable/tailscale_1.90.9_amd64.tgz

### 安装

首先直接在 iStore 商店安装tailscale, **不要启动服务**，然后进行如下升级:

```bash
VERSION=1.90.9
ARCH=amd64
# ARCH=arm64

wget https://pkgs.tailscale.com/stable/tailscale_${VERSION}_${ARCH}.tgz

tar -zxvf tailscale_${VERSION}_${ARCH}.tgz

mv tailscale_${VERSION}_${ARCH}/tailscale /usr/sbin/
mv tailscale_${VERSION}_${ARCH}/tailscaled /usr/sbin/
```

```bash
tailscale up --accept-dns=false --accept-routes --advertise-exit-node --advertise-routes=172.18.18.0/23 --hostname=n305-op

tailscale up --accept-dns=false --accept-routes --advertise-exit-node --advertise-routes=192.168.8.0/24 --hostname=mt3000

tailscale up --auth-key=xxx XXX

```

在OpenWrt上新建一个接口，协议选**静态地址**，设备选**tailscale0**，地址为Taliscale管理页面上分配的**地址100.X.X.X**，掩码255.0.0.0。防火墙区域选lan区域。

## ZeroTier 设置

docker pull zyclonite/zerotier:router-1.16.0

在 ZeroTier 网页端配置路由，[ZeroTier Central - Networks](https://my.zerotier.com/)

- 找到 "Advanced" -> "Managed Routes"（路由管理）。
- 添加一条规则：
  Destination (目标网段)： 输入你家里的物理网段，例如 192.168.1.0/24。
  Via (经过)： 输入那台网关设备的 ZeroTier 虚拟 IP，例如 10.147.20.5。
  点击 Submit (提交)。

默认免费只能添加一条规则，使用时可以手动切换
