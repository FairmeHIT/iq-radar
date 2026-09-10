#!/usr/bin/env bash
# 打包 IQRadar 源码用于在新机器上部署（级别：代码 only + 外网 + 外部网关）。
#
# 用法： ./scripts/pack.sh [output.tar.gz]
#
# 排除所有运行时产物、依赖缓存、外部检出、敏感配置；保留全部源码 + 配置 +
# uv.lock。新机器解压后按 README「快速开始」部署即可。

set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ts="$(date +%Y%m%d)"
out="${1:-/tmp/opencode/iqradar-code-${ts}.tar.gz}"

mkdir -p "$(dirname -- "$out")"

# 必须显式带上 uv.lock（被 .gitignore 排除，但 start.sh 的 uv sync --locked 必需）。
required_files=(uv.lock)

cd "$project_root"
missing=()
for f in "${required_files[@]}"; do
  [[ -f "$f" ]] || missing+=("$f")
done
if (( ${#missing[@]} > 0 )); then
  printf 'ERROR: missing required files: %s\n' "${missing[*]}" >&2
  exit 1
fi

parent="$(dirname -- "$project_root")"
base="$(basename -- "$project_root")"

# 排除模式：路径相对 <parent>/（即带 <base>/ 前缀）。
tar -czf "$out" \
  --exclude="$base/.venv" \
  --exclude="$base/.uv-cache" \
  --exclude="$base/.npm-cache" \
  --exclude="$base/.hf-cache" \
  --exclude="$base/.pytest_cache" \
  --exclude="$base/.coverage" \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude="$base/.docker-build" \
  --exclude="$base/.docker-buildx" \
  --exclude="$base/.docker-config" \
  --exclude="$base/checkouts" \
  --exclude="$base/vendor" \
  --exclude='node_modules' \
  --exclude="$base/web/dist" \
  --exclude="$base/web/test-results" \
  --exclude="$base/web/playwright-report" \
  --exclude="$base/data" \
  --exclude="$base/NUL" \
  --exclude="$base/.env" \
  -C "$parent" \
  "$base"

printf 'OK: %s\n' "$out"
du -h "$out"
printf 'contents (top-level):\n'
tar -tzf "$out" | sed "s#^$base/##" | awk -F/ 'NF<=2' | sort -u | head -40
