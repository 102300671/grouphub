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
import time
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional

import httpx
from dotenv import load_dotenv
from nonebot import get_driver, logger
from nonebot.adapters import Bot, Event

from .._lib import aisync, aitools
from .._lib.bots import qq_openapi_request, resolve_sender_qq
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

_BUILTIN_CONFIGS_ENV = "AI_BUILTIN_CONFIGS"


def _parse_builtin_configs() -> List[Dict[str, str]]:
    """解析 .env.prod 的内置配置：主默认（AI_* 变量，name=默认配置）+ AI_BUILTIN_CONFIGS。

    AI_BUILTIN_CONFIGS 为 JSON 数组，每项 {name, api_base, api_key, model,
    system_prompt, searxng_url}；解析失败仅告警并忽略额外配置。
    """
    cfg = get_config()
    items: List[Dict[str, str]] = [
        {
            "name": "默认配置",
            "api_base": cfg["api_base"],
            "api_key": cfg["api_key"],
            "model": cfg["model"],
            "system_prompt": cfg["system_prompt"],
            "searxng_url": cfg["searxng_url"],
        }
    ]
    raw = os.getenv(_BUILTIN_CONFIGS_ENV, "").strip()
    if not raw:
        return items
    try:
        extra = json.loads(raw)
    except (ValueError, TypeError) as exc:
        logger.warning(f"[ai] AI_BUILTIN_CONFIGS 解析失败（忽略额外配置）：{exc}")
        return items
    if not isinstance(extra, list):
        logger.warning("[ai] AI_BUILTIN_CONFIGS 必须是 JSON 数组，已忽略")
        return items
    for idx, entry in enumerate(extra):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip() or f"内置配置{idx + 1}"
        items.append(
            {
                "name": name,
                "api_base": str(entry.get("api_base") or "").strip(),
                "api_key": str(entry.get("api_key") or "").strip(),
                "model": str(entry.get("model") or "").strip(),
                "system_prompt": str(entry.get("system_prompt") or "").strip() or None,
                "searxng_url": str(entry.get("searxng_url") or "").strip() or None,
            }
        )
    return items


async def _sync_builtin_configs() -> None:
    try:
        await aisync.push_defaults(_parse_builtin_configs())
    except Exception as exc:  # noqa: BLE001 后端离线不阻断启动
        logger.warning(f"[ai] 启动同步内置配置失败（问答仍可用本地默认）：{exc}")


