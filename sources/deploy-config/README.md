# deploy-config —— 部署配置生成器

`gen_deploy_config.py` 把 sb-xray 的部署配置从**单一真相源确定性生成**,取代手工拼装 `.env` / `config.env` / `nodes.list`。它是独立 CLI 工具:人、CI、agent 都能直接调,不依赖任何 skill。

> 设计原则:能用确定性代码做的(生成配置)就写成可测试的脚本,别靠每次临场拼装(易漂移)。脚本**只产配置、不执行任何写操作**;真正落地复用现有幂等 init 脚本(`../vps/vps-cn-exit-init.sh`、`../openwrt/openwrt-init.sh`,它们已 compare-then-write)。

## 真相源

读仓库根 `.credentials/`(已 gitignore,本地私有,**永不入库**):

| 文件 | 角色 |
|------|------|
| `node.list` | VPS 节点清单,每行 `<名> <FQDN> <token>` |
| `openwrt-config.env` | OpenWrt 回国出口配置权威版本(KEY=VALUE) |

canary 节点名取 `.ops/operator.conf` 的 `SBX_CANARY_NODE`(也可 `--canary` 覆盖)。

## 用法

```bash
# 生成全部 → .ops/generated/（gitignore）
python3 sources/deploy-config/gen_deploy_config.py gen

# 只生成一台 VPS 的 .env
python3 sources/deploy-config/gen_deploy_config.py gen --target vps --node <名>

# 只生成 OpenWrt 那套（config.env + nodes.list）
python3 sources/deploy-config/gen_deploy_config.py gen --target openwrt

# 打到屏幕不写文件（注意 openwrt/nodes.list 含 token，谨慎）
python3 sources/deploy-config/gen_deploy_config.py gen --target vps --node <名> --stdout

# 漂移检测：先把设备现配置拉到本地，再与「期望」比对（只读）
ssh <连接参数> '<node>' 'cat /root/sb-xray/.env' > /tmp/<名>.actual.env
python3 sources/deploy-config/gen_deploy_config.py diff --node <名> --actual /tmp/<名>.actual.env
```

**全局参数**:`--credentials-dir`(默认仓库根 `.credentials/`)、`--operator-conf`、`--canary`。
**退出码**:`gen`/`diff` 成功 `0`;`diff` 有漂移 `1`;真相源缺必需键 `2`(不带病下发,先补真相源再重生成)。

## 产出物（落 `.ops/generated/`）

```
vps/<node>.env       # CN_EXIT_MODE / ENABLE_REVERSE / ENABLE_SOCKS5_PROXY / tsip / domain
                     # canary 节点额外带 WATCHTOWER_SCHEDULE
openwrt/config.env   # 校验后的设备 config.env（缺必需键即报错）
openwrt/nodes.list   # 去注释的节点清单（设备上的复数名）
```

`tsip`(socks5 腿回国出口)取真相源里 OpenWrt 的固定 Tailscale IP 键;`domain` 取各节点 FQDN。

## 同步流程（真相源 / init 脚本 / compose 更新后）

```
① 改 .credentials/ 真相源
② 重新生成：gen ...
③ diff 设备实际（只读，逐节点拉 .env 比对）→ 列出漂移
④ 有漂移 → 推送 + 重跑幂等 init（写操作）
⑤ 验证（健康 + 回国 + 关键状态）→ 留痕
```

要靠新 compose env 才正确运行的发布属 `requires-compose-sync`:不走 watchtower 自动分发,必须全量 `git pull` + 同步 compose + 重生成 `.env` + 重跑 init(见项目纪律 §2)。

## 测试

```bash
python3 -m pytest tests/test_gen_deploy_config.py -q   # 单元 + CLI 端到端
ruff check sources/deploy-config/gen_deploy_config.py
mypy --strict sources/deploy-config/gen_deploy_config.py
```
`tests/conftest.py` 把本目录加入导入路径,故测试能 `import gen_deploy_config`。

## 与 project-operator 的关系

本脚本是**机制**;`project-operator` skill(本地,gitignore)是**编排者** —— 它告诉 agent 何时生成、怎么 diff、推哪些节点要不要确认、全程留痕。脚本离开 skill 照样独立可用;skill 只是众多调用者之一。
