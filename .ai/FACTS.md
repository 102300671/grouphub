# 已实测事实基线（2026-10-16 建 · 2026-10-26 补 M0 第一轮实测）

> 这些数字都是实测得到的，接手时可直接依赖，不必重做。若与你的假设冲突，以重测为准并更新本文件。

## 代码基线

| 项 | 值 |
|---|---|
| 本地设备 / 云端 / GitHub HEAD | 曾全部 `0b1f1a1`（三向一致）。**2026-10-26 已推进到 `9ca6b93`**（临时 `/test` 命令 + ②b 对照组） |
| `backend/app/renderer.py` md5 | `80b8dba671e123c7d68b85f6111824f7` |
| `frontend/src/views/AIView.vue` md5 | `5277293a7de22aa2ba1bd436529771aa` |
| 前端产物 | `AIView-Cs6XqcZn.css` 18084B、`AIView-Bg6kHXDY.js` 31380B（设备与云端逐字节一致） |
| 前端构建 | 设备 `npm run build`（vue-tsc + vite）约 6s；云端 2.99s |

## 渲染能力（关键）

| 场景 | 结果 |
|---|---|
| 本地设备 chromium + `--no-sandbox --disable-dev-shm-usage --disable-gpu` | **卡死**，70s 超时被杀（exit 124） |
| 本地设备同上 **+ `--use-gl=swiftshader`** | **2.6s 出图**，md 25341B / text 24513B，中文正常、表头蓝底与圆角边框正确 |
| 本地设备 Python 环境 | 无 `backend/.venv`；`qqbot/.venv/bin/python3` 有 markdown+playwright，可直接加载 `app/renderer.py` |
| 浏览器内核 | 设备与云端均为 `/root/.cache/ms-playwright/chromium-1234` |
| 中文字体 | 设备 **40 条**（Noto Sans/Serif CJK 全套）；云端 **1 条**（Droid Sans Fallback）→ 本地与线上字形必然不同 |
| 参考实现 | `/root/code/bot/qqbot/timetable/bot.py`：按 TracerPid / osrelease 含 PRoot / `/data/data/com.termux` 判定，无 GPU 也走 swiftshader，经 `RENDER__PROVIDER_CONFIG__LAUNCH_ARGS` 注入 |

## 图片 URL 与站点可达性（实测）

| 测试 | 结果 |
|---|---|
| `http://59.110.163.88:4173/` | **200**，0.08s |
| `https://59.110.163.88:4173/` | **不通**（000） |
| `https://59.110.163.88:5244/` | **不通**（000） |
| alist 渲染图直链**带有效 sign** | `image/png`，187023B，PNG 魔数正确 → **取到真图** |
| 同一 URL**去掉 sign** | `application/json`，51B，`{"code":401,…}` → **被拒** |
| 同一 URL**故意填错 sign** | `application/json`，49B，`{"code":401,…}` → **被拒** |
| 不存在的文件名 + 垃圾 sign | 同样是 `{"code":401}` → 401 优先于 404，无法据此枚举存在性 |
| 结论（**更正**） | alist **确实在校验签名**，直链不能无凭据抓取 |
| alist 全局设置（实测库中值） | `sign_all = 'true'`（全站强制签名）；**`link_expiration = '0'`** ← 有效期被关闭，这才是"永不过期"的确切来源（不是我先前说的"没有该配置项"） |
| alist 相关键全集 | 签名/有效期/链接语义相关只有 5 个键：`link_expiration`、`sign_all`、`forward_direct_link_params`、`ignore_direct_link_params`、`meta_not_found_cache_expire` |
| alist 版本 | WebVersion **3.65.0**（`/opt/alist/alist server`，已运行约 1 天 4 小时）；二进制内置设置项里确实存在 `Sign all` 与 `Link expiration` |
| sign 形态 | URL 尾部 `…:0` 的那个 `0` 就是过期时间戳位，与 `link_expiration=0` 对应；`alist_client.py` 注释"sign 基于文件 hash，永不过期"与此一致 |
| 未确认项 | `x_storages` 本版本无 `name` / `additional_info` 列（查询报错），**存储级 `enable_sign` 状态未取到**，需要时走管理界面或修正 SQL 再查 |
| `backend/app/config.py` | 无任何"站点公网基址"配置项 |
| `public_url()` | 主动把绝对直链降级为同源相对路径（`/zfile`、`/alist`），**sign 参数原样保留** |

