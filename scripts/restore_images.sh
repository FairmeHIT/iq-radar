#!/usr/bin/env bash
# 从 data/prewarm/images/ 恢复评测预热镜像（docker load）。
# 被 docker prune / buildx prune 清掉后，跑这个即可恢复，无需重新联网拉取。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IN="$ROOT/data/prewarm/images"

if [ ! -d "$IN" ]; then
  echo "没有备份目录: $IN（先运行 scripts/backup_images.sh）"
  exit 0
fi

shopt -s nullglob
files=("$IN"/*.tar.gz)
if [ "${#files[@]}" -eq 0 ]; then
  echo "没有找到备份文件（先运行 scripts/backup_images.sh）"
  exit 0
fi

echo "共 ${#files[@]} 个备份文件，开始恢复..."
for f in "${files[@]}"; do
  echo ">>> docker load < $(basename "$f")"
  if ! docker load < "$f"; then
    echo "!! 恢复失败: $f"
    exit 1
  fi
done

echo "================================================"
echo "恢复完成。当前相关镜像:"
docker images --format '{{.Repository}}:{{.Tag}}' \
  | grep -E '^(iqradar-prewarm/|ghcr.io/laude-institute/t-bench|taichidev/taichi|tb2-)' \
  | sort -u || true
echo "--- TB2 任务环境镜像（按任务去重后应有每任务一条）---"
docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null \
  | grep -E '__env-|__verifier__trial-' | sort -u || true
