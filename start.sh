#!/usr/bin/env bash

set -euo pipefail
umask 077

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
port="${IQ_RADAR_PORT:-8080}"
models_path="${IQRADAR_MODELS_PATH:-configs/models.yaml}"
benchmark_path="${IQRADAR_BENCHMARK_PATH:-configs/benchmark.yaml}"
prices_path="${IQRADAR_PRICES_PATH:-configs/prices.example.yaml}"
deepswe_runs_path="${IQRADAR_DEEPSWE_RUNS_PATH:-data/deepswe/runs}"
reporting_path="${IQRADAR_REPORTING_PATH:-data/reporting}"

if ! [[ "$port" =~ ^[0-9]+$ ]] || (( port < 1 || port > 65535 )); then
  printf 'ERROR: IQ_RADAR_PORT must be an integer from 1 to 65535.\n' >&2
  exit 1
fi

for command in uv node npm docker; do
  if ! command -v "$command" >/dev/null 2>&1; then
    printf 'ERROR: required command not found: %s\n' "$command" >&2
    exit 1
  fi
done

if python3 - "$port" <<'PY'
import socket
import sys

with socket.socket() as sock:
    sock.settimeout(0.2)
    if sock.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0:
        raise SystemExit(0)
raise SystemExit(1)
PY
then
  printf 'ERROR: port already in use: %s\n' "$port" >&2
  exit 1
fi

cd "$project_root"

# WSL 根文件系统只读，默认的 ~/.cache/uv 不可写；把 uv 缓存重定向到项目内
# 可写的 .uv-cache，否则 `uv sync` 会在锁/临时文件处报 "Read-only file system"。
export UV_CACHE_DIR="${UV_CACHE_DIR:-$project_root/.uv-cache}"
mkdir -p "$UV_CACHE_DIR"

# 题目目录统一在 data/datasets/。git 自带的任务树仍在 checkouts/ 里，
# 这里放相对 symlink（data/ 与 checkouts/ 都 gitignore，新机器启动时补上）。
ensure_dataset_link() {
  local link="$1"
  local target="$2"
  local dest_dir
  dest_dir="$(dirname "$link")"
  mkdir -p "$dest_dir"
  if [[ -L "$link" ]]; then
    if [[ "$(readlink "$link")" == "$target" ]]; then
      return
    fi
    rm "$link"
  elif [[ -e "$link" ]]; then
    printf 'WARN: %s exists and is not a symlink; leaving it alone.\n' "$link" >&2
    return
  fi
  if [[ -e "$dest_dir/$target" ]]; then
    ln -s "$target" "$link"
  fi
}
ensure_dataset_link data/datasets/deep-swe ../../checkouts/deep-swe/tasks
ensure_dataset_link data/datasets/terminal-bench-2 ../../checkouts/terminal-bench/tasks

if [[ -f .env ]]; then
  chmod 600 .env
fi
for private_path in data/deepswe data/reporting data/raw data/aggregate; do
  if [[ -e "$private_path" ]]; then
    if ! chmod -R go-rwx "$private_path" 2>/dev/null; then
      printf 'WARN: some files under %s are owned by another runtime and could not be restricted.\n' "$private_path" >&2
    fi
  fi
done
uv sync --locked

if [[ ! -d web/node_modules ]]; then
  npm ci --prefix web
fi

npm run build --prefix web
printf 'IQRadar is available at http://127.0.0.1:%s\n' "$port"

server_pid=""

cleanup() {
  trap - EXIT INT TERM
  if [[ -n "$server_pid" ]]; then
    kill "$server_pid" 2>/dev/null || true
  fi
  wait "$server_pid" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

uv run python -m iqradar.cli serve \
  --host 127.0.0.1 \
  --port "$port" \
  --models "$models_path" \
  --benchmark "$benchmark_path" \
  --prices "$prices_path" \
  --deepswe-runs-path "$deepswe_runs_path" \
  --reporting-path "$reporting_path" &
server_pid=$!

set +e
wait "$server_pid"
status=$?
set -e
exit "$status"
