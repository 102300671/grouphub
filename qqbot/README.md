# qqbot —— 群图书馆机器人（nonebot2）

群图书馆网站的 QQ 机器人侧，基于 [nonebot2](https://nonebot.dev/)（`~fastapi` driver）。

**架构约定**：bot 不直连数据库，与 backend 之间只通过 HTTP API 通信，
内部接口用 `X-Bot-Token`（与 `backend/.env` 的 `BOT_API_TOKEN` 一致）鉴权。

- backend 反向调 bot：发验证码私聊、登录通知、触发头像拉取、触发成员同步
- bot 正向调 backend：群成员/头像 upsert、热门/搜索作品、群内「安利」入库

## 插件清单（`plugins/`）

| 插件 | 职责 |
| --- | --- |
| `group_member_sync.py` | 群成员白名单同步：进群/退群事件实时 upsert、启动与定时（`FULL_SYNC_INTERVAL_HOURS`）全量同步；超级用户命令 `sync_member` |
| `avatar_sync.py` | 群成员头像同步：backend 通知后从 QQ 头像外链下载并转存 zfile；超级用户命令 `sync_avatar` |
| `auth_code.py` | 验证码通道：接收 backend 推送的登录验证码并私聊下发；登录成功通知 |
| `works.py` | 群内作品命令：`热门`/`hot`、`搜索 <关键词>`/`search`、`安利 <标题> ...`/`recommend`（无账号自动建号入库） |
| `_lib/client.py` | 公共库：封装对 backend `/bot/*` 内部 API 的 HTTP 调用（token、超时、错误处理） |

## bot 侧监听的 HTTP 路由（`~fastapi` driver，端口 `PORT`，默认 8083）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/bot/auth/send-code` | backend → bot：私聊下发验证码 |
| POST | `/bot/auth/notify-login` | backend → bot：登录成功通知（日志/提示） |
| POST | `/bot/admin/trigger_sync` | backend 管理端 → bot：触发群成员全量同步 |
| POST | `/bot/avatars/fetch` | backend → bot：拉取并转存指定 QQ 头像 |

所有路由都校验 `X-Bot-Token`，token 未配置或不符一律 401（fail-closed）。

## 配置（`.env.prod`）

| 变量 | 说明 |
| --- | --- |
| `DRIVER` | `~fastapi+~httpx+~websockets`（fastapi 提供 HTTP 服务，websockets 连 OneBot） |
| `HOST` / `PORT` | bot HTTP 服务监听，默认 `0.0.0.0:8083` |
| `BACKEND_API_BASE` | backend 地址，如 `http://127.0.0.1:8003` |
| `BOT_API_TOKEN` | 内部 API 共享密钥，必须与 `backend/.env` 的 `BOT_API_TOKEN` 完全一致 |
| `SITE_BASE_URL` | 站点前端地址，用于群消息里的详情跳转链接 |
| `SYNC_GROUPS` | 要同步的群号，英文逗号分隔 |
| `FULL_SYNC_INTERVAL_HOURS` | 全量同步间隔（小时），启动时也会触发一次 |
| `ONEBOT_WS_URL` | OneBot v11 反向 WebSocket 地址（NapCat/Lagrange 等） |
| `QQ_BOTS` | （可选）QQ 官方开放平台机器人配置 |

## 本地运行

```bash
# 建议独立虚拟环境
python -m venv .venv && . .venv/bin/activate
pip install nb-cli "nonebot2[fastapi,httpx,websockets]" nonebot-adapter-onebot
# 首次：创建 .env.prod，按上文「配置」表格填好 token / 群号 / WS 地址
nb run
```

## Docker（可选）

`Dockerfile` 已随仓库提供，配合 `deploy/docker-compose.yml` 一键启动：

```bash
cd deploy && docker compose up -d --build
```

> 不用 Docker 时直接 `nb run` 即可，Dockerfile / compose 可完全忽略。
