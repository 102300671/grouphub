# 群图书馆 —— qqbot（nonebot2）镜像
# 部署方式见 deploy/docker-compose.yml；不用 Docker 可忽略本文件
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# nb-cli 提供 `nb run`；其余依赖与 pyproject.toml 保持一致
RUN pip install --no-cache-dir \
    "nb-cli>=1.4.0" \
    "nonebot2[fastapi,httpx,websockets]>=2.5.0" \
    "nonebot-adapter-onebot>=2.4.6" \
    "nonebot-adapter-qq>=1.7.2" \
    "nonebot-plugin-status>=0.9.0" \
    "nonebot-plugin-sentry>=2.0.0" \
    "nonebot-plugin-localstore>=0.7.4"

# bot.py 提供显式入口（QQ 官方主用 + OneBot v11 备用）；plugins/ 是业务插件
COPY bot.py pyproject.toml ./
COPY plugins ./plugins

EXPOSE 8083

# HOST / PORT / DRIVER / BOT_API_TOKEN 等全部由 env_file（.env.prod）注入
CMD ["nb", "run"]
