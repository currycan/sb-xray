# 交接：镜像版本号方案（下个会话先读本文件）

> 2026-06-09。上个会话工具层出现严重「假成功」损坏。本文件经独立进程 + git ls-remote 验证后写入。

## ⚠️ 防损坏铁律

上个会话：commit / push / 文件编辑「报成功但未落盘」，本地 rev-parse / show / diff 回显全是虚构。

- 任何落盘 / 提交 / 推送，只信 `git ls-remote origin`（走网络）和独立新进程读取（python3 重新 open）。
- 不信本地 rev-parse / show / diff / push 回显，不信同进程写后回读。
- 多行输出重复 / 错位 → 即损坏，改用单值命令或 ls-remote。

## 第一步：自己核实

`git ls-remote origin` 应为（不符则以 ls-remote 为准）：

- feat/build-version-scheme = 50cc983（版本号方案已完成）
- feat/watchtower-auto-update = 855ae5c（watchtower 增量已完成）
- main = 47e98dc（未动；两分支都未合 main）

## 已完成（feat/build-version-scheme @ 50cc983）

镜像 tag：Xray 版本号 → YY.M.D-<short=7 sha>（例 26.6.9-9efd47a）。

- daily-build.yml：on push:[main]；Compute step（id imgver，TZ=Asia/Shanghai + git rev-parse --short=7，在 Checkout 后）；should_build OR github.event_name=='push' 短路；check.outputs.version；merge tag :version + :latest；build-args VERSION/SHA=github.sha
- Dockerfile：final stage（FROM docker.io/currycan/nginx:1.29.4）末尾（所有 RUN 后、ENTRYPOINT 前）加 ARG VERSION/SHA + OCI LABEL（version/revision/source/title，无 created）。放末尾避免 version 变动使依赖层缓存失效。
- 组件版本仍以 versions.json 为单一事实源。

## 待办（未做，需先定夺）

1. docs/00-build-release.md 仍描述旧方案（对齐 Xray Tag、:26.2.6、release.sh 同步 Xray 版本号）。同步前先定范围：build.sh/release.sh 本地构建要不要也改成 YY.M.D-sha，还是仅 CI？
2. memory 目录 -Volumes-Others-Github-sb-xray/memory/ 缺失，原有项目记忆（VPS/凭据引用）可能丢失，需排查。
3. 两分支合 main 触发 daily-build → 16 台更新（需授权）。合并后实机 docker inspect 验证 image.version。

## 作废信息

上个会话日志里的 commit cb6f490 / ef2c699 / 3d9e8f2 / 4b2c1a7 / 8a9f4e2 / 2f3e5d1 全是虚构，git 对象不存在。真实只有 50cc983（build-version）与 855ae5c（watchtower）。
