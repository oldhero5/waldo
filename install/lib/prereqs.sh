#!/usr/bin/env bash
# Install missing system dependencies (Docker, uv, Node, build basics).
#
# Each ensure_* function is idempotent: it checks first, installs only if
# missing, and refuses to act if it can't do the install non-interactively
# (no sudo, unsupported package manager, etc.) — instead printing a clear
# manual instruction and returning non-zero.

# Run a command with sudo if not root. If sudo isn't available, run as-is
# (the caller will see permission errors).
_sudo() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        "$@"
    fi
}

# True if we can run privileged commands non-interactively.
_can_sudo_noninteractive() {
    [ "$(id -u)" -eq 0 ] && return 0
    command -v sudo >/dev/null 2>&1 || return 1
    sudo -n true >/dev/null 2>&1
}

ensure_curl() {
    command -v curl >/dev/null 2>&1 && { log_ok "curl"; return 0; }
    log_warn "curl missing — installing"
    case "$WALDO_PKG" in
        apt)    _sudo apt-get update -qq && _sudo apt-get install -y -qq curl ca-certificates ;;
        dnf)    _sudo dnf install -y -q curl ca-certificates ;;
        pacman) _sudo pacman -S --noconfirm --needed curl ca-certificates ;;
        brew)   brew install curl ;;
        *) log_fatal "Cannot install curl automatically. Please install it and re-run." ;;
    esac
}

ensure_git() {
    command -v git >/dev/null 2>&1 && { log_ok "git"; return 0; }
    log_warn "git missing — installing"
    case "$WALDO_PKG" in
        apt)    _sudo apt-get update -qq && _sudo apt-get install -y -qq git ;;
        dnf)    _sudo dnf install -y -q git ;;
        pacman) _sudo pacman -S --noconfirm --needed git ;;
        brew)   brew install git ;;
        *) log_fatal "Cannot install git automatically. Please install it and re-run." ;;
    esac
}

ensure_docker() {
    if command -v docker >/dev/null 2>&1; then
        if docker info >/dev/null 2>&1; then
            log_ok "docker (engine reachable)"
            return 0
        else
            log_warn "docker installed but daemon not reachable"
            if [ "$WALDO_OS" = "macos" ]; then
                log_info "Open Docker Desktop or run: open -a Docker"
            elif [ "$WALDO_IS_WSL" = "1" ]; then
                log_info "Enable WSL integration in Docker Desktop (Settings → Resources → WSL Integration)"
            else
                log_info "Try: sudo systemctl start docker  (and add yourself to the docker group)"
            fi
            log_fatal "Docker daemon must be running before Waldo can start."
        fi
    fi

    log_warn "docker missing"
    if [ "$WALDO_OS" = "macos" ]; then
        log_info "Install Docker Desktop or OrbStack:"
        log_info "  brew install --cask docker        # Docker Desktop"
        log_info "  brew install orbstack             # or OrbStack (lighter)"
        log_fatal "Install Docker, start it, then re-run this script."
    fi

    if [ "$WALDO_IS_WSL" = "1" ]; then
        log_info "On WSL2, install Docker Desktop on Windows and enable WSL integration."
        log_info "https://docs.docker.com/desktop/wsl/"
        log_fatal "Install Docker Desktop on Windows, then re-run this script inside WSL."
    fi

    # Linux — use Docker's official convenience script if we can sudo.
    if [ "$WALDO_OS" = "linux" ]; then
        if ! _can_sudo_noninteractive; then
            log_info "Install Docker Engine (you will be prompted for sudo):"
            log_info "  curl -fsSL https://get.docker.com | sh"
            log_info "  sudo usermod -aG docker \$USER && newgrp docker"
            log_fatal "Cannot install docker without sudo. Install manually, then re-run."
        fi
        log_info "Installing Docker via get.docker.com (requires sudo)…"
        curl -fsSL https://get.docker.com | _sudo sh 2>&1 | sed 's/^/      /'
        _sudo systemctl enable --now docker 2>/dev/null || true
        if ! groups "$USER" | grep -q '\bdocker\b'; then
            log_info "Adding $USER to docker group (effective on next login)…"
            _sudo usermod -aG docker "$USER" || true
            log_warn "Log out and back in for docker group to take effect (or run: newgrp docker)."
        fi
    fi
}

ensure_uv() {
    if command -v uv >/dev/null 2>&1; then
        log_ok "uv ($(uv --version 2>/dev/null | awk '{print $2}'))"
        return 0
    fi
    log_warn "uv missing — installing via astral.sh"
    curl -LsSf https://astral.sh/uv/install.sh | sh 2>&1 | sed 's/^/      /'
    # Make the freshly-installed uv discoverable in this shell.
    for d in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
        case ":$PATH:" in *":$d:"*) ;; *) [ -d "$d" ] && PATH="$d:$PATH" ;; esac
    done
    export PATH
    command -v uv >/dev/null 2>&1 || log_fatal "uv installed but not on PATH. Add ~/.local/bin to PATH."
}

