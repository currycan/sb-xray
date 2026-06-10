# nikki — OpenWrt 上的 mihomo 客户端配置

本目录存放 OpenWrt [nikki](https://github.com/nikkinikki-org/OpenWrt-nikki) 插件的一份参考 UCI 配置（`nikki` 文件）。nikki 是基于 [mihomo](https://github.com/MetaCubeX/mihomo)（Clash.Meta 内核）的 OpenWrt 透明代理插件，作用与 [OpenClash](https://github.com/vernesong/OpenClash) 相同：在路由器侧把局域网流量按规则分流到代理。

## 与 OpenClash 的取舍关系

nikki 是 OpenClash 的**备选方案**，两者二选一，不同时启用。

- **OpenClash**：本仓库的默认/主用方案，生态成熟、覆写模板与教程丰富（见 `../openclash/readme.md`、`../ACL4SSR/readme.md`）。
- **nikki**：更轻量、直接对接 mihomo 内核、配置走 OpenWrt 原生 UCI。当 OpenClash 在某些设备上偏重、或希望用更贴近 mihomo 原生形态的客户端时，作为替代选用。

选定其中一个即可，规则集（geosite / geoip）与机场订阅在两者间是通用的。

## 配置文件说明

`nikki` 是一份 OpenWrt UCI 配置（对应 `/etc/config/nikki`），按 nikki 插件的配置段组织，关键段落如下：

| 配置段 | 作用 |
|--------|------|
| `config config 'config'` | 总开关、订阅 profile、定时重启与配置自检 |
| `config proxy 'proxy'` | 透明代理模式（TCP `redirect` / UDP `tun`）、DNS 劫持、IPv4/IPv6 代理开关、代理端口范围 |
| `config mixin 'mixin'` | 注入 mihomo 运行参数：日志级别、各监听端口（http/socks/mixed/redir/tproxy）、DNS（fake-ip）、面板 UI、geox 规则源与自动更新 |
| `config env 'env'` | mihomo 运行环境开关（安全路径检查、QUIC GSO/ECN 等） |
| `config *_access_control` | 路由器侧 / 局域网侧的代理与 DNS 放行控制 |
| `config authentication` | 控制面板登录鉴权 |
| `config nameserver` / `nameserver_policy` | 国内外 DNS 分流策略 |
| `config sniff` | 按协议（HTTP/TLS/QUIC）做流量嗅探以还原目标域名 |
| `config subscription` | 机场订阅源（`name` / `url` / `user_agent`） |

> **使用前必改**：`config subscription` 的 `url` 是占位 `<YOUR_SUBSCRIBE_LINK>`，填入自己的机场订阅链接。控制面板的鉴权用户名/密码与 `api_secret` 应改成自己的值，切勿沿用文件内的示例值。

## 相关资源

- `../openclash/readme.md` — 主用 OpenClash 方案
- `../ACL4SSR/readme.md` — 订阅转换与规则模板（OpenClash / nikki 通用）
- [OpenWrt-nikki 上游仓库](https://github.com/nikkinikki-org/OpenWrt-nikki)
- [mihomo（Clash.Meta 内核）](https://github.com/MetaCubeX/mihomo)
