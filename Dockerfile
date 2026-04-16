# ==========================================
# 第一阶段: Sub-Store 构建层
# 从源码构建 Sub-Store 的前端和后端
# ==========================================
FROM node:alpine AS sub-store-builder

ARG TARGETARCH

RUN apk add --no-cache git curl build-base python3

# --- Shoutrrr ---
ARG SHOUTRRR_VERSION="0.8.0"
# arch=$(arch | sed 's/x86_64/amd64/' | sed 's/aarch64/arm64/' | sed 's/armv7l/armv6/' | sed 's/armv7/armv6/');
RUN set -ex; \
  curl -fsSL --retry 5 --retry-delay 5 "https://github.com/containrrr/shoutrrr/releases/download/v${SHOUTRRR_VERSION}/shoutrrr_linux_${TARGETARCH}.tar.gz" | tar -xzC /tmp/; \
  chmod +x /tmp/shoutrrr; \
  mv /tmp/shoutrrr /usr/local/bin/

# --- Http-Meta ---
WORKDIR /sub-store/http-meta
ARG HTTP_META_VERSION="1.1.0"
RUN set -ex; \
  curl -fsSL --retry 5 --retry-delay 5 "https://github.com/xream/http-meta/releases/download/${HTTP_META_VERSION}/http-meta.bundle.js" -o /sub-store/http-meta.bundle.js; \
  curl -fsSL --retry 5 --retry-delay 5 "https://github.com/xream/http-meta/releases/download/${HTTP_META_VERSION}/tpl.yaml" -o /sub-store/http-meta/tpl.yaml

# --- Mihomo ---
ARG MIHOMO_VERSION="1.19.23"
RUN set -ex; \
  curl -fsSL --retry 5 --retry-delay 5 "https://github.com/MetaCubeX/mihomo/releases/download/v${MIHOMO_VERSION}/mihomo-linux-${TARGETARCH}-v${MIHOMO_VERSION}.gz" | gzip -d > /tmp/http-meta; \
  chmod +x /tmp/http-meta; \
  mv /tmp/http-meta /sub-store/http-meta/

# --- Sub-Store 后端 ---
WORKDIR /sub-store
ARG SUB_STORE_BACKEND_VERSION="2.21.95"
RUN set -ex; \
  curl -fsSL --retry 5 --retry-delay 5 "https://github.com/sub-store-org/Sub-Store/releases/download/${SUB_STORE_BACKEND_VERSION}/sub-store.bundle.js" -o /sub-store/sub-store.bundle.js

# --- Sub-Store 前端 ---
WORKDIR /app/frontend
ARG SUB_STORE_FRONTEND_VERSION="2.16.52"
ENV SUB_STORE_WEBBASEPATH="sub-store"
RUN set -ex; \
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

RUN apk add --no-cache git curl

ARG SUI_VERSION="1.4.1"
RUN set -ex; \
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

RUN apk --no-cache --update add \
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
ENV GOPROXY=https://goproxy.cn,https://proxy.golang.org,direct

WORKDIR /app

# ===== 安装 crypctl =====
# NOTE: crypctl source is in a private repo; fork users should provide their own implementation
RUN set -ex; \
  (git clone --filter=blob:none --no-checkout https://github.com/currycan/key.git /app/key || (sleep 5 && git clone --filter=blob:none --no-checkout https://github.com/currycan/key.git /app/key)); \
  cd /app/key && git checkout HEAD -- docker/crypctl; \
  cd docker/crypctl && go build -ldflags="-s -w" -trimpath -o crypctl main.go; \
  upx --lzma --best crypctl; \
  mv crypctl /usr/local/bin/

# --- Dufs ---
ARG DUFS_VERSION="0.45.0"
RUN set -ex; \
  case "${TARGETARCH}" in \
    amd64)   BINARY_FILE="dufs-v${DUFS_VERSION}-x86_64-unknown-linux-musl.tar.gz";; \
    arm64)   BINARY_FILE="dufs-v${DUFS_VERSION}-arm-unknown-linux-musleabihf.tar.gz";; \
    *)       echo "Unsupported architecture: ${TARGETARCH}"; exit 1 ;; \
  esac; \
  curl -fsSL --retry 5 --retry-delay 5 "https://github.com/sigoden/dufs/releases/download/v${DUFS_VERSION}/${BINARY_FILE}" | tar -xzC /tmp/; \
  upx --lzma --best /tmp/dufs; \
  mv /tmp/dufs /usr/local/bin/

