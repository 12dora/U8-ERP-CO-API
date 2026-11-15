#!/usr/bin/env bash
# 定位 pwsh：先 PATH，再 ~/.cache/u8co/pwsh-<版本>。
# 缓存没有就从官方 GitHub release 下载 linux-x64 包，校验脚本里固定的 sha256。
# curl 使用环境里的 https_proxy / HTTPS_PROXY。不要依赖别的目录里解压好的副本。
set -euo pipefail

SOURCE=$(readlink -f "${BASH_SOURCE[0]}")
ROOT=$(cd "$(dirname "$SOURCE")/../.." && pwd)
METRICS="$ROOT/scripts/quality/ps-metrics.ps1"

PWSH_VERSION=7.4.6
PWSH_SHA256=6f6015203c47806c5cc444c19d8ed019695e610fbd948154264bf9ca8e157561
PWSH_ASSET="powershell-${PWSH_VERSION}-linux-x64.tar.gz"
PWSH_URL="https://github.com/PowerShell/PowerShell/releases/download/v${PWSH_VERSION}/${PWSH_ASSET}"

JSON=0
SMELL=0
EXPLICIT=0
declare -a FILES=()

usage() {
  cat <<'EOF'
用法: scripts/quality/pwsh.sh [--json] [--smell] [-- 文件...]

  不带文件参数时扫描整个仓库的 *.ps1。
  -- 后面是显式文件列表；列表为空就什么都不查（退出码 0）。
  --json    把度量打成 JSON，退出码 0（只要 pwsh 跑起来）
  --smell   只报告解析错误和圈复杂度。文件长度和函数长度由 sizes.py 报。
EOF
}

cache_root() {
  local base=${XDG_CACHE_HOME:-$HOME/.cache}
  printf '%s\n' "${base}/u8co/pwsh-${PWSH_VERSION}"
}

path_pwsh() {
  local bin
  bin=$(command -v pwsh 2>/dev/null || true)
  [[ -n "$bin" && -x "$bin" ]] || return 1
  "$bin" -NoProfile -Command 'exit 0' >/dev/null 2>&1 || return 1
  printf '%s\n' "$bin"
}

download_pwsh() {
  local dest=$1
  local tmp archive
  tmp=$(mktemp -d)
  archive="$tmp/$PWSH_ASSET"
  echo "正在下载 PowerShell ${PWSH_VERSION}（linux-x64）到 ${dest}" >&2
  if ! curl -fsSL --retry 3 --retry-delay 2 -o "$archive" "$PWSH_URL"; then
    rm -rf "$tmp"
    cat >&2 <<EOF
下载 PowerShell ${PWSH_VERSION} 失败。
地址：${PWSH_URL}
curl 会使用环境里的 https_proxy / HTTPS_PROXY。
确认代理可用，或把官方 linux-x64 压缩包解压到 ${dest} 后再运行。
EOF
    exit 1
  fi
  if ! echo "${PWSH_SHA256}  ${archive}" | sha256sum -c - >/dev/null; then
    rm -rf "$tmp"
    echo "PowerShell 压缩包的 sha256 与脚本中固定的值不一致，已删除，不会使用。" >&2
    exit 1
  fi
  rm -rf "$dest"
  mkdir -p "$dest"
  tar -xzf "$archive" -C "$dest"
  rm -rf "$tmp"
  chmod +x "$dest/pwsh"
  if [[ ! -x "$dest/pwsh" ]]; then
    echo "解压后没有可执行文件 ${dest}/pwsh。" >&2
    exit 1
  fi
}

locate_pwsh() {
  local bin dir
  if bin=$(path_pwsh); then
    printf '%s\n' "$bin"
    return 0
  fi
  dir=$(cache_root)
  bin="${dir}/pwsh"
  if [[ -x "$bin" ]] && "$bin" -NoProfile -Command 'exit 0' >/dev/null 2>&1; then
    printf '%s\n' "$bin"
    return 0
  fi
  download_pwsh "$dir"
  printf '%s\n' "$bin"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --json) JSON=1 ;;
    --smell) SMELL=1 ;;
    --) EXPLICIT=1; shift; FILES+=("$@"); break ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "未知参数: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [[ "$EXPLICIT" -eq 1 && ${#FILES[@]} -eq 0 ]]; then
  if [[ "$JSON" -eq 1 ]]; then
    printf '%s\n' '{"version":"","results":[]}'
  else
    echo "ps OK"
  fi
  exit 0
fi

bin=$(locate_pwsh)
ver=$("$bin" -NoProfile -Command '$PSVersionTable.PSVersion.ToString()')
echo "pwsh ${ver}" >&2

args=(-NoProfile -File "$METRICS" -Root "$ROOT")
if [[ "$JSON" -eq 1 ]]; then
  args+=(-Json)
fi
if [[ "$SMELL" -eq 1 ]]; then
  args+=(-Smell)
fi
if [[ ${#FILES[@]} -gt 0 ]]; then
  args+=("${FILES[@]}")
fi
exec "$bin" "${args[@]}"
