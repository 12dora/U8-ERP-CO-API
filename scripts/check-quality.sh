#!/usr/bin/env bash
# 代码异味门禁：Ruff（Python）、文件与函数长度、PowerShell AST、C#（lizard）。
# 任一失败则以非 0 退出。本仓库没有基线，超限就是失败。
# 已有 uv 缓存和 pwsh 时不再访问网络。
set -euo pipefail

SOURCE=$(readlink -f "${BASH_SOURCE[0]}")
ROOT=$(cd "$(dirname "$SOURCE")/.." && pwd)
cd "$ROOT"

RUFF_VERSION=0.16.9
LIZARD_VERSION=1.17.13
CHANGED=0

usage() {
  cat <<'EOF'
用法: scripts/check-quality.sh [--changed]

  检查 Python 异味（Ruff）、文件 / 函数长度、PowerShell 的解析和圈复杂度，
  以及 co/**/*.cs 的文件行数和 lizard 函数度量。
  --changed    只检查相对 HEAD 有改动的文件（含未跟踪文件）
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --changed) CHANGED=1 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "未知参数: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if ! command -v python3 >/dev/null 2>&1 || ! python3 -c 'import sys' >/dev/null 2>&1; then
  cat >&2 <<'EOF'
未找到可用的 python3。
体积门禁是标准库脚本，需要 python3 能执行。
EOF
  exit 1
fi

if ! command -v uvx >/dev/null 2>&1; then
  cat >&2 <<EOF
未找到 uvx。
Python 异味检查固定使用 uvx ruff@${RUFF_VERSION}。
C# 函数度量固定使用 uvx --from lizard==${LIZARD_VERSION} lizard。
两者都不装进本仓库。
请安装 uv（https://docs.astral.sh/uv/），装好后 PATH 里会有 uvx。
EOF
  exit 1
fi

declare -a CHANGED_FILES=()
if [[ "$CHANGED" -eq 1 ]]; then
  if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "--changed 需要在 git 工作区里运行" >&2
    exit 2
  fi
  while IFS= read -r -d '' file; do
    [[ -n "$file" ]] && CHANGED_FILES+=("$file")
  done < <(git diff -z --name-only --diff-filter=ACMR HEAD; git ls-files -z --others --exclude-standard)
fi

