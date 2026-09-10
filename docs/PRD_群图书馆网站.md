# 群图书馆网站 — PRD（产品需求文档）

| 项目 | 内容 |
| --- | --- |
| 产品名称 | 群图书馆（暂定名，待定） |
| 版本 | v0.1 Draft |
| 类型 | 私域内部站点（仅群成员访问） |
| 状态 | 讨论稿，待评审后进入开发 |

---

## 1. 背景与目标

我们有一个 QQ 群，群友之间互相安利、分享看的小说/动漫。目前这些"看过什么"的信息散落在各自脑子里或聊天记录里，没有沉淀下来。

**建站目的：**
- 建立一份**公共作品库**：由群成员共同维护，谁先上传谁为上传者，其他人以"支持者/推荐者"身份加入，避免重复上传同一部作品。
- 沉淀每个成员的**个人阅读进度与评价**（可选标记）。
- 提供**站内阅读/观看入口**（可传文件 + 跳转官方源站），方便群友直接读。
- 承载**书评打分、话题讨论、同人创作展示**等社区互动。
- 作为 nonebot2 QQ 机器人的数据底座，后续机器人可读取/同步站点数据，并预留接入 AI 的能力。

> 定位：这个站主要由"我（管理员）"来写和维护，群友不一定高频使用，因此要**好维护、低成本、可扩展**。

---

## 2. 用户角色与权限

| 角色 | 说明 | 核心能力 |
| --- | --- | --- |
| **管理员 (Admin)** | "我"本人，唯一/极少 | 管理所有作品、审核/删除任何内容、管理成员、配置机器人同步等。 |
| **群友 / 普通用户 (Member)** | QQ 登录注册的成员 | 上传作品、绑定自己的阅读关系、写书评打分、参与讨论、发布同人创作；只能编辑/删除自己的内容（除管理员）。 |

- 权限模型：**基于角色 + 数据归属**。普通用户的操作仅限自身数据；上传与创建人人开放（宽松）；删除需遵循"谁建谁删 / 管理员可删"。

---

## 3. 核心概念与关系模型

### 3.1 作品（Work）
一件被收录的作品，例如一部小说或一个番剧。关键字段：
- `title`、`author(s)`、`type`（小说/动漫/…扩展位）、`cover_url`（封面，可选；图片存于 zfile 等外部存储）、`summary`
- `tags`（标签，可扩展为独立标签表 + 多对多）
- `uploader_id`：**上传者**，即第一个创建该作品记录的人（不可随意变更）。
- **外部文件入口（zfile 等第三方存储，可选）**：如小说文本、动漫/番剧视频及后续扩展类型。本站不直接存放大文件，而是记录指向外部存储的引用并据此渲染（见 §6.2）。字段 `external_file` = { provider(enum, 默认 zfile), url }，可为空。
- **源站跳转链接**：**0 个或多个**官方/正版站点链接（作品页放一组链接按钮）。
- `status`：草稿 / 已发布；`sort_rank`：排序。

### 3.2 用户与作品的关系（ReadingRecord）——"多人看过不重复上传"的核心
每个成员对每件作品，可绑定一条记录，**relation_role** 为以下之一/组合：

| role | 含义 | 规则 |
| --- | --- | --- |
| `uploader` | **上传者** | 自动归属第一个创建作品的人；唯一、不可被普通用户篡改。 |
| `supporter` | **支持者** | 在作品上传本站前就看过它的人。**自选加入**。 |
| `recommender` | **推荐者** | 推荐本书的人。**自选加入**。 |
| `reading_status` | 阅读状态（非关系，可选） | `未开始 / 正在阅读(reading_now) / 在读中(in_progress) / 已看完(finished) / 计划(read)`；**自选更新**。 |

- `(user_id, work_id)` 唯一。
- 每条记录仅**本人可编辑自己的 role/状态**（防止误删他人支持者）。
- 一件作品可同时有 N 个支持者、M 个推荐者，上传者始终只有一个。

### 3.3 评价（Review）
- 针对某作品的打分与短评：`rating`(1–5 星或评分)、`title`、`content`、`created_at`/`updated_at`。
- 仅本人可编辑自己的评价；聚合平均分为作品热度指标之一。

### 3.4 话题讨论区（Topic）
轻量论坛，结构化但不重：
- `topics`：主题帖（标题、封面、正文、创建者、状态）。
- `topic_posts`：回帖（作者、内容、时间）。
- 支持**置顶 / 锁定**（管理员）、搜索。

### 3.5 同人创作展示（Fanwork）
面向创作的展示区，类似 AO3：
- 绑定可选的"原作 work_id"。
- 内容形式：**文字正文 + 图片 / 视频 / 音频附件（均为可选）**、标题/分类标签、状态（草稿/公开）。
- 画廊式浏览、可按原作/标签过滤；作者可管理自己发布的内容。

---

## 4. 功能需求清单

### F1 — 账户与登录（依赖 nonebot2 插件 #1：group_member_sync 同步白名单）
- **强校验前置**：所有注册/登录请求，第一步就是查 `group_members` 表中该 QQ 是否存在「`is_active = true`」的记录；无则直接拒绝，错误提示统一为「该 QQ 不在本群，请先加群」。
- **注册（A 通道，密码方式）**：QQ 号 + 密码 → 校验白名单通过 → 创建 `users` 记录；昵称默认取 `group_members.nickname_in_group`，用户可再修改。
- **登录（A 通道，密码方式）**：QQ 号 + 密码 → 白名单 → 密码哈希校验 → 返回 Session/Token。基础安全：失败锁定、限流、密码哈希存储。
- **登录（B 通道，验证码方式，可选）**：QQ 号 → 站点生成 6 位验证码 → 调 nonebot2 插件 #2（auth_code）向 QQ 私聊发送 → 用户回填 → 校验通过即自动登录或自动注册后登录（昵称取群名片）。
- **退群自动失效**：插件 #1 同步退群事件将 `is_active=false` → 现有 token 在下次鉴权时失效、被强制登出，无法再注册/登录。
- **管理后台辅助按钮**：「手动触发群成员全量重同步」—— 调用内部 `batch_upsert`，用于机器人刚上线或事件丢失后的兜底。

### F2 — 作品库与搜索
- 浏览作品列表（按类型/热度/更新时间过滤）。
- **全文检索**：标题、作者、标签、简介；支持拼音/繁简可选。
- 作品详情页：**当关联人数（支持者 + 推荐者 + 正在阅读，不含上传者）达到设定阈值时**，显示"**张三、李四 等共 N 人**"并可点击展开完整列表；否则不展示该计数。信息 + 阅读状态 + 跳转链接组 + 外部文件渲染入口 + 评价聚合 + 相关讨论与同人。

### F3 — 上传作品（宽松）
两种模式，人人可用：
- **仅名称**：只填标题，后续由管理员补详情。
- **带详情**：填写完整字段 + 附件 + 跳转链接组。
- 校验、去重提示（已存在则引导合并/标记支持者而非新建）。

### F4 — 外部文件入口 / 观看（走 zfile，不存本站）
- **第三方存储引用**：小说文本、动漫/番剧视频及后续扩展类型均**不直接上传到本站**，而是通过 **zfile 等外部存储**托管。站点记录指向该文件的链接并据此渲染（iframe/embed 或跳转），由 zfile 完成实际承载与播放。
- **多跳转链接**：作品页展示一组"在官方站点阅读/观看"按钮（0+）。
- **外部集成钩子（留口子）**：存储提供方用 `external_file.provider` 枚举抽象，新增存储方式只改配置、不碰核心逻辑；渲染入口按 provider 路由。后续 zfile、AI、机器人均在此类接口处接入。
- 版权与安全提示；仅管理员/上传者可管理外部文件引用与链接。

### F5 — 个人进度标记（ReadingRecord）
- 自选绑定支持者/推荐者、更新正在阅读等状态；本人可见并维护。

### F6 — 书评打分（Review）
见 §3.3，写入作品详情页。

### F7 — 话题讨论区
见 §3.4。

### F8 — 同人创作展示
见 §3.5。

---

## 5. 非功能需求

| 类别 | 要求 |
| --- | --- |
| **性能** | 作品量初期小、后续增长；列表与搜索应 < 1s（P95）；分页加载，避免一次性全量。 |
| **安全** | 密码哈希存储；登录限流/防爆破；文件上传类型+大小校验、存 OSS/本地磁盘隔离；私域站点需基础访问控制。 |
| **版权** | 站内正文以"群友自愿上传的同人/自创内容为主"，作品信息指向官方正版源站；不主动抓取盗版。 |
| **可用性** | 管理员后台简洁好维护；前端对非技术群友友好、加载快。 |
| **可维护性** | 单库即可；代码模块化便于后续加类型/功能。 |
| **可扩展** | 新作品类型通过配置/扩展位加入；机器人/AI 接入通过接口解耦。 |

---

## 6. QQ 登录 / 机器人与 AI —— nonebot2 集成（分阶段落地）

> 总体思路：**nonebot2 插件作为「群侧代理」主动与站点后端交互**——主动维护 `group_members` 白名单、提供 QQ 侧验证码通道，并承载后续可插拔能力（安利、推荐、内容审核、AI 等）。站点本身仍保持「小而美」单体，不依赖 QQ 平台。

### 6.1 核心校验规则（不再需要管理员手动导入名单）
- QQ 能否注册/登录的**唯一强判断**：该 QQ 在 `group_members` 表中存在至少一条 `is_active = true` 记录（即「当前在任一个绑定群内」）。
- 白名单来源不再是管理员手动导入 CSV，而是 **nonebot2 插件通过事件 + 全量拉取实时写入**。
- 退群自动失效：当群成员被踢/主动退群 → 插件捕获事件 → 把对应 `group_members.is_active` 置为 `false` → 后续登录/注册自动拒绝。

### 6.2 群成员实时同步（插件 #1：group_member_sync，必装）
由 nonebot2 插件定时 + 事件驱动维护 `group_members` 表，**不允许前端或管理员手动增删**（仅允许管理后台手动触发一次「全量重同步」）。

#### 6.2.1 同步策略
| 触发方式 | 说明 | 频率/时机 |
| --- | --- | --- |
| **全量拉取** | 调用 QQ OpenAPI / 适配器 `get_group_member_list`，拿到整群成员列表，与数据库合并 upsert，不在列表且群内无对应成员的记录置 `is_active=false` | 启动时 1 次 + 每 6 小时 1 次 + 管理员后台手动触发 |
| **增量事件** | 监听 `group_member_increase`（进群）、`group_member_decrease`（退群/被踢）、`group_card_changed`（群名片变更）等事件即时更新单条 | 事件触发即同步 |

#### 6.2.2 站点提供的内部 API（供 nonebot2 调用，使用独立的 `BOT_API_TOKEN` 鉴权）
| 方法 | 入参 | 语义 |
| --- | --- | --- |
| `POST /bot/members/upsert_one` | `{ group_id, qq, nickname_in_group }` | 单条插入或更新 is_active=true，更新 last_synced_at |
| `POST /bot/members/batch_upsert` | `{ group_id, members: [{qq, nickname_in_group}...], mark_inactive_others: true }` | 批量 upsert；如标记为 true，则该 group_id 下不在本次名单中的旧记录置为 is_active=false |
| `POST /bot/members/set_inactive` | `{ group_id, qq }` | 标记单条为 is_active=false（退群/被踢时调用） |
| `GET  /bot/members/status?qq=` | qq | 返回 `{ in_group: bool, groups: [{group_id, is_active}], last_synced_at }`（调试/插件自检用） |

> 以上接口统一使用 `X-Bot-Token: <BOT_API_TOKEN>` 请求头鉴权，Token 由部署时环境变量注入，**与普通用户 Session 完全隔离**。

### 6.3 注册 / 登录 — 后端与 QQ 侧双通道

#### 6.3.1 双通道策略
| 方式 | 场景 | 流程 |
| --- | --- | --- |
| **A. 站点 QQ+密码注册登录**（保留） | 群友在浏览器里使用 | `注册/登录 → 查 group_members.is_active=true → 通过 → 走常规密码校验/创建用户`；若不在群内则直接拒绝 |
| **B. QQ 私聊验证码登录**（扩展） | 希望省去记密码、或密码登录失败时兜底 | 见 §6.3.2 |

#### 6.3.2 验证码登录流程（依赖 nonebot2 插件 #2：auth_code，可选）
```
用户在站点点「QQ 验证码登录」→ 输入自己的 QQ 号
    ↓
站点: POST /auth/send-code { qq }
    → 校验 group_members.in_group = true（不在群直接拒绝）
    → 生成 6 位 code，写入 verification_codes 表（qq, code, expires_at, used=false）
    → 通过 内部 API POST /bot/auth/send-code { qq, code } 通知 nonebot2 插件
    ↓
nonebot2 插件: bot.send_private_msg(qq, "你的验证码是 XXX，10 分钟内有效")
    ↓
用户收到验证码，在站点填写后提交 → POST /auth/confirm-code { qq, code }
    → 校验 code 存在、未过期、未使用
    → 通过则登录（若 user 不存在则自动创建，昵称取自群名片）；标记 code.used=true
```

对应站点内部 API（供 nonebot2 调用）：
| 方法 | 入参 | 语义 |
| --- | --- | --- |
| `POST /bot/auth/send-code` | `{ qq, code }` | 通知插件向指定 QQ 私聊发送验证码；插件可扩展为「@提醒 + 临时会话」 |
| `POST /bot/auth/notify-login` | `{ qq, login_at, ip }` | 可选：登录成功后通过 QQ 私聊推送「安全通知」，增强账号安全感 |

