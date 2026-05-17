#!/usr/bin/env bash
# Platform + GPU detection. Sets WALDO_OS, WALDO_DISTRO, WALDO_ARCH,
# WALDO_IS_WSL, WALDO_PKG, WALDO_GPU.

detect_platform() {
    WALDO_ARCH="$(uname -m)"
    WALDO_IS_WSL=0
    WALDO_DISTRO=""
    WALDO_PKG=""

    case "$(uname -s)" in
        Darwin)
            WALDO_OS="macos"
            if command -v brew >/dev/null 2>&1; then
                WALDO_PKG="brew"
            fi
            ;;
        Linux)
            WALDO_OS="linux"
            if grep -qiE "(microsoft|wsl)" /proc/version 2>/dev/null; then
                WALDO_IS_WSL=1
            fi
            if [ -r /etc/os-release ]; then
                # shellcheck disable=SC1091
                . /etc/os-release
                WALDO_DISTRO="${ID:-}"
                case "$WALDO_DISTRO" in
                    ubuntu|debian|pop|linuxmint) WALDO_PKG="apt" ;;
                    fedora|rhel|centos|rocky|almalinux) WALDO_PKG="dnf" ;;
                    arch|manjaro) WALDO_PKG="pacman" ;;
                    *)
                        if   command -v apt-get >/dev/null 2>&1; then WALDO_PKG="apt"
                        elif command -v dnf     >/dev/null 2>&1; then WALDO_PKG="dnf"
                        elif command -v pacman  >/dev/null 2>&1; then WALDO_PKG="pacman"
                        fi
                        ;;
                esac
            fi
            ;;
        *)
            WALDO_OS="unknown"
            ;;
    esac

    export WALDO_OS WALDO_DISTRO WALDO_ARCH WALDO_IS_WSL WALDO_PKG
}

# detect_gpu — sets WALDO_GPU to one of: nvidia, apple, none.
# Also exports WALDO_NVIDIA_DRIVER, WALDO_NVIDIA_CT (container toolkit) "yes"/"no".
detect_gpu() {
    WALDO_GPU="none"
    WALDO_NVIDIA_DRIVER="no"
    WALDO_NVIDIA_CT="no"

    if [ "$WALDO_OS" = "macos" ]; then
        if [ "$WALDO_ARCH" = "arm64" ]; then
            WALDO_GPU="apple"
        fi
    else
        if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
            WALDO_GPU="nvidia"
            WALDO_NVIDIA_DRIVER="yes"
        fi
        # Toolkit detection: nvidia-ctk binary OR a configured nvidia runtime.
        if command -v nvidia-ctk >/dev/null 2>&1; then
            WALDO_NVIDIA_CT="yes"
        elif command -v docker >/dev/null 2>&1 && docker info 2>/dev/null | grep -qi "Runtimes:.*nvidia"; then
            WALDO_NVIDIA_CT="yes"
        fi
    fi
    export WALDO_GPU WALDO_NVIDIA_DRIVER WALDO_NVIDIA_CT
}

print_platform_summary() {
    local wsl_suffix=""
    [ "$WALDO_IS_WSL" = "1" ] && wsl_suffix=" — WSL2"
    log_info "OS:       $WALDO_OS${WALDO_DISTRO:+ ($WALDO_DISTRO)}${wsl_suffix}"
    [ "$WALDO_IS_WSL" = "1" ] && log_info "WSL2:     yes"
    log_info "Arch:     $WALDO_ARCH"
    log_info "Pkg mgr:  ${WALDO_PKG:-none}"
    case "$WALDO_GPU" in
        nvidia)
            log_info "GPU:      NVIDIA detected (driver: $WALDO_NVIDIA_DRIVER, container toolkit: $WALDO_NVIDIA_CT)"
            ;;
        apple)
            log_info "GPU:      Apple Silicon (MPS) — workers will run natively"
            ;;
        none)
            log_info "GPU:      none — workers will run on CPU"
            ;;
    esac
}
