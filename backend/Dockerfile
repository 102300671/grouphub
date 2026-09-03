# 群图书馆 —— backend（FastAPI）镜像
# 部署方式见 deploy/docker-compose.yml；不用 Docker 可忽略本文件
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 先装依赖（利用层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再拷代码
COPY app ./app

# SQLite 数据目录（compose 会把宿主机 ./backend/data 挂到这里）
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8003

# 配置全部由环境变量 / .env 注入（env_file）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003"]