### 6.4 站点面向普通用户的 Auth API 契约
| 方法 | 入参 | 语义 |
| --- | --- | --- |
| `POST /auth/register` | `{ qq, password, nickname? }` | 先查 `group_members.is_active=true`，不通过则报错「你的 QQ 不在本群」；通过则创建 users 记录并返回 token |
| `POST /auth/login` | `{ qq, password }` | 先查群成员有效性 → 再校验密码哈希 → 返回 token |
| `POST /auth/send-code` | `{ qq }` | 触发验证码流程（需站点主动调用 nonebot2） |
| `POST /auth/confirm-code` | `{ qq, code }` | 验证码校验通过 → 自动注册/登录 → 返回 token |
| `POST /auth/logout` | （token in header） | 使当前 token 失效 |

### 6.5 插件化架构（为后续扩展预留统一入口）
群侧能力统一走「插件 → 内部 API（X-Bot-Token 鉴权）→ 站点」路径，便于横向叠加：

| 插件编号 | 名称 | 阶段 | 职责 |
| --- | --- | --- | --- |
| **#1** | `group_member_sync` | MVP 必做 | §6.2 群成员白名单实时同步 |
| **#2** | `auth_code` | 体验增强 | §6.3.2 QQ 私聊验证码登录、登录安全通知 |
| **#3** | `works_recommend` | 社区运营 | 群内触发「/热门 / /搜索 -k <词>」，调用站点搜索/热度排行并返回到群聊 |
| **#4** | `works_submit` | 社区运营 | 群消息中识别「/安利 -t xxx」指令 → 调用作品「仅名称模式上传」接口，把聊天记录里的随手安利沉淀为作品 |
| **#5** | `content_moderation` | 扩展 | 接入 AI 对书评/讨论/同人做内容审核，违规内容自动标记并推送管理员私聊 |
| **#6** | `works_suggest` | 扩展 | AI 补全作品简介、自动打标签、相似作品推荐 |
| **#7** (开放) | — | 后续新增 | 新增插件只要求：① 遵守 `X-Bot-Token` 内部鉴权；② 调用 `/bot/*` 前缀 API；③ 新增接口在 FastAPI 路由统一注册，不污染普通用户路由 |

### 6.6 AI 接入位置
- **插件 #5/#6 内部分层调用**：nonebot2 插件本身不写死模型，而是在插件内部预留一个 `LLMService` 适配层（默认空实现，后续接 OpenAI 兼容接口）。
- 不把 AI 逻辑放进站点后端，保持站点轻量化；站点仅暴露「触发审核 / 触发补全」的内部接口回调，AI 结果再由插件回写。
- 在 MVP 阶段 AI 接入点**只定义接口**，不实现。

### 6.7 数据补充（新增表）
```
verification_codes          # 验证码登录临时表
  id, qq, code(char(6)), used(bool), expires_at, created_at
  # 索引: (qq, expires_at)；过期后由定时任务清理或直接覆盖

bot_api_tokens              # （可选，如需要多插件/多实例区分 Token 来源）
  id, token_hash, plugin_name, scopes(json), enabled, created_at
  # MVP 阶段直接用环境变量 BOT_API_TOKEN 即可，无需建表；多 Token 场景再加
```

---

## 7. 数据模型（核心集合）

```
users
  id, qq, password_hash, nickname, avatar_url, role(enum), created_at

group_members              # 群成员白名单（由 nonebot2 插件实时同步，注册/登录校验用）
  id, qq, group_id, nickname_in_group,
  is_active(bool, default true),              # true=当前在群内；false=已退群
  first_seen_at, last_synced_at
  # 唯一键 (qq, group_id)；一个 QQ 可在多个群，多群场景下任一 is_active=true 即视为有效成员

works                      # 作品库
  id, title, author, type(enum可扩展), cover_url(nullable), summary,
  uploader_id → users.id, status(enum), sort_rank, created_at, updated_at, tags_json

work_links                 # 源站跳转（0+，一对多）
  work_id → works.id, site_name, url

work_external_files        # 外部存储引用（zfile，可选，1+；大文件不存本站）
  id, work_id → works.id, provider(enum zfile), url, file_name, mime_type, size_bytes, uploader_id, created_at

work_tags                  # 标签（可扩展为规范化标签表 + 多对多 join）
  id, name;  works_tags(work_id, tag_id)

user_works                 # 用户-作品关系（支持者/推荐者/阅读状态）
  user_id → users.id, work_id → works.id,
  relation_role(enum: supporter/recommender), reading_status(enum|null), created_at, updated_at

reviews                    # 评价打分
  id, work_id → works.id, user_id → users.id, rating(int 1-5), title, content, created_at, updated_at

topics                     # 讨论主题
  id, creator_id → users.id, title, cover_url, body, status(enum: open/pinned/locked/archived), created_at

topic_posts                # 回帖
  id, topic_id → topics.id, user_id → users.id, content, created_at

fanworks                   # 同人创作展示
  id, work_id(nullable), author_id → users.id, title, category, cover_url, body/附件, status(enum: draft/published), created_at

sessions / auth_tokens     # 登录态（会话表或 token 存储）
```

> 关系模型说明：上传者 = `works.uploader_id`；支持者/推荐者 = `user_works.relation_role`；正在阅读等 = `user_works.reading_status`。三者分离，互不干扰、各自独立扩展。

---

## 推荐技术栈（轻量化优先）

- **数据库**：首选 **SQLite**——单文件、零配置，非常契合"私域小站 + 单人维护"的场景；若后续数据量/并发增长再迁 **PostgreSQL**。
- **后端**：**FastAPI**（Python，与 nonebot2 同生态，便于机器人/AI 共享代码），自带 OpenAPI/Swagger。
- **前端**：轻量静态站（Vite + React/Vue）部署为静态文件即可；SSR 视需求。
- **部署**：内网/NAS/单服务器，Docker Compose 一键起服务。
- 其余无硬性要求，遵循"好维护、低成本"原则选型。

## 8. 里程碑 / 迭代排期建议

> 每个里程碑都拆成**「站点」+「nonebot2 插件」**两条并行线，可独立推进、互为依赖点已注明。

- **MVP（尽快能跑）**
  - 站点：F1(登录/注册 - 仅密码通道) + F2(库+搜索) + F3(上传:仅名称与带详情) + F5(用户-作品关系 full) + 作品详情页。
  - **nonebot2：插件 #1 `group_member_sync`（必做，F1 依赖）**
    - 实现 `/bot/members/*` 内部 API（FastAPI）+ 插件端调用。
    - 启动全量拉取 + 每 6h 全量 + 进/退群事件即时同步。
    - 管理后台「手动触发全量重同步」按钮。

- **体验增强**
  - 站点：F6(书评打分)、F4(站内文件渲染 + 多跳转链接)、**F1 B 通道（验证码登录）**。
  - **nonebot2：插件 #2 `auth_code`（验证码/登录通知）**
    - 实现 `/bot/auth/send-code`、`/bot/auth/notify-login` 接口。
    - 群名片取昵称回填用户资料。

- **社区运营**
  - 站点：F7(话题讨论区)、F8(同人创作展示)。
  - **nonebot2：插件 #3 `works_recommend` + #4 `works_submit`**
    - `/热门` `/搜索 -k xxx` → 调用站点搜索/热度排行。
    - `/安利 -t <作品名>` → 走 F3「仅名称模式上传」。

- **机器人 / AI（扩展）**
  - **nonebot2：插件 #5 `content_moderation` + #6 `works_suggest`**
    - #5：书评/讨论/同人内容审核回调接口，违规标记 + 管理员私聊告警。
    - #6：AI 补全简介、自动打标签、相似作品推荐，在插件内预留 `LLMService` 适配层。
  - 站点侧对应开放「审核回调 / 作品属性更新」的内部接口。

> 建议先落地 **MVP 的两条线（站点 + 插件 #1）**，拿到可跑的版本后再逐步加，降低维护成本（毕竟主要维护者是"我"）。

---

## 9. 开放问题 / 待确认
1. **作品类型扩展位**：除了小说/动漫，是否现在就预留漫画、影视？PRD 已按 enum 留位。
2. **同人创作内容形态（已定）**：支持文字正文 + 图片 / 视频 / 音频附件，且均**可选**。本条已确认，后续只需细化上传控件与展示样式。
3. **"正在阅读"是否在作品首页强展示**（给管理员看整体进度），还是仅个人页可见？默认建议个人页可见、管理后台可统计。
4. **站点访问控制**：私域站是纯 IP/域名白名单 + 登录即可，还是有更细的访问策略？
5. **人数统计阈值（待确认）**：作品详情页"张三、李四 等共 N 人"及展开列表仅在关联人数 ≥ 某阈值时展示。默认建议设为 **3**（即只要有少量互动就展示），可放管理后台配置；具体数值待你拍板。
