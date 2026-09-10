# 群资源站（Grouphub）

一个 **QQ 群私域资源共享站**（原名"群图书馆"）：为百合（GL）同好群搭建的内部网站。群友共同维护一份**公共作品库**（小说 / 番剧·动漫 / 电影 / 同人文），提供站内阅读与观看、选章/选集下载、书评打分、话题讨论、同人创作展示，并由 nonebot2 QQ 机器人联动（注册验证绑定、白名单同步、群内发作品等）。

> 定位：私域内部站点，仅群成员（经 QQ 群白名单）可访问；好维护、低成本、可扩展，主要面向管理员自维护、群友低门槛使用。

---

## 功能一览

- **公共作品库**：谁先上传谁为上传者，其他人以"支持者 / 推荐者"身份加入；支持小说、番剧/动漫、电影、同人文、其他类型
- **站内阅读 / 观看**：TXT/MD 自动切章在线阅读（UTF-8 无乱码）、视频/图片站内观看；作品页只预览第一章，完整阅读跳独立阅读页
- **选章 / 选集下载**：单文本文件按章抽取合并 TXT；多文件打包 ZIP；番剧按集、电影按文件多选下载
- **文件存储（zfile + alist 双源主备）**：大文件（作品文件上限 4GB）经统一存储层上传，主源不可用自动回退备源并记录直链；封面/附件走通用上传（32MB）
- **直链添加**：作品文件和论坛/评论/同人附件除上传文件外，可直接粘贴外站直链（视频/图片/文档），站内观看与下载均由后端代理拉取
- **书评打分**：作品页 1–5 星评分 + 短评（可带附件/直链）
- **话题论坛**：发主题、回帖（可带附件/直链）、搜索
- **同人创作**：文字正文 + 图片/视频/音频附件，支持草稿与发布，可绑定原作
- **权限模型**：群成员注册（密码 + 群内把验证码发给机器人验证）/ 登录（密码），"谁建谁删 / 管理员可删"；管理员后台管理
- **QQ 机器人联动（nonebot2）**：注册绑定码核验（用户发码给机器人 → 加白名单 + 绑定官方 openid）、群成员白名单自动同步、头像同步、群内提交作品入库
- **内网穿透友好**：zfile/alist 均经同源代理（`/zfile`、`/alist`），只需暴露前端端口即可完整访问
- **百合主题**：粉紫渐变、👭 品牌元素，文案区分阅读（小说）/ 观看（番剧、电影）

---

## 技术栈

| 端 | 技术 |
| --- | --- |
| 后端 | Python 3.12 · FastAPI · SQLAlchemy 2 · Pydantic 2 |
| 数据库 | SQLite（`backend/data/grouphub.db`，可平滑换 PostgreSQL） |
| 前端 | Vue 3 · TypeScript · Vite · Pinia · Vue Router |
| 存储 | zfile（自建网盘 :8081）+ alist（:5244）双源主备（`storage.py` 统一调度）+ 外站直链（代理拉取） |
| 机器人 | nonebot2（QQ 官方适配器主用 · Access Token 鉴权，OneBot v11 备用；插件位于 `qqbot/plugins/`） |
| 部署 | deploy/setup.sh 一键脚本 · systemd · docker-compose |

---

## 目录结构

```
grouphub/
├── backend/              # FastAPI 后端
│   ├── app/
│   │   ├── api/          # 路由：works / auth / forum / reviews / fanworks / uploads / admin / bot
│   │   ├── chapters.py   # 章节切分 / 代理拉取 / Content-Type 修正
│   │   ├── storage.py    # 统一存储层：zfile / alist 双源主备自动切换
│   │   ├── zfile_client.py  # zfile 引擎对接
│   │   ├── alist_client.py  # alist 引擎对接
│   │   ├── models.py     # ORM 模型
│   │   ├── schemas.py    # Pydantic 入参/出参
│   │   ├── config.py     # .env 配置
│   │   └── main.py
│   ├── data/grouphub.db   # SQLite（自动创建）
│   ├── .env.example      # 环境变量示例
│   └── start.sh          # 开发启动（uvicorn --reload，:8003）
├── frontend/             # Vue3 前端（dev :5173）
│   ├── src/views/        # 作品库 / 阅读页 / 上传 / 论坛 / 同人 / 管理后台
│   ├── src/api/          # API 客户端
│   ├── vite.config.ts    # 代理 /api → backend、/zfile → zfile；allowedHosts 已放行（内网穿透）
│   └── .env.example
├── qqbot/                # nonebot2 机器人（QQ 官方主用 + OneBot 备用）
│   ├── bot.py            # 入口：注册双适配器
│   └── plugins/
│       ├── cli_router.py         # GNU 命令分发入口（唯一 on_message 路由）
│       ├── commands/             # 命令实现：work / auth / sync / help
│       ├── group_member_sync.py  # QQ 群成员白名单同步（事件 + HTTP）
│       ├── avatar_sync.py        # 头像同步（事件 + HTTP）
│       ├── auth_code.py          # 注册绑定码核验 + 登录验证码通道（HTTP）
│       └── _lib/                 # 适配器调度 / backend 客户端 / cli 命令解析核心
├── deploy/               # setup.sh 一键部署 + systemd + docker-compose
├── docs/                 # PRD 文档
├── files/                # zfile 存储根（works/novel、works/movie 等）
```

