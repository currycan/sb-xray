# 说明

## gl-inet.sh —— 统一一键工具箱（BE3600 / BE6500 / MT-3000）

`gl-inet.sh` 合并自 `be3600.sh` / `be6500.sh` / `mt3000.sh` / `mt3000-overlay.sh`，单文件适配三款 GL.iNet 设备。启动时合并 `/tmp/sysinfo/model` 与 hostname（GL.iNet 默认设为 `GL-BE6500` / `GL-BE3600` / `GL-MT3000`）自动识别机型——BE 系列的 `/tmp/sysinfo/model` 是 Qualcomm 板名不含型号数字，靠 hostname 兜底；识别失败（如改过 hostname）可用 `gl-inet.sh --device be3600|be6500|mt3000` 手动指定，或在菜单提示时手选。

- **机型差异**：arch.conf 源、iStore 安装法、一键流程 quickstart 走法、WAN 防火墙、distfeeds 恢复、自动风扇、overlay 换分区（仅 MT-3000）——均按机型自动切换；BE6500 一键流程沿用其 mdadm 跳过保护。
- **三款通用能力**：argon 主题、iStore、AdGuardHome、wireguard、文件管理器、自定义软件源、quickstart、高级卸载、`g` 快捷命令、脚本自更新。
- **不含 Docker**：Docker 安装功能已移除——低端机型（如 MT-3000 256MB RAM）的源/内存支持不确定，BE 系列又无法 extroot 扩容、内部 flash 仅 ~285MB 装不下；需要时用 GL 原生面板或自行 `opkg install dockerd`。
- **取代**：旧四脚本暂时保留，待 gl-inet.sh 在三款设备上稳定后移除。

> ⚠️ **overlay 换分区仅 MT-3000 可用**：BE 系列（BE3600/BE6500）的 GL SDK4 固件 preinit(`80_mount_root`) 写死 `mount_ext4 "systemrw" /overlay`、不读 fstab 的 `config mount 'overlay'`，U 盘 extroot 扩容在 BE 上不生效（机械步骤会跑但重启后 /overlay 仍在内部 flash）。故该菜单项按 profile 仅对 MT-3000 显示。U 盘在 BE 上仍可作数据盘/NAS（GL 原生 `gl_nas_diskmanager`）。

### 菜单 0「一键初始化」

全自动跑除 **6 AdGuard / 9 wireguard / 13 overlay / 14 更新脚本** 外的全部功能项，交互项一律用默认值（11 软件源最先执行，取默认 TUNA 镜像；5 风扇 48℃；7 自动继续）。

**MT-3000 两阶段**：先扩容 overlay 到 U 盘（默认 5GB）→ 自动重启 → 重启后**再点一次菜单 0** 进入阶段 2 安装其余（先扩容腾空间、再把包装进大分区）。靠 `/etc/.glinet_init_overlay_tried` 标记防止重复抹盘。

> ⚠️ **未验证**：overlay 在 MT-3000 固件上是否真正生效**尚未实测**——本两阶段是按「生效」假设写的。若 MT-3000 与 BE 同为 GL SDK4（preinit 写死 `systemrw`），扩容不生效，阶段 1 重启后 `df /overlay` 仍是内部 flash；此时标记会让第二次跑直接进阶段 2（并提示固件可能不支持），不会反复抹 U 盘。需在 MT-3000 真机确认后去除此 caveat。

### 获取脚本（首次部署）

**方法 A —— 设备直接从 GitHub 拉取（推荐，设备联网即可，免 PC 中转）**

路由器 SSH 里一行搞定：

```sh
wget -O gl-inet.sh https://raw.githubusercontent.com/currycan/sb-xray/main/sources/openclash/gl-inet.sh && chmod +x gl-inet.sh && ./gl-inet.sh
```

中国大陆访问 `raw.githubusercontent.com` 不稳时，换 jsDelivr CDN 镜像（同一文件）：