# --- Cloudflared ---
ARG CLOUDFLARED_VERSION="2026.3.0"
RUN set -ex; \
  curl -fsSL --retry 5 --retry-delay 5 "https://github.com/cloudflare/cloudflared/releases/download/${CLOUDFLARED_VERSION}/cloudflared-linux-${TARGETARCH}" -o /tmp/cloudflared; \
  chmod +x /tmp/cloudflared; \
  upx --lzma --best /tmp/cloudflared; \
  mv /tmp/cloudflared /usr/local/bin/

# --- X-UI ---
ARG XUI_VERSION="2.8.11"
RUN set -ex; \
  (git clone --recursive --depth 1 --shallow-submodules --branch v${XUI_VERSION} https://github.com/MHSanaei/3x-ui /app/xui || (sleep 5 && git clone --recursive --depth 1 --shallow-submodules --branch v${XUI_VERSION} https://github.com/MHSanaei/3x-ui /app/xui)); \
  cd /app/xui && go build -ldflags="-s -w" -trimpath -o x-ui main.go; \
  upx --lzma --best x-ui; \
  mv x-ui /usr/local/bin/

# --- S-UI ---
COPY --from=s-ui-front-builder /app/s-ui /app/s-ui
RUN set -ex; \
  cd /app/s-ui; \
  go build -ldflags="-s -w" -trimpath -tags "with_quic,with_grpc,with_utls,with_acme,with_gvisor" -o sui main.go; \
  upx --lzma --best sui; \
  mv sui /usr/local/bin/

# --- Sing-box ---
ARG SING_BOX_VERSION="1.13.8"
RUN set -ex; \
  curl -fsSL --retry 5 --retry-delay 5 "https://github.com/SagerNet/sing-box/releases/download/v${SING_BOX_VERSION}/sing-box-${SING_BOX_VERSION}-linux-${TARGETARCH}.tar.gz" | tar --strip-components=1 -xzC /tmp/; \
  mv /tmp/sing-box /usr/local/bin/

# --- Xray ---
ARG XRAY_VERSION="26.4.13"
RUN set -ex; \
  case "${TARGETARCH}" in \
    amd64)   BINARY_FILE="Xray-linux-64.zip";; \
    arm64)   BINARY_FILE="Xray-linux-arm64-v8a.zip";; \
    *)       echo "Unsupported architecture: ${TARGETARCH}"; exit 1 ;; \
  esac; \
  mkdir -p /tmp/xray; \
  cd /tmp/xray; \
  curl -fsSLO --retry 5 --retry-delay 5 "https://github.com/XTLS/Xray-core/releases/download/v${XRAY_VERSION}/${BINARY_FILE}"; \
  unzip "${BINARY_FILE}"; \
  rm -f "${BINARY_FILE}" geoip.dat geosite.dat; \
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
RUN set -ex; \
  runtime_pkgs="curl bash iproute2 net-tools tzdata bash-completion ca-certificates python3 py3-pip gettext libc6-compat gcompat vim libqrencode-tools jq sqlite nodejs grep sed coreutils dumb-init"; \
  apk add -U --no-cache --virtual .runtime-deps ${runtime_pkgs}; \
  echo -e "[global]\nbreak-system-packages = true" > /etc/pip.conf; \
  pip install --no-cache-dir -U pip supervisor; \
  rm -rf /tmp/*; \
  rm -rf /var/cache/apk/*

# 安装 acme.sh
ENV AUTO_UPGRADE=1
ENV LE_WORKING_DIR=/acme.sh
ENV LE_CONFIG_HOME=/acmecerts
ENV ACMESH_DEBUG=2
ENV PATH=/acme.sh/:$PATH
RUN set -ex && curl -L https://get.acme.sh | sh

# x-ui dependences
RUN set -ex; \
  apk add -U --no-cache fail2ban; \
  rm -f /etc/fail2ban/jail.d/alpine-ssh.conf; \
  cp /etc/fail2ban/jail.conf /etc/fail2ban/jail.local; \
  sed -i "s/^\[ssh\]$/&\nenabled = false/" /etc/fail2ban/jail.local; \
  sed -i "s/^\[sshd\]$/&\nenabled = false/" /etc/fail2ban/jail.local; \
  sed -i "s/#allowipv6 = auto/allowipv6 = auto/g" /etc/fail2ban/fail2ban.conf; \
  rm -rf /var/cache/apk/*; \
  rm -rf /tmp/*

ENV WORKDIR=/sb-xray
ENV LOGDIR=/var/log/
ENV SUPERVISOR_LOG_MAX_BYTES="20MB"

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

# ENTRYPOINT [ "/scripts/entrypoint.sh" ]
ENTRYPOINT ["/usr/bin/dumb-init", "--", "/scripts/entrypoint.sh"]
CMD  [ "supervisord" ]
