# 目标架构

## 核心原则

1. **工具不感知自己在哪一端。** 工具实现只有一份，端差异全部下沉到 adapter 接口后面。
2. **协议是编码/解码问题，不是业务问题。** 原生 tool_call 与文本包协议只是两种"信封"，解出来的业务对象必须完全一致。
3. **手册与执行器同源。** 给模型看的参数说明和真正执行的代码来自同一个 `Tool` 定义，杜绝漂移。

## 分层

```
┌──────────────────────────────────────────────────────────┐
│  shared/aitools（仓库内共享包，唯一实现）· M1 已落地       │
│                                                          │
│  __init__.py    对外唯一入口：build_agent()               │
│  agent.py       Agent：持有注册表，跑一轮问答              │
│  registry.py    Package / Tool / Param / ToolCall         │
│                 ImageArtifact / Context / Registry         │
│  prompt.py      date_hint / package_catalog / 工具手册     │
│  protocol/      textcodec（文本协议）· names（名字映射）   │
│  loop.py        agent 循环：activate → 执行 → 回灌 → 收敛   │
│  tools/         web:search text md html screenshot         │
│                 site:hot search add                       │
│  imagesize.py   Range 拉文件头 → 宽高（PNG/JPEG/GIF/WebP） │
│  ports.py       ChatBackend / ImageSink / SearchBackend   │
│                 Renderer / SiteDAO                        │
│  errors.py      错误文案规范（「错误：」前缀）              │
│  logs.py        标准库 logging（共享包不依赖 nonebot）     │
│  ── M3 才建 ── natcodec.py（原生 tool）· capability.py（判定）│
└───────────────┬──────────────────────┬───────────────────┘
                │                      │
        backend adapter         qqbot adapter
        （FastAPI 侧）          （nonebot 侧）
        SSE + 落库 + 存储       消息发送 + 富媒体 + HTTP DAO
```

## 关键设计点

### 1. ports：让工具与宿主解耦

工具只依赖抽象端口，两端各自注入实现：

| port | 生命周期 | backend 注入 | qqbot 注入 |
|---|---|---|---|
| `ChatBackend` | per-turn | 现有 httpx 流式调用 | 现有 `_chat()`（M1 已改成接受 `on_reasoning`） |
| `ImageSink` | per-request | 存 `render_static/` 返回 URL | 富媒体直接发当前会话（`QQImageSink`） |
| `SearchBackend` | 进程级 | SearXNG（地址取用户配置） | SearXNG（地址取 `/ai 配置`） |
| `Renderer` | 进程级 | backend 自写 playwright | nonebot-plugin-htmlrender + playwright |
| `SiteDAO` | 进程级 | 进程内 SQLAlchemy 查询 | 走 backend `/bot/works/*` |
| `Store` | — | M4 才需要（图片落盘），M1 未建 | 同左 |

**生命周期分层是 M1 落地的关键**：`ChatBackend` 必须 per-turn（QQ 端每个用户的
base/key/model 不同，进程级注入会串号），`ImageSink` 必须 per-request（发到当前会话），
其余三个是进程级、由 `build_agent()` 构造时注入。
调用者身份（QQ 号 / 用户 id）不单独设端口，随 `Context` 传下去。

`ImageSink` 的两种实现正是 M3 的落点：**工具产出统一的 `ImageArtifact`（含 bytes、相对路径、绝对 URL），由 sink 决定"发图"还是"给链接"**，工具本身不分支。

### 2. capability：一模型一判定，判定一次

- 判定键 = `(model, api_base)` 归一化哈希。同一模型不同端点可能能力不同，故不把 model 当唯一键。
- 取值：`native`（原生 tool_call）/ `text`（文本协议）/ `unknown`。
- 优先级：人工强制覆盖 > 已缓存判定 > 自动探测一次并持久化。
- 探测方式：一次最小 tool_call 请求，检查响应里是否出现 `tool_calls` 字段；探测失败降级为 `text` 并记录失败原因，避免反复探测。
- 回退：运行期若 `native` 判定失效（端点换模型、上游变更），单次请求捕获异常后回落 `text`，并标记该判定待复核，不静默改库。

### 3. protocol：两种信封，一个内部表示

`natcodec` 与 `textcodec` 都解码成同一组 `ToolCall{name, args, raw}`，之后的循环、执行、回灌完全共用。文本协议保留现有的两阶段（`pkg:activate` 不计工具额度）；原生模式则一次性把包内工具全部塞进 `tools` 数组，**不再需要 activate**，因此 activate 轮只在 `text` 模式出现。

### 4. 命名映射（必须做）

工具名 `web:search` 含冒号，OpenAI 兼容端点的函数名普遍要求 `^[a-zA-Z0-9_-]{1,64}$`（本仓库已实测过同类校验报错）。原生模式必须做双向映射：

```
web:search  ⇄  web_search
site:add    ⇄  site_add
pkg:activate（仅 text 模式存在，原生模式不下发）
```

映射表由 registry 从同一份定义生成，两端不许手写。

### 5. 交付：消息内容统一为 markdown

按 DECISIONS D3，`AIMessage.content` 一律存 markdown，Web 渲染器与 QQ markdown 消息共用同一份文本。渲染图在 markdown 里是 `![alt #宽px #高px](绝对URL)` —— **尺寸提示是必带的**（M0 实测：缺尺寸时手机端只显示 alt 文本，电脑端才 fallback），宽高来自 `shared/aitools/imagesize.py` 的探测。两端各自决定呈现方式。XSS 防线：Web 端引入 `markdown-it` + `DOMPurify`（当前前端**没有任何** md 渲染器或 sanitizer，见 FACTS）。

### 6. 同步：不复制消息，只借上下文

按 DECISIONS D4，跨端历史只作为 prompt 上下文注入，不产生镜像消息、不由机器人代发用户消息。需要新增的是"读另一端历史"的取数函数与注入格式，消息时间线各端自治。

### 7. M1 的落地方式（2026-10-26 落地，与上面的目标态有出入的地方）

- **放在仓库根 `shared/`，不做 pip 安装**：两端把仓库根插进 `sys.path` 后 `from shared.aitools import build_agent`。
  好处是云端部署仍只是 `git pull` + 重启，不用动两个 venv；代价是部署耦合从"装包"变成"路径引导"（见 RISKS R8）。
- **注册表是实例**（`Registry`），`build_agent()` 造一个 `Agent` 持有它 —— 避免"QQ 端进程级注册"与
  "backend 请求级构造工具表"两种模型打架。共享包内**没有全局可变状态**。
- **工具 handler 签名统一为 `async (args, ctx) -> str`**：旧实现的 `set_current_user_qq` /
  `set_current_image_sender` 两个 contextvar 被显式的 `Context` 取代（per-request 状态不再走全局）。
- **参数说明是结构化的**（`Param`）而不是裸字符串：手册文案由 `Param` 渲染，`default` 只有一个来源，
  手册与执行器在结构上就不可能漂移（漂移 1 的根治手段，详见 `CONTRACT.md`）。
- **共享包不 import nonebot**：日志只用标准库 `logging`，QQ 端在 `plugins/_lib/aitools_host.py`
  里做 loguru 桥接。

---

内容由 AI 生成