> ⚠️ **方法论留痕（本文件曾经写错过）**：alist 对失败请求返回的是 **HTTP 200 + JSON 错误体**，不是 401/404 状态码。
> 只看 `curl -w "%{http_code}"` 会把"签名无效"误判成"图片可任意抓取"，本文件初版就是这么错的（16:12 版）。
> **判定规则：验证资源可达性必须同时看 `content-type` 与响应体魔数/前缀，状态码单独不可信。** V2/V1 已按此口径执行。

## ⚠️ 被动回复次数上限 = 5（群聊）· 2026-10-26 实测撞线

| 场景 | 有效期 | **每条消息可回复次数** |
|---|---|---|
| 单聊 | 60 分钟 | 4 次 |
| **群聊** | **5 分钟** | **5 次** |
| 频道 | 5 分钟 | — |

- 出处：QQ 开放平台「发送群聊消息」+「消息收发概述」
- 错误码 **`40034128` 被动回复时间或次数超限**（排查建议：收到事件后尽快回复）
- 相关错误码：`40034005` 回复消息 msg_id 已过期 · `40054005` 消息被去重（每次请求要用不同 msg_seq）

**实测**：`/test` 一次发 7 条 → 前 5 条正常送达，**第 6 条抛 `ActionFailed: 40034128`**，
截图里正好能看到那 5 条。时间上只隔了 7 秒，所以撞的是**次数**不是 5 分钟窗口。
第二轮 12 条更早挂。

**不是适配器的锅**：`nonebot-adapter-qq` 的 `GroupMessageCreateEvent` 分支有
`event._reply_seq += 1` 并传 `msg_seq=event._reply_seq` —— **msg_seq 递增是对的**
（官方也要求"相同的 msg_id + msg_seq 重复发送会失败，可递增 msg_seq 实现多次回复"）。
问题纯粹是**平台侧的次数硬上限**。

> **设计约束（写给后续做 QQ 端输出的人）**：
> 一次用户触发**最多只能回 5 条**。要发更多内容必须：
> ① 合并进一条消息（但 `msg_type` 单值，markdown 与图片不能同条 —— markdown 优先，图片会被丢）；
> ② 拆成子命令，让用户分次触发；
> ③ 走**主动消息**（不带 msg_id）—— 群聊主动消息频控：Bot 维度 60/qpm（未认证 30/qpm）、
>    单关系维度 20/qpm、每群每天最多 1000 条，且**需要群主在管理端开启"主动推送消息能力"**。

## ⚠️ 图片转存是静默失败，要用 `force_verify_image_resource` 才能查

官方 `MessageMarkdown` 字段：

> `force_verify_image_resource` boolean 否 是否校验图片转存结果，当为 true 时，
> 如果出现图片转存失败，则会返回错误，消息不会发送。**默认为 false**

默认 false 时，**markdown 里的图片转存失败不报错**：消息照发，图片位置只剩 alt 文本
（第一轮实测：只显示 `[blocks]`，URL 都没了）。
用 true 才能让平台**直接返回错误码**，把"静默失败"变成"可诊断的失败"。

⚠️ `nonebot-adapter-qq` 的 `post_group_messages` **没暴露这个字段**
（连 `models/` 里都没有），要用就得**走原始 HTTP**：`bot.get_access_token()` +
`bot.adapter.get_api_base()` + `httpx` 直接 POST `/v2/groups/{group_openid}/messages`，
自己管 `msg_seq`（与适配器共用 `event._reply_seq` 计数器，避免 msg_id+msg_seq 重复被去重）。

> 附带好处：`force_verify` 失败时**消息根本没发出去，不占那 5 次被动回复额度**。

