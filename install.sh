#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
SOURCE_BIN="$SCRIPT_DIR/openvas-cli"
SOURCE_PY="$SCRIPT_DIR/openvas_cli.py"
TARGET_DIR="${OPENVAS_CLI_INSTALL_DIR:-$HOME/.local/bin}"
TARGET_BIN="$TARGET_DIR/openvas-cli"
GVM_CLI_BIN_DEFAULT="$HOME/.local/bin/gvm-cli"
DEFAULT_CONFIG_PATH="$HOME/.config/openvas-cli/openvas-cli.conf"
AUTO_PATH_UPDATE="${OPENVAS_CLI_AUTO_PATH:-1}"
PROFILE_FILE_OVERRIDE="${OPENVAS_CLI_SHELL_PROFILE:-}"

usage() {
  cat <<'EOF'
Usage:
  install.sh install
  install.sh uninstall
  install.sh reinstall
  install.sh status

Environment:
  OPENVAS_CLI_INSTALL_DIR    Override install directory. Default: ~/.local/bin
  OPENVAS_CLI_AUTO_PATH     Auto append install dir to shell profile. Default: 1
  OPENVAS_CLI_SHELL_PROFILE Override target shell profile path
EOF
}

say_ok() {
  printf 'OK %s
' "$1"
}

say_warn() {
  printf 'WARN %s
' "$1"
}

say_fail() {
  printf 'FAIL %s
' "$1" >&2
}

ensure_source() {
  if [[ ! -f "$SOURCE_BIN" ]]; then
    say_fail "missing entrypoint: $SOURCE_BIN"
    exit 1
  fi
  if [[ ! -x "$SOURCE_BIN" ]]; then
    chmod +x "$SOURCE_BIN"
    say_ok "made entrypoint executable: $SOURCE_BIN"
  fi
  if [[ ! -f "$SOURCE_PY" ]]; then
    say_fail "missing python file: $SOURCE_PY"
    exit 1
  fi
  say_ok "source files present"
}

check_python() {
  if command -v python3 >/dev/null 2>&1; then
    say_ok "python3 found: $(command -v python3)"
  else
    say_fail "python3 not found"
    exit 1
  fi
}

check_gvm_cli() {
  if command -v gvm-cli >/dev/null 2>&1; then
    say_ok "gvm-cli found: $(command -v gvm-cli)"
    return
  fi
  if [[ -x "$GVM_CLI_BIN_DEFAULT" ]]; then
    say_ok "gvm-cli found: $GVM_CLI_BIN_DEFAULT"
    return
  fi
  say_warn "gvm-cli not found in PATH or $GVM_CLI_BIN_DEFAULT"
}

check_target_dir() {
  if [[ -e "$TARGET_DIR" && ! -d "$TARGET_DIR" ]]; then
    say_fail "install dir exists but is not a directory: $TARGET_DIR"
    exit 1
  fi
  mkdir -p "$TARGET_DIR"
  if [[ ! -w "$TARGET_DIR" ]]; then
    say_fail "install dir is not writable: $TARGET_DIR"
    exit 1
  fi
  say_ok "install dir writable: $TARGET_DIR"
}

path_contains_target() {
  case ":$PATH:" in
    *":$TARGET_DIR:"*) return 0 ;;
    *) return 1 ;;
  esac
}

detect_profile_file() {
  if [[ -n "$PROFILE_FILE_OVERRIDE" ]]; then
    printf '%s
' "$PROFILE_FILE_OVERRIDE"
    return
  fi
  local shell_name
  shell_name="$(basename "${SHELL:-bash}")"
  case "$shell_name" in
    zsh) printf '%s
' "$HOME/.zshrc" ;;
    bash) printf '%s
' "$HOME/.bashrc" ;;
    *) printf '%s
' "$HOME/.profile" ;;
  esac
}

profile_contains_target() {
  local profile_file="$1"
  [[ -f "$profile_file" ]] && grep -F "$TARGET_DIR" "$profile_file" >/dev/null 2>&1
}

check_path_hint() {
  if path_contains_target; then
    say_ok "PATH contains $TARGET_DIR"
  else
    say_warn "PATH does not contain $TARGET_DIR in the current shell"
  fi
}

ensure_profile_path_entry() {
  if [[ "$AUTO_PATH_UPDATE" != "1" ]]; then
    say_warn "automatic PATH update disabled"
    return
  fi
  if path_contains_target; then
    say_ok "current shell already sees $TARGET_DIR"
    return
  fi
  local profile_file
  profile_file="$(detect_profile_file)"
  mkdir -p "$(dirname "$profile_file")"
  if profile_contains_target "$profile_file"; then
    say_ok "shell profile already contains $TARGET_DIR: $profile_file"
    echo "Reload your shell or run: source $profile_file"
    return
  fi
  {
    printf '
# Added by openvas-cli installer
'
    printf 'export PATH="%s:$PATH"
' "$TARGET_DIR"
  } >> "$profile_file"
  say_ok "added $TARGET_DIR to shell profile: $profile_file"
  echo "Reload your shell or run: source $profile_file"
}

check_config_hint() {
  say_ok "recommended config path: $DEFAULT_CONFIG_PATH"
  case "$SCRIPT_DIR" in
    /mnt/*)
      say_warn "workspace is on a mounted Windows path; keep secrets in $DEFAULT_CONFIG_PATH instead of /mnt"
      ;;
  esac
}

run_prereqs() {
  ensure_source
  check_python
  check_gvm_cli
  check_target_dir
  check_path_hint
  check_config_hint
}

cmd_install() {
  run_prereqs
  ln -sfn "$SOURCE_BIN" "$TARGET_BIN"
  echo "Installed: $TARGET_BIN -> $SOURCE_BIN"
  ensure_profile_path_entry
  echo "Next steps:"
  echo "  openvas-cli onboard"
  echo "  openvas-cli doctor"
}

cmd_uninstall() {
  if [[ -L "$TARGET_BIN" || -f "$TARGET_BIN" ]]; then
    rm -f "$TARGET_BIN"
    echo "Removed: $TARGET_BIN"
  else
    echo "Not installed: $TARGET_BIN"
  fi
}

cmd_reinstall() {
  cmd_install
}

cmd_status() {
  run_prereqs
  if [[ -L "$TARGET_BIN" ]]; then
    echo "Installed: $TARGET_BIN -> $(readlink "$TARGET_BIN")"
  elif [[ -f "$TARGET_BIN" ]]; then
    echo "Occupied by regular file: $TARGET_BIN"
  else
    echo "Not installed: $TARGET_BIN"
  fi
  if [[ "$AUTO_PATH_UPDATE" == "1" ]]; then
    echo "Shell profile target: $(detect_profile_file)"
  fi
}

main() {
  local action="${1:-install}"
  case "$action" in
    install) cmd_install ;;
    uninstall) cmd_uninstall ;;
    reinstall) cmd_reinstall ;;
    status) cmd_status ;;
    -h|--help|help) usage ;;
    *)
      echo "Unknown action: $action" >&2
      usage >&2
      exit 1
      ;;
  esac
}

main "$@"
