"""命令实现：AI 问答（/ai <问题>）。

连接任意 OpenAI 兼容的 chat/completions 接口：
  - 远程 API：OpenAI / DeepSeek / Kimi / GLM / 硅基流动 等，配 AI_API_BASE + AI_API_KEY；
  - 本地模型：Ollama / llama.cpp server / LM Studio / vLLM（均自带 OpenAI 兼容端点），
    配 AI_LOCAL_BASE_URL，群里用 `--local` 或 /ai 配置切换。

配置优先级：/ai 配置（管理员，持久化到 data/ai_config.json）> .env.prod 的 AI_* > 内置默认。
多轮对话：按会话保留最近 AI_MAX_HISTORY 轮上下文，/ai 重置 清空。

联网采用文本协议工具调用（plugins/_lib/aitools.py）：模型自行判断是否需要搜索，
需要时输出 <tool_call> 块，bot 执行 web:search（本地 SearXNG）后把结果回灌，
无需命令行加开关，也不依赖模型原生 function calling。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

import httpx
from dotenv import load_dotenv
from nonebot import get_driver, logger
from nonebot.adapters import Bot, Event

from .._lib import aisync, aitools
from .._lib.bots import resolve_sender_qq
from .._lib.cli import Command, CommandError, Option, ParseResult

_ENV_PATH = Path(__file__).resolve().parents[2] / ".env.prod"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH, override=False)

# ------------------ 配置 ------------------

# 运行时配置文件（/ai 配置 写入；锚定到 qqbot 目录，不依赖进程 cwd）
_CONFIG_PATH = Path(
    os.getenv("AI_CONFIG_PATH", Path(__file__).resolve().parents[2] / "data" / "ai_config.json")
)

_DEFAULTS: Dict[str, str] = {
    "api_base": "https://api.deepseek.com/v1",
    "api_key": "",
    "model": "deepseek-chat",
    "local_base_url": "http://127.0.0.1:11434/v1",
    "local_key": "ollama",
    "local_model": "qwen2.5:7b",
    "searxng_url": "http://127.0.0.1/searxng",
    "system_prompt": "你是群资源站的助手机器人，回答简洁、友好，默认使用中文。",
}

_ENV_KEYS = {
    "api_base": "AI_API_BASE",
    "api_key": "AI_API_KEY",
    "model": "AI_MODEL",
    "local_base_url": "AI_LOCAL_BASE_URL",
    "local_key": "AI_LOCAL_API_KEY",
    "local_model": "AI_LOCAL_MODEL",
    "searxng_url": "AI_SEARXNG_URL",
    "system_prompt": "AI_SYSTEM_PROMPT",
}

_MAX_ROUNDS = max(1, int(os.getenv("AI_MAX_HISTORY", "6") or "6"))
_SEARCH_RESULTS = max(1, int(os.getenv("AI_SEARCH_RESULTS", "5") or "5"))
_MAX_TOOL_STEPS = max(1, int(os.getenv("AI_MAX_TOOL_STEPS", "2") or "2"))
_TIMEOUT = float(os.getenv("AI_TIMEOUT", "120") or "120")


# ------------------ 启动时把默认远程配置同步到后端（前端 AI 页据此展示） ------------------

async def _sync_builtin_default() -> None:
    cfg = get_config()
    try:
        await aisync.push_default(
            {
                "api_base": cfg["api_base"],
                "api_key": cfg["api_key"],
                "model": cfg["model"],
                "system_prompt": cfg["system_prompt"],
                "searxng_url": cfg["searxng_url"],
            }
        )
    except Exception as exc:  # noqa: BLE001 后端离线不阻断启动
        logger.warning(f"[ai] 启动同步默认配置失败（问答仍可用本地默认）：{exc}")


get_driver().on_startup(_sync_builtin_default)


def _read_file_config() -> Dict[str, str]:
    try:
        data = json.loads(_CONFIG_PATH.read_text("utf-8"))
        return {k: str(v) for k, v in data.items() if k in _DEFAULTS and v}
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[ai] 读取配置文件失败（忽略）：{_CONFIG_PATH} -> {exc}")
        return {}


def _write_file_config(updates: Dict[str, str]) -> None:
    current = _read_file_config()
    current.update(updates)
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(
        json.dumps(current, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )


def get_config() -> Dict[str, str]:
    """内置默认 < .env.prod < 运行时配置文件。空值不覆盖。"""
    cfg = dict(_DEFAULTS)
    for key, env_name in _ENV_KEYS.items():
        val = os.getenv(env_name, "").strip()
        if val:
            cfg[key] = val
    cfg.update(_read_file_config())
    return cfg


# ------------------ 对话历史（内存态，重启即清） ------------------

_HISTORY: Dict[str, List[Dict[str, str]]] = {}


def _session_key(event: Event) -> str:
    try:
        return event.get_session_id() or "default"
    except Exception:  # noqa: BLE001
        return "default"


# ------------------ OpenAI 兼容客户端 ------------------

_client: Optional[httpx.AsyncClient] = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(_TIMEOUT, connect=10.0))
    return _client


async def _chat(
    messages: List[Dict[str, str]], *, base: str, key: str, model: Optional[str]
) -> str:
    """调用 chat/completions，返回首条回复文本。失败抛 CommandError（中文可读）。

    base/key/model 由调用方按「用户生效配置」或「.env.prod 默认」解析后传入。
    """
    if not base:
        raise CommandError(
            "⚠️ 尚未配置 AI 接口地址。\n"
            "管理员可用 /ai 配置 --base-url <URL> 设置，或在 .env.prod 填 AI_API_BASE。"
        )
    if not model:
        raise CommandError(
            "⚠️ 尚未配置模型名。\n"
            "管理员可用 /ai 配置 --model <名> 设置；本地模型请确认已指定 AI_LOCAL_MODEL。"
        )

    url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        resp = await _http().post(
            url, json={"model": model, "messages": messages}, headers=headers
        )
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        logger.error(f"[ai] 请求失败：{url} -> {exc!r}")
        raise CommandError(f"⚠️ AI 接口连接失败（{url}）：{exc}") from exc

    if resp.status_code != 200:
        try:
            data = resp.json()
            detail = (
                (data.get("error") or {}).get("message")
                or data.get("message")
                or resp.text[:200]
            )
        except Exception:  # noqa: BLE001
            detail = resp.text[:200]
        logger.error(f"[ai] 接口返回 {resp.status_code}：{detail}")
        raise CommandError(f"⚠️ AI 接口返回 {resp.status_code}：{detail}")

    try:
        content = resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as exc:
        logger.error(f"[ai] 返回格式异常：{resp.text[:300]}")
        raise CommandError("⚠️ AI 返回格式异常，请检查模型名是否正确。") from exc
    if not (content and str(content).strip()):
        raise CommandError("⚠️ AI 返回了空内容。")
    return str(content).strip()


# ------------------ SearXNG 联网搜索 ------------------

async def _web_search(
    query: str, *, limit: Optional[int] = None
) -> tuple[List[Dict[str, str]], List[str]]:
    """经本地 SearXNG 的 JSON API 检索。

    返回 (结果列表[{title,url,content}], 无响应引擎说明列表)。
    limit: 最多返回多少条（None 或 <=0 时用 _SEARCH_RESULTS 默认值）。
    要求 SearXNG 的 settings.yml 中 search.formats 包含 json。
    """
    cfg = get_config()
    base = (cfg["searxng_url"] or "").rstrip("/")
    if not base:
        raise RuntimeError("未配置 SearXNG 地址（AI_SEARXNG_URL 或 /ai 配置 --searxng-url）")
    url = base if base.endswith("/search") else f"{base}/search"
    try:
        resp = await _http().get(
            url,
            params={"q": query, "format": "json", "language": "zh-CN"},
            headers={"Accept": "application/json"},
        )
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise RuntimeError(f"无法连接 SearXNG（{url}）：{exc}") from exc
    if resp.status_code != 200:
        raise RuntimeError(f"SearXNG 返回 {resp.status_code}：{resp.text[:150]}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise RuntimeError("SearXNG 未返回 JSON（请确认 search.formats 已启用 json）") from exc
    cap = limit if (limit and limit > 0) else _SEARCH_RESULTS
    items: List[Dict[str, str]] = []
    for item in (data.get("results") or [])[:cap]:
        record = {
            "title": str(item.get("title") or "").strip(),
            "url": str(item.get("url") or "").strip(),
            "content": str(item.get("content") or "").strip(),
        }
        if record["title"]:
            items.append(record)
    dead: List[str] = []
    for engine, reason in (data.get("unresponsive_engines") or []):
        dead.append(f"{engine}（{reason}）")
    return items, dead


def _build_reference(items: List[Dict[str, str]]) -> str:
    """把搜索结果拼成给模型的参考资料块，并给出可群里展示的来源列表。"""
    blocks = []
    for i, r in enumerate(items, 1):
        blocks.append(
            f"[{i}] {r['title']}\n    {r['url']}\n    {r['content']}"
        )
    return (
        "以下是联网搜索到的参考资料（请优先依据这些资料回答用户问题；"
        "资料没有覆盖的内容按你自己的知识补充，并在回答末尾按 [序号] 列出参考来源）：\n"
        + "\n".join(blocks)
    )


# ------------------ 注册文本协议工具（aitools） ------------------

async def _web_search_tool(args: Dict[str, str]) -> str:
    """web:search 工具入口：模型给 query，返回带序号的参考资料文本。"""
    query = (args.get("query") or "").strip()
    if not query:
        return "错误：缺少必填参数 query（搜索关键词）。"
    items, dead_engines = await _web_search(query)
    if not items:
        logger.warning(f"[ai] web:search 零结果，query={query!r}，无响应引擎：{dead_engines or '（无报告）'}")
        engine_note = ""
        if dead_engines:
            engine_note = (
                "\n注意：本次所有搜索引擎均无响应，故障来自搜索服务侧而非关键词，"
                f"详情：{'、'.join(dead_engines[:6])}。不要反复换词重试超过一次，"
                "请如实告知用户搜索引擎暂时不可用，并给出可自行查证的渠道。"
            )
        return (
            "未搜索到任何结果。可以换一个更精炼或英文的关键词重试一次；"
            "若仍无结果就直接回答用户。" + engine_note
        )
    if dead_engines:
        logger.info(f"[ai] web:search 命中 {len(items)} 条，部分引擎无响应：{dead_engines}")
    return _build_reference(items)


aitools.register_package(
    aitools.Package(
        name="web",
        description="联网搜索：通过本地 SearXNG 检索网页，获取最新资讯、版本信息、攻略资料等。",
    )
)
aitools.register_tool(
    aitools.Tool(
        namespace="web",
        name="search",
        description="联网搜索网页，用于获取最新资讯、近期版本/数值、攻略资料或你不确定的事实。",
        handler=_web_search_tool,
        params={"query": "搜索关键词（必填，精炼，不要包含'帮我搜一下'等客套话）"},
    )
)
logger.info(f"[ai] 已注册工具包：{', '.join(p.name for p in aitools.registered_packages())}"
            f"（工具：{', '.join(t.fullname for t in aitools.registered_tools())}）")


# ------------------ 消息切分 ------------------

def _split_message(text: str, limit: int = 1500) -> List[str]:
    """按段落边界切长回复（QQ 官方通道对单条消息长度有限制）。"""
    if len(text) <= limit:
        return [text]
    chunks: List[str] = []
    cur = ""
    for para in text.split("\n"):
        while len(para) > limit:  # 单段超长则硬切
            chunks.append(para[:limit])
            para = para[limit:]
        if cur and len(cur) + len(para) + 1 > limit:
            chunks.append(cur)
            cur = para
        else:
            cur = f"{cur}\n{para}" if cur else para
    if cur:
        chunks.append(cur)
    return chunks


# ------------------ 命令定义 ------------------

ASK_COMMAND = Command(
    ns_en="ai", ns_zh="问",
    quick=("ai", "AI", "问"),
    summary="向 AI 提问（支持远程 API / 本地模型）",
    brief="<问题>",
    usage="<问题> [选项]",
    examples=(
        "/ai 用一句话介绍量子纠缠",
        "/问 --local 帮我写一个 Python 快排",
        "/ai 泰拉瑞亚最新版本更新了什么",
    ),
    options=(
        Option(long="local", help="本次走本地模型端点"),
        Option(long="model", short="m", value_name="<名>", help="临时指定模型名"),
    ),
    handler="commands.ai:ask",
    allow_positional=True,
    require_registered=True,
    notes=(
        "群里使用需 @机器人；问题直接跟在命令后面写。",
        "自动携带最近几轮对话作为上下文；/ai 重置 可清空。",
        "涉及最新资讯/版本/攻略时机器人会自行判断并联网搜索（本地 SearXNG），无需加参数。",
    ),
)

RESET_COMMAND = Command(
    ns_en="ai", ns_zh="问", sub_en="reset", sub_zh="重置",
    summary="清空本会话的 AI 对话记忆",
    brief="",
    usage="",
    examples=("/ai 重置",),
    handler="commands.ai:reset",
    require_registered=True,
)

CONFIG_COMMAND = Command(
    ns_en="ai", ns_zh="问", sub_en="config", sub_zh="配置",
    summary="查看/修改 AI 接口配置",
    brief="--show",
    usage="[选项]",
    examples=(
        "/ai 配置 --show",
        "/ai 配置 --base-url https://api.deepseek.com/v1 --key sk-xxx --model deepseek-chat",
        "/ai 配置 --local-base-url http://127.0.0.1:11434/v1 --local-model qwen2.5:7b",
        "/ai 配置 --searxng-url http://127.0.0.1/searxng",
    ),
    options=(
        Option(long="show", help="查看当前配置（密钥打码）"),
        Option(long="base-url", value_name="<URL>", help="远程 API 端点（OpenAI 兼容）"),
        Option(long="key", value_name="<KEY>", help="远程 API 密钥"),
        Option(long="model", value_name="<名>", help="远程默认模型名"),
        Option(long="local-base-url", value_name="<URL>", help="本地模型端点"),
        Option(long="local-key", value_name="<KEY>", help="本地端点密钥（可为空）"),
        Option(long="local-model", value_name="<名>", help="本地默认模型名"),
        Option(long="searxng-url", value_name="<URL>", help="SearXNG 联网搜索地址（AI 自主联网时使用）"),
        Option(long="system", value_name="<提示词>", help="系统提示词"),
    ),
    handler="commands.ai:config",
    admin_only=True,
    notes=(
        "修改后持久化到 data/ai_config.json，优先于 .env.prod 的 AI_* 变量；",
        "保存成功会清空全部会话记忆（模型/提示词可能已变化）。",
    ),
)

SEARCHWEB_COMMAND = Command(
    ns_en="searchweb", ns_zh="搜网",
    quick=("搜网", "searchweb"),
    summary="联网搜索（只返回简介和链接）",
    brief="<关键词>",
    usage="<关键词> [选项]",
    examples=(
        "/搜网 泰拉瑞亚 1.4.5 更新内容",
        "/搜网 量子纠缠 是什么",
        "/搜网 -n 10 Rust 2024 版本特性",
    ),
    options=(
        Option(long="limit", short="n", value_name="<数>", kind="int",
               default=5, min_value=1, max_value=20,
               help="返回条数，默认 5，上限 20"),
    ),
    handler="commands.ai:search_web",
    allow_positional=True,
    require_registered=True,
    notes=(
        "只返回简介和链接，不回灌完整正文；需要总结请用 /ai 提问让机器人联网搜索并归纳。",
        "走本地 SearXNG，需要管理员已配置 AI_SEARXNG_URL。",
    ),
)

MINE_CONFIGS_COMMAND = Command(
    ns_en="ai", ns_zh="问", sub_en="configs", sub_zh="我的配置",
    summary="查看可用 AI 配置（内置默认 + 你在网页端新建的）",
    brief="",
    usage="",
    examples=("/ai 我的配置",),
    handler="commands.ai:list_mine",
    require_registered=True,
    notes=(
        "在网页端 AI 页面可新建/编辑自己的配置（用你自己的 API Key）；",
        "然后用「/ai 切换 <配置名>」在群内切过去。",
    ),
)

USE_CONFIG_COMMAND = Command(
    ns_en="ai", ns_zh="问", sub_en="use", sub_zh="切换",
    summary="切换本群对话使用的 AI 配置",
    brief="<配置名>",
    usage="<配置名>",
    examples=(
        "/ai 切换 我的DeepSeek",
        "/ai 切换 默认",
    ),
    handler="commands.ai:use_config",
    allow_positional=True,
    require_registered=True,
    notes=("配置名可用 /ai 我的配置 查看；本地配置仅网页端可用，群内切不过去。",),
)

# 供 cli_router 收集；命名与其他模块统一为 COMMANDS
COMMANDS = (
    ASK_COMMAND,
    RESET_COMMAND,
    MINE_CONFIGS_COMMAND,
    USE_CONFIG_COMMAND,
    CONFIG_COMMAND,
    SEARCHWEB_COMMAND,
)


# ------------------ handlers ------------------

def _event_group_id(event: Event) -> str:
    """从事件取群号：OneBot 自带 group_id；QQ 官方没有，解析 session id，
    再不行用 SYNC_GROUPS 首个兜底。"""
    gid = getattr(event, "group_id", None)
    if gid:
        return str(gid)
    try:
        sid = event.get_session_id() or ""
    except Exception:  # noqa: BLE001
        sid = ""
    parts = sid.split("_")
    if len(parts) >= 2 and parts[0] == "group":
        return parts[1]
    return (os.getenv("SYNC_GROUPS", "").split(",")[0].strip() or "default")


async def _group_title(bot: Bot, group_id: str) -> Optional[str]:
    """尝试取群名称作为会话标题（OneBot 有 get_group_info；失败返回 None）。"""
    if not group_id.isdigit():
        return None
    try:
        info = await bot.get_group_info(group_id=int(group_id))
        name = (
            info.get("group_name")
            if isinstance(info, dict)
            else getattr(info, "group_name", None)
        )
        return f"群聊 · {name}" if name else None
    except Exception:  # noqa: BLE001
        return None


async def ask(bot: Bot, event: Event, result: ParseResult) -> None:
    question = " ".join(result.positional).strip()
    if not question:
        await bot.send(
            event,
            "⚠️ 请把问题直接跟在命令后面。\n\n"
            "用法：/ai <问题>\n例：/ai 用一句话介绍量子纠缠",
        )
        return

    session = _session_key(event)
    use_local_flag = bool(result.get("local"))
    model_override = result.get("model")

    # 把调用者真实 QQ 注入 aitools 上下文，供 site:add 等需要身份的工具使用
    caller_qq = await resolve_sender_qq(event)
    qq_token = aitools.set_current_user_qq(caller_qq)

    cfg = get_config()
    base = ""
    key = ""
    endpoint_model: Optional[str] = None
    history: List[Dict[str, str]] = []
    conversation_id: Optional[int] = None
    backend_ok = False
    user_system_prompt: Optional[str] = None

    if not use_local_flag:
        # 优先走后端：解析该用户在网页端/群内选中的生效配置 + 同步会话
        try:
            active = await aisync.get_active_config(caller_qq)
            if active.get("kind") == "local":
                await bot.send(
                    event,
                    "⚠️ 你当前选中的是「本地配置」：请求走你自己设备的本地网络，"
                    "机器人所在的服务器访问不到你的本地模型。\n"
                    "请在网页端 AI 页面使用该配置，或用「/ai 切换 默认」切回远程配置。",
                )
                aitools.reset_current_user_qq(qq_token)
                return
            base = (active.get("api_base") or "").rstrip("/")
            key = active.get("api_key") or ""
            endpoint_model = active.get("model") or None
            user_system_prompt = active.get("system_prompt")

            group_id = _event_group_id(event)
            group_title = await _group_title(bot, group_id)
            conv = await aisync.group_conversation(caller_qq, group_id, group_title)
            conversation_id = conv.get("conversation_id")
            history = [
                {"role": m["role"], "content": m["content"]}
                for m in (conv.get("messages") or [])
            ]
            backend_ok = True
        except Exception as exc:  # noqa: BLE001 后端离线 → 降级本地默认
            logger.warning(f"[ai] 后端配置/会话获取失败，降级 .env.prod 默认：{exc}")

    if not base:
        # 降级路径：.env.prod / data/ai_config.json；--local 走机器人本地端点
        if use_local_flag:
            base = (cfg["local_base_url"] or "").rstrip("/")
            key = cfg["local_key"] or ""
            endpoint_model = cfg["local_model"]
        else:
            base = (cfg["api_base"] or "").rstrip("/")
            key = cfg["api_key"] or ""
            endpoint_model = cfg["model"]
        history = _HISTORY.setdefault(session, [])

    final_model = model_override or endpoint_model

    messages: List[Dict[str, str]] = []
    # 生效配置的人设提示词（用户自建配置没写则回退默认）+ 工具协议手册
    system_parts = [user_system_prompt or cfg["system_prompt"], aitools.package_catalog()]
    system_content = "\n\n".join(part for part in system_parts if part)
    if system_content:
        messages.append({"role": "system", "content": system_content})
    messages.extend(history)
    messages.append({"role": "user", "content": question})

    async def _on_agent_event(kind: str, payload: Dict[str, object]) -> None:
        if kind == "before_chat" and payload.get("step") == 0:
            await bot.send(event, "🤔 思考中…")
        elif kind == "before_chat":
            await bot.send(event, "🤔 继续思考中…")
        elif kind == "tool_call" and payload.get("name") == "web:search":
            await bot.send(event, f"🔍 联网搜索：{payload.get('args', {}).get('query', '')}")
        elif kind == "tool_call" and payload.get("name") == "site:add":
            await bot.send(event, f"📤 上传作品到站点：{payload.get('args', {}).get('title', '')}")
        # tool_error 的详细日志由 _lib/aitools 统一输出

    async def _chat_once(msgs: List[Dict[str, str]]) -> str:
        return await _chat(msgs, base=base, key=key, model=final_model)

    try:
        answer = await aitools.run_agent_turn(
            _chat_once,
            messages,
            on_event=_on_agent_event,
            max_tool_steps=_MAX_TOOL_STEPS,
        )
    except CommandError as exc:
        await bot.send(event, exc.message)
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("[ai] 提问处理异常")
        await bot.send(event, f"⚠️ 执行出错：{exc}")
        return
    finally:
        aitools.reset_current_user_qq(qq_token)

    if not answer:
        answer = "（模型没有返回有效内容）"

    # 持久化：后端在线 → 同步到群会话；否则走内存历史（降级模式）
    if backend_ok and conversation_id is not None:
        try:
            await aisync.append_messages(
                conversation_id,
                caller_qq,
                [
                    {"role": "user", "content": question},
                    {"role": "assistant", "content": answer},
                ],
            )
        except Exception as exc:  # noqa: BLE001 同步失败不影响已发出的回答
            logger.warning(f"[ai] 群会话消息同步失败：{exc}")
    else:
        # 只把最终问答记入历史，工具调用过程留在本轮 messages，不污染后续上下文
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})
        if len(history) > _MAX_ROUNDS * 2:
            del history[: len(history) - _MAX_ROUNDS * 2]

    for chunk in _split_message(answer):
        await bot.send(event, chunk)


async def reset(bot: Bot, event: Event, result: ParseResult) -> None:
    session = _session_key(event)
    had_memory = _HISTORY.pop(session, None) is not None
    caller_qq = await resolve_sender_qq(event)
    archived = False
    try:
        group_id = _event_group_id(event)
        archived = await aisync.reset_conversation(caller_qq, group_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[ai] 后端会话归档失败：{exc}")
    if archived:
        await bot.send(
            event,
            "🧹 已重置群内 AI 会话并归档，下次提问将开启独立新会话"
            "（旧记录仍可在网页端 AI 页面查看）。",
        )
    elif had_memory:
        await bot.send(event, "🧹 已清空本会话的 AI 对话记忆。")
    else:
        await bot.send(event, "本会话当前没有对话记忆。")


def _mask(key: str) -> str:
    if not key:
        return "（未设置）"
    if len(key) <= 8:
        return key[:2] + "****"
    return f"{key[:4]}****{key[-4:]}"


_CONFIG_OPTION_MAP = {
    "base-url": "api_base",
    "key": "api_key",
    "model": "model",
    "local-base-url": "local_base_url",
    "local-key": "local_key",
    "local-model": "local_model",
    "searxng-url": "searxng_url",
    "system": "system_prompt",
}


async def config(bot: Bot, event: Event, result: ParseResult) -> None:
    updates = {
        cfg_name: str(value)
        for opt_name, cfg_name in _CONFIG_OPTION_MAP.items()
        if (value := result.get(opt_name))
    }
    if updates:
        try:
            _write_file_config(updates)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[ai] 配置持久化失败")
            await bot.send(event, f"⚠️ 配置保存失败：{exc}")
            return
        _HISTORY.clear()
        # 重新同步内置默认到后端（前端 AI 页跟着更新）
        try:
            fresh = get_config()
            await aisync.push_default(
                {
                    "api_base": fresh["api_base"],
                    "api_key": fresh["api_key"],
                    "model": fresh["model"],
                    "system_prompt": fresh["system_prompt"],
                    "searxng_url": fresh["searxng_url"],
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[ai] 默认配置同步后端失败：{exc}")

    cfg = get_config()
    lines = [
        f"✅ 已更新 {len(updates)} 项配置，会话记忆已清空。" if updates else "⚙️ 当前 AI 配置：",
        f"远程端点：{cfg['api_base']}",
        f"远程密钥：{_mask(cfg['api_key'])}",
        f"远程模型：{cfg['model']}",
        f"本地端点：{cfg['local_base_url']}",
        f"本地密钥：{_mask(cfg['local_key'])}",
        f"本地模型：{cfg['local_model']}",
        f"联网搜索：{cfg['searxng_url'] or '（未配置）'}",
        f"系统提示词：{cfg['system_prompt'] or '（未设置）'}",
    ]
    if updates:
        lines.append(f"本次更新：{'、'.join(sorted(updates))}")
    await bot.send(event, "\n".join(lines))


# ------------------ /搜网 handler ------------------

def _fmt_web_brief(items: List[Dict[str, str]]) -> str:
    """精简格式：标题+简介+链接（不回灌完整正文，给群友直接看链接用）。

    与 _build_reference 的区别：后者拼成完整参考资料块回灌给 AI 用于推理；
    本函数只展示 content 作为简介一行 + URL，省去正文重复，便于群友快速点开。
    """
    if not items:
        return "暂无结果。"
    lines = []
    for i, r in enumerate(items, 1):
        lines.append(f"{i}. {r['title']}")
        content = (r.get("content") or "").strip()
        if content:
            if len(content) > 120:
                content = content[:119] + "…"
            lines.append(f"   {content}")
        lines.append(f"   {r['url']}")
    return "\n".join(lines)


async def search_web(bot: Bot, event: Event, result: ParseResult) -> None:
    """联网搜索并只回简介+链接，不回灌完整正文。"""
    query = " ".join(result.positional).strip()
    if not query:
        await bot.send(
            event,
            "⚠️ 请把搜索关键词直接跟在命令后面。\n\n"
            "用法：/搜网 <关键词>\n例：/搜网 泰拉瑞亚 最新版本",
        )
        return
    limit = result.get("limit", 5)
    try:
        items, dead_engines = await _web_search(query, limit=limit)
    except RuntimeError as exc:
        logger.error(f"[ai /搜网] 搜索失败：{exc}")
        await bot.send(event, f"⚠️ 搜索失败：{exc}")
        return
    if not items:
        msg = f"🔍 没搜到「{query}」相关结果。"
        if dead_engines:
            msg += "\n（部分搜索引擎无响应，可稍后重试）"
        await bot.send(event, msg)
        return
    body = _fmt_web_brief(items)
    head = f"🔍 「{query}」的搜索结果（{len(items)} 条）："
    await bot.send(event, f"{head}\n{body}")


# ------------------ /ai 我的配置 / /ai 切换 ------------------

async def list_mine(bot: Bot, event: Event, result: ParseResult) -> None:
    """列出内置默认 + 用户自建配置，标注当前生效项。"""
    qq = await resolve_sender_qq(event)
    try:
        data = await aisync.list_configs(qq)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[ai 我的配置] 查询失败：{exc}")
        await bot.send(event, f"⚠️ 配置查询失败：{exc}")
        return

    active_id = data.get("active_id", 0)
    lines = ["⚙️ 可用 AI 配置："]
    for item in data.get("items", []):
        tags = []
        if item.get("is_builtin"):
            tags.append("内置默认·免费")
        if item.get("kind") == "local":
            tags.append("本地·仅网页端")
        if item.get("id") == active_id:
            tags.append("✓ 当前使用")
        tag = f" [{ '，'.join(tags) }]" if tags else ""
        model = item.get("model") or "（未设模型）"
        lines.append(f"· {item['name']} — {model}{tag}")
    lines.append("")
    lines.append("切换：/ai 切换 <配置名>；新配置请在网页端 AI 页面新建。")
    await bot.send(event, "\n".join(lines))


async def use_config(bot: Bot, event: Event, result: ParseResult) -> None:
    """按名称切换用户生效配置。"""
    name = " ".join(result.positional).strip()
    if not name:
        await bot.send(
            event,
            "⚠️ 请指定配置名。\n用法：/ai 切换 <配置名>\n可用 /ai 我的配置 查看。",
        )
        return

    qq = await resolve_sender_qq(event)
    try:
        data = await aisync.list_configs(qq)
    except Exception as exc:  # noqa: BLE001
        await bot.send(event, f"⚠️ 配置查询失败：{exc}")
        return

    items = data.get("items", [])
    target_id: Optional[int] = None
    if name in ("默认", "default", "内置", "内置默认", "默认配置"):
        target_id = 0
    else:
        for item in items:
            if item.get("name") == name:
                target_id = item.get("id")
                break
    if target_id is None:
        await bot.send(
            event,
            f"⚠️ 找不到名为「{name}」的配置。\n"
            "可用 /ai 我的配置 查看；新配置请先在网页端 AI 页面新建。",
        )
        return

    try:
        await aisync.activate(qq, target_id)
    except Exception as exc:  # noqa: BLE001
        await bot.send(event, f"⚠️ 切换失败：{exc}")
        return
    chosen = next((i for i in items if i["id"] == target_id), None)
    shown = chosen["name"] if chosen else "内置默认"
    await bot.send(event, f"✅ 已切换为「{shown}」，后续对话使用该配置。")
