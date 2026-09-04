# 群资源站（Grouphub）

一个 **QQ 群私域资源共享站**（原名"群图书馆"）：为百合（GL）同好群搭建的内部网站。群友共同维护一份**公共作品库**（小说 / 番剧·动漫 / 电影 / 同人文），提供站内阅读与观看、选章/选集下载、书评打分、话题讨论、同人创作展示，并由 nonebot2 QQ 机器人联动（白名单同步、验证码登录、群内发作品等）。

> 定位：私域内部站点，仅群成员（经 QQ 群白名单）可访问；好维护、低成本、可扩展，主要面向管理员自维护、群友低门槛使用。

---

## 功能一览

- **公共作品库**：谁先上传谁为上传者，其他人以"支持者 / 推荐者"身份加入；支持小说、番剧/动漫、电影、同人文、其他类型
- **站内阅读 / 观看**：TXT/MD 自动切章在线阅读（UTF-8 无乱码）、视频/图片站内观看；作品页只预览第一章，完整阅读跳独立阅读页
- **选章 / 选集下载**：单文本文件按章抽取合并 TXT；多文件打包 ZIP；番剧按集、电影按文件多选下载
- **文件存储（zfile 集成）**：大文件（作品文件上限 4GB）存 zfile 自建网盘并记录直链；封面/附件走通用上传（32MB）
- **直链添加**：作品文件和论坛/评论/同人附件除上传文件外，可直接粘贴外站直链（视频/图片/文档），站内观看与下载均由后端代理拉取
- **书评打分**：作品页 1–5 星评分 + 短评（可带附件/直链）
- **话题论坛**：发主题、回帖（可带附件/直链）、搜索
- **同人创作**：文字正文 + 图片/视频/音频附件，支持草稿与发布，可绑定原作
- **权限模型**：群成员注册登录（QQ 验证码 / 密码），"谁建谁删 / 管理员可删"；管理员后台管理
- **QQ 机器人联动（nonebot2）**：群成员白名单自动同步、头像同步、验证码登录下发、群内提交作品入库
- **内网穿透友好**：zfile 经同源代理（`/zfile`），只需暴露前端端口即可完整访问
- **百合主题**：粉紫渐变、👭 品牌元素，文案区分阅读（小说）/ 观看（番剧、电影）

---

## 技术栈

| 端 | 技术 |
| --- | --- |
| 后端 | Python 3.12 · FastAPI · SQLAlchemy 2 · Pydantic 2 |
| 数据库 | SQLite（`backend/data/grouphub.db`，可平滑换 PostgreSQL） |
| 前端 | Vue 3 · TypeScript · Vite · Pinia · Vue Router |
| 存储 | zfile（自建网盘，`http://localhost:8081`）+ 外站直链（代理拉取） |
| 机器人 | nonebot2（出站 WebSocket，插件位于 `qqbot/plugins/`） |
| 部署 | deploy/setup.sh 一键脚本 · systemd · docker-compose |

---

## 目录结构

```
grouphub/
├── backend/              # FastAPI 后端
│   ├── app/
│   │   ├── api/          # 路由：works / auth / forum / reviews / fanworks / uploads / admin / bot
│   │   ├── chapters.py   # 章节切分 / 代理拉取 / Content-Type 修正
│   │   ├── zfile_client.py  # zfile 对接：上传、直链、public_url 同源改写
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
├── qqbot/                # nonebot2 机器人
│   └── plugins/
│       ├── group_member_sync.py  # QQ 群成员白名单同步
│       ├── avatar_sync.py        # 头像同步
│       ├── auth_code.py          # 验证码登录下发
│       └── works.py              # 群内发作品
├── deploy/               # setup.sh 一键部署 + systemd + docker-compose
├── docs/                 # PRD 文档
├── files/                # zfile 存储根（works/novel、works/movie 等）
└── zfile-api-example/    # zfile 拉直链 API 示例
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

### 3. zfile（:8081）

自建网盘，用于存作品文件/封面/附件，提供直链。后端通过 `ZFILE_BASE_URL` / `ZFILE_STORAGE_KEY` 对接（见 `backend/.env.example`）。

### 4. QQ 机器人（nonebot2）

```bash
cd qqbot
pip install -e .          # 使用项目自带 .venv
# 配置 OneBot 适配器 WS 连接后启动
```

依赖说明：验证码登录（插件 #2）与登录通知依赖 bot 的 HTTP 服务（`QQBOT_API_BASE`）；bot 离线时站点自动降级。

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
| `ZFILE_BASE_URL` | zfile 地址，默认 `http://localhost:8081` |
| `ZFILE_STORAGE_KEY` | zfile 存储 key（如 `library`） |
| `ZFILE_PUBLIC_PREFIX` | 直链同源改写前缀，默认 `/zfile`（配合 nginx/vite 代理） |
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
```

---

## 参考

- 产品文档：[docs/PRD_群图书馆网站.md](docs/PRD_群图书馆网站.md)