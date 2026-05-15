#!/usr/bin/env bash
# Waldo one-shot installer for Linux, macOS, and WSL.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/oldhero5/waldo/main/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/oldhero5/waldo/main/install.sh | bash -s -- [flags]
#   ./install.sh [flags]            # from inside a cloned repo
#
# Flags (all optional):
#   --dir PATH         Where to clone the repo if needed.   Default: ~/waldo
#   --branch NAME      Branch to clone.                     Default: main
#   --repo URL         Git URL to clone from.               Default: https://github.com/oldhero5/waldo.git
#   --skip-prereqs     Don't install Docker/uv/Node — assume they're present.
#   --skip-models      Don't download SAM 3.1 weights at the end.
#   --skip-up          Don't run docker compose up — only set everything up.
#   --cpu              Force CPU mode even if a GPU is detected.
#   --gpu nvidia|apple|none   Override GPU detection.
#   --hf-token TOKEN   Hugging Face read token (otherwise prompted up front,
#                      or read from $HF_TOKEN). Required for SAM 3 weights.
#   --no-sudo          Don't try to install prereqs with sudo. Print a list of
#                      what's missing and exit so you can install by hand,
#                      then re-run with --skip-prereqs.
#   --yes              Non-interactive: accept all prompts.
#   --no-color         Disable colored output.
#   -h, --help         Show this help.

set -euo pipefail

WALDO_REPO_DEFAULT="https://github.com/oldhero5/waldo.git"
WALDO_BRANCH_DEFAULT="main"
WALDO_DIR_DEFAULT="$HOME/waldo"

WALDO_REPO="$WALDO_REPO_DEFAULT"
WALDO_BRANCH="$WALDO_BRANCH_DEFAULT"
WALDO_DIR=""
SKIP_PREREQS=0
SKIP_MODELS=0
SKIP_UP=0
FORCE_CPU=0
GPU_OVERRIDE=""

# ── Argument parsing ─────────────────────────────────────────────
print_help() { sed -n '2,21p' "${BASH_SOURCE[0]:-$0}" 2>/dev/null | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
    case "$1" in
        --dir)         WALDO_DIR="$2"; shift 2 ;;
        --branch)      WALDO_BRANCH="$2"; shift 2 ;;
        --repo)        WALDO_REPO="$2"; shift 2 ;;
        --skip-prereqs) SKIP_PREREQS=1; shift ;;
        --skip-models) SKIP_MODELS=1; shift ;;
        --skip-up)     SKIP_UP=1; shift ;;
        --cpu)         FORCE_CPU=1; shift ;;
        --gpu)         GPU_OVERRIDE="$2"; shift 2 ;;
        --hf-token)    export HF_TOKEN="$2"; shift 2 ;;
        --no-sudo)     export WALDO_NO_SUDO=1; shift ;;
        --yes|-y)      export WALDO_ASSUME_YES=1; shift ;;
        --no-color)    export NO_COLOR=1; shift ;;
        -h|--help)     print_help; exit 0 ;;
        *) echo "Unknown flag: $1" >&2; print_help; exit 1 ;;
    esac
done

# ── Locate self / repo root ──────────────────────────────────────
# When piped through curl, BASH_SOURCE is empty and we're not in a repo.
# Otherwise, source helpers from $(dirname "$0")/install/lib.
SCRIPT_PATH="${BASH_SOURCE[0]:-}"
if [ -n "$SCRIPT_PATH" ] && [ -f "$SCRIPT_PATH" ]; then
    SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
else
    SCRIPT_DIR=""
fi

# If we're running outside a repo (curl | bash), we'll clone first and
# re-source the helpers from there. Until then, define minimal logging.
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/install/lib/log.sh" ]; then
    # shellcheck source=install/lib/log.sh
    . "$SCRIPT_DIR/install/lib/log.sh"
    # shellcheck source=install/lib/detect.sh
    . "$SCRIPT_DIR/install/lib/detect.sh"
    # shellcheck source=install/lib/prereqs.sh
    . "$SCRIPT_DIR/install/lib/prereqs.sh"
    # shellcheck source=install/lib/env.sh
    . "$SCRIPT_DIR/install/lib/env.sh"
    HAVE_HELPERS=1
else
    HAVE_HELPERS=0
    # Minimal stubs — replaced once we clone.
    log_step()  { printf '\n==> %s\n' "$*"; }
    log_info()  { printf '   %s\n' "$*"; }
    log_ok()    { printf '   ok %s\n' "$*"; }
    log_warn()  { printf '   ! %s\n' "$*"; }
    log_err()   { printf '   x %s\n' "$*" >&2; }
    log_fatal() { log_err "$*"; exit 1; }
fi

# ── Banner ───────────────────────────────────────────────────────
cat <<'BANNER'

   _       __      __    __
  | |     / /___ _/ /___/ /___
  | | /| / / __ `/ / __  / __ \
  | |/ |/ / /_/ / / /_/ / /_/ /
  |__/|__/\__,_/_/\__,_/\____/

  Self-hosted ML for video object detection.
  Installer for Linux, macOS, and WSL2.

BANNER

# ── Step 1: get the repo ────────────────────────────────────────
log_step "Locating Waldo repo"

# Already inside a repo (script lives next to docker-compose.yml)?
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/docker-compose.yml" ] && [ -f "$SCRIPT_DIR/.env.example" ]; then
    WALDO_DIR="$SCRIPT_DIR"
    log_ok "Using existing repo at $WALDO_DIR"
else
    : "${WALDO_DIR:=$WALDO_DIR_DEFAULT}"
    if [ -d "$WALDO_DIR/.git" ] && [ -f "$WALDO_DIR/docker-compose.yml" ]; then
        log_ok "Existing clone at $WALDO_DIR — pulling latest"
        git -C "$WALDO_DIR" fetch --quiet origin "$WALDO_BRANCH" || true
        git -C "$WALDO_DIR" checkout --quiet "$WALDO_BRANCH" 2>/dev/null || true
        git -C "$WALDO_DIR" pull --ff-only --quiet origin "$WALDO_BRANCH" 2>/dev/null || \
            log_warn "Could not fast-forward $WALDO_BRANCH (local changes?). Continuing with current state."
    else
        if [ -e "$WALDO_DIR" ] && [ -n "$(ls -A "$WALDO_DIR" 2>/dev/null || true)" ]; then
            log_fatal "$WALDO_DIR exists and is not empty. Pass --dir to choose another path."
        fi
        log_info "Cloning $WALDO_REPO ($WALDO_BRANCH) into $WALDO_DIR"
        # We need git for this — bootstrap if missing.
        if ! command -v git >/dev/null 2>&1; then
            log_warn "git missing — installing minimally to clone"
            if   command -v apt-get >/dev/null 2>&1; then sudo apt-get update -qq && sudo apt-get install -y -qq git
            elif command -v dnf     >/dev/null 2>&1; then sudo dnf install -y -q git
            elif command -v pacman  >/dev/null 2>&1; then sudo pacman -S --noconfirm --needed git
            elif command -v brew    >/dev/null 2>&1; then brew install git
            else log_fatal "Install git manually, then re-run."
            fi
        fi
        git clone --branch "$WALDO_BRANCH" --depth 1 "$WALDO_REPO" "$WALDO_DIR" 2>&1 | sed 's/^/      /'
    fi
fi

cd "$WALDO_DIR"

# Re-source helpers from the (now-cloned) repo if we were running from curl.
if [ "$HAVE_HELPERS" = "0" ]; then
    if [ -f "$WALDO_DIR/install/lib/log.sh" ]; then
        # shellcheck source=install/lib/log.sh
        . "$WALDO_DIR/install/lib/log.sh"
        # shellcheck source=install/lib/detect.sh
        . "$WALDO_DIR/install/lib/detect.sh"
        # shellcheck source=install/lib/prereqs.sh
        . "$WALDO_DIR/install/lib/prereqs.sh"
        # shellcheck source=install/lib/env.sh
        . "$WALDO_DIR/install/lib/env.sh"
    else
        log_fatal "Repo at $WALDO_DIR is missing install/lib helpers (wrong branch?)."
    fi
fi

# ── Step 2: detect platform + GPU ────────────────────────────────
log_step "Detecting platform"
detect_platform
detect_gpu
if [ "$FORCE_CPU" = "1" ]; then
    WALDO_GPU="none"
fi
if [ -n "$GPU_OVERRIDE" ]; then
    case "$GPU_OVERRIDE" in
        nvidia|apple|none) WALDO_GPU="$GPU_OVERRIDE" ;;
        *) log_fatal "--gpu must be one of: nvidia, apple, none" ;;
    esac
fi
print_platform_summary

[ "$WALDO_OS" = "unknown" ] && log_fatal "Unsupported OS. This installer targets Linux, macOS, and WSL2."

# ── Ask for the HF token up front (before slow prereq + build steps) ──
# Skip if --skip-models is set (the token isn't needed in that case).
# The actual .env write happens later in prompt_hf_token; this just
# captures the token into the environment so the user can walk away.
if [ "$SKIP_MODELS" != "1" ] && [ -z "${HF_TOKEN:-}" ]; then
    # If a previous run already saved it to .env, don't re-prompt.
    existing_token=""
    if [ -f "$WALDO_DIR/.env" ]; then
        existing_token="$(awk -F= '$1=="HF_TOKEN"{sub(/^[^=]*=/,""); print; exit}' "$WALDO_DIR/.env")"
    fi
    if [ -z "$existing_token" ]; then
        if [ "${WALDO_ASSUME_YES:-0}" != "1" ] && { [ -t 0 ] || [ -e /dev/tty ]; }; then
            log_step "Hugging Face token"
            log_info "Waldo needs a Hugging Face read token to download SAM 3 weights."
            log_info "Get one at https://huggingface.co/settings/tokens (read access is enough)."
            log_info "You also need to accept the license at https://huggingface.co/facebook/sam3."
            log_info "Press Enter to skip — you can set HF_TOKEN in .env later."
            printf '   %s? %sPaste HF token: ' "$_C_CYAN" "$_C_RESET"
            entered=""
            if [ -e /dev/tty ]; then
                read -r entered </dev/tty || entered=""
            else
                read -r entered || entered=""
            fi
            if [ -n "$entered" ]; then
                export HF_TOKEN="$entered"
                log_ok "HF_TOKEN captured"
            fi
        fi
    fi
fi

# ── Step 3: prereqs ──────────────────────────────────────────────
if [ "${WALDO_NO_SUDO:-0}" = "1" ]; then
    _no_sudo_report
    log_info ""
    log_info "Re-run with --skip-prereqs once everything above is installed."
    exit 0
elif [ "$SKIP_PREREQS" = "1" ]; then
    log_step "Skipping prerequisite install (--skip-prereqs)"
else
    log_step "Installing prerequisites"
    # Warm sudo creds once so the user types their password at most once
    # (a no-op when already root or sudo is passwordless).
    if [ "$WALDO_OS" = "linux" ] && [ "$WALDO_IS_WSL" != "1" ]; then
        _sudo_warmup || log_warn "Continuing without warm sudo — individual steps may prompt or fail."
    fi
    ensure_curl
    ensure_git
    ensure_make
    ensure_docker
    ensure_uv
    ensure_node
    ensure_nvidia_container_toolkit
fi

# Re-detect GPU after potential toolkit install.
detect_gpu
[ "$FORCE_CPU" = "1" ] && WALDO_GPU="none"
[ -n "$GPU_OVERRIDE" ] && WALDO_GPU="$GPU_OVERRIDE"

# ── Step 4: .env ─────────────────────────────────────────────────
log_step "Configuring environment"
ensure_env_file "$WALDO_DIR"
configure_device_for_gpu "$WALDO_DIR/.env"
log_ok "DEVICE set to $(get_env_value "$WALDO_DIR/.env" DEVICE)"
prompt_hf_token "$WALDO_DIR/.env"

# ── Step 5: GPU passthrough check ────────────────────────────────
if [ "$WALDO_GPU" = "nvidia" ] && [ "$WALDO_NVIDIA_CT" = "yes" ] && command -v docker >/dev/null 2>&1; then
    log_step "Verifying NVIDIA GPU passthrough into Docker"
    if docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi >/tmp/waldo-gpu-check.log 2>&1; then
        log_ok "GPU is visible inside containers"
        grep -E "(Driver Version|CUDA Version|^\| *[0-9]+ )" /tmp/waldo-gpu-check.log | head -8 | sed 's/^/      /'
    else
        log_warn "GPU passthrough check failed (see /tmp/waldo-gpu-check.log)"
        log_info "Falling back to CPU profile so install can complete."
        WALDO_GPU="none"
        configure_device_for_gpu "$WALDO_DIR/.env"
    fi
fi

# ── Step 6: pick compose profile ─────────────────────────────────
case "$WALDO_GPU" in
    nvidia) WALDO_PROFILE="nvidia" ;;
    apple|none) WALDO_PROFILE="apple" ;;
esac
log_info "Compose profile: $WALDO_PROFILE"

# ── Step 7: download models ──────────────────────────────────────
HF_TOKEN_VAL="$(get_env_value "$WALDO_DIR/.env" HF_TOKEN)"
if [ "$SKIP_MODELS" = "1" ]; then
    log_step "Skipping model download (--skip-models)"
elif [ -z "$HF_TOKEN_VAL" ]; then
    log_step "Skipping model download (HF_TOKEN not set)"
    log_info "Edit .env, set HF_TOKEN, then run: bash scripts/download_models.sh"
else
    log_step "Downloading SAM 3.1 weights (this can take a few minutes)"
    if ! ( cd "$WALDO_DIR" && set -a && . ./.env && set +a && bash scripts/download_models.sh ) 2>&1 | sed 's/^/      /'; then
        log_warn "Model download hit an error. You can retry later with: bash scripts/download_models.sh"
    fi
fi

# ── Step 8: build the React UI (vite -> app/static, baked into the image) ──
# The app container serves the SPA out of app/static. Vite's outDir is
# ../app/static, so we have to build BEFORE `docker compose build` copies app/.
if [ "$SKIP_UP" = "1" ]; then
    log_step "Skipping UI build (--skip-up)"
else
    log_step "Building UI (vite → app/static)"
    if [ ! -d "$WALDO_DIR/ui/node_modules" ]; then
        log_info "Installing UI deps (npm install --legacy-peer-deps)…"
        ( cd "$WALDO_DIR/ui" && npm install --legacy-peer-deps --no-audit --no-fund ) 2>&1 | sed 's/^/      /'
    fi
    ( cd "$WALDO_DIR/ui" && npm run build ) 2>&1 | sed 's/^/      /'
    if [ -f "$WALDO_DIR/app/static/index.html" ]; then
        log_ok "UI built ($(du -sh "$WALDO_DIR/app/static" 2>/dev/null | awk '{print $1}'))"
    else
        log_warn "UI build finished but app/static/index.html is missing — the app container will return 404 at /."
    fi
fi

# ── Step 9: bring up the stack ───────────────────────────────────
if [ "$SKIP_UP" = "1" ]; then
    log_step "Skipping stack startup (--skip-up)"
else
    log_step "Starting Waldo (docker compose --profile $WALDO_PROFILE up -d --build)"
    if [ "$WALDO_OS" = "macos" ] && [ "$WALDO_GPU" = "apple" ]; then
        # Apple path — workers run natively for MPS access.
        log_info "macOS Apple Silicon: running infra+app in Docker, MLX workers natively (make up-mac)"
        ( cd "$WALDO_DIR" && make --no-print-directory up-mac ) 2>&1 | sed 's/^/      /'
    else
        ( cd "$WALDO_DIR" && docker compose --profile "$WALDO_PROFILE" up -d --build ) 2>&1 | sed 's/^/      /'
    fi
fi

# ── Done ─────────────────────────────────────────────────────────
log_step "Done"
cat <<EOF

  ${_C_GREEN}${_C_BOLD}Waldo is installed.${_C_RESET}

  Repo:       $WALDO_DIR
  Profile:    $WALDO_PROFILE
  Web UI:     ${_C_CYAN}http://localhost:8000${_C_RESET}
  MinIO:      ${_C_CYAN}http://localhost:9001${_C_RESET}  (minioadmin / minioadmin)

  First-run admin password is printed in the app logs:
    docker compose logs app | grep -A 2 "bootstrapped first admin"

  Useful commands (run from $WALDO_DIR):
    make logs              # tail all containers
    make down              # stop everything
    make gpu-check         # verify NVIDIA passthrough
    make download-models   # re-download SAM 3.1 weights

EOF
