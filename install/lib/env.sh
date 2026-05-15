#!/usr/bin/env bash
# .env bootstrap and HF_TOKEN handling.

ensure_env_file() {
    local root="$1"
    if [ -f "$root/.env" ]; then
        log_ok ".env exists"
        return 0
    fi
    if [ ! -f "$root/.env.example" ]; then
        log_fatal "$root/.env.example not found — is this a Waldo repo?"
    fi
    cp "$root/.env.example" "$root/.env"
    log_ok ".env created from .env.example"
}

# Set or update a KEY=VALUE line in .env. Idempotent and safe with values
# that contain '/', '&', or special sed chars (we use awk).
set_env_value() {
    local file="$1" key="$2" value="$3"
    local tmp; tmp="$(mktemp)"
    awk -v k="$key" -v v="$value" '
        BEGIN { found = 0 }
        $0 ~ "^[[:space:]]*"k"=" { print k"="v; found = 1; next }
        { print }
        END { if (!found) print k"="v }
    ' "$file" > "$tmp"
    mv "$tmp" "$file"
}

# Read existing value of KEY in .env (empty string if unset).
get_env_value() {
    local file="$1" key="$2"
    awk -F= -v k="$key" '$1==k { sub(/^[^=]*=/,""); print; exit }' "$file"
}

# Configure DEVICE/DTYPE based on detected GPU.
configure_device_for_gpu() {
    local file="$1"
    case "$WALDO_GPU" in
        nvidia)
            set_env_value "$file" DEVICE   cuda
            set_env_value "$file" DTYPE    bfloat16
            ;;
        apple)
            set_env_value "$file" DEVICE   mps
            set_env_value "$file" DTYPE    float32
            ;;
        none)
            set_env_value "$file" DEVICE   cpu
            set_env_value "$file" DTYPE    float32
            ;;
    esac
}

prompt_hf_token() {
    local file="$1"
    local current; current="$(get_env_value "$file" HF_TOKEN)"
    if [ -n "$current" ]; then
        log_ok "HF_TOKEN already set in .env"
        return 0
    fi
    if [ -n "${HF_TOKEN:-}" ]; then
        set_env_value "$file" HF_TOKEN "$HF_TOKEN"
        log_ok "HF_TOKEN copied from environment"
        return 0
    fi
    log_warn "HF_TOKEN not set — required to download SAM 3.1 weights"
    log_info "Get one at https://huggingface.co/settings/tokens (read access is enough)"
    if [ "${WALDO_ASSUME_YES:-0}" = "1" ]; then
        log_warn "--yes mode: leaving HF_TOKEN blank; set it later in .env before downloading models"
        return 0
    fi
    local token=""
    if [ -t 0 ] || [ -e /dev/tty ]; then
        printf '   %s? %sPaste HF token (or press Enter to skip): ' "$_C_CYAN" "$_C_RESET"
        if [ -e /dev/tty ]; then
            read -r token </dev/tty || token=""
        else
            read -r token || token=""
        fi
    fi
    if [ -n "$token" ]; then
        set_env_value "$file" HF_TOKEN "$token"
        log_ok "HF_TOKEN saved to .env"
    else
        log_warn "Skipped — set HF_TOKEN in .env before running 'make download-models'."
    fi
}