ensure_node() {
    # Vite 8 + rolldown require Node >= 20.19 (or >= 22.12). We require 20.19+
    # so that `npm run build` in ui/ doesn't crash with "Cannot find native binding".
    local need_major=20 need_minor=19
    if command -v node >/dev/null 2>&1; then
        local v major minor
        v="$(node --version 2>/dev/null | sed 's/^v//')"
        major="${v%%.*}"; minor="${v#*.}"; minor="${minor%%.*}"
        if [ "${major:-0}" -gt "$need_major" ] \
           || { [ "${major:-0}" -eq "$need_major" ] && [ "${minor:-0}" -ge "$need_minor" ]; }; then
            log_ok "node v$v"
            return 0
        fi
        log_warn "node v$v is too old (need ${need_major}.${need_minor}+) — installing newer"
    else
        log_warn "node missing — installing"
    fi

    case "$WALDO_OS" in
        macos)
            if [ -n "$WALDO_PKG" ]; then
                brew install node@20 || brew install node
            else
                log_fatal "Install Homebrew or Node.js 20+ manually, then re-run."
            fi
            ;;
        linux)
            if ! _can_sudo_noninteractive; then
                log_info "Install Node.js 20 manually:"
                log_info "  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -"
                log_info "  sudo apt install -y nodejs"
                log_fatal "Cannot install node without sudo. Install manually, then re-run."
            fi
            case "$WALDO_PKG" in
                apt)
                    curl -fsSL https://deb.nodesource.com/setup_20.x | _sudo -E bash - 2>&1 | sed 's/^/      /'
                    _sudo apt-get install -y -qq nodejs
                    ;;
                dnf)
                    curl -fsSL https://rpm.nodesource.com/setup_20.x | _sudo bash - 2>&1 | sed 's/^/      /'
                    _sudo dnf install -y -q nodejs
                    ;;
                pacman)
                    _sudo pacman -S --noconfirm --needed nodejs npm
                    ;;
                *)
                    log_fatal "Unsupported package manager. Install Node.js 20+ manually, then re-run."
                    ;;
            esac
            ;;
        *)
            log_fatal "Unknown OS — install Node.js 20+ manually, then re-run."
            ;;
    esac
}

ensure_make() {
    command -v make >/dev/null 2>&1 && { log_ok "make"; return 0; }
    log_warn "make missing — installing"
    case "$WALDO_PKG" in
        apt)    _sudo apt-get install -y -qq make ;;
        dnf)    _sudo dnf install -y -q make ;;
        pacman) _sudo pacman -S --noconfirm --needed make ;;
        brew)   xcode-select -p >/dev/null 2>&1 || xcode-select --install ;;
        *) log_fatal "Install make manually, then re-run." ;;
    esac
}

ensure_nvidia_container_toolkit() {
    [ "$WALDO_GPU" = "nvidia" ] || return 0
    [ "$WALDO_NVIDIA_CT" = "yes" ] && { log_ok "nvidia-container-toolkit"; return 0; }

    if [ "$WALDO_IS_WSL" = "1" ]; then
        log_info "On WSL2, NVIDIA GPU support comes from Docker Desktop + the Windows driver."
        log_info "Make sure Docker Desktop's WSL integration is enabled and the Windows NVIDIA driver is installed."
        return 0
    fi

    log_warn "nvidia-container-toolkit missing — installing (required for GPU passthrough)"
    if ! _can_sudo_noninteractive; then
        log_info "Install nvidia-container-toolkit manually, then re-run:"
        log_info "  https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html"
        log_fatal "Cannot install nvidia-container-toolkit without sudo."
    fi

    case "$WALDO_PKG" in
        apt)
            curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | _sudo gpg --dearmor --yes -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
            curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
                | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
                | _sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
            _sudo apt-get update -qq
            _sudo apt-get install -y -qq nvidia-container-toolkit
            ;;
        dnf)
            curl -fsSL https://nvidia.github.io/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo \
                | _sudo tee /etc/yum.repos.d/nvidia-container-toolkit.repo >/dev/null
            _sudo dnf install -y -q nvidia-container-toolkit
            ;;
        *)
            log_fatal "Install nvidia-container-toolkit manually for $WALDO_PKG, then re-run."
            ;;
    esac

    _sudo nvidia-ctk runtime configure --runtime=docker 2>&1 | sed 's/^/      /'
    _sudo systemctl restart docker 2>/dev/null || true
    WALDO_NVIDIA_CT="yes"
}
