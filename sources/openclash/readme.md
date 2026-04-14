# 说明

[vernesong/OpenClash: A Clash Client For OpenWrt](https://github.com/vernesong/OpenClash)

[OpenClash/master/smart at core · vernesong/OpenClash](https://github.com/vernesong/OpenClash/tree/core/master/smart)

[Release Config Clash Meta - 2026-03-04 02:34 · rtaserver/Config-Open-ClashMeta](https://github.com/rtaserver/Config-Open-ClashMeta/releases/tag/latest)

https://raw.githubusercontent.com/vernesong/OpenClash/core/master/smart/clash-linux-arm64.tar.gz

## AdGuardHome 1（**不启用 DNS 缓存**）

功能：

- 替换DnsMasq
- 去广告，DNS 重写

启动：

```bash
docker run -d --name AdGuard-Home --net host -v /opt/docker/AGH_Docker:/opt/adguardhome/work -v /opt/docker/AGH_Docker:/opt/adguardhome/conf -p 3000:3000 --restart always  adguard/adguardhome:latest
```

## AdGuardHome 2

功能：

- 代理上游 DNS

- DNS缓存

启动：

```bash

# 创建网络
docker network create --subnet=172.18.10.0/24 --gateway 172.18.10.1 MyNET

# 启动应用

docker run -d --name AdGuard-Home1 -v /opt/docker/AGH_Docker1:/opt/adguardhome/work -v /opt/docker/AGH_Docker1:/opt/adguardhome/conf -p 3001:3000 --restart always --net MyNET --ip 172.18.10.2 adguard/adguardhome:latest

```

上海电信DNS：202.96.209.133 / 116.228.111.118
