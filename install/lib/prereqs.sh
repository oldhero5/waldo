#!/usr/bin/env bash
# Install missing system dependencies (Docker, uv, Node, build basics).
#
# Each ensure_* function is idempotent: it checks first, installs only if
# missing, and refuses to act if sudo is genuinely unavailable. When sudo
# requires a password we warm it up once at the start of the run via
# _sudo_warmup, so subsequent silent calls succeed.

# Run a command with sudo if not root. Pulls password input from /dev/tty
# (when available) so the prompt still works under `curl … | bash`, where
# stdin is the curl pipe. If sudo isn't available, run as-is.
_sudo() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        if [ -e /dev/tty ]; then
            sudo "$@" </dev/tty
        else
            sudo "$@"
        fi
    else
        "$@"
    fi
}

# True if sudo is available at all (root counts).
_have_sudo() {
    [ "$(id -u)" -eq 0 ] && return 0
    command -v sudo >/dev/null 2>&1
}

# True if sudo can run RIGHT NOW without prompting (root, NOPASSWD, or warm
# credentials cache). Distinct from _have_sudo so we can decide whether to
# warm up vs. give up.
_can_sudo_noninteractive() {
    [ "$(id -u)" -eq 0 ] && return 0
    command -v sudo >/dev/null 2>&1 || return 1
    sudo -n true >/dev/null 2>&1
}

# Warm sudo credentials once at the top of the prereq step, so the user
# enters their password once and the rest of the run is silent. Reads the
# password from /dev/tty when available — works under `curl | bash`.
_sudo_warmup() {
    [ "$(id -u)" -eq 0 ] && return 0
    [ "${WALDO_NO_SUDO:-0}" = "1" ] && return 0
    command -v sudo >/dev/null 2>&1 || return 0
    _can_sudo_noninteractive && return 0  # already cached / NOPASSWD

    if [ ! -e /dev/tty ]; then
        log_warn "sudo needs a password but no terminal is available."
        log_info "Re-run from a terminal (not a curl|bash pipe), or pre-authorize:"
        log_info "  sudo -v && curl -fsSL https://raw.githubusercontent.com/oldhero5/waldo/main/install.sh | bash"
        log_info "Or pass --no-sudo to print missing prerequisites and exit."
        return 1
    fi

    log_info "sudo password may be required for prerequisite installs."
    log_info "(You'll be prompted once; future calls in this run reuse the cached credentials.)"
    if ! sudo -v </dev/tty; then
        log_warn "sudo warm-up failed."
        return 1
    fi
    log_ok "sudo authorized"
    return 0
}

# Print a punch-list of missing prereqs and exit successfully — used when
# the user passes --no-sudo. The user installs them by hand and re-runs
# with --skip-prereqs.
_no_sudo_report() {
    log_step "Missing prerequisites (--no-sudo mode)"
    local missing=0
    for cmd in curl git make docker; do
        if command -v "$cmd" >/dev/null 2>&1; then
            log_ok "$cmd"
        else
            log_warn "$cmd is missing"
            missing=$((missing + 1))
        fi
    done
    if command -v uv >/dev/null 2>&1; then
        log_ok "uv"
    else
        log_warn "uv is missing"
        missing=$((missing + 1))
    fi
    if command -v node >/dev/null 2>&1; then
        log_ok "node ($(node --version 2>/dev/null))"
    else
        log_warn "node is missing"
        missing=$((missing + 1))
    fi

    if [ "$missing" -eq 0 ]; then
        log_ok "All prereqs already installed — re-run with --skip-prereqs."
        return 0
    fi

    log_info ""
    log_info "Install the above with your package manager, e.g. on Ubuntu/Debian:"
    log_info "  sudo apt-get update && sudo apt-get install -y curl git make ca-certificates"
    log_info "  curl -fsSL https://get.docker.com | sudo sh"
    log_info "  sudo usermod -aG docker \$USER && newgrp docker"
    log_info "  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt-get install -y nodejs"
    log_info "  curl -LsSf https://astral.sh/uv/install.sh | sh"
    log_info ""
    log_info "Then re-run this installer with --skip-prereqs."
    return 0
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
        if ! _have_sudo; then
            log_info "Install Docker Engine manually, then re-run:"
            log_info "  curl -fsSL https://get.docker.com | sh"
            log_info "  sudo usermod -aG docker \$USER && newgrp docker"
            log_fatal "No sudo available. Install Docker by hand, then re-run with --skip-prereqs."
        fi
        # Warm sudo creds once if not cached — keeps every later _sudo silent.
        _sudo_warmup || log_fatal "Could not authorize sudo. See message above."
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
            if ! _have_sudo; then
                log_info "Install Node.js 20 manually:"
                log_info "  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -"
                log_info "  sudo apt install -y nodejs"
                log_fatal "No sudo available. Install Node by hand, then re-run with --skip-prereqs."
            fi
            _sudo_warmup || log_fatal "Could not authorize sudo. See message above."
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
    if ! _have_sudo; then
        log_info "Install nvidia-container-toolkit manually, then re-run:"
        log_info "  https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html"
        log_fatal "No sudo available. Install nvidia-container-toolkit by hand, then re-run with --skip-prereqs."
    fi
    _sudo_warmup || log_fatal "Could not authorize sudo. See message above."

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
