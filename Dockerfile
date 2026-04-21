# syntax=docker/dockerfile:1.7
# 需要 BuildKit 1.7+ 以支持 --mount=type=cache（build.sh / CI 已使用 docker buildx）
# ==========================================
# 第一阶段: Sub-Store 构建层
# 从源码构建 Sub-Store 的前端和后端
# ==========================================
FROM node:alpine AS sub-store-builder

ARG TARGETARCH

# apk 包缓存挂载：跨构建复用下载，节省 30-60s 冷构建时间
RUN --mount=type=cache,target=/var/cache/apk,sharing=locked \
    apk add git curl build-base python3

# --- Shoutrrr ---
ARG SHOUTRRR_VERSION="0.8.0"
# 完整性：从上游发布的 shoutrrr_${VER}_checksums.txt 校验
RUN set -ex; \
  BINARY_FILE="shoutrrr_linux_${TARGETARCH}.tar.gz"; \
  curl -fsSL --retry 5 --retry-delay 5 -o "/tmp/${BINARY_FILE}" \
    "https://github.com/containrrr/shoutrrr/releases/download/v${SHOUTRRR_VERSION}/${BINARY_FILE}"; \
  curl -fsSL --retry 5 --retry-delay 5 -o "/tmp/shoutrrr_checksums.txt" \
    "https://github.com/containrrr/shoutrrr/releases/download/v${SHOUTRRR_VERSION}/shoutrrr_${SHOUTRRR_VERSION}_checksums.txt"; \
  cd /tmp && grep "  ${BINARY_FILE}\$" shoutrrr_checksums.txt | sha256sum -c -; \
  tar -xzf "/tmp/${BINARY_FILE}" -C /tmp/; \
  chmod +x /tmp/shoutrrr; \
  mv /tmp/shoutrrr /usr/local/bin/; \
  rm -f "/tmp/${BINARY_FILE}" /tmp/shoutrrr_checksums.txt

# --- Http-Meta ---
WORKDIR /sub-store/http-meta
ARG HTTP_META_VERSION="1.1.0"
# 完整性：_SHA256 由 build.sh / CI 从 GitHub API .assets[].digest 填入
ARG HTTP_META_BUNDLE_SHA256=""
ARG HTTP_META_TPL_SHA256=""
RUN set -ex; \
  [ -n "${HTTP_META_BUNDLE_SHA256}" ] || { echo "ERROR: HTTP_META_BUNDLE_SHA256 build-arg required"; exit 1; }; \
  [ -n "${HTTP_META_TPL_SHA256}" ]    || { echo "ERROR: HTTP_META_TPL_SHA256 build-arg required";    exit 1; }; \
  curl -fsSL --retry 5 --retry-delay 5 "https://github.com/xream/http-meta/releases/download/${HTTP_META_VERSION}/http-meta.bundle.js" -o /sub-store/http-meta.bundle.js; \
  echo "${HTTP_META_BUNDLE_SHA256}  /sub-store/http-meta.bundle.js" | sha256sum -c -; \
  curl -fsSL --retry 5 --retry-delay 5 "https://github.com/xream/http-meta/releases/download/${HTTP_META_VERSION}/tpl.yaml" -o /sub-store/http-meta/tpl.yaml; \
  echo "${HTTP_META_TPL_SHA256}  /sub-store/http-meta/tpl.yaml" | sha256sum -c -

# --- Mihomo ---
ARG MIHOMO_VERSION="1.19.23"
ARG MIHOMO_AMD64_SHA256=""
ARG MIHOMO_ARM64_SHA256=""
RUN set -ex; \
  case "${TARGETARCH}" in \
    amd64) EXPECTED_SHA="${MIHOMO_AMD64_SHA256}";; \
    arm64) EXPECTED_SHA="${MIHOMO_ARM64_SHA256}";; \
    *)     echo "Unsupported architecture: ${TARGETARCH}"; exit 1 ;; \
  esac; \
  [ -n "${EXPECTED_SHA}" ] || { echo "ERROR: MIHOMO_$(echo "${TARGETARCH}" | tr a-z A-Z)_SHA256 build-arg required"; exit 1; }; \
  curl -fsSL --retry 5 --retry-delay 5 -o /tmp/mihomo.gz \
    "https://github.com/MetaCubeX/mihomo/releases/download/v${MIHOMO_VERSION}/mihomo-linux-${TARGETARCH}-v${MIHOMO_VERSION}.gz"; \
  echo "${EXPECTED_SHA}  /tmp/mihomo.gz" | sha256sum -c -; \
  gzip -d -c /tmp/mihomo.gz > /tmp/http-meta; \
  rm -f /tmp/mihomo.gz; \
  chmod +x /tmp/http-meta; \
  mv /tmp/http-meta /sub-store/http-meta/

# --- Sub-Store 后端 ---
WORKDIR /sub-store
ARG SUB_STORE_BACKEND_VERSION="2.21.95"
ARG SUB_STORE_BACKEND_SHA256=""
RUN set -ex; \
  [ -n "${SUB_STORE_BACKEND_SHA256}" ] || { echo "ERROR: SUB_STORE_BACKEND_SHA256 build-arg required"; exit 1; }; \
  curl -fsSL --retry 5 --retry-delay 5 "https://github.com/sub-store-org/Sub-Store/releases/download/${SUB_STORE_BACKEND_VERSION}/sub-store.bundle.js" -o /sub-store/sub-store.bundle.js; \
  echo "${SUB_STORE_BACKEND_SHA256}  /sub-store/sub-store.bundle.js" | sha256sum -c -

# --- Sub-Store 前端 ---
WORKDIR /app/frontend
ARG SUB_STORE_FRONTEND_VERSION="2.16.52"
ENV SUB_STORE_WEBBASEPATH="sub-store"
# pnpm store 缓存挂载 → 跨构建复用 npm 依赖下载
RUN --mount=type=cache,target=/root/.local/share/pnpm/store \
    --mount=type=cache,target=/root/.npm,sharing=locked \
  set -ex; \
  (git clone --depth 1 --branch ${SUB_STORE_FRONTEND_VERSION} https://github.com/sub-store-org/Sub-Store-Front-End /app/frontend || (sleep 5 && git clone --depth 1 --branch ${SUB_STORE_FRONTEND_VERSION} https://github.com/sub-store-org/Sub-Store-Front-End /app/frontend)); \
  npm install -g pnpm; \
  pnpm install --frozen-lockfile; \
  VITE_PUBLIC_PATH="/${SUB_STORE_WEBBASEPATH}/" pnpm run build; \
  mv /app/frontend/dist /sub-store/frontend

# ==========================================
# 第二阶段: S-UI 前端构建层
# 构建 S-UI 的前端静态资源
# ==========================================
FROM node:alpine AS s-ui-front-builder

RUN --mount=type=cache,target=/var/cache/apk,sharing=locked \
    apk add git curl

ARG SUI_VERSION="1.4.1"
# npm 全局缓存挂载
RUN --mount=type=cache,target=/root/.npm,sharing=locked \
  set -ex; \
  (git clone --depth 1 --branch v${SUI_VERSION} https://github.com/alireza0/s-ui /app/s-ui || (sleep 5 && git clone --depth 1 --branch v${SUI_VERSION} https://github.com/alireza0/s-ui /app/s-ui)); \
  (git clone --depth 1 --branch main https://github.com/alireza0/s-ui-frontend /app/s-ui-frontend || (sleep 5 && git clone --depth 1 --branch main https://github.com/alireza0/s-ui-frontend /app/s-ui-frontend)); \
  cd /app/s-ui-frontend && npm install && npm run build; \
  mv /app/s-ui-frontend/dist /app/s-ui/web/html

# ==========================================
# 第三阶段: 主构建层 (Golang)
# 构建 Go 语言二进制文件 (x-ui, s-ui 后端, crypctl) 并安装其他工具
# ==========================================
FROM golang:1-alpine AS builder

ARG TARGETARCH

RUN --mount=type=cache,target=/var/cache/apk,sharing=locked \
    apk -U add \
      ca-certificates \
      build-base \
      upx \
      curl \
      git \
      gcc \
      unzip; \
    update-ca-certificates

ENV CGO_ENABLED=1
ENV CGO_CFLAGS="-D_LARGEFILE64_SOURCE"
ENV GOTOOLCHAIN=auto
# 默认 goproxy.cn 优先（国内构建友好）；CI 可用 --build-arg GOPROXY=... 覆盖为 proxy.golang.org
ARG GOPROXY="https://goproxy.cn,https://proxy.golang.org,direct"
ENV GOPROXY=${GOPROXY}

WORKDIR /app

# ===== 安装 crypctl =====
# NOTE: crypctl source is in a public repo (currycan/key/docker/crypctl)
# Go 构建/模块缓存挂载 → 跨构建复用
RUN --mount=type=cache,target=/root/.cache/go-build \
    --mount=type=cache,target=/go/pkg/mod \
  set -ex; \
  (git clone --filter=blob:none --no-checkout https://github.com/currycan/key.git /app/key || (sleep 5 && git clone --filter=blob:none --no-checkout https://github.com/currycan/key.git /app/key)); \
  cd /app/key && git checkout HEAD -- docker/crypctl; \
  cd docker/crypctl && go build -ldflags="-s -w" -trimpath -o crypctl main.go; \
  upx --lzma --best crypctl; \
  mv crypctl /usr/local/bin/

# --- Dufs ---
ARG DUFS_VERSION="0.45.0"
ARG DUFS_AMD64_SHA256=""
ARG DUFS_ARM64_SHA256=""
RUN set -ex; \
  case "${TARGETARCH}" in \
    amd64)   BINARY_FILE="dufs-v${DUFS_VERSION}-x86_64-unknown-linux-musl.tar.gz"; EXPECTED_SHA="${DUFS_AMD64_SHA256}";; \
    arm64)   BINARY_FILE="dufs-v${DUFS_VERSION}-arm-unknown-linux-musleabihf.tar.gz"; EXPECTED_SHA="${DUFS_ARM64_SHA256}";; \
    *)       echo "Unsupported architecture: ${TARGETARCH}"; exit 1 ;; \
  esac; \
  [ -n "${EXPECTED_SHA}" ] || { echo "ERROR: DUFS_$(echo "${TARGETARCH}" | tr a-z A-Z)_SHA256 build-arg required"; exit 1; }; \
  curl -fsSL --retry 5 --retry-delay 5 -o "/tmp/${BINARY_FILE}" \
    "https://github.com/sigoden/dufs/releases/download/v${DUFS_VERSION}/${BINARY_FILE}"; \
  echo "${EXPECTED_SHA}  /tmp/${BINARY_FILE}" | sha256sum -c -; \
  tar -xzf "/tmp/${BINARY_FILE}" -C /tmp/; \
  rm -f "/tmp/${BINARY_FILE}"; \
  upx --lzma --best /tmp/dufs; \
  mv /tmp/dufs /usr/local/bin/

# --- Cloudflared ---
ARG CLOUDFLARED_VERSION="2026.3.0"
ARG CLOUDFLARED_AMD64_SHA256=""
ARG CLOUDFLARED_ARM64_SHA256=""
RUN set -ex; \
  case "${TARGETARCH}" in \
    amd64) EXPECTED_SHA="${CLOUDFLARED_AMD64_SHA256}";; \
    arm64) EXPECTED_SHA="${CLOUDFLARED_ARM64_SHA256}";; \
    *)     echo "Unsupported architecture: ${TARGETARCH}"; exit 1 ;; \
  esac; \
  [ -n "${EXPECTED_SHA}" ] || { echo "ERROR: CLOUDFLARED_$(echo "${TARGETARCH}" | tr a-z A-Z)_SHA256 build-arg required"; exit 1; }; \
  curl -fsSL --retry 5 --retry-delay 5 "https://github.com/cloudflare/cloudflared/releases/download/${CLOUDFLARED_VERSION}/cloudflared-linux-${TARGETARCH}" -o /tmp/cloudflared; \
  echo "${EXPECTED_SHA}  /tmp/cloudflared" | sha256sum -c -; \
  chmod +x /tmp/cloudflared; \
  upx --lzma --best /tmp/cloudflared; \
  mv /tmp/cloudflared /usr/local/bin/

# --- X-UI ---
ARG XUI_VERSION="2.8.11"
RUN --mount=type=cache,target=/root/.cache/go-build \
    --mount=type=cache,target=/go/pkg/mod \
  set -ex; \
  (git clone --recursive --depth 1 --shallow-submodules --branch v${XUI_VERSION} https://github.com/MHSanaei/3x-ui /app/xui || (sleep 5 && git clone --recursive --depth 1 --shallow-submodules --branch v${XUI_VERSION} https://github.com/MHSanaei/3x-ui /app/xui)); \
  cd /app/xui && go build -ldflags="-s -w" -trimpath -o x-ui main.go; \
  upx --lzma --best x-ui; \
  mv x-ui /usr/local/bin/

# --- S-UI ---
COPY --from=s-ui-front-builder /app/s-ui /app/s-ui
RUN --mount=type=cache,target=/root/.cache/go-build \
    --mount=type=cache,target=/go/pkg/mod \
  set -ex; \
  cd /app/s-ui; \
  go build -ldflags="-s -w" -trimpath -tags "with_quic,with_grpc,with_utls,with_acme,with_gvisor" -o sui main.go; \
  upx --lzma --best sui; \
  mv sui /usr/local/bin/

# --- Sing-box ---
ARG SING_BOX_VERSION="1.13.8"
ARG SING_BOX_AMD64_SHA256=""
ARG SING_BOX_ARM64_SHA256=""
RUN set -ex; \
  case "${TARGETARCH}" in \
    amd64) EXPECTED_SHA="${SING_BOX_AMD64_SHA256}";; \
    arm64) EXPECTED_SHA="${SING_BOX_ARM64_SHA256}";; \
    *)     echo "Unsupported architecture: ${TARGETARCH}"; exit 1 ;; \
  esac; \
  [ -n "${EXPECTED_SHA}" ] || { echo "ERROR: SING_BOX_$(echo "${TARGETARCH}" | tr a-z A-Z)_SHA256 build-arg required"; exit 1; }; \
  BINARY_FILE="sing-box-${SING_BOX_VERSION}-linux-${TARGETARCH}.tar.gz"; \
  curl -fsSL --retry 5 --retry-delay 5 -o "/tmp/${BINARY_FILE}" \
    "https://github.com/SagerNet/sing-box/releases/download/v${SING_BOX_VERSION}/${BINARY_FILE}"; \
  echo "${EXPECTED_SHA}  /tmp/${BINARY_FILE}" | sha256sum -c -; \
  tar --strip-components=1 -xzf "/tmp/${BINARY_FILE}" -C /tmp/; \
  rm -f "/tmp/${BINARY_FILE}"; \
  mv /tmp/sing-box /usr/local/bin/

# --- Xray ---
ARG XRAY_VERSION="26.4.13"
# 完整性：从上游发布的 ${BINARY_FILE}.dgst 文件中提取 SHA2-256 字段校验
RUN set -ex; \
  case "${TARGETARCH}" in \
    amd64)   BINARY_FILE="Xray-linux-64.zip";; \
    arm64)   BINARY_FILE="Xray-linux-arm64-v8a.zip";; \
    *)       echo "Unsupported architecture: ${TARGETARCH}"; exit 1 ;; \
  esac; \
  mkdir -p /tmp/xray; \
  cd /tmp/xray; \
  curl -fsSLO --retry 5 --retry-delay 5 "https://github.com/XTLS/Xray-core/releases/download/v${XRAY_VERSION}/${BINARY_FILE}"; \
  curl -fsSL --retry 5 --retry-delay 5 -o "${BINARY_FILE}.dgst" \
    "https://github.com/XTLS/Xray-core/releases/download/v${XRAY_VERSION}/${BINARY_FILE}.dgst"; \
  EXPECTED_SHA=$(awk -F'= ' '/^SHA2-256/ {print $2}' "${BINARY_FILE}.dgst"); \
  [ -n "${EXPECTED_SHA}" ] || { echo "ERROR: Failed to extract SHA256 from ${BINARY_FILE}.dgst"; exit 1; }; \
  echo "${EXPECTED_SHA}  ${BINARY_FILE}" | sha256sum -c -; \
  unzip "${BINARY_FILE}"; \
  rm -f "${BINARY_FILE}" "${BINARY_FILE}.dgst" geoip.dat geosite.dat; \
  upx --lzma --best xray; \
  mkdir -p /usr/local/bin/bin; \
  mv xray /usr/local/bin/bin/xray-linux-${TARGETARCH}; \
  ln -sf /usr/local/bin/bin/xray* /usr/local/bin/xray;

# ==========================================
# 第四阶段: 最终镜像层
# 基础镜像: currycan/nginx:1.29.4
# 合并所有构建产物和运行时环境
# ==========================================
FROM docker.io/currycan/nginx:1.29.4

# Create a non-root user and group
RUN addgroup -S appgroup && adduser -S appuser -G appgroup