> ⚠️ **踩坑：适配器返回 `yarl.URL`，httpx 不认。**
> `bot.adapter.get_api_base()` 返回的是 **yarl.URL**，而 httpx 只接受 `str` / `httpx.URL`，
> 直接传会抛 `TypeError: Invalid type for url. Expected str or http.URL, got <class 'yarl.URL'>`。
> 必须 `httpx.URL(str(base / "v2" / "groups" / openid / "messages"))`。
> 第一版没转，四个变体全挂在**请求构造阶段**，转存校验压根没跑起来 ——
> 表现和"平台拒绝"很像，但其实是本地错误。**教训：报"平台拒绝"之前先确认请求真的发出去了。**
>
> **自检手法**（不发任何消息）：真 token + **假 group_openid** 打一次 QQ API。
> 预期拿到平台的 HTTP 错误（如 `404 {"code":11321,"message":"请求Query参数group_id无效"}`）——
> 说明请求已正确到达并被处理；若是 TypeError/连接异常，就是本地代码有问题。
> 脚本：`qqbot/qq_api_selftest.py`（M0 临时工具，验完删除）。

## M0 第一轮实测（2026-10-26 上午 · 群消息 · QQ 官方适配器）

用临时 `/test` 命令发，**用户截图回报**。这是本文件里第一条**不依赖文档口径**的 QQ 渲染事实。

| 场景 | 结果 | 证据 |
|---|---|---|
| ① 图片段 URL 直发（`S.image(url)` → msg_type=7） | ✅ **完全正常**，图片直接显示 | 截图：四色块图正常显示，尺寸正常 |
| ② markdown 段（`S.markdown()` → msg_type=2） | ✅ **群聊可用！** 粗体 / 斜体 / 删除线 / 行内代码 / **代码块** / **表格** 全部正确渲染 | 截图：代码块是完整卡片（等宽字体 + 灰底 + `python` 语言标签 + 复制/全屏按钮）；表格有网格线、表头加粗 |
| ② markdown 嵌图 `![alt](url)` | 🟡 **平台支持，但客户端差异极大**（详见下节） | **手机端**：一律显示 `[alt]`；**电脑端**：A/D 出图、B/C 不出 |
| ③ **纯文本通道**（msg_type=0，原样发 markdown 源码） | ⚪ **完全不解析 markdown** —— 这是**预期行为**，不是 bug | 截图：`![blocks](http://…)` 显示为**原样文字**（且被识别成可点击链接，URL 完整显示）；代码块显示为**原样反引号** `… python def m0(): …`；表格显示为**原样竖线** `\| 场景 \| 期望效果 \|` |

> ⚠️ **第一轮那条「② 嵌图 ❌ 失败 / 平台不支持嵌图」的结论是错的，已作废。**
> 当时手上**只有手机端截图**（只看到 `[blocks]`），据此推断"平台不支持"——
> 实际上**平台是支持的**，只是**QQ 手机端不渲染 markdown 里的图**，降级成 alt 文本；
> 电脑端能正常出图。
>
> **教训（写死）**：渲染类结论**必须手机 + 电脑两端都看**。
> 单端截图只能**证伪**（"这里没出图"），不能**证成**（"这东西不支持"）。

**这两条旧口径被实测推翻或改写**：

| 旧口径 | 原出处 | 实测结果 |
|---|---|---|
| "QQ Markdown 不支持代码块与 HTML" | pushplus 文档（第三方，2026-09-29） | ❌ **作废**。代码块支持且是带语法标签/复制按钮的完整卡片。三方文档在代码块这条上不可信 |
| "被动 Markdown 消息需申请权限" / "含链接消息需后台配置 URL 白名单、域名需 ICP 备案" | 官方知识库群机器人使用规范（2024-09-09，偏旧） | 🟡 **一半对一半错**：markdown 消息本身发得出去（截图 ② 证明）；但**「域名需 ICP 备案」这条在嵌图上被实测坐实** —— 自建未备案域名（B/C）不出图，裸 IP（A）和外部公网域名（D）出图 |
| "markdown 内图片…开放平台会下载转存该资源" | 官方 Markdown 消息（2026-04-23） | ✅ **成立**。实测裸 IP + http 与外部公网 https 都能出图 |

