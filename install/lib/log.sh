#!/usr/bin/env bash
# Pretty logging helpers shared by the installer scripts.

if [ -t 1 ] && command -v tput >/dev/null 2>&1 && [ -z "${NO_COLOR:-}" ]; then
    _C_RESET="$(tput sgr0)"
    _C_BOLD="$(tput bold)"
    _C_RED="$(tput setaf 1)"
    _C_GREEN="$(tput setaf 2)"
    _C_YELLOW="$(tput setaf 3)"
    _C_BLUE="$(tput setaf 4)"
    _C_CYAN="$(tput setaf 6)"
    _C_DIM="$(tput dim 2>/dev/null || echo '')"
else
    _C_RESET=""; _C_BOLD=""; _C_RED=""; _C_GREEN=""; _C_YELLOW=""; _C_BLUE=""; _C_CYAN=""; _C_DIM=""
fi

log_step()  { printf '\n%s==>%s %s%s%s\n' "$_C_BLUE$_C_BOLD" "$_C_RESET" "$_C_BOLD" "$*" "$_C_RESET"; }
log_info()  { printf '   %s%s%s\n' "$_C_DIM" "$*" "$_C_RESET"; }
log_ok()    { printf '   %s✓%s %s\n' "$_C_GREEN" "$_C_RESET" "$*"; }
log_warn()  { printf '   %s!%s %s\n' "$_C_YELLOW" "$_C_RESET" "$*"; }
log_err()   { printf '   %sx%s %s\n' "$_C_RED" "$_C_RESET" "$*" >&2; }
log_fatal() { log_err "$*"; exit 1; }

# log_run "human description" -- cmd args...
# Streams command output indented; fails the script if the command fails.
log_run() {
    local desc="$1"; shift
    if [ "${1:-}" = "--" ]; then shift; fi
    log_info "$desc"
    "$@" 2>&1 | sed 's/^/      /'
    return "${PIPESTATUS[0]}"
}

confirm() {
    # confirm "Question?" [default-y|default-n]
    local prompt="$1"
    local default="${2:-default-n}"
    local hint="[y/N]"
    [ "$default" = "default-y" ] && hint="[Y/n]"

    if [ "${WALDO_ASSUME_YES:-0}" = "1" ]; then
        log_info "$prompt $hint -> yes (--yes)"
        return 0
    fi

    local reply
    printf '   %s? %s%s %s ' "$_C_CYAN" "$_C_RESET" "$prompt" "$hint"
    if ! read -r reply </dev/tty 2>/dev/null; then
        # No tty (e.g. piped) — default to no unless caller passed default-y
        [ "$default" = "default-y" ] && return 0 || return 1
    fi
    case "$reply" in
        y|Y|yes|YES) return 0 ;;
        n|N|no|NO)   return 1 ;;
        "")          [ "$default" = "default-y" ] && return 0 || return 1 ;;
        *)           [ "$default" = "default-y" ] && return 0 || return 1 ;;
    esac
}
