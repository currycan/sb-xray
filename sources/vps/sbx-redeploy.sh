#!/usr/bin/env bash
set -euo pipefail

# sbx-redeploy.sh —— sb-xray 运行时更新（重拉 compose + 重建容器 + 清理）
#
# 属「运行 sb-xray」工作流（与 init 解耦），随 vps-init.sh 安装时拉到 ~/sb-xray。
# 用途：把运行中的容器更新到最新 compose + 最新镜像，并清掉可重生成的运行目录
# （让 nginx 等配置按模板重新渲染），保留持久状态（证书 / 面板 DB / sub-store / data）。
#
# 做什么（等价于手动流程）：
#   1. 重新下载 docker-compose.yml（COMPOSE_URL 可覆盖；下载失败保留旧文件）
#   2. docker compose down
#   3. docker compose pull（拉最新镜像）
#   4. 删除可重生成目录：sb-xray/ logs/ nginx/（仅这三个，绝对路径，防误删）
#   5. docker compose up -d
#   6. docker image prune -af（清理悬空旧镜像）
#
# 变量：
#   SBXRAY_DIR   sb-xray 运行目录（默认 ~/sb-xray）
#   COMPOSE_URL  docker-compose.yml 下载源（默认仓库 main 的 raw）
#
# 退出码：0 成功；非 0 任一步骤失败（set -e）。

log()  { printf '[sbx-redeploy] %s\n' "$*"; }
die()  { printf '[sbx-redeploy] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'USAGE'
用法: ./sbx-redeploy.sh [-h|--help]

sb-xray 运行时更新：重拉 docker-compose.yml → down → pull → 清可重生成目录
（sb-xray/ logs/ nginx/）→ up -d → 镜像 prune。持久状态（pki/ acmecerts/ x-ui/
sub-store/ data/ geo/ 等）不动。

变量：SBXRAY_DIR（默认 ~/sb-xray）/ COMPOSE_URL（默认仓库 main 的 raw）。
USAGE
}

case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    "") : ;;
    *) die "未知参数: $1（-h|--help 查看用法）" ;;
esac

SBXRAY_DIR="${SBXRAY_DIR:-$HOME/sb-xray}"
COMPOSE_URL="${COMPOSE_URL:-https://raw.githubusercontent.com/currycan/sb-xray/main/docker-compose.yml}"

# 防误删守卫：SBXRAY_DIR 必须非空、存在、且确是 sb-xray 部署（含 docker-compose.yml）
[ -n "$SBXRAY_DIR" ] || die "SBXRAY_DIR 为空，拒绝运行"
[ -d "$SBXRAY_DIR" ] || die "未找到 sb-xray 目录: $SBXRAY_DIR"
[ -f "$SBXRAY_DIR/docker-compose.yml" ] || die "$SBXRAY_DIR 下无 docker-compose.yml，疑非 sb-xray 部署目录，拒绝运行"
command -v docker >/dev/null 2>&1 || die "未找到 docker"

# docker compose v2 优先，回退 v1
dc() {
    if docker compose version >/dev/null 2>&1; then docker compose "$@"; else docker-compose "$@"; fi
}

cd "$SBXRAY_DIR"

# 1. 重新下载 compose（失败不覆盖旧文件）
log "下载 docker-compose.yml ← $COMPOSE_URL"
if curl -fsSL "$COMPOSE_URL" -o docker-compose.yml.new && [ -s docker-compose.yml.new ]; then
    mv docker-compose.yml.new docker-compose.yml
    log "  docker-compose.yml 已更新"
else
    rm -f docker-compose.yml.new
    log "  下载失败，保留现有 docker-compose.yml"
fi

# 2~3. 停 + 拉最新镜像
log "docker compose down"
dc down
log "docker compose pull"
dc pull

# 4. 仅清可重生成目录（绝对路径，逐个；持久状态目录不动）
log "清理可重生成目录：sb-xray/ logs/ nginx/"
rm -rf "$SBXRAY_DIR/sb-xray" "$SBXRAY_DIR/logs" "$SBXRAY_DIR/nginx"

# 5~6. 起 + 清悬空镜像
log "docker compose up -d"
dc up -d
log "docker image prune -af"
docker image prune -af >/dev/null 2>&1 || true

log "=== 完成：容器已按最新 compose + 镜像重建 ==="
