# 说明

## gl-inet.sh —— 统一一键工具箱（BE3600 / BE6500 / MT-3000）

`gl-inet.sh` 合并自 `be3600.sh` / `be6500.sh` / `mt3000.sh` / `mt3000-overlay.sh`，单文件适配三款 GL.iNet 设备。启动时合并 `/tmp/sysinfo/model` 与 hostname（GL.iNet 默认设为 `GL-BE6500` / `GL-BE3600` / `GL-MT3000`）自动识别机型——BE 系列的 `/tmp/sysinfo/model` 是 Qualcomm 板名不含型号数字，靠 hostname 兜底；识别失败（如改过 hostname）可用 `gl-inet.sh --device be3600|be6500|mt3000` 手动指定，或在菜单提示时手选。

- **机型差异**：arch.conf 源、iStore 安装法、一键流程 quickstart 走法、WAN 防火墙、distfeeds 恢复、自动风扇、overlay 换分区（仅 MT-3000）——均按机型自动切换；BE6500 一键流程沿用其 mdadm 跳过保护。
- **三款通用能力**：argon 主题、iStore、AdGuardHome、wireguard、文件管理器、Docker（dockerman + compose 单一入口）、自定义软件源、quickstart、高级卸载、`g` 快捷命令、脚本自更新。

> ⚠️ **overlay 换分区仅 MT-3000 可用**：BE 系列（BE3600/BE6500）的 GL SDK4 固件 preinit(`80_mount_root`) 写死 `mount_ext4 "systemrw" /overlay`、不读 fstab 的 `config mount 'overlay'`，U 盘 extroot 扩容在 BE 上不生效（机械步骤会跑但重启后 /overlay 仍在内部 flash）。故该菜单项按 profile 仅对 MT-3000 显示。U 盘在 BE 上仍可作数据盘/NAS（GL 原生 `gl_nas_diskmanager`）。

### 菜单 0「一键初始化」

全自动跑除 **6 AdGuard / 9 wireguard / 14 overlay / 15 更新脚本** 外的全部功能项，交互项一律用默认值（12 软件源最先执行，取默认 TUNA 镜像；5 风扇 48℃；7 自动继续；11 空间不足也装）。

**MT-3000 两阶段**：先扩容 overlay 到 U 盘（默认 5GB）→ 自动重启 → 重启后**再点一次菜单 0** 进入阶段 2 安装其余（先扩容腾空间、再把包装进大分区）。靠 `/etc/.glinet_init_overlay_tried` 标记防止重复抹盘。

> ⚠️ **未验证**：overlay 在 MT-3000 固件上是否真正生效**尚未实测**——本两阶段是按「生效」假设写的。若 MT-3000 与 BE 同为 GL SDK4（preinit 写死 `systemrw`），扩容不生效，阶段 1 重启后 `df /overlay` 仍是内部 flash；此时标记会让第二次跑直接进阶段 2（并提示固件可能不支持），不会反复抹 U 盘。需在 MT-3000 真机确认后去除此 caveat。
- **取代**：旧四脚本暂时保留，待 gl-inet.sh 在三款设备上稳定后移除。

### 上传到设备（GL.iNet 固件用 dropbear，无 SFTP）

GL.iNet 固件的 SSH 服务是 **dropbear**，默认**不含 `sftp-server` 子系统**，因此 `scp` / SFTP 客户端直传会失败（TCP 能连上，但 SFTP 子系统协商失败）。三种处理：

- **SSH 管道直传（推荐，免装包）**：`ssh root@<设备> 'cat > /root/gl-inet.sh' < gl-inet.sh`。
- **旧版 SCP 协议**：`scp -O gl-inet.sh root@<设备>:/root/`（`-O` 让 scp 走传统 SCP 协议而非 SFTP）。
- **启用 SFTP**：`opkg update && opkg install openssh-sftp-server`，装完 scp/SFTP 恢复可用。

> 用 `sshpass` 连 dropbear 时建议显式加 `-o PreferredAuthentications=password -o PubkeyAuthentication=no`，否则公钥协商可能干扰密码认证、误报 `Permission denied`。

### 真机验证 checklist（每款设备）

部署后在对应设备上至少验证：

1. 启动后菜单顶部「当前机型」识别正确（错误则用 `--device` 复核子串匹配）。
2. 菜单项 1「一键 iStoreOS 风格化」跑通，8080 端口 luci 生效。
3. 抽测：argon 主题（2）、iStore（3）、quickstart（13）、Docker（11）。
4. MT-3000 额外验证：第 16 项 distfeeds 恢复、overlay 换分区（14，需插 U 盘）。
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
