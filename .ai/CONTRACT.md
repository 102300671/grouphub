# 共享包接口契约

实现时以此为准。改动契约本身需要先更新 `DECISIONS.md`。

## 目录形态（M1 建立）

```
shared/aitools/
  __init__.py        对外唯一入口：build_agent()
  registry.py        Package / Tool / ToolCall / ImageArtifact
  protocol/
    textcodec.py     文本协议：编解码 + 漂移容错（现两份正则合并于此）
    natcodec.py      原生 tool_call schema 生成与解析
    names.py         web:search ⇄ web_search 双向映射表（由 registry 生成）
  capability.py      判定：覆盖 > 缓存 > 探测一次
  loop.py            run_turn()：activate → 执行 → 回灌 → 收敛
  tools/
    web.py           search / text / md / html / screenshot
    site.py          hot / search / add
  ports.py           ChatBackend / ImageSink / Store / SiteDAO（全部 Protocol）
  errors.py          错误文案规范（见下）
```

## 核心数据结构

```python
@dataclass(frozen=True)
class Tool:
    namespace: str            # "web" | "site"
    name: str                 # "search"；对外全名 "web:search"
    description: str          # 唯一真相：text 手册与 native schema 共用
    params: dict[str, str]    # 参数名 → 说明（含"必填/可选 + 真实默认值"）
    handler: Callable         # async (args: dict, ctx: Context) -> str
    defaults: dict[str, int]  # 数值参数的真实默认值，手册文案由此生成 ← 修掉漂移1

@dataclass
class Context:
    owner_id: int | None      # backend 侧用户
    user_qq: str | None       # qqbot 侧发起人
    conversation_id: int | None
    channel: str              # "web" | "qq"
    sink: ImageSink           # 图片交付出口

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
| 手册默认值文案 | "默认 X"字样必须由 `Tool.defaults` 生成，禁止手写 | 修掉漂移1（当前 680/860/900 与代码 420/540/600 不符） |

## ports 的职责边界

- 工具**只允许**通过 ports 触达宿主；出现 `import fastapi` / `import nonebot` 即为违规。
- `ChatBackend`：只负责"发一轮模型请求，返回文本 + 思维链分片回调"，不含工具逻辑。
- `ImageSink`：D3 未落地前，Web 实现返回 `rel_url`、QQ 实现直接发图并返回文本回执；切换只改这里。
- `SiteDAO` / `Store`：QQ 端保持现有 HTTP 走 `/bot/ai/*`，backend 端保持进程内 SQLModel。

---

内容由 AI 生成