---

## 快速启动（开发）

### 1. 后端（:8003）

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # 按需修改（见下方配置说明）
./start.sh                # uvicorn --reload，http://localhost:8003/docs
```

### 2. 前端（:5173）

```bash
cd frontend
npm install
npm run dev               # http://localhost:5173
```

### 3. 存储源（zfile :8081 / alist :5244）

文件存储支持 **zfile 与 alist 双源主备**（`STORAGE_PRIMARY` 决定主源，主源不可用自动切备源）。至少配好一个即可；对接参数见 `backend/.env.example`（`ZFILE_*` / `ALIST_*`）。alist 的 `ALIST_PATH_PREFIX`：本地存储设为存储源名（如 `/grouphub`），云盘留空。

### 4. QQ 机器人（nonebot2）

```bash
cd qqbot
pip install -e .          # 使用项目自带 .venv
cp .env.prod.example .env.prod   # 填 QQ_BOTS（AppID + AppSecret，Access Token 鉴权）、SYNC_GROUPS 等
nb run
```

适配器：**QQ 官方为主用**（`QQ_BOTS`，群内 @机器人 触发），**OneBot v11 为备用**（NapCat 等，负责群成员列表/头像等官方没有的 API），可双开。详细说明见 [qqbot/README.md](qqbot/README.md)。

依赖说明：bot 的登录验证码通道与登录通知依赖 bot 的 HTTP 服务（`QQBOT_API_BASE`）；bot 离线时站点自动降级。

### 注册 / 登录流程

- **注册**：站点输入 QQ 号 + 密码 → 页面显示 6 位绑定码 → 群里 @机器人 发送「/绑定 -c <码>」（或私聊）→ 机器人核销：QQ 加入白名单 + 绑定官方 openid → 页面自动检测并登录
- **登录**：QQ 号 + 密码（退群成员 token 立即失效）

---

## 关键配置（backend/.env）

| 变量 | 说明 |
| --- | --- |
| `DATABASE_URL` | SQLite 路径，默认 `sqlite:///./data/grouphub.db` |
| `JWT_SECRET_KEY` | 生产环境务必替换为随机长字符串 |
| `ADMIN_QQS` | 管理员 QQ 白名单（逗号分隔），注册/登录自动赋 admin 角色 |
| `SITE_NAME` | 站名，默认「群资源站」 |
| `MAX_UPLOAD_MB` | 封面/附件/通用上传上限，默认 32MB |
| `MAX_WORK_UPLOAD_MB` | 作品文件（小说/番剧/电影）上限，默认 4096MB（注意 zfile 后台自身限制） |
| `STORAGE_PRIMARY` | 主存储源：`zfile`（默认）或 `alist`；另一源自动作为回退 |
| `ZFILE_BASE_URL` | zfile 地址，默认 `http://localhost:8081` |
| `ZFILE_USERNAME` / `ZFILE_PASSWORD` | zfile 登录账号 |
| `ZFILE_STORAGE_KEY` | zfile 存储 key（如 `grouphub`） |
| `ZFILE_PUBLIC_PREFIX` | 直链同源改写前缀，默认 `/zfile`（配合 nginx/vite 代理） |
| `ALIST_BASE_URL` | alist 地址，默认 `http://localhost:5244` |
| `ALIST_USERNAME` / `ALIST_PASSWORD` | alist 登录账号 |
| `ALIST_PATH_PREFIX` | alist 路径前缀：本地存储设为存储源名（如 `/grouphub`），云盘留空 |
| `ALIST_PUBLIC_PREFIX` | alist 直链同源改写前缀，默认 `/alist` |
| `QQBOT_API_BASE` | bot HTTP 服务地址（:8083） |
| `HOST` / `PORT` | 监听地址，默认 `0.0.0.0:8003` |

前端 `frontend/.env`：`VITE_API_BASE=/api`（开发走 Vite 代理）；生产直接部署 dist 产物时改为真实后端地址。

外站直链的站内观看/下载由后端代理拉取，若源站有防盗链或需登录态会拉取失败（直链仅适合公开可访问的 URL）。

---

## 部署（生产）

```bash
bash deploy/setup.sh
```

脚本会：初始化 backend venv 与 .env、安装 qqbot 依赖，并提示复制 `deploy/*.service` 到 systemd（可选）。也提供 `deploy/docker-compose.yml`。

**nginx 补充代理**（内网穿透只暴露前端端口时同样适用）：

```nginx
location /zfile {
    proxy_pass http://127.0.0.1:8081;
}
location /alist {
    proxy_pass http://127.0.0.1:5244;
}
```

---

## 参考

- 产品文档：[docs/PRD_群图书馆网站.md](docs/PRD_群图书馆网站.md)
- 机器人命令规范：[docs/机器人命令规范.md](docs/机器人命令规范.md)（GNU 风格 `/命令 --选项`）