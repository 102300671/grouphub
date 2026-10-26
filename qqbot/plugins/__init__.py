"""nonebot2 插件目录。

仓库根加入 sys.path：两端共享的 shared/aitools 放在仓库根，两端各自 import，
不做 pip 安装 —— 部署时云端只需 git pull + 重启。

放在这里是因为 nonebot 加载插件时一定先 import 本包（父包先于子模块），
本文件里的路径引导因此对所有插件模块生效。
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]  # qqbot/plugins/ → 仓库根
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
