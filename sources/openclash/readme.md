# 说明

> `gl-inet.sh`（GL.iNet 一键工具箱）已移至 [`../openwrt/gl-inet.sh`](../openwrt/gl-inet.sh)，文档见 [`../openwrt/gl-inet.md`](../openwrt/gl-inet.md)。

## op-amd / op-arm —— OpenClash 配置模板（由 openwrt-init.sh 渲染应用）

`op-amd`（x86_64）/ `op-arm`（aarch64）是路由器 `/etc/config/openclash` 的完整模板，由 [`../openwrt/openwrt-init.sh`](../openwrt/openwrt-init.sh) 按架构自动选择、注入私有值后幂等应用到路由器，无需手动拷贝。

模板中的占位符与对应 config.env 变量：

| 占位符 / 注入点 | config.env 变量 | 说明 |
|----------------|-----------------|------|
| `<OPENCLASH_DASHBOARD_PASSWORD>` | `OPENCLASH_DASHBOARD_PASSWORD` | dashboard 登录密码（必填） |
| `config_subscribe` 块的 `option address` | `OPENCLASH_SUBS`（`名=URL` 空格分隔） | 按 `option name` 匹配注入订阅地址 |

渲染规则：注入后仍含占位地址（如 `<YOUR_SUBSCRIBE_LINK`、`【订阅地址`）或未提供地址的 `config_subscribe` 块会被**整块裁剪**——模板里的 AllOne / 示例块仅作填写示范，不会落到路由器上。应用前自动备份 `.bak.<时间戳>`，无差异时跳过。

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
docker network create --subnet=<内网网段>/24 --gateway <内网网关IP> MyNET

# 启动应用

docker run -d --name AdGuard-Home1 -v /opt/docker/AGH_Docker1:/opt/adguardhome/work -v /opt/docker/AGH_Docker1:/opt/adguardhome/conf -p 3001:3000 --restart always --net MyNET --ip <容器固定IP> adguard/adguardhome:latest

```

本地运营商 DNS：`<运营商DNS主>` / `<运营商DNS备>`（按所在地区填写当地运营商的公共 DNS）