### ②b 嵌图对照实验（第二轮 · 2026-10-26 10:24 实跑 · **结论已定**）

同一段 markdown 图片语法，**只换图片直链**。

| 变体 | 直链 | 电脑端 | 手机端 |
|---|---|---|---|
| A 裸 IP + http | `http://59.110.163.88/m0test/blocks.png` | ✅ **出图** | `[alt]` |
| B 域名 + http | `http://jianying.run.place/m0test/blocks.png` | ❌ **无图** | `[alt]` |
| C 域名 + https（自签） | `https://jianying.run.place:8443/m0test/blocks.png` | ❌ **无图** | `[alt]` |
| D 外部公网 https | `https://www.python.org/static/img/python-logo.png` | ✅ **出图** | `[alt]` |

**两条互相独立的原因轴 —— 千万别混为一谈：**

| 轴 | 现象 | 原因 | 规避 |
|---|---|---|---|
| **① 来源/平台侧** | B/C 无图，A/D 有图 | **自建域名未 ICP 备案**（`jianying.run.place` 没备案）。裸 IP 与外部公网域名都可以 | 用**已备案域名**或**外部 CDN 域名**放图 |
| **② 客户端渲染侧** | 手机端全是 `[alt]`，电脑端才出图 | **QQ 手机端不渲染 markdown 里的图片**，降级成 alt 文本；桌面端能渲染 | **手机端为主的应用不能靠 markdown 嵌图** → 图片走 `msg_type=7` 独立图片段，或"渲染成一整张图"再发 |

> **② 这条是项目级的关键约束**：群资源站的用户以手机为主，
> 任何"markdown 里插图"的方案在手机上等于没有图。
> M4 的设计必须按"**手机上图片要能看见**"来定，不能只看电脑端。

> **未确认的小点**：`force_verify_image_resource=true` 那轮，B/C 究竟是
> 「平台直接拒发（汇总里应有错误码）」还是「转存成功但客户端不渲染」——
> 要看 `/test 3` 末尾那张汇总里 B/C 两行写的是"平台报错 code=xxx"还是别的。
> **两种情况的应对不同**：前者是备案在平台侧拦、后者是渲染侧拦。

### 由此得到的 M4 设计结论（2026-10-26）

M4「图片与 markdown」原计划"硬依赖 M0 结论"，现在结论齐了，**方案要按下面重定**：

| 用途 | 走哪条路 | 依据 |
|---|---|---|
| **文字富文本**（标题/粗体/斜体/删除线/行内代码/**代码块**/**表格**） | ✅ **直接用 `msg_type=2` 的 markdown 段** | 群聊实测全支持，手机电脑都正常 |
| **图片** | ⚠️ **不能靠 markdown 嵌图**（手机端一律 `[alt]`）。要手机端也能看见，必须：`msg_type=7` **独立图片段单发一条**，或**整段渲染成一张图**再发 | 手机端不渲染 markdown 里的图 |
| **图片的存放地址** | 必须是**已备案域名**或**外部 CDN 域名**（或裸 IP）。**自建未备案域名不可用** | ②b 实测 B/C 无图 |
| **单条消息的内容量** | ≤5 条被动回复；`msg_type` 单值，markdown 与图片不能同条 | 40034128 / 适配器 msg_type 逻辑 |

> 换句话说：**markdown 段负责"文字排版"，图片走独立消息** ——
> 这和项目原本"两套渲染器各写一份样式"的思路正好相反，
> 现在可以**砍掉 QQ 端的图片渲染分支**，统一走"渲染成图"或"独立图片段"。

