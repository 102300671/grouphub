#!/usr/bin/env bash
# ================================================================
# 一键 setup —— 非 Docker 部署，单服务器 / NAS 推荐使用
# 功能：
#   1. backend: 若没装依赖就创建 .venv + pip install；复制 .env.example → .env（若不存在）
#   2. qqbot  : 使用项目自带 .venv（在 qqbot/ 下）；pip install -e .，按需装 apscheduler/适配器
#   3. 可选：复制 deploy/*.service → /etc/systemd/system/（需 sudo，脚本末尾会打印提示）
#
# 用法：
#   cd /home/jianying/code/library
#   bash deploy/setup.sh
# ================================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BACKEND_DIR="$ROOT_DIR/backend"
QQBOT_DIR="$ROOT_DIR/qqbot"

echo "========================================================"
echo "  群资源站  一键部署脚本"
echo "  ROOT=$ROOT_DIR"
echo "========================================================"

# ---- 1. backend ----
echo
echo "▶ 准备 backend ..."
cd "$BACKEND_DIR"
if [ ! -d ".venv" ]; then
  echo "  创建 backend .venv ..."
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip >/dev/null
echo "  安装 backend 依赖 ..."
pip install -r requirements.txt
if [ ! -f .env ]; then
  cp .env.example .env
  echo "  [新建] 已生成 backend/.env，请按需修改其中的 JWT_SECRET_KEY、BOT_API_TOKEN 等"
else
  echo "  [跳过] backend/.env 已存在，未覆盖"
fi
mkdir -p data
deactivate

# ---- 2. qqbot ----
echo
echo "▶ 准备 qqbot ..."
NB_VENV="${NB_VENV:-$HOME/venv/nb2}"
cd "$QQBOT_DIR"

if [ ! -x "$NB_VENV/bin/nb" ]; then
  echo "  ⚠ 未找到 $NB_VENV/bin/nb"
  echo "    请先创建 nonebot2 环境并安装适配器，例如："
  echo "      python3 -m venv ~/venv/nb2"
  echo "      source ~/venv/nb2/bin/activate"
  echo "      pip install -U nb-cli"
  echo "      pip install -e \"$QQBOT_DIR\""
  echo "      pip install nonebot-adapter-onebot nonebot-plugin-apscheduler fastapi uvicorn"
  echo "    然后重新运行本脚本"
  exit 1
fi
# shellcheck disable=SC1091
source "$NB_VENV/bin/activate"

echo "  使用 nonebot 环境：$NB_VENV"
echo "  安装/校验 qqbot 依赖（pip install -e .） ..."
pip install -e . >/dev/null

# 推荐 nonebot-plugin-apscheduler（定时全量同步）
if ! python -c "import nonebot_plugin_apscheduler" 2>/dev/null; then
  echo "  推荐：安装 nonebot-plugin-apscheduler 用于定时全量同步"
  pip install nonebot-plugin-apscheduler || echo "    安装失败，可手动重试；不影响启动（仅启动时同步一次）"
fi

# 适配器二选一：未装任何适配器时友好提示
if ! python -c "import nonebot.adapters.onebot.v11" 2>/dev/null \
   && ! python -c "import nonebot.adapters.qq" 2>/dev/null; then
  echo "  ⚠ 未检测到任何 QQ 适配器，请二选一："
  echo "      pip install nonebot-adapter-onebot    # OneBotV11（NapCat / Lagrange）"
  echo "      pip install nonebot-adapter-qq        # QQ 官方开放平台"
fi

if [ ! -f .env.prod ]; then
  echo "  [新建] 请基于 README 创建 qqbot/.env.prod，并填写 BACKEND_API_BASE、BOT_API_TOKEN、SYNC_GROUPS、适配器配置"
fi
deactivate

# ---- 3. systemd 提示 ----
echo
echo "========================================================"
echo "  完成！"
echo "  - backend 启动脚本：$BACKEND_DIR/start.sh  (它会自动激活 .venv)"
echo "  - qqbot   启动方式：source $NB_VENV/bin/activate && cd $QQBOT_DIR && nb run"
echo
echo "  如需 systemd 管理，执行（按需替换 service 文件里的 User/Group）："
echo "      sudo cp $ROOT_DIR/deploy/library-backend.service  /etc/systemd/system/"
echo "      sudo cp $ROOT_DIR/deploy/library-qqbot.service    /etc/systemd/system/"
echo "      sudo systemctl daemon-reload"
echo "      sudo systemctl enable --now library-backend library-qqbot"
echo "========================================================"
