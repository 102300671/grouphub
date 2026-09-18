"""qqbot 入口 —— 适配器注册（QQ 官方主用，OneBot v11 备用）。

- 主用：QQ 官方开放平台适配器（nonebot-adapter-qq）
  连接配置来自 .env.prod 的 QQ_BOTS（群 @ 消息用 c2c_group_at_messages intent）。
- 备用：OneBot v11 适配器（nonebot-adapter-onebot，NapCat/LLOneBot 等）
  反向 WS：NapCat 连 ws://<本机>:8083/onebot/v11/ws
  正向 WS：配置 ONEBOT_WS_URLS（注意是复数）。

`nb run` 检测到本文件后以 bot.py 作为入口（systemd / Docker 均无需改动）。
"""
import os
from pathlib import Path

import nonebot
from loguru import logger as _loguru_logger
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.adapters.qq import Adapter as QQAdapter
from nonebot.log import default_filter, default_format

nonebot.init()

# 日志双写：终端 sink 由 nonebot 自动配置；这里额外加一个文件 sink。
# 文件：log/bot-YYYY-MM-DD.log，每天 00:00 轮转，默认保留 14 天（LOG_RETENTION 可覆盖）。
# filter/format 与终端一致，遵循 .env.prod 的 LOG_LEVEL；enqueue 保证多线程写入安全。
_LOG_DIR = Path(os.getenv("LOG_DIR", Path(__file__).resolve().parent / "log"))
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_loguru_logger.add(
    _LOG_DIR / "bot-{time:YYYY-MM-DD}.log",
    rotation="00:00",
    retention=os.getenv("LOG_RETENTION", "14 days"),
    encoding="utf-8",
    enqueue=True,
    level=0,
    filter=default_filter,
    format=default_format,
)
nonebot.logger.info(f"日志文件输出已启用：{_LOG_DIR}（按天轮转，保留 {os.getenv('LOG_RETENTION', '14 天')}）")

driver = nonebot.get_driver()

# 主用：QQ 官方开放平台（未配置 QQ_BOTS 时不会建立连接，自动落到备用）
driver.register_adapter(QQAdapter)
# 备用：OneBot v11（未配置 WS 连接 / NapCat 未反向接入时不会建立连接）
driver.register_adapter(OneBotV11Adapter)

nonebot.load_builtin_plugins("echo", "single_session")
nonebot.load_from_toml("pyproject.toml")

nonebot.logger.info("适配器已注册：QQ 官方（主用） + OneBot v11（备用）")

if __name__ == "__main__":
    nonebot.run()
