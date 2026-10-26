# 共享包接口契约

实现时以此为准。改动契约本身需要先更新 `DECISIONS.md`。

## 目录形态（M1 建立）

```
shared/aitools/
  __init__.py        对外唯一入口：build_agent()
  agent.py           Agent：持有注册表，跑一轮问答
  registry.py        Package / Tool / Param / ToolCall / ImageArtifact
                     Context / Registry
  prompt.py          date_hint / package_catalog / package_tools_manual
  protocol/
    textcodec.py     文本协议：编解码 + 漂移容错（两份正则已合并于此）
    names.py         web:search ⇄ web_search 双向映射（由 registry 生成）
  loop.py            run_turn()：activate → 执行 → 回灌 → 收敛
  tools/
    web.py           search / text / md / html / screenshot + SearXNGSearch
    site.py          hot / search / add + WORK_TYPES
  imagesize.py       图片尺寸探测（Range 拉头 → 宽高 → markdown 尺寸提示）
  ports.py           ChatBackend / ImageSink / SearchBackend / Renderer
                     / SiteDAO（全部 Protocol）
  errors.py          错误文案规范（见下）
  logs.py            标准库 logging（共享包不得依赖 nonebot）
  ── M3 建立 ──
  protocol/natcodec.py   原生 tool_call schema 生成与解析
  capability.py          判定：覆盖 > 缓存 > 探测一次
```

> M1 实际落地时相对本表有三处调整（已同步到下文）：`params` 用结构化的 `Param`
> 而不是裸字符串；`Tool.defaults` 改为**派生属性**（不单独存）；`Store` 端口 M1 不建（M4 再加）。

## 核心数据结构

```python
@dataclass(frozen=True)
class Param:
    desc: str                 # 说明主干，如「图片宽度像素」
    required: bool = True     # 必填 / 可选
    default: int | None       # 数值默认值（**唯一真源**）
    note: str = ""            # 括号内补充，如「最大 20」
    suffix: str = ""          # 括号后补充，如「：novel/anime/...」
    # 渲染结果示例：搜索关键词（必填，精炼，不要包含'帮我搜一下'等客套话）

@dataclass(frozen=True)
class Tool:
    namespace: str            # "web" | "site"
    name: str                 # "search"；对外全名 "web:search"
    description: str          # 唯一真相：text 手册与 native schema 共用
    handler: Callable         # async (args: dict, ctx: Context) -> str
    params: dict[str, Param]  # 参数名 → 结构化说明
    # defaults 是**派生属性**（汇总 Param.default），不单独存字段
    #   → 手册文案与执行兜底同源，漂移 1 在结构上不可能发生

@dataclass
class Context:
    owner_id: int | None      # backend 侧用户
    user_qq: str | None       # qqbot 侧发起人
    conversation_id: int | None
    channel: str              # "web" | "qq"
    sink: ImageSink | None    # 图片交付出口；None = 本会话不支持发图

@dataclass
class ImageArtifact:
    bytes_: bytes
    filename: str
    rel_url: str | None       # 同源相对路径（Web 用）
    abs_url: str | None       # 公网绝对 URL（QQ markdown 用，D3 才必填）
```

## 必须遵守的行为约定

| 项 | 约定 | 原因 |
|---|---|---|
| 工具失败 | 返回以 `错误：` 开头的中文文本，**不抛异常** | 现两份实现都靠这个让模型自纠；`ok` 判定也依赖前缀 |
| 轨迹事件 | `round` / `reasoning` / `tool_call` / `tool_result`，`summary` 取内容前 120 字并压平换行 | 前端步骤卡片还原依赖此结构，历史消息已入库 |
| `pkg:activate` | 不计入 `max_tool_steps`，仅 text 模式存在 | 保持现有轮数语义与已落库轨迹结构 |
| 工具额度 | `max_tool_steps` 改为可配置单一来源，默认沿用现值并显式记录 | 修掉"3 硬编码 / 2 可配"的分歧 |
| 渲染参数 | chromium 启动参数由单一函数按环境判定（移植 timetable 的三重检测） | 修掉漂移6 |
| 手册默认值文案 | "默认 X"字样由 `Param.default` 渲染，禁止手写 | 修掉漂移1（当前 680/860/900 与代码 420/540/600 不符）；M1 已实现，单测锁定 |
| 工具 handler 签名 | `async (args: dict, ctx: Context) -> str` | per-request 状态（身份、发图出口）显式传递，取代 contextvar |
| 尺寸探测失败 | 返回 `None`，**不抛异常** | 探测失败要能降级（退回无尺寸写法），不能把渲染链路带崩 |

## ports 的职责边界

- 工具**只允许**通过 ports 触达宿主；出现 `import fastapi` / `import nonebot` 即为违规。
- `ChatBackend`：只负责"发一轮模型请求，返回文本 + 思维链分片回调"，不含工具逻辑。
  **必须接受 `on_reasoning` 关键字参数**（M1 修掉的老 bug：漏传导致 `/ai` 必抛 `TypeError`）。
- `ImageSink`：D3 未落地前，Web 实现返回 `rel_url`、QQ 实现直接发图并返回文本回执；切换只改这里。
- `SearchBackend` / `Renderer` / `SiteDAO`：进程级，`build_agent()` 构造时注入。
  `SiteDAO.submit(payload, ctx)` 的**提交者身份由 ctx 决定**（QQ 端用 `ctx.user_qq`，
  Web 端用 `ctx.owner_id`），端口实现自己映射到后端字段。
- `Store`：M1 不建，M4 做图片落盘时再加。

### ports 的生命周期（M1 定稿）

| 端口 | 生命周期 | 原因 |
|---|---|---|
| `ChatBackend` | per-turn | QQ 端每用户 base/key/model 不同，进程级注入会串号 |
| `ImageSink` | per-request | 图片要发到"当前会话" |
| `SearchBackend` / `Renderer` / `SiteDAO` | 进程级 | 与会话无关，装配一次 |

---

内容由 AI 生成
