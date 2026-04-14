# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SB-Xray is a Docker-based proxy platform combining dual-core engines (Xray + Sing-box) with Nginx gateway, management panels (X-UI, S-UI), subscription management (Sub-Store), and file serving (Dufs). All components run as supervised processes in a single container.

## Build & Test Commands

```bash
# Build Docker image (fetches latest versions from GitHub, builds multi-arch)
./build.sh

# Build with pinned default versions (no GitHub API calls)
USE_DEFAULT_VERSIONS=true ./build.sh

# Run container
docker-compose up -d

# Run entrypoint unit tests (bash-based, red/green assertions)
bash scripts/test_entrypoint.sh

# Create a release (tags git, creates GitHub release)
./release.sh
```

## Architecture

### Build Pipeline (Dockerfile — 4 stages)

1. **node:alpine** — Builds Sub-Store frontend/backend, downloads Http-Meta + Mihomo + Shoutrrr
2. **node:alpine** — Clones and builds S-UI frontend
3. **golang:1-alpine** — Compiles Go binaries: xray, sing-box, x-ui, s-ui, dufs, cloudflared, crypctl
4. **currycan/nginx** (runtime) — Copies all artifacts, entrypoint hands off to supervisord

`build.sh` auto-fetches latest GitHub release versions and syncs them into Dockerfile ARG defaults.

### Runtime Initialization (entrypoint.sh — 16 stages)

The entrypoint is a ~1400-line bash script that runs sequentially at container start:

- **Stages 1-4**: Directory init, env analysis, secrets decryption, template rendering (envsubst + jq validation)
- **Stages 5-7**: ISP speed testing, routing logic (auto-select best ISP pool), TCP Brutal detection
- **Stages 8-11**: Streaming service reachability probes (Netflix, Disney+, YouTube, TikTok, ChatGPT, Claude, Gemini) — results drive conditional routing
- **Stages 12-15**: Config generation (proxy providers, client subscriptions, server outbounds), ACME certificate issuance
- **Stage 16**: Hand off to supervisord

### Process Supervision

supervisord manages: nginx, xray, sing-box, x-ui, s-ui, sub-store, dufs, cron

### Networking

- Host network mode, primary port 443 (TCP/UDP)
- Nginx does TLS termination and SNI-based routing
- Internal services communicate via Unix Domain Sockets
- Xray handles: VLESS-Reality, XHTTP, VMess-WS
- Sing-box handles: Hysteria2, TUIC, AnyTLS

## Key Directories

- `templates/` — Config templates rendered by entrypoint via envsubst. Subdirs: `xray/`, `sing-box/`, `nginx/`, `supervisord/`, `client_template/`, `providers/`, `dufs/`
- `scripts/` — Runtime scripts: `entrypoint.sh` (main), `show-config.sh` (display config/share links), `check_ip_type.sh` (IP classification), `geo_update.sh` (GeoIP DB update), `test_entrypoint.sh` (unit tests)
- `sources/` — Rule sets (ACL4SSR), OpenClash configs, Zashboard settings, custom hacks
- `docs/` — 5-part documentation: architecture, protocols, routing/clients, ops/troubleshooting, build/release

## Conventions

- Shell scripts use 4-space indentation; YAML/Dockerfile use 2-space (see `.editorconfig`)
- Entrypoint uses `ensure_var` for persistent variables cached to an ENV file
- Template variables use `${VAR}` syntax processed by envsubst
- Config JSON files use numbered prefixes for merge ordering (e.g., `01_reality_inbounds.json`)
- Client templates support multiple proxy group strategies: urltest, fallback, load-balance, consistent-hashing
- sing-box binary is **incompatible with UPX compression** (causes segfault) — never add UPX for sing-box

## Environment Variables

50+ env vars control the container (defined as Dockerfile ARGs with defaults). Key categories:

- **Proxy**: `DEST_HOST`, `DOMAIN`, `CDNDOMAIN`, `LISTENING_PORT`, protocol-specific ports
- **Certs**: `ACMESH_REGISTER_EMAIL`, `ACMESH_SERVER_NAME`, `SSL_PATH`
- **ISP Routing**: `DEFAULT_ISP`, `PROVIDERS`
- **Panels**: `XUI_PORT`/`SUI_PORT` + `*_WEBBASEPATH`
- **Sub-Store**: `SUB_STORE_BACKEND_API_PORT`, `SUB_STORE_FRONTEND_PORT`

## Language

Project documentation and comments are primarily in Chinese. Code comments, variable names, and git messages may mix Chinese and English.
