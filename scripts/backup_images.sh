#!/usr/bin/env bash
# 备份评测预热镜像到项目文件夹 data/prewarm/images/，防止 docker prune 清掉。
# 恢复: scripts/restore_images.sh
#
# 覆盖几类镜像:
#   iqradar-prewarm/*          deep-swe 预热的 agent 镜像
#   ghcr.io/.../t-bench/*      terminal-bench 基础镜像
#   taichidev/taichi:*         terminal-bench accelerate 任务基础镜像
#
# 说明: 备份的是"镜像存档"而非构建缓存。docker buildx prune 清掉缓存后，
#       pier 构建时会重跑 agent 安装步骤（几分钟、依赖小网络下载）；
#       镜像存档保证最重的部分（数 GB 的 ECR 基础层）永远在本地。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/data/prewarm/images"
mkdir -p "$OUT"

collect() { docker images --format '{{.Repository}}:{{.Tag}}' | grep -E "$1" || true; }

mapfile -t IMAGES < <({
  collect '^iqradar-prewarm/'
  collect '^ghcr.io/laude-institute/t-bench/'
  collect '^taichidev/taichi:'
} | sort -u)

if [ "${#IMAGES[@]}" -eq 0 ]; then
  echo "没有找到需要备份的镜像（先运行 scripts/prewarm_environments.py 预热）"
  exit 0
fi

echo "共 ${#IMAGES[@]} 个镜像，输出目录: $OUT"
for img in "${IMAGES[@]}"; do
  safe="$(printf '%s' "$img" | tr '/:' '__')"
  file="$OUT/${safe}.tar.gz"
  if [ -s "$file" ]; then
    echo "跳过（已存在）: $img"
    continue
  fi
  echo ">>> docker save $img | gzip -> ${safe}.tar.gz"
  if ! docker save "$img" | gzip -1 > "$file"; then
    echo "!! 备份失败: $img"
    rm -f "$file"
    exit 1
  fi
done

docker images --format '{{.Repository}}:{{.Tag}}' \
  | grep -E '^(iqradar-prewarm/|ghcr.io/laude-institute/t-bench/|taichidev/taichi)' \
  | sort -u > "$OUT/MANIFEST.txt"

echo "================================================"
echo "备份完成，共 $(wc -l < "$OUT/MANIFEST.txt") 个镜像"
du -sh "$OUT"
echo "恢复方法: scripts/restore_images.sh"