# 安装基础组件
# apk/pip 均走 cache mount；--virtual 仍保留便于日后统一 apk del
RUN --mount=type=cache,target=/var/cache/apk,sharing=locked \
    --mount=type=cache,target=/root/.cache/pip,sharing=locked \
  set -ex; \
  runtime_pkgs="curl bash iproute2 net-tools tzdata bash-completion ca-certificates python3 py3-pip py3-jinja2 py3-httpx py3-yaml py3-pydantic gettext libc6-compat gcompat vim libqrencode-tools jq sqlite nodejs grep sed coreutils dumb-init"; \
  apk -U add --virtual .runtime-deps ${runtime_pkgs}; \
  echo -e "[global]\nbreak-system-packages = true" > /etc/pip.conf; \
  pip install -U pip supervisor; \
  rm -rf /tmp/*

# 安装 acme.sh
ENV AUTO_UPGRADE=1
ENV LE_WORKING_DIR=/acme.sh
ENV LE_CONFIG_HOME=/acmecerts
ENV ACMESH_DEBUG=2
ENV PATH=/acme.sh/:$PATH
RUN set -ex && curl -L https://get.acme.sh | sh

# x-ui dependences
RUN --mount=type=cache,target=/var/cache/apk,sharing=locked \
  set -ex; \
  apk -U add fail2ban; \
  rm -f /etc/fail2ban/jail.d/alpine-ssh.conf; \
  cp /etc/fail2ban/jail.conf /etc/fail2ban/jail.local; \
  sed -i "s/^\[ssh\]$/&\nenabled = false/" /etc/fail2ban/jail.local; \
  sed -i "s/^\[sshd\]$/&\nenabled = false/" /etc/fail2ban/jail.local; \
  sed -i "s/#allowipv6 = auto/allowipv6 = auto/g" /etc/fail2ban/fail2ban.conf; \
  rm -rf /tmp/*

ENV WORKDIR=/sb-xray
ENV LOGDIR=/var/log/
ENV SUPERVISOR_LOG_MAX_BYTES="20MB"
# xray 与 sing-box 共用日志级别：debug | info | warning | error
ENV LOG_LEVEL="warning"

# shoutrrr-forwarder 事件总线：
#   SHOUTRRR_URLS 为空时 dry-run（仅日志，不推送），这是默认安全状态
#   接收 xray rules.webhook 的 POST 并转发给 shoutrrr CLI
ENV SHOUTRRR_URLS=""
ENV SHOUTRRR_FORWARDER_PORT="18085"
ENV SHOUTRRR_TITLE_PREFIX="[sb-xray]"

# VLESS Reverse Proxy（M3，默认关闭）
# ENABLE_REVERSE=true 时 entrypoint 往 REALITY 入站追加 reverse client UUID，
# 并按 REVERSE_DOMAINS（逗号分隔，例如 "domain:home.lan,domain:nas.lan"）生成 routing 规则
# 落地机（bridge）配置：见 templates/xray/reverse_bridge_client.json
ENV ENABLE_REVERSE="false"
ENV REVERSE_DOMAINS=""

# M4 新入站 feature flag（Xray v26.4.17）
#   Hy2 / XHTTP-H3 已永久启用（无开关，见 templates/xray/04_hy2_inbounds.json 与 02_xhttp_h3_inbounds.json）
#   ENABLE_XICMP     ：ICMP echo 承载代理的紧急通道（仅极端封锁场景；需要 cap_add=NET_RAW）
#   ENABLE_XDNS      ：DNS 查询载荷承载代理的紧急通道（仅极端封锁场景；需要用户控制的 NS 域名 XDNS_DOMAIN）
#   ENABLE_ECH       ：TLS ECH 占位开关；M4-5 TLS 层接入尚未实现，置 true 暂无效果
ENV ENABLE_XICMP="false"
ENV ENABLE_XDNS="false"
ENV ENABLE_ECH="false"
ENV PORT_XHTTP_H3="4443"
ENV PORT_XICMP_ID="12345"
ENV PORT_XDNS="5353"
ENV XDNS_DOMAIN=""

WORKDIR ${WORKDIR}

COPY --from=builder --chmod=755 /usr/local/bin/ /usr/local/bin/
COPY --from=sub-store-builder --chmod=755 /usr/local/bin/ /usr/local/bin/
COPY --from=sub-store-builder /sub-store/ /sub-store/
COPY --chmod=755 scripts /scripts
COPY templates/ /templates/
COPY sources /sources
# NOTE: vimrc from private repo; optional, remove if not available
ADD https://raw.githubusercontent.com/currycan/key/master/vimrc /root/.vimrc

# time zone
ENV TZ="Asia/Singapore"

# xray
ENV DEST_HOST="www.microsoft.com"
ENV DOMAIN=""
ENV CDNDOMAIN=""
ENV LISTENING_PORT="443"
ENV PORT_HYSTERIA2="6443"
ENV PORT_TUIC="8443"
ENV PORT_ANYTLS="4433"

# acme.sh
ENV ACMESH_REGISTER_EMAIL=""
# zerossl/google
ENV ACMESH_SERVER_NAME="zerossl"
# certs path
ENV SSL_PATH=/pki

# 节点特性后缀
ENV NODE_SUFFIX=""

# ISP proxy
ENV DEFAULT_ISP="LA_ISP"

# AI 服务路由配置
# Gemini 访问策略: false=使用代理(推荐), true=强制直连(仅在验证可用后设置)
ENV GEMINI_DIRECT=""

# 新增provider
ENV PROVIDERS=""

# x-ui
ENV XUI_LOG_LEVEL="info"
ENV XUI_DEBUG="false"
ENV XUI_LOG_FOLDER="/x-ui/db"
ENV XUI_WEBBASEPATH="xui"
ENV XUI_ACCOUNT="admin"
ENV XUI_PORT="8888"

# s-ui
ENV SUI_LOG_LEVEL="info"
ENV SUI_DEBUG="false"
ENV SUI_PORT="3095"
ENV SUI_SUB_PORT="3096"
ENV SUI_DB_FOLDER="/s-ui/db"
ENV SUI_WEBBASEPATH="sui"
ENV SUI_SUB_PATH="sub"

# sub-store
ENV SUB_STORE_META_FOLDER="/sub-store/http-meta/"
ENV SUB_STORE_DOCKER=true
ENV SUB_STORE_FRONTEND_PATH="/sub-store/frontend/"
ENV SUB_STORE_DATA_BASE_PATH="/sub-store/data"
ENV SUB_STORE_BACKEND_API_PORT="3000"
ENV SUB_STORE_BACKEND_API_HOST="127.0.0.1"
ENV SUB_STORE_FRONTEND_PORT="3001"
ENV SUB_STORE_FRONTEND_HOST="127.0.0.1"
ENV SUB_STORE_WEBBASEPATH="sub-store"
ENV SUB_STORE_FRONTEND_BACKEND_PATH=""
ENV SUB_STORE_BACKEND_SYNC_CRON="55 23 * * *"

# dufs
ENV DUFS_SERVE_PATH="/data"
ENV DUFS_PORT=""
ENV DUFS_BIND="0.0.0.0"
ENV DUFS_PATH_PREFIX="/dufs"
ENV DUFS_ALLOW_ALL="false"
ENV DUFS_ALLOW_UPLOAD="true"
ENV DUFS_ALLOW_DELETE="true"
ENV DUFS_ALLOW_SEARCH="true"
ENV DUFS_ALLOW_SYMLINK="true"
ENV DUFS_ALLOW_ARCHIVE="true"
ENV DUFS_ENABLE_CORS="true"
ENV DUFS_RENDER_INDEX="true"
ENV DUFS_RENDER_TRY_INDEX="true"
ENV DUFS_RENDER_SPA="true"
ENV DUFS_LOG_FORMAT=""
ENV DUFS_COMPRESS="low"

HEALTHCHECK --interval=30s --timeout=30s --start-period=15s --retries=3 \
  CMD supervisorctl status xray | grep -q 'RUNNING' || exit 1

EXPOSE 80 443

VOLUME ${DUFS_SERVE_PATH} ${WORKDIR} ${SUB_STORE_DATA_BASE_PATH} ${LOGDIR} /etc/nginx/conf.d /etc/nginx/stream.d /etc/nginx/dhparam

STOPSIGNAL SIGTERM

# Python entrypoint (Phase 5 switch). Legacy /scripts/entrypoint.sh
# is still invoked internally for un-migrated config-render stages.
ENTRYPOINT ["/usr/bin/dumb-init", "--", "python3", "/scripts/entrypoint.py", "run"]
CMD  [ "supervisord" ]