```sh
wget -O gl-inet.sh https://cdn.jsdelivr.net/gh/currycan/sb-xray@main/sources/openclash/gl-inet.sh && chmod +x gl-inet.sh && ./gl-inet.sh
```

装好后下次直接输快捷键 `g` 运行；菜单 14「更新本脚本」从同一 GitHub raw 源自更新（jsDelivr 有 CDN 缓存，要最新版优先用 raw）。

**方法 B —— 从本地电脑上传**

GL.iNet 固件的 SSH 是 **dropbear**，默认**不含 `sftp-server`**，`scp` / SFTP 直传会失败（TCP 能连上但子系统协商失败）。三选一：

- **SSH 管道直传**（免装包，最省事）：`ssh root@<设备IP> 'cat > /root/gl-inet.sh' < gl-inet.sh`
- **旧版 SCP 协议**：`scp -O gl-inet.sh root@<设备IP>:/root/`
- **启用 SFTP**：`opkg update && opkg install openssh-sftp-server`，之后 scp / SFTP 恢复可用。




### 菜单一览

| # | 功能 | 备注 |
|---|------|------|
| 0 | 一键初始化（全自动） | 跑除 6/9/13/14 外全部，交互项用默认值；MT-3000 两阶段 |
| 1 | 一键 iStoreOS 风格化 | profile 决定 iStore 法 / quickstart 走法 |
| 2 | 安装 Argon 紫色主题 | |
| 3 | 单独安装 iStore 商店 | |
| 4 | 隐藏首页非必要 UI 元素 | |
| 5 | 设置风扇工作温度 | 交互，回车默认 **48℃** |
| 6 | 启用/关闭 AdGuardHome | |
| 7 | 安装个性化 UI 辅助插件 | 交互，回车继续 |
| 8 | 安装高级卸载插件 | |
| 9 | 安装 luci-app-wireguard | |
| 10 | 安装文件管理器 | |
| 11 | 设置/删除自定义软件源 | 交互，回车默认 **TUNA 镜像** |
| 12 | 手动安装/更新 quickstart 首页 | |
| 13 | Overlay 换分区助手 | **仅 MT-3000 显示**；子项自定义大小回车默认 **5GB** |
| 14 | 更新本脚本 | 从 sb-xray 仓库 raw 自更新 |
| 15 | 恢复原厂 OPKG 配置 | **仅 MT-3000 显示** |
| R | 恢复出厂设置 | 需手输 `yes` |
| Q | 退出 | |

### 默认值与幂等

- **交互默认值**：风扇温度（48℃）、自定义软件源（TUNA 镜像）、overlay 自定义包大小（5GB）均可直接回车采用，也可手输覆盖。
- **幂等**：所有功能可重复执行而不累积垃圾 / 损坏配置（`uci set`、清空再写、`grep -q` 去重、`sed` 替换均幂等；`uci add` / CSS 追加有存在性防护）。唯一破坏性操作 overlay 换分区有「已扩容则确认」防护，一键初始化用 `/etc/.glinet_init_overlay_tried` 标记防止重复抹盘。
- **真实 IP**：安装提示里的 luci / AdGuard 地址动态取 `uci get network.lan.ipaddr`（设备真实 LAN IP），不再写死 `192.168.8.1`。

### 真机验证 checklist（每款设备）

部署后在对应设备上至少验证：

1. 启动后菜单顶部「当前机型」识别正确（错误则用 `--device` 复核子串匹配）。
2. 菜单项 1「一键 iStoreOS 风格化」跑通，8080 端口 luci 生效。
3. 抽测：argon 主题（2）、iStore（3）、quickstart（12）。
4. MT-3000 额外验证：第 15 项 distfeeds 恢复、overlay 换分区（13，需插 U 盘）。
5. BE6500 确认一键流程未触发 quickstart（mdadm 保护）。

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
