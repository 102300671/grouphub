"""qqbot 入口 —— 适配器注册（QQ 官方主用，OneBot v11 备用）。

- 主用：QQ 官方开放平台适配器（nonebot-adapter-qq）
  连接配置来自 .env.prod 的 QQ_BOTS（群 @ 消息用 c2c_group_at_messages intent）。
- 备用：OneBot v11 适配器（nonebot-adapter-onebot，NapCat/LLOneBot 等）
  反向 WS：NapCat 连 ws://<本机>:8083/onebot/v11/ws
  正向 WS：配置 ONEBOT_WS_URLS（注意是复数）。

`nb run` 检测到本文件后以 bot.py 作为入口（systemd / Docker 均无需改动）。
"""
import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter
from nonebot.adapters.qq import Adapter as QQAdapter

nonebot.init()

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
