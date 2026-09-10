#!/usr/bin/env bash
# 备份 Terminal-Bench 2.0 已构建的 Docker 镜像到 data/prewarm/images/，
# 防止 docker prune / buildx prune 清掉后下次评测重复联网下载。
#
# 与 scripts/backup_images.sh（覆盖 deep-swe / t-bench 基础镜像）
# 互补：本脚本专门处理 TB2 的 *运行时构建产物*。
#
# 两类镜像：
#   1) 任务环境镜像  <task>__<trial-suffix>__env-main[:latest] 等
#      harbor 给每个 trial 一个 7 位随机 ShortUUID 后缀，一次评测会为同一
#      任务产生 2-N 个内容相同的 tag（共享 buildkit 层）。本脚本按
#      「任务 + 角色」去重，每任务每角色只存最新的一个 tag，避免把同一层
#      存多份。恢复后下次评测 compose build 仍会重新 tag 新随机后缀，但
#      buildkit 缓存（若也在）/本地基础镜像层会命中——重建秒级。
#   2) TB2 基础镜像    扫描 tasks/*/environment/Dockerfile 的 FROM 与
#      docker-compose.yaml 的 image:，收集去重后 docker save。这些
#      ghcr.io / docker.io 镜像在本网络被限速，是“下次零联网”的关键。
#
# 恢复：scripts/restore_images.sh（通用 docker load，无需改动）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/data/prewarm/images"
TASKS="$ROOT/data/datasets/terminal-bench-2"
mkdir -p "$OUT"

# save_image <repo:tag> <output-basename>
# 已存在同名存档则跳过；失败即退出。
save_image() {
  local img="$1" base="$2" file="$OUT/${base}.tar.gz"
  if [ -s "$file" ]; then
    echo "跳过（已存在）: $img -> ${base}.tar.gz"
    return 0
  fi
  echo ">>> docker save $img | gzip -1 -> ${base}.tar.gz"
  if ! docker save "$img" 2>/dev/null | gzip -1 > "$file"; then
    echo "!! 备份失败: $img"
    rm -f "$file"
    return 1
  fi
}

# --- 1) TB2 任务环境镜像：按 (task, role) 去重，留最新 ----------------------
# repo 形如 music-harmony__hzt3xb5__verifier__trial-main
#      或 payments-pipeline-fix__wc6ugjz__env-seeder
# 拆分：task=${1}  role=${3..}  suffix=${2}
# 按 (task,role) 分组取 CreatedAt 最新。
mapfile -t ENV_IMGS < <(docker images --format '{{.Repository}}:{{.Tag}} {{.CreatedAt}}' \
  | awk '/__env-[a-z]/ || /__verifier__trial-/ {print}' \
  | sort -u)

if [ "${#ENV_IMGS[@]}" -gt 0 ]; then
  # 用 awk 做去重：key=task||role，保留 CreatedAt（ISO，可字典序比较）最大者。
  # 输出 "repo:tag" 列表。
  mapfile -t DEDUP < <(printf '%s\n' "${ENV_IMGS[@]}" | awk '
    {
      full=$1; ts=$2" "$3" "$4" "$5" "$6;   # CreatedAt 形如 "2026-09-05 22:53:29 +0800 CST"
      n=split(full, a, ":");                 # 按 : 拆 repo:tag
      repo=a[1];
      gsub(/:latest$/, "", full);            # 去掉 trailing :latest 方便按 __ 拆
      split(full, p, "__");
      task=p[1]; role=p[3];
      for (i=4; i in p; i++) role=role"__"p[i];
      key=task"\t"role;
      if (!(key in best) || ts > bestts[key]) { best[key]=full; bestts[key]=ts; }
    }
    END { for (k in best) print best[k]; }')
  echo "TB2 环境镜像：${#ENV_IMGS[@]} 个 tag -> 去重后 ${#DEDUP[@]} 个（每任务每角色取最新）"
  for img in "${DEDUP[@]}"; do
    [ -n "$img" ] || continue
    # 存档名：把 __ 和 : 折成单 -，前缀 tb2-env-
    safe="$(printf '%s' "$img" | sed -E 's/:latest$//; s/__/-/g; s/[:/]/-/g')"
    save_image "$img:latest" "tb2-env-${safe}" || exit 1
  done
else
  echo "（没有发现 TB2 环境镜像，先跑评测或 scripts/prewarm_environments.py --benchmark terminal-bench-2）"
fi

# --- 2) TB2 基础镜像：扫描 Dockerfile FROM + compose image ----------------
declare -a BASE_PATTERNS=()
scan_from() {
  local f="$1"
  [ -f "$f" ] || return 0
  # FROM 行：去掉 --platform=xxx、AS alias，取首个 token；剥离 digest
  awk 'tolower($1)=="from"{
    for(i=2;i<=NF;i++){ if($i ~ /^--/) continue; if(tolower($i)=="as") break;
      ref=$i; sub(/@sha256:.*/, "", ref); print ref; break } }' "$f"
}
scan_compose_image() {
  local f="$1"
  [ -f "$f" ] || return 0
  # YAML 里 image: <ref> （简单行匹配，够用）
  awk 'match($0, /[[:space:]]*image:[[:space:]]*"?([^"[:space:]]+)"?/, m){ print m[1] }' "$f"
}

declare -A BASESET=()
while IFS= read -r taskdir; do
  [ -d "$taskdir/environment" ] || continue
  scan_from "$taskdir/environment/Dockerfile"
  scan_compose_image "$taskdir/environment/docker-compose.yaml"
done < <(find -L "$TASKS" -maxdepth 1 -mindepth 1 -type d 2>/dev/null) \
  | sort -u > /tmp/tb2-bases.txt

# 过滤：只保存当前本地已存在的（避免 save 不存在的 ref 报错）+ 跳过已是
# TB2 env tag 的（形如 xxx__yyy）。ghcr.io/laude-institute/t-bench 与
# taichidev/taichi 已由 backup_images.sh 覆盖，这里不重复。
n_base=0
while IFS= read -r ref; do
  [ -n "$ref" ] || continue
  case "$ref" in
    *__*) continue ;;                       # TB2 env tag，跳过
    ghcr.io/laude-institute/t-bench/*) continue ;;
    taichidev/taichi*) continue ;;
  esac
  # 本地是否存在该 ref（docker image inspect 接受 repo:tag 或 repo@digest）
  if docker image inspect "$ref" >/dev/null 2>&1; then
    safe="$(printf '%s' "$ref" | sed -E 's/@sha256.*//; s/[:/]/-/g')"
    save_image "$ref" "tb2-base-${safe}" || exit 1
    n_base=$((n_base+1))
  else
    echo "  跳过基础镜像（本地不存在，下次需要时会重新拉取）: $ref"
  fi
done < /tmp/tb2-bases.txt
rm -f /tmp/tb2-bases.txt

echo "================================================"
echo "TB2 备份完成。环境镜像 $(ls "$OUT"/tb2-env-*.tar.gz 2>/dev/null | wc -l) 个，基础镜像 $n_base 个"
du -sh "$OUT" 2>/dev/null | cut -f1 | xargs echo "存档目录总大小:"
echo "恢复方法: scripts/restore_images.sh"