get_driver().on_startup(_sync_builtin_configs)


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
    messages: List[Dict[str, str]],
    *,
    base: str,
    key: str,
    model: Optional[str],
    on_delta: Optional[Callable[[str], Awaitable[None]]] = None,
    on_reasoning: Optional[Callable[[str], Awaitable[None]]] = None,
) -> str:
    """调用 chat/completions（流式），返回首条回复文本。失败抛 CommandError（中文可读）。

    base/key/model 由调用方按「用户生效配置」或「.env.prod 默认」解析后传入。
    on_delta 可选：每收到一段正文增量就回调，用于 QQ 单聊实时流式打字机效果。
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
    client = _http()
    # 先带 enable_thinking（思考走独立 reasoning_content 通道，本函数不转发它，
    # QQ 只收到干净正文；且该模式下工具调用标签更规范）。
    # 上游不认识该参数（400/422）时自动去参重试，兼容严格 OpenAI 端点。
    payloads = [
        {"model": model, "messages": messages, "stream": True, "enable_thinking": True},
        {"model": model, "messages": messages, "stream": True},
    ]
    resp = None
    last_err_body = ""
    for idx, payload in enumerate(payloads):
        candidate = await client.send(
            client.build_request("POST", url, json=payload, headers=headers),
            stream=True,
        )
        if idx == 0 and candidate.status_code in (400, 422):
            last_err_body = (await candidate.aread()).decode("utf-8", "ignore")[:200]
            await candidate.aclose()
            logger.info(f"[ai] 上游不支持 enable_thinking，降级普通请求：{last_err_body}")
            continue
        resp = candidate
        break
    assert resp is not None

    collected = ""
    flushed = 0  # 已通过 on_delta 推送的长度；<tool_call> 块原文不推送（QQ 不闪 XML）
    try:
        if resp.status_code != 200:
            text = (await resp.aread()).decode("utf-8", "ignore")[:300]
            logger.error(f"[ai] 接口返回 {resp.status_code}：{text}")
            raise CommandError(f"⚠️ AI 接口返回 {resp.status_code}：{text}")
        async for line in resp.aiter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
                delta = chunk["choices"][0].get("delta", {})
                # reasoning_content 为模型思考链：QQ 消息不展示，仅回调收集（随轨迹落库）
                r_piece = delta.get("reasoning_content") or ""
                if r_piece and on_reasoning is not None:
                    try:
                        await on_reasoning(r_piece)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug(f"[ai] on_reasoning 回调异常（不影响主流程）：{exc}")
                piece = delta.get("content") or ""
            except (ValueError, KeyError, IndexError):
                continue
            if piece:
                collected += piece
                if on_delta is None:
                    continue
                open_idx = collected.lower().find("<tool_call")
                safe_end = open_idx if open_idx >= 0 else len(collected)
                if safe_end > flushed:
                    try:
                        await on_delta(collected[flushed:safe_end])
                    except Exception as exc:  # noqa: BLE001
                        logger.debug(f"[ai] on_delta 回调异常（不影响主流程）：{exc}")
                    flushed = safe_end
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        logger.error(f"[ai] 请求失败：{url} -> {exc!r}")
        raise CommandError(f"⚠️ AI 接口连接失败（{url}）：{exc}") from exc
    finally:
        await resp.aclose()

    if not collected.strip():
        raise CommandError("⚠️ AI 返回了空内容。")
    return collected.strip()


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

def _c2c_openid(event: Event) -> Optional[str]:
    """官方单聊（C2C）用户 openid；群聊 / OneBot 返回 None。"""
    oid = _event_openid(event)
    if oid and oid.startswith("c2c:"):
        return oid[len("c2c:"):]
    return None


class _C2CStreamer:
    """QQ 官方单聊实时流式发送器：边收模型增量边推 stream_messages。

    协议：input_mode=replace（每帧传当前累计全文），input_state 1=生成中 / 10=完成，
    index 从 0 递增，首帧返回 stream_msg_id 供后续帧携带；帧间 ~300ms 节流。
    完成后必须调用 finish() 发送 input_state=10 收尾。
    """

    def __init__(self, bot: Bot, event: Event, openid: str, msg_id: Optional[str] = None):
        self.bot = bot
        self.event = event
        self.openid = openid
        self.msg_id = msg_id
        self.accumulated = ""
        self.msg_seq = int(time.time() * 1000) % 65536
        self.stream_msg_id: Optional[str] = None
        self.index = 0
        self._last_send = 0.0
        self._closed = False

    def reset(self) -> None:
        """清空累计文本：工具轮结束、最终回答开始前调用。

        下一次 send 的 replace 帧会用最终答案整体覆盖中间轮内容，
        stream_msg_id / index 沿用同一条流式消息，无需重开。
        """
        self.accumulated = ""
        self._last_send = 0.0

    async def send(self, piece: str) -> None:
        """追加一段增量文本，节流发送（~5 帧/秒）。"""
        if self._closed:
            return
        self.accumulated += piece
        now = time.monotonic()
        if now - self._last_send < 0.2:  # 节流：每 200ms 最多一帧
            return
        self._last_send = now
        await self._push(input_state=1)

    async def finish(self) -> None:
        """收尾：发送 input_state=10 完成帧。"""
        if self._closed:
            return
        self._closed = True
        try:
            await self._push(input_state=10)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[ai] C2C 流式收尾失败，降级普通消息：{exc}")
            for chunk in _split_message(self.accumulated):
                await self.bot.send(self.event, chunk)

    async def _push(self, input_state: int) -> None:
        body: Dict[str, object] = {
            "input_mode": "replace",
            "input_state": input_state,
            "content_type": "markdown",
            "content_raw": self.accumulated,
            "msg_seq": self.msg_seq,
            "index": self.index,
        }
        if self.msg_id:
            body["msg_id"] = self.msg_id
        if self.stream_msg_id:
            body["stream_msg_id"] = self.stream_msg_id
        resp = await qq_openapi_request(
            self.bot, "POST", f"/v2/users/{self.openid}/stream_messages", json_body=body
        )
        if self.stream_msg_id is None and isinstance(resp, dict) and resp.get("id"):
            self.stream_msg_id = str(resp["id"])
        self.index += 1


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
CONV_COMMAND = Command(
    ns_en="ai", ns_zh="问", sub_en="conv", sub_zh="会话",
    summary="查看/切换 AI 会话（默认只列当前组的）",
    brief="[列表|默认|新|<序号>|移 <序号>] [选项]",
    usage="[动作] [选项]",
    examples=(
        "/ai 会话",
        "/ai 会话 3",
        "/ai 会话 默认",
        "/ai 会话 新",
        "/ai 会话 --scope web",
        "/ai 会话 移 12 --scope web",
    ),
    options=(
        Option(long="scope", short="s", value_name="<范围>", default="current",
               help="查看/移动范围：current(当前组,默认) | all(QQ大组全部) | web(前端大组)"),
    ),
    handler="commands.ai:conv",
    allow_positional=True,
    require_registered=True,
    notes=(
        "不带动作默认列出当前 openid 组会话；<序号> 从列表编号，切换后继续该会话。",
        "默认会话：提问时不指定就继续它；/ai 会话 默认 [<序号>] 查看/设置。",
        "查看其它 openid 组或前端大组的会话请加 --scope all|web，可移动过来继续。",
    ),
)


COMMANDS = (
    ASK_COMMAND,
    RESET_COMMAND,
    CONV_COMMAND,
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


def _event_openid(event: Event) -> Optional[str]:
    """AI 会话组定位键：官方群聊=群 openid（每群一组，组名=群名）；
    官方私聊=c2c:user_openid；OneBot 返回 None（后端按群号兜底）。"""
    g = getattr(event, "group_openid", None)
    if g:
        return str(g)
    try:
        uid = str(event.get_user_id() or "")
    except Exception:  # noqa: BLE001
        return None
    if not uid or uid.isdigit():
        return None
    return f"c2c:{uid}"


def _is_private(event: Event) -> bool:
    """是否为私聊。QQ 官方：无 group_openid；OneBot：message_type=private。"""
    if getattr(event, "message_type", None) == "private":
        return True
    if getattr(event, "group_openid", None):
        return False
    if getattr(event, "group_id", None):
        return False
    return True


async def _bot_name(bot: Bot) -> Optional[str]:
    """机器人显示名：优先适配器 API，失败用 QQ_BOT_NAME 配置。"""
    for meth in ("get_self_info", "get_login_info"):
        fn = getattr(bot, meth, None)
        if fn is None:
            continue
        try:
            info = await fn()
        except Exception:  # noqa: BLE001
            continue
        try:
            if isinstance(info, dict):
                name = info.get("nickname") or info.get("nick") or info.get("bot_name")
            else:
                name = getattr(info, "nickname", None) or getattr(info, "bot_name", None)
            if name:
                return str(name).strip()[:50]
        except Exception:  # noqa: BLE001
            continue
    return os.getenv("QQ_BOT_NAME") or None


async def _folder_name(bot: Bot, event: Event) -> str:
    """openid 组名：群聊=群名，私聊=机器人名。"""
    if not _is_private(event):
        gid = _event_group_id(event)
        t = await _group_title(bot, gid)
        if t and t.startswith("群聊 · "):
            return t[len("群聊 · "):]
        return f"群 {gid}"
    name = await _bot_name(bot)
    return name or "私聊"


def _parse_index(text: str) -> Optional[int]:
    if not text or not text.isdigit():
        return None
    n = int(text)
    return n if n >= 1 else None


async def conv(bot: Bot, event: Event, result: ParseResult) -> None:
    """/ai 会话：列表 / 切换 / 默认 / 新建 / 移动。"""
    qq = await resolve_sender_qq(event)
    openid = _event_openid(event)
    scope = result.get("scope") or "current"

    async def _load(scope_: str) -> tuple:
        data = await aisync.list_conversations(qq, openid=openid, scope=scope_)
        return (data.get("items") or []), (data.get("current_id") or None)

    try:
        args = list(result.positional)
        action = args[0] if args else "list"

        if action in ("新", "new"):
            group_id = _event_group_id(event)
            group_title = await _group_title(bot, group_id)
            folder_name = await _folder_name(bot, event)
            info = await aisync.group_conversation(
                qq, group_id, group_title,
                openid=openid, folder_name=folder_name, force_new=True,
            )
            title = info.get("title") or "新会话"
            await bot.send(event, f"🆕 已开启新会话「{title}」，后续提问自动使用。")
            return

        if action in ("默认", "default"):
            items, _ = await _load("current")
            if len(args) >= 2:
                idx = _parse_index(args[1])
                if idx is None or idx > len(items):
                    raise CommandError("⚠️ 序号无效，请先 /ai 会话 查看编号。")
                target = items[idx - 1]
                await aisync.switch_conversation(qq, target["id"])
                shown = target.get("title") or f"会话#{target['id']}"
                await bot.send(event, f"⭐ 已将「{shown}」设为默认会话。")
            else:
                cur = next((i for i in items if i.get("is_default")), None)
                if cur:
                    shown = cur.get("title") or f"会话#{cur['id']}"
                    msg = f"当前默认会话：{shown}"
                else:
                    msg = "当前默认会话：（无）"
                await bot.send(event, msg + "\n设置：/ai 会话 默认 <序号>")
            return

        if action in ("移", "move"):
            if len(args) < 2:
                raise CommandError("⚠️ 用法：/ai 会话 移 <序号> [--scope all|web]")
            items, _ = await _load(scope)
            idx = _parse_index(args[1])
            if idx is None or idx > len(items):
                raise CommandError("⚠️ 序号无效，请先查看目标列表。")
            target = items[idx - 1]
            folder_name = await _folder_name(bot, event)
            await aisync.move_conversation(
                qq, target["id"], openid=openid, folder_name=folder_name
            )
            shown = target.get("title") or f"会话#{target['id']}"
            await bot.send(
                event,
                f"✅ 已把「{shown}」移到当前组并设为默认，可继续对话。",
            )
            return

        if action in ("删", "del", "delete"):
            if len(args) < 2:
                raise CommandError("⚠️ 用法：/ai 会话 删 <序号> [--scope all|web]")
            items, _ = await _load(scope)
            idx = _parse_index(args[1])
            if idx is None or idx > len(items):
                raise CommandError("⚠️ 序号无效，请先查看目标列表。")
            target = items[idx - 1]
            shown = target.get("title") or f"会话#{target['id']}"
            await aisync.delete_conversation(qq, target["id"])
            await bot.send(event, f"🗑️ 已删除会话「{shown}」。")
            return

        idx = _parse_index(action)
        if idx is not None:
            items, _ = await _load("current")
            if not items or idx > len(items):
                raise CommandError("⚠️ 当前组没有这个会话，先 /ai 会话 查看编号。")
            target = items[idx - 1]
            await aisync.switch_conversation(qq, target["id"])
            shown = target.get("title") or f"会话#{target['id']}"
            await bot.send(
                event,
                f"✅ 已切换到「{shown}」，后续提问继续此会话。",
            )
            return

        items, current_id = await _load(scope)
        scope_label = {"current": "当前组", "all": "QQ 大组全部", "web": "前端大组"}.get(scope, scope)
        if not items:
            await bot.send(
                event,
                f"📭 {scope_label}还没有会话。\n直接 /ai 提问会自动创建；也可 /ai 会话 新 开新会话。",
            )
            return
        lines = [f"📋 {scope_label}的会话："]
        for n, it in enumerate(items, 1):
            star = "⭐" if it.get("is_default") else "  "
            title = it.get("title") or f"会话#{it['id']}"
            extra = ""
            if scope != "current" and it.get("folder_name"):
                extra = f"（{it['folder_name']}）"
            last = (it.get("last_message") or "").strip()
            if len(last) > 40:
                last = last[:39] + "…"
            suffix = f" — {last}" if last else ""
            lines.append(f"{star}{n}. {title}{extra}{suffix}")
        lines.append("")
        if scope == "current":
            lines.append("切换：/ai 会话 <序号>；默认：/ai 会话 默认 [<序号>]；新建：/ai 会话 新")
            lines.append("其它范围：--scope all（QQ大组全部）/ --scope web（前端大组）")
        else:
            lines.append("继续：/ai 会话 移 <序号> --scope all|web（移到当前组）")
        await bot.send(event, "\n".join(lines))
    except CommandError as exc:
        await bot.send(event, exc.message)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        try:
            detail = exc.response.json().get("detail", "") or ""
        except Exception:  # noqa: BLE001
            detail = ""
        if status == 404 and "绑定" in str(detail):
            await bot.send(
                event,
                "⚠️ 你的 QQ 尚未绑定站点账号，无法使用 AI 会话。\n"
                "请先在网页端注册（注册页会给你验证码，发给机器人完成绑定），"
                "绑定后再来使用。",
            )
        elif status == 404:
            await bot.send(
                event, f"⚠️ 未找到目标：{detail or '请先 /ai 会话 查看编号。'}"
            )
        else:
            await bot.send(event, f"⚠️ 操作失败（{status}）：{detail or '请稍后重试'}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("[ai 会话] 处理异常")
        await bot.send(event, f"⚠️ 操作失败：{exc}")


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
            openid = _event_openid(event)
            folder_name = await _folder_name(bot, event)
            conv = await aisync.group_conversation(
                caller_qq, group_id, group_title,
                openid=openid, folder_name=folder_name,
            )
            conversation_id = conv.get("conversation_id")
            history = [
                {"role": m["role"], "content": m["content"]}
                for m in (conv.get("messages") or [])
            ]
            backend_ok = True
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            try:
                detail = exc.response.json().get("detail", "") or ""
            except Exception:  # noqa: BLE001
                detail = ""
            if status == 404 and "绑定" in str(detail):
                await bot.send(
                    event,
                    "⚠️ 你的 QQ 尚未绑定站点账号，无法创建 AI 会话。\n"
                    "请先在网页端注册（注册页会给你验证码，发给机器人完成绑定），"
                    "绑定后再来提问。",
                )
                aitools.reset_current_user_qq(qq_token)
                return
            logger.warning(f"[ai] 后端配置/会话获取失败，降级 .env.prod 默认：{exc}")
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

    c2c_uid = _c2c_openid(event)
    is_c2c_stream = _is_private(event) and c2c_uid is not None
    streamer: Optional[_C2CStreamer] = None
    if is_c2c_stream:
        streamer = _C2CStreamer(bot, event, c2c_uid, msg_id=getattr(event, "id", None) or None)

    # 思维链/工具链轨迹：随 assistant 消息同步后端，前端（含本会话网页端视图）
    # 可完整还原；QQ 群内仍只收到正文与少量工具提示。
    trace_steps: List[Dict[str, object]] = []

    async def _on_agent_event(kind: str, payload: Dict[str, object]) -> None:
        if kind == "round":
            index = int(payload.get("index") or 0)
            trace_steps.append({"reasoning": "", "calls": []})
            # 单聊流式：工具轮之后的最终回答要整体覆盖中间内容，重置累计文本
            if streamer is not None and index >= 1:
                streamer.reset()
        elif kind == "reasoning":
            if trace_steps:
                trace_steps[-1]["reasoning"] += str(payload.get("text") or "")
        elif kind == "tool_call":
            call_obj: Dict[str, object] = {
                "id": int(payload.get("id") or 0),
                "kind": payload.get("kind") or "tool",
                "name": str(payload.get("name") or ""),
                "args": payload.get("args")
                if isinstance(payload.get("args"), dict)
                else {},
                "raw": str(payload.get("raw") or ""),
            }
            if trace_steps:
                trace_steps[-1]["calls"].append(call_obj)
            # 群聊精简提示：激活包不提示，只保留真实工具动作
            if call_obj["kind"] == "tool":
                args = call_obj["args"]
                if call_obj["name"] == "web:search":
                    await bot.send(event, f"🔍 联网搜索：{args.get('query', '')}")
                elif call_obj["name"] == "site:add":
                    await bot.send(event, f"📤 上传作品到站点：{args.get('title', '')}")
        elif kind == "tool_result":
            cid = int(payload.get("id") or 0)
            result_obj = {
                "ok": bool(payload.get("ok")),
                "summary": str(payload.get("summary") or ""),
                "content": str(payload.get("content") or ""),
            }
            for step in reversed(trace_steps):
                target = next(
                    (c for c in step["calls"] if c["id"] == cid), None
                )
                if target is not None:
                    target["result"] = result_obj
                    break

    async def _chat_once(msgs: List[Dict[str, str]]) -> str:
        return await _chat(
            msgs, base=base, key=key, model=final_model,
            on_delta=streamer.send if streamer is not None else None,
            on_reasoning=lambda text: _on_agent_event("reasoning", {"text": text}),
        )

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
                    {
                        "role": "assistant",
                        "content": answer,
                        "agent_steps": trace_steps,
                    },
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

    if streamer is not None:
        # QQ 官方单聊：实时流式已在 _chat 中边生成边推送，这里收尾
        await streamer.finish()
    else:
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
        # 重新同步内置配置到后端（前端 AI 页跟着更新）
        try:
            await aisync.push_defaults(_parse_builtin_configs())
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[ai] 内置配置同步后端失败：{exc}")

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

    lines = ["⚙️ 可用 AI 配置："]
    for item in data.get("items", []):
        tags = []
        if item.get("is_builtin"):
            tags.append("内置")
        if item.get("kind") == "local":
            tags.append("本地·仅网页端")
        if item.get("is_active"):
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
