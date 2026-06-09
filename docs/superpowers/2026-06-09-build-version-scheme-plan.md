# 镜像版本号方案 — 实现交接

- 日期: 2026-06-09
- 分支: `feat/build-version-scheme`（已从 `origin/main` @ 47e98dc 干净建好，零其它改动）
- 状态: 已设计/已决策，**待实现**（本 session 工具读取出现损坏，CI 改动需可靠读 `daily-build.yml`，故推迟到新 session）

## 1. 要解决的问题

| 问题 | 现状 | 后果 |
|------|------|------|
| 版本号不可区分 | docker tag = `versions.json` 的 `xray` 字段（`daily-build.yml:393`：`-t IMAGE:${{ needs.check.outputs.xray }}`），xray 锁死 `26.3.27` | 每次构建同名，改了自己代码也看不出生效 |
| 借 xray 版号 | 镜像 90% 是 sb-xray 自己的代码，却挂 xray 的版号 | 名不副实、不伦不类 |
| push 不触发构建 | `on:` 只有 `schedule`(cron 每天 UTC 02:00) + `workflow_dispatch`，**无 `on: push`** | 提交代码到 main 后不会自动构建 |

## 2. 已与用户确认的决策

- **镜像 tag 格式：`YY.M.D-<short-sha>`**，例 `26.6.9-47ba786`
  - 日期（北京时区）看新旧；git short sha 精确对应 commit，代码一变 sha 就变，一眼看出这次构建有没有生效。
- **加 `on: push` 到 main → 提交即构建**（覆盖 `:latest` + 打新版本 tag）。
- xray 等组件版本**降级为镜像 OCI label**（`docker inspect` 可见），`versions.json` 仍是组件版本单一事实源，只是不再当 docker tag。
- `:latest` 保留（watchtower 用）。

## 3. 实现清单（6 处）

**`.github/workflows/daily-build.yml`**
1. `on:` 增加 `push: { branches: [main] }`。
2. `check` job 新增输出 `version`：`YY.M.D-<short-sha>`。
   - 日期用北京时区：`TZ=Asia/Shanghai date +%y.%-m.%-d`（注意 runner 默认 UTC）。
   - short sha：`git rev-parse --short HEAD`（actions/checkout 默认浅克隆够用，short sha 可得；本方案不用 `git describe`，无需 `fetch-depth: 0`）。
3. `check` job 的 `should_build`：**push 或 force_build → 必构建**；cron → 维持现状（组件版本/digest 变才构建，避免每天空跑）。需读现有 should_build 计算逻辑后在其上加 `github.event_name == 'push'` 短路。
4. `merge` job 打 tag（约 `:393`）：`:${{ needs.check.outputs.xray }}` → `:${{ needs.check.outputs.version }}`，`:latest` 保留。

**`Dockerfile`**
5. 加 OCI label，把组件版本从「tag」降级为「可查元数据」：
   - `org.opencontainers.image.version=<日期+sha>`
   - `org.opencontainers.image.revision=<git sha>`
   - `org.opencontainers.image.created=<构建时间>`
   - 自定义 label 记组件版本，如 `org.sb-xray.component.xray=26.3.27` / `.sing_box=1.13.13`（值经 build-arg 传入）。
6. `daily-build.yml` 的 build/bake 步骤把 `version` / `sha` / 组件版本经 `build-args` 传给 Dockerfile。

## 4. 影响与注意

- **push 到 main 会立即构建并覆盖 `:latest`** → 16 台节点 next 03:00/04:00（watchtower）自动更新到刚提交的代码。意味着 **main 必须保持可发布**，别 push 半成品。
- 频繁 push 多次跑 CI；已有 `concurrency: { group: daily-build, cancel-in-progress: true }`，会取消进行中的旧构建。
- 这套版本号可以喂给 watchtower 通知（`feat/watchtower-auto-update` 的 `sbx-canary-check`）：让中文卡片的「镜像构建」行直接显示 `26.6.9-47ba786`，自动更新通知一眼看到更新到哪个版本。两分支合并后可做这个串联。

## 5. 本 session 已完成的前置

- 诊断清楚版本号来源（`daily-build.yml:393` ← `versions.json.xray`）与触发条件（无 `on: push`）。
- 确认 `versions.json` 是组件版本 + digest 的事实源（项目根目录）。
- 分支 `feat/build-version-scheme` 已干净就绪。

## 6. ⚠ 给下一个 session 的提醒

- 本 session 后期**工具读取输出出现损坏**（Read/awk 返回错乱内容），CI 改动被推迟。新 session 先用简单命令验证工具读取正常（如 `git rev-parse --short HEAD` 对照预期），再读 `daily-build.yml` 实施。
- 实施前务必**真实读取** `daily-build.yml` 的 `check` job（should_build 计算）和 `merge` job（tag 行）当前内容，不要凭本文档记忆的行号硬改——行号是近似值。
- 改完在分支上手动 `workflow_dispatch` 跑一次（或 push 一个无害提交）验证：tag 是否变成 `YY.M.D-sha`、label 是否注入、push 是否触发。**确认无误再合并 main**（合并即对 16 台生效）。
