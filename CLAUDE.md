# CLAUDE.md

## 1. Verified, Not Imagined

**Everything you state must be grounded in evidence you actually checked — never in assumption.**

- Separate what you observed from what you inferred. Assert only what you verified.
- Before stating something as true, confirm it: run the command, read the file, check the source.
- If you can't verify it yet, say so — mark it unverified rather than asserting it.
- When new evidence contradicts an earlier claim, retract it explicitly in the same turn.

The test: for every claim, could you point to what you checked to back it? If not, verify it or don't assert it.

---

## 2. Watchtower 自动更新发布纪律（漂移缓解契约）

生产节点经 watchtower 自动跟进已发布镜像的 `:latest` 标签。watchtower **不读 docker-compose.yml**——它从现有容器 inspect 出已实例化的 env 重建新镜像。因此在运维 `git pull` 同步 compose 之前，新发布引入的 compose env 不会生效。两条硬约束：

- **(a) 新增 env 必须镜像内默认兜底。** 凡新增 `docker-compose.yml` 的 env，必须在 `scripts/entrypoint.py` / `scripts/sb_xray` 内有对应 `os.environ.get(key, 合理默认)`，且默认值向后兼容。保证 watchtower 用旧 env 集重建新镜像时不崩，新功能暂用镜像内默认值直至运维 `git pull` 同步。
- **(b) 修复必须镜像内默认生效。** 任何修复/安全类变更必须落在镜像内默认行为里，不得以「运维设新 compose env」为前提。若某发布确实必须靠新 env 才能正确运行，在发布说明标记 `requires-compose-sync`——**该发布不走 watchtower 自动分发**，强制全量 `git pull && docker compose up -d`。否则修复镜像被自动拉下却因缺 env 不生效，造成虚假安全感。

---

## 3. superpowers 开发过程文件归置

所有 superpowers 工作流产物（设计 spec、实现 plan、brainstorm、会话 summary/handoff 等过程文件）一律放在项目根 `.superpowers/` 下，按类型归子目录：`specs/`（设计文档）、`plans/`（计划、总结、交接）、`brainstorm/`（头脑风暴 session）。

- **不要放进 `docs/`。** `docs/` 只存编号的正式项目文档（`00–09`，house style 见 project-docs 规范）；superpowers 是开发过程文件，性质不同。
- **不入库。** `.superpowers/` 已在 `.gitignore`，属本地开发过程文件，**不提交到代码仓库**。
- 入库文件（如 `docker-compose.yml`、`sources/vps/*`）若需引用 superpowers 设计文档，注意该路径在 clone 出的仓库中不存在——仅作本地开发参考。

---

## 4. 不写入环境特定信息

本文件及任何入库文档只描述**项目级、可移植的约束与约定**，不写入部署环境或个人/组织特定信息——节点数量、主机名/域名、IP、账号名、凭据、服务商、规模数字等一律不得出现。这类信息属运维配置，留在 `.credentials/`、私有运维手册或不入库的部署清单中。

约束须以**机制与不变量**表述，而非以**当前部署现状**表述：写「生产节点经 watchtower 自动跟进 `:latest`」，不写「N 台 VPS……」。