# 与 ruff.toml 的 include 一致：api/、co/、events/、mcp/ 下的 Python，以及泄露扫描脚本、文档站点脚本和它们的测试。
is_app_py() {
  [[ "$1" == *.py ]] || return 1
  [[ "$1" == api/* || "$1" == co/* || "$1" == events/* || "$1" == mcp/* ]] && return 0
  [[ "$1" == scripts/scan-public.py || "$1" == scripts/docs/*.py || "$1" == scripts/tests/* ]]
}

# 与 cs_metrics.py 一致：co/ 下的 C#（co/bridge 等）。各项检查都按目录遍历，缺少的目录直接跳过。
is_co_cs() {
  [[ "$1" == co/*.cs ]]
}

py_version=$(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')
echo "python3 ${py_version}"
echo "ruff ${RUFF_VERSION}（uvx ruff@${RUFF_VERSION}）"
echo "lizard ${LIZARD_VERSION}（uvx --from lizard==${LIZARD_VERSION} lizard）"

declare -a RUFF_TARGETS=()
declare -a SIZE_ARGS=()
declare -a PS_FILES=()
declare -a CS_FILES=()
if [[ "$CHANGED" -eq 1 ]]; then
  SIZE_ARGS=(--subset)
  for file in "${CHANGED_FILES[@]}"; do
    SIZE_ARGS+=("$file")
    if is_app_py "$file"; then
      RUFF_TARGETS+=("$file")
    fi
    if [[ "$file" == *.ps1 ]]; then
      PS_FILES+=("$file")
    fi
    if is_co_cs "$file"; then
      CS_FILES+=("$file")
    fi
  done
fi

ruff_status=0
size_status=0
ps_status=0
cs_status=0

format_ruff() {
  # python3 - 用标准输入读脚本。JSON 必须走参数文件，不能再走管道。
  python3 - "$ROOT" "$1" <<'PY'
import json, re, sys
from pathlib import Path

root = Path(sys.argv[1])
limits = {"C901": 10, "PLR0912": 12, "PLR0913": 5, "PLR0915": 50}
metric_re = re.compile(r"\((\d+)\s*>")
name_re = re.compile(r"`([^`]+)`")
raw = Path(sys.argv[2]).read_text(encoding="utf-8")
try:
    diags = json.loads(raw or "[]")
except json.JSONDecodeError:
    sys.stderr.write(raw)
    raise SystemExit(1)
rows = []
for diag in diags:
    code = diag.get("code")
    if code not in limits:
        continue
    matched = metric_re.search(diag.get("message") or "")
    metric = matched.group(1) if matched else "?"
    path = Path(diag.get("filename") or "")
    if path.is_absolute():
        try:
            path = path.resolve().relative_to(root)
        except ValueError:
            pass
    line = diag.get("location", {}).get("row", 1)
    named = name_re.search(diag.get("message") or "")
    suffix = f" name={named.group(1)}" if named else ""
    rows.append(f"{path.as_posix()}:{line} {code} metric={metric} limit={limits[code]}{suffix}")
if rows:
    print("\n".join(rows))
    print(f"ruff FAIL {len(rows)}")
    raise SystemExit(1)
print("ruff OK")
PY
}

run_ruff() {
  local -a cmd=(uvx "ruff@${RUFF_VERSION}" check --config "$ROOT/ruff.toml" --output-format json --no-cache)
  if [[ "$CHANGED" -eq 1 ]]; then
    if [[ ${#RUFF_TARGETS[@]} -eq 0 ]]; then
      echo "ruff SKIP 没有 Python 改动"
      return 0
    fi
    cmd+=("${RUFF_TARGETS[@]}")
  else
    cmd+=(.)
  fi
  local out err rc
  err=$(mktemp)
  set +e
  out=$("${cmd[@]}" 2>"$err")
  rc=$?
  set -e
  if [[ "$rc" -gt 1 ]]; then
    cat "$err" >&2
    rm -f "$err"
    echo "Ruff 运行失败（退出码 ${rc}）。需要能执行 uvx ruff@${RUFF_VERSION}。" >&2
    return 1
  fi
  if [[ -s "$err" ]]; then
    cat "$err" >&2
  fi
  rm -f "$err"
  local json
  json=$(mktemp)
  printf '%s' "$out" > "$json"
  set +e
  format_ruff "$json"
  rc=$?
  set -e
  rm -f "$json"
  return "$rc"
}

echo "======== Ruff ========"
if run_ruff; then
  ruff_status=0
else
  ruff_status=$?
fi

echo "======== 体积 ========"
if python3 "$ROOT/scripts/quality/sizes.py" "${SIZE_ARGS[@]}"; then
  size_status=0
else
  size_status=$?
fi

echo "======== PowerShell ========"
run_ps() {
  if [[ "$CHANGED" -eq 1 && ${#PS_FILES[@]} -eq 0 ]]; then
    echo "ps SKIP 没有 ps1 改动"
    return 0
  fi
  if [[ "$CHANGED" -eq 1 ]]; then
    bash "$ROOT/scripts/quality/pwsh.sh" --smell -- "${PS_FILES[@]}"
    return $?
  fi
  bash "$ROOT/scripts/quality/pwsh.sh" --smell
}

if run_ps; then
  ps_status=0
else
  ps_status=$?
fi

echo "======== C# ========"
run_cs() {
  if [[ "$CHANGED" -eq 1 && ${#CS_FILES[@]} -eq 0 ]]; then
    echo "cs SKIP 没有 C# 改动"
    return 0
  fi
  if [[ "$CHANGED" -eq 1 ]]; then
    python3 "$ROOT/scripts/quality/cs_metrics.py" --subset -- "${CS_FILES[@]}"
    return $?
  fi
  python3 "$ROOT/scripts/quality/cs_metrics.py"
}

if run_cs; then
  cs_status=0
else
  cs_status=$?
fi

label() {
  if [[ "$1" -eq 0 ]]; then
    echo "通过"
  else
    echo "失败"
  fi
}

echo "======== 汇总 ========"
echo "Ruff        $(label "$ruff_status")"
echo "体积        $(label "$size_status")"
echo "PowerShell  $(label "$ps_status")"
echo "C#          $(label "$cs_status")"
if [[ "$ruff_status" -eq 0 && "$size_status" -eq 0 && "$ps_status" -eq 0 && "$cs_status" -eq 0 ]]; then
  echo "通过"
  exit 0
fi
echo "未通过"
exit 1
