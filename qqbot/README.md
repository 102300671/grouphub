# qqbot —— 群资源站机器人（nonebot2）

群资源站网站的 QQ 机器人侧，基于 [nonebot2](https://nonebot.dev/)（`~fastapi` driver）。

**架构约定**：bot 不直连数据库，与 backend 之间只通过 HTTP API 通信，
内部接口用 `X-Bot-Token`（与 `backend/.env` 的 `BOT_API_TOKEN` 一致）鉴权。

- backend 反向调 bot：发验证码私聊、登录通知、触发头像拉取、触发成员同步
- bot 正向调 backend：群成员/头像 upsert、热门/搜索作品、群内「安利」入库

## 插件清单（`plugins/`）

| 插件 | 职责 |
| --- | --- |
| `group_member_sync.py` | 群成员白名单同步：进群/退群事件实时 upsert、启动与定时（`FULL_SYNC_INTERVAL_HOURS`）全量同步；超级用户命令 `sync_member` |
| `avatar_sync.py` | 群成员头像同步：backend 通知后从 QQ 头像外链下载并转存 zfile；超级用户命令 `sync_avatar` |
| `auth_code.py` | 注册绑定 + 登录验证码通道：①（主流程，方向反转）用户在站点获取绑定码后**发给机器人**（群内 @机器人「绑定 <码>」或私聊），bot 调 backend `/bot/auth/verify-register` 核销 → QQ 加白名单 + 绑定官方 openid；②（旧通道）接收 backend 推送的登录验证码并私聊下发、登录成功通知 |
| `works.py` | 群内作品命令：`热门`/`hot`、`搜索 <关键词>`/`search`、`安利 <标题> ...`/`recommend`（无账号自动建号入库）；官方通道下 openid 经注册绑定映射解析回真实 QQ |
| `_lib/bots.py` | 公共库：适配器识别/调度、私聊发送、openid → 真实 QQ 解析（带 TTL 缓存） |
| `_lib/client.py` | 公共库：封装对 backend `/bot/*` 内部 API 的 HTTP 调用（token、超时、错误处理） |

## bot 侧监听的 HTTP 路由（`~fastapi` driver，端口 `PORT`，默认 8083）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/bot/auth/send-code` | backend → bot：私聊下发登录验证码（旧通道） |
| POST | `/bot/auth/notify-login` | backend → bot：登录成功通知（日志/提示） |
| POST | `/bot/auth/verify-register` | bot → backend：注册绑定码核销（验码 → 加白名单 → 绑定 openid） |
| GET | `/bot/auth/resolve-openid` | bot → backend：openid → 真实 QQ 解析（官方通道群命令用） |
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
| `QQ_BOTS` | （主用）QQ 官方开放平台机器人配置（JSON 数组：`id`=AppID、`secret`=AppSecret；`token` 字段 schema 必填但已不参与鉴权，留空 `""` 即可，见 `.env.prod.example`） |
| `QQ_AUTH_BASE` | （可选）Access Token 换取接口，默认 `https://bots.qq.com/app/getAppAccessToken`，官方文档现用 `https://api.bot.qq.com/app/getAppAccessToken` |
| `ONEBOT_WS_URLS` | （备用）OneBot v11 正向 WebSocket 地址列表（NapCat/Lagrange 等，注意复数 URLS） |
| `ONEBOT_ACCESS_TOKEN` | （备用）OneBot access token，与 NapCat 侧一致 |

## 适配器：QQ 官方为主用，OneBot v11 为备用

`bot.py` 同时注册两个适配器，连接方式互不影响，可同时在线：

- **主用 QQ 官方**（`nonebot-adapter-qq`）：配置 `QQ_BOTS` 后经 WebSocket 网关接入。
  鉴权：旧 Bot Token 已废弃，适配器自动用 `AppID + AppSecret` 换 access_token
  （7200s 有效自动刷新），WS / openapi 全部以 `Authorization: QQBot {access_token}` 完成。
  群内命令走 @机器人 触发（intent `c2c_group_at_messages`），`bot.send` 被动回复。
  平台限制：拿不到真实 QQ 号（只有 openid，注册绑定时建立映射）、没有群成员列表 API、
  单聊主动消息受平台限制（私聊需好友名额，注册绑定主打群内 @机器人）。
- **备用 OneBot v11**（`nonebot-adapter-onebot`）：正向 WS（`ONEBOT_WS_URLS`）或
  反向 WS（NapCat 连 `ws://<本机>:8083/onebot/v11/ws`）。

能力分工（`plugins/_lib/bots.py` 统一调度）：

| 能力 | 通道 |
| --- | --- |
| 注册绑定（用户发码给 bot，核销即加白名单 + 绑 openid） | 官方 / OneBot 均可（群内 @机器人 或私聊） |
| 群内命令交互（热门/搜索/安利/同步命令） | 主用 QQ 官方 / 备用 OneBot，谁收到事件谁回复 |
| 成员白名单同步（列表/信息/进退群事件） | 仅 OneBot v11（官方无此 API，仅官方在线时告警跳过） |
| 头像同步（注册触发单个） | 优先 OneBot 在群校验；仅官方在线时跳过校验直接推 qlogo 外链 |
| 头像全量同步 | 仅 OneBot v11（依赖成员列表 API） |
| 登录验证码私聊下发（旧通道） | 仅 OneBot v11 能按真实 QQ 号寻址；仅官方在线时兜底尝试 `send_to_c2c`（多数账号会被平台拒绝） |

> 注册绑定码核销一步即完成加白名单（不依赖 OneBot），但白名单的**日常维护**
> （退群自动置 `is_active=false`、新成员进群同步）仍靠 OneBot 通道，
> 长期只跑官方适配器会让退群成员残留访问权限，建议官方 + OneBot 双开。

## 本地运行

```bash
# 建议独立虚拟环境
python -m venv .venv && . .venv/bin/activate
pip install nb-cli "nonebot2[fastapi,httpx,websockets]" nonebot-adapter-onebot nonebot-adapter-qq
# 首次：创建 .env.prod，按上文「配置」表格填好 AppID/AppSecret（QQ_BOTS）、群号或 WS 地址
nb run   # 检测到 bot.py 后以其为入口
```

## Docker（可选）

`Dockerfile` 已随仓库提供，配合 `deploy/docker-compose.yml` 一键启动：

```bash
cd deploy && docker compose up -d --build
```

> 不用 Docker 时直接 `nb run` 即可，Dockerfile / compose 可完全忽略。
