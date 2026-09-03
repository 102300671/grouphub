#!/usr/bin/env bash
# backend 一键启动脚本（推荐配合 venv 使用）
# 用法：
#   cd backend
#   python -m venv .venv && . .venv/bin/activate
#   pip install -r requirements.txt
#   cp .env.example .env     # 然后修改 .env
#   ./start.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 保证 data 目录存在（SQLite 文件位置）
mkdir -p data

# 加载 .env（如存在）
if [ -f .env ]; then
  # shellcheck disable=SC2046
  export $(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' .env | grep -v '^#' | xargs)
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8003}"

echo "🌸 启动群资源站 backend：${HOST}:${PORT}"
echo "   文档地址：http://localhost:${PORT}/docs"
exec uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
