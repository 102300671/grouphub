"""grouphub 共享包。

放在仓库根，两端（qqbot / backend）各自把仓库根加入 sys.path 后 import，
不做 pip 安装 —— 部署时云端只需 git pull + 重启。

约束（见 .ai/CONTRACT.md）：
- 本包**不得** import fastapi / nonebot，宿主能力一律走 aitools/ports.py 抽象；
- 同一份实现只写一次，QQ 端与 Web 端共用，禁止各自复制。
"""
