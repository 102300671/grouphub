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
| ② markdown 嵌图 `![alt](url)` | 🔴 **不带尺寸提示时手机端不渲染**（根因已确证，见 ②c 节） | 标准 `![alt](url)`：手机端一律 `[alt]`，电脑端能出（桌面端有 fallback）。**加上 `#宽px #高px` 后手机端正常出图** |
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
| "被动 Markdown 消息需申请权限" / "含链接消息需后台配置 URL 白名单、域名需 ICP 备案" | 官方知识库群机器人使用规范（2024-09-09，偏旧） | 🟡 **markdown 消息本身发得出去**（截图 ② 证明）。「域名需 ICP 备案」这条**方向对但机制未定** —— 自建未备案域名（B/C）在电脑端确实不出图，但 ②b 汇总显示平台**并没有拒发** B/C，所以**不是平台侧拦的**，见下面的三条件模型 |
| "markdown 内图片…开放平台会下载转存该资源" | 官方 Markdown 消息（2026-04-23） | ✅ **成立**。实测裸 IP + http 与外部公网 https 都能出图 |

### ②b 嵌图对照实验（第二轮 · 2026-10-26 10:24 实跑 · **结论已定**）

同一段 markdown 图片语法，**只换图片直链**。

| 变体 | 直链 | 电脑端 | 手机端 |
|---|---|---|---|
| A 裸 IP + http | `http://59.110.163.88/m0test/blocks.png` | ✅ **出图** | `[alt]` |
| B 域名 + http | `http://jianying.run.place/m0test/blocks.png` | ❌ **无图** | `[alt]` |
| C 域名 + https（自签） | `https://jianying.run.place:8443/m0test/blocks.png` | ❌ **无图** | `[alt]` |
| D 外部公网 https | `https://www.python.org/static/img/python-logo.png` | ✅ **出图** | `[alt]` |

### ②b 的汇总结果推翻了我上一版归因（2026-10-26 10:24 那轮）

`/test 3` 末尾汇总里 **A/B/C/D 四条全是「✅ 已发出（图片转存成功，应该能看到图）」** ——
**平台一条都没拒发**。所以我上一版写的「B/C 无图 = 自建域名未备案被平台拒转存」**不成立**，
已作废。

### ⭐ 官方图片语法要带**尺寸提示**（2026-10-26 调研，两个独立来源对上）

| 来源 | 证据 |
|---|---|
| **官方 markdown 文档**<br>`bot.q.qq.com/wiki/develop/api-v2/server-inter/message/type/markdown.html` | 「图片」小节里**唯一**那张示例就是 `![text #208px #320px](https://resource5-1255303497.cos.ap-guangzhou.myqcloud.com/.../building.png)` —— 官方语法是 **`![alt #宽px #高px](url)`**，不是标准 `![alt](url)` |
| **腾讯自家 openclaw-qqbot 插件**<br>`tencent-connect/openclaw-qqbot` | 专门实现「**Size hint injection**」：把标准 `![alt](url)` 转成 `![#Wpx #Hpx](url)`。做法是 **HTTP Range 拉前 64KB** 解析 PNG IHDR / JPEG / GIF / WebP 头取真实尺寸。其文档原文："QQ Bot's Markdown format **requires** a special image syntax that includes width and height hints. This improves rendering performance by allowing the client to **reserve space before the image loads**." |

官方文档**从头到尾没提过手机端/电脑端的差异** —— 这是官方自己的实现细节，不是有意支持的语法。

### ⭐ 三条件模型（当前最自洽的解释，待 `/test 5` 验证）

出图需要**同时**满足三条：

| # | 条件 | 谁负责 | 不满足的表现 |
|---|---|---|---|
| ① | **图片资源要能被平台转存** | 平台侧 | 客户端拿到空资源 → 无图 |
| ② | **markdown 图片语法要带尺寸提示** `![alt #Wpx #Hpx](url)` | **客户端侧** | 图片节点建不起来 → 降级成 alt 文本 `[alt]` |
| ③ | **客户端要支持渲染** | 客户端侧 | 永远只显示 `[alt]` |

对照实测：

- **电脑端出图（A/D）**：①✅（裸 IP、外部公网域名可转存）②❌（没带尺寸）③✅（桌面端有 fallback 能自行取尺寸）→ 仍能显示
- **电脑端无图（B/C）**：① 存疑（平台没报错，但自建未备案域名很可能转存失败）→ 无资源可显示
- **手机端全 `[alt]`**：③ 存疑 / ② 很可能是主因 —— **手机端严格按官方语法解析，缺尺寸提示就降级**

→ **可检验的推论**：加上尺寸提示后，**手机端**应该能出图（A、D 立刻能验证）；
若手机端还是 `[alt]`，则 ③ 不成立，得走"独立图片段"路线。

### ⭐ ②c 尺寸提示对照（第三轮 · 2026-10-26 12:09 实跑 · **结论确证**）

| 变体 | 直链 | 尺寸提示 | **手机端** | 电脑端 |
|---|---|---|---|---|
| E | 裸 IP + http | `#480px #480px` | ✅ **出图** | ✅ |
| F | 外部公网 https | `#580px #164px` | ✅ **出图** | ✅ |
| G | 域名 http（未备案） | `#480px #480px` | 🟡 **"加载中"占位**（尺寸 480×480 正确） | ❌ 无图 |
| H | 裸 IP + http | **无** | `[t]`（对照组，**复现成功**） | ✅ |

