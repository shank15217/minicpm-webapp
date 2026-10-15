#!/usr/bin/env bash
# install.sh — provision minicpm-webapp on a NEW machine (RHEL-family or Debian-family).
#
# Installs: system packages for headless Chromium/Playwright, the `uv` package
# manager, the project venv (uv sync), and the Playwright Chromium browser.
# Does NOT install llama.cpp or download models — see docs/DEPLOY.md steps 2-3.
#
# Usage:  sudo ./scripts/install.sh            (or as root)
# Idempotent: safe to re-run.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

log() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }

# ---------------------------------------------------------------------------
# 1. OS detection
# ---------------------------------------------------------------------------
if command -v dnf >/dev/null 2>&1; then
    OS_FAMILY="rhel"
elif command -v apt-get >/dev/null 2>&1; then
    OS_FAMILY="debian"
else
    echo "ERROR: no dnf or apt-get found. This script supports RHEL/Rocky/Fedora and Debian/Ubuntu only." >&2
    exit 1
fi
log "Detected package manager family: $OS_FAMILY"

# ---------------------------------------------------------------------------
# 2. System packages (base tools + headless Chromium runtime libraries)
#
# Playwright's `install --with-deps` only supports apt; on RHEL-family the
# Chromium runtime libs must be installed explicitly. The list below is the
# set verified working on Rocky Linux 10 (pvellm).
# ---------------------------------------------------------------------------
BASE_PKGS=(curl ca-certificates)
CHROMIUM_PKGS_RHEL=(
    nss nspr atk at-spi2-atk at-spi2-core
    cups-libs libdrm libxkbcommon
    libX11 libxcb libXext libXfixes libXcomposite libXdamage libXrandr
    mesa-libgbm alsa-lib pango cairo
)
CHROMIUM_PKGS_DEBIAN=(
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2
    libgbm1 libasound2 libpango-1.0-0 libcairo2
)

if [ "$OS_FAMILY" = "rhel" ]; then
    log "Installing system packages via dnf"
    dnf install -y "${BASE_PKGS[@]}" "${CHROMIUM_PKGS_RHEL[@]}"
else
    log "Installing system packages via apt-get"
    apt-get update -y
    # libasound2 is named libasound2t64 on Debian 13 / Ubuntu 24.04+;
    # try the plain name first, fall back per-package.
    for pkg in "${BASE_PKGS[@]}" "${CHROMIUM_PKGS_DEBIAN[@]}"; do
        apt-get install -y "$pkg" 2>/dev/null \
            || apt-get install -y "${pkg}t64" 2>/dev/null \
            || echo "WARN: could not install $pkg (continuing)" >&2
    done
fi

# ---------------------------------------------------------------------------
# 3. uv (Python package + project manager). uv also provides CPython itself,
#    so a distro python3.12 is NOT required (python >=3.10 is).
#    NOTE (verified on pvellm): a uv-created venv contains NO pip. Always use
#    `uv sync` / `uv pip ...`, never `python -m pip`.
# ---------------------------------------------------------------------------
if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
    log "Installing uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"
uv --version

# ---------------------------------------------------------------------------
# 4. Project venv + dependencies (uv.lock is the source of truth)
# ---------------------------------------------------------------------------
log "Syncing project venv (.venv) with uv"
uv sync --extra dev

# ---------------------------------------------------------------------------
# 5. Playwright Chromium
#    --with-deps works on Debian-family; on RHEL the libs came from dnf above.
# ---------------------------------------------------------------------------
log "Installing Playwright Chromium browser"
if [ "$OS_FAMILY" = "debian" ]; then
    uv run playwright install --with-deps chromium
else
    uv run playwright install chromium
fi
# Smoke test: launch headless and print the browser version.
uv run python - <<'EOF'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    print("Chromium OK:", b.version)
    b.close()
EOF

log "Done. Next: llama.cpp + MiniCPM-V model (docs/DEPLOY.md), then ./scripts/start_app.sh"