> 云服务器 nginx 已加 `location /m0test/ { alias /var/www/html/m0test/; }`
> （`sites-enabled/jsp-base`，`jianying.run.place` 的 80 与 8443 两个 server 块各一份）。
> ⚠ **踩过的坑**：nginx 会加载 `sites-enabled/` 下的**所有**文件，`.bak` 也不例外 ——
> 把备份放在 `sites-enabled/` 里会造出重复 `server_name` 冲突，
> 且**新加的 server 块被忽略**（表现为"配置改了却不生效"）。备份要放 `/etc/nginx/backups/`。

## QQ 开放平台约束（检索到的文档口径，需 M0 实测复核）

| 事项 | 出处口径 | 可信度 |
|---|---|---|
| 消息类型：1 纯文本 / 2 Markdown / 7 富媒体（需先上传取 `file_info`） | bot.q.qq.com 消息类型总览（2026-07-23） | 官方 |
| markdown 内图片"请使用可在公网访问的资源 url，**开放平台会下载转存该资源**" | bot.q.qq.com Markdown 消息（2026-04-23） | 官方 |
| 群消息支持文本/Markdown/富媒体；**群消息不支持流式参数**；被动消息有效时间 5 分钟 | bot.q.qq.com 发送群聊消息（2026-09-03） | 官方 |
| 含链接消息需后台配置 URL 白名单、域名需 ICP 备案；被动 Markdown 消息需申请权限（旧口径提近一月平均 DAU 2000） | 官方知识库群机器人使用规范（2024-09-09，**偏旧**） | 官方但需复核 |
| ~~QQ Markdown 不支持代码块与 HTML~~；单条约 2000 字 | pushplus 文档（2026-09-29，第三方） | ❌ **代码块部分已被实测证伪**（2026-10-26 实测支持，见 M0 章节）；"单条约 2000 字"**未验证** |
| 富媒体上传仅支持 URL 直传与分片合并，不支持 Base64 直传；图片 png/jpg，软限 20MB | 官方知识库（2026-04-01） | 需复核 |

## 前端依赖现状

`package.json` 运行时依赖仅 `axios` / `pinia` / `vue` / `vue-router`，node_modules 中**没有** markdown-it、marked、dompurify、highlight.js、sanitize 任何一项 → D3 需要新增渲染器与 sanitizer 依赖。

## backend 依赖清单缺陷（设备实测复现）

设备上新建 `backend/.venv` 并按 `backend/requirements.txt` 安装后，自检缺失 **`markdown` 与 `playwright`** —— 而这两个恰是 `app/renderer.py` 的必需依赖，即**照 requirements 装出来的环境跑不了任何渲染**。

- 云端生产 venv 实际版本（对齐基准）：`Markdown 3.11.1`、`playwright 1.62.0`、`fastapi 0.141.1`、`uvicorn 0.53.0`、`httpx 0.28.1`
- 设备已补装 `Markdown==3.11.1` + `playwright==1.62.0`，与云端浏览器缓存 `chromium-1234` 匹配
- `sqlmodel` **不需要**（`app/models.py` 用的是 `sqlalchemy` + `declarative_base`，云端生产 venv 也没装 sqlmodel）
- 待办：把 `markdown` / `playwright` 写进 `requirements.txt`（建议带版本上限，与云端对齐），否则下次重装仍会踩坑

## 现存 6 处漂移（待统一时逐一消除）

1. 渲染宽度默认值：代码 text420/md540/html600，工具手册与 QQ 端仍是 680/860/900。
2. 两套渲染器：`backend/app/renderer.py`（自写 CSS）vs `qqbot/plugins/_lib/renderer.py`（nonebot-plugin-htmlrender）。
3. 协议解析逻辑两份拷贝（约 150 行正则 ×2）。
4. `web:search` 挂载条件不对称（backend 有 `if searxng_url` 判断，qqbot 无条件）。
5. `_MAX_TOOL_STEPS`（3 硬编码 / 默认 2 可配）、`web:screenshot` 的 `width` 参数一边有一边无、`site:add` 描述文案分叉。
6. **chromium 启动参数三处各写一遍**：backend 硬编码缺 swiftshader、云端 `qqbot/.env.prod` 显式带 swiftshader、timetable 自动检测。

---

内容由 AI 生成