**E vs H 是干净的变量隔离**：同一个 URL（裸 IP http）、同一个 alt，
**只差有没有 `#宽px #高px`**，结果一个出图一个退化成 `[t]`。

→ **确证：手机端不渲染 markdown 图片的原因，就是没按官方语法带尺寸提示。**
→ 官方语法 `![alt #Wpx #Hpx](url)` 是**手机端出图的必要条件**。
→ **手机端是可以用 markdown 嵌图的**，只要语法写对 ——
上一版"手机上 markdown 嵌图没戏、得砍掉 QQ 端渲染分支"的判断**同样作废**。

**G 顺带把第二个因素也坐实了**：G 的尺寸被正确解析（占位框就是 480×480），
但图片卡在"加载中" —— 说明**尺寸因素已解决，剩下的是"这个地址的图片加载不出来"**。
即 **②（尺寸）与 ①（地址可加载/可转存）是两个独立因素**。

> **G 的"加载中"是最终态还是还在转存？未确认** —— 也可能是 QQ 侧转存尚未完成。
> 若过几分钟仍是"加载中"或变成裂图，则自建未备案域名这条路彻底不通。
> 这条要用户过几分钟再看一眼。

### 官方 markdown 消息能力的其它约束（M0 顺带查到的，M4 要用）

来源：`bot.q.qq.com/wiki/develop/api-v2/server-inter/message/type/markdown.html`

| 事项 | 口径 | 对本项目的影响 |
|---|---|---|
| **能力开通** | 「沙箱环境可使用自定义 Markdown 消息能力调试，**其他环境目前需要内邀开通**」 | 本机器人**已开通**（群聊 markdown 渲染正确即证）。新机器人默认没有 |
| 发送场景 | 单聊 / 群聊 / 文字子频道 / 频道私信，**机器人发送均支持** | 群聊已验；单聊未验 |
| **换多行** | 官方示范用 **`\u200B`（零宽空格）**分行，不是空行 | 做 markdown 渲染时注意：连续换行要插零宽空格，否则可能被折行合并 |
| **富媒体**（`msg_type=7`） | 需**先上传**取 `file_info` 再发；`file_info` **有 ttl 会过期**；**单聊与群聊的上传接口不互通** | 走"独立图片段"兜底路线时要注意：不能把群聊的 file_info 拿到单聊用 |
| 互动召回消息 | `is_wakeup=true`，用户主动对话后 30 天内分 4 个周期各可下发 1 条 | 后续做"主动推送提醒"时可用，与被动消息的 5 次/5 分钟限制无关 |

> 官方文档支持的 markdown 语法全集（实测已覆盖前 4 类）：
> 标题 · 文字样式（粗体/下划线加粗/斜体/加粗斜体/删除线）· 链接 · **图片（带尺寸）** ·
> 有序/无序列表 · 列表嵌套 · 块引用 · 水平分割线 · 换多行（`\u200B`）。
> **HTML 不在支持列表里**（第三方文档说的"不支持代码块"是错的，但"不支持 HTML"未见反例）。

### 由此得到的 M4 设计结论（2026-10-26）

| 用途 | 走哪条路 | 依据 |
|---|---|---|
| **文字富文本**（标题/粗体/斜体/删除线/行内代码/**代码块**/**表格**） | ✅ **直接用 `msg_type=2` 的 markdown 段** | 群聊实测全支持，手机电脑都正常 |
| **图片（首选，已验证可行）** | 走 `msg_type=2` 嵌图，**必须写成 `![alt #Wpx #Hpx](url)`**，发之前**自动探测图片尺寸** | ②c 实测：带尺寸提示 → **手机端出图** |
| **图片尺寸探测** | **HTTP Range 拉前 64KB** 解析 PNG IHDR / JPEG / GIF / WebP 头取宽高，照 `tencent-connect/openclaw-qqbot` 的 `image-size.ts` 做 | 腾讯官方插件同款做法 |
| **图片地址** | 裸 IP、外部公网 https **实测可用**；自建未备案域名**卡在"加载中"**（未确认是转存中还是不通）→ 长期建议**已备案域名 / CDN** | ②c：E/F 出图，G 卡住 |
| **兜底** | 尺寸探测失败 / 地址加载不出来 → `msg_type=7` **独立图片段单发一条**，或**整段渲染成一张图** | `msg_type` 单值，markdown 与图片不能同条 |
| **单条消息的内容量** | ≤5 条被动回复；`msg_type` 单值 | 40034128 / 适配器 msg_type 逻辑 |

> **M4 的路走通了**：markdown 段负责文字排版 + 图片（**带尺寸提示**），
> 平台侧不拒、客户端侧也能渲染。
> **不需要为了手机端单独维护一套"渲染成图"的分支** —— 之前的"砍掉/不砍"纠结到此结束，
> 改成：**统一在 markdown 里嵌图，但必须补尺寸提示**。

> ⚠️ **别忘了 ①（地址）这条**：尺寸对了但地址加载不出来（G），
> 一样看不到图。**图往哪儿放**这件事（备案域名 / CDN）仍然是独立决策。

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
