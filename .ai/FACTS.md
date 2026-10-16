# 已实测事实基线（2026-10-16）

> 这些数字都是实测得到的，接手时可直接依赖，不必重做。若与你的假设冲突，以重测为准并更新本文件。

## 代码基线

| 项 | 值 |
|---|---|
| 本地设备 / 云端 / GitHub HEAD | 全部 `0b1f1a1`（三向一致，工作区无未提交差异） |
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
| alist 全局设置 | `x_setting_items.sign_all = true`（全站强制签名）；设置项里**没有**签名有效期项 |
| sign 形态 | URL 尾部为 `…:0`，`alist_client.py` 注释自述"sign 基于文件 hash，**永不过期**" → 凭据长期有效（残留风险见 RISKS R3） |
| `backend/app/config.py` | 无任何"站点公网基址"配置项 |
| `public_url()` | 主动把绝对直链降级为同源相对路径（`/zfile`、`/alist`），**sign 参数原样保留** |

> ⚠️ **方法论留痕（本文件曾经写错过）**：alist 对失败请求返回的是 **HTTP 200 + JSON 错误体**，不是 401/404 状态码。
> 只看 `curl -w "%{http_code}"` 会把"签名无效"误判成"图片可任意抓取"，本文件初版就是这么错的（16:12 版）。
> **判定规则：验证资源可达性必须同时看 `content-type` 与响应体魔数/前缀，状态码单独不可信。** V2/V1 已按此口径执行。

## QQ 开放平台约束（检索到的文档口径，需 M0 实测复核）

| 事项 | 出处口径 | 可信度 |
|---|---|---|
| 消息类型：1 纯文本 / 2 Markdown / 7 富媒体（需先上传取 `file_info`） | bot.q.qq.com 消息类型总览（2026-07-23） | 官方 |
| markdown 内图片"请使用可在公网访问的资源 url，**开放平台会下载转存该资源**" | bot.q.qq.com Markdown 消息（2026-04-23） | 官方 |
| 群消息支持文本/Markdown/富媒体；**群消息不支持流式参数**；被动消息有效时间 5 分钟 | bot.q.qq.com 发送群聊消息（2026-09-03） | 官方 |
| 含链接消息需后台配置 URL 白名单、域名需 ICP 备案；被动 Markdown 消息需申请权限（旧口径提近一月平均 DAU 2000） | 官方知识库群机器人使用规范（2024-09-09，**偏旧**） | 官方但需复核 |
| QQ Markdown 不支持代码块与 HTML；单条约 2000 字 | pushplus 文档（2026-09-29，第三方） | 定性参考，不可当事实数据 |
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
