"""QQ 官方 openapi「指令面板」（/v2/panels）封装。

nonebot-adapter-qq 1.7.x 未封装该系列 API，统一走 _lib.bots.qq_openapi_request
直调（get_access_token 复用适配器的 token 缓存与自动刷新）。

接口清单（https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/）：
  POST   /v2/panels                     创建面板（10 QPM，机器人上限 20 个）
  GET    /v2/panels?scope=...           面板列表，按 scope 筛选（30 QPM）
  GET    /v2/panels/{panel_id}          面板详情
  PUT    /v2/panels/{panel_id}          修改面板内容（10 QPM）
  PUT    /v2/panels/{panel_id}/target   修改关联用户/群（60 QPM）
  DELETE /v2/panels/{panel_id}          删除面板（10 QPM）

平台约束（封装内已做前置校验，越界直接 ValueError，避免消耗限频额度）：
  - scope ∈ c2c/group/channel/dm；target_type ∈ all/specific（channel/dm 只能 all）
  - 面板元素最多 20 个；name 显示宽度 ≤ 14（约 7 个汉字）、desc ≤ 30
  - link 元素的 url 必须 https:// 开头
  - specific 关联的 user_openids / group_openids 一次最多 20 个

元素说明：
  - command：用户点击后 name 内容填入聊天输入框（配合 on_command 触发，如「绑定」）
  - link：用户点击后浏览器打开 url（如站点首页）

实测平台行为（2026-09 验证）：
  - 创建后异步生效：立即 get/update/delete 会报 30006（面板不存在），约 5 秒内可见；
    create_panel 内部已自动等待可见，调用方可立即续做 update/delete。
  - target_type=specific 当前对部分机器人返回 30001/30016（参数错误/必填缺失），
    all 全局模式正常；ensure_group_panel 会自动降级 all（单群部署效果等同）。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from nonebot import logger
from nonebot.adapters import Bot

from .bots import qq_openapi_request

_SCOPES = {"c2c", "group", "channel", "dm"}
_TARGET_TYPES = {"all", "specific"}
_PANEL_ITEM_LIMIT = 20
_TARGET_OPENID_LIMIT = 20


# ------------------ 元素构造 helper（带校验） ------------------

def _display_width(s: str) -> int:
    """显示宽度：东亚宽字符算 2，其余算 1（对齐官方「14 字符 ≈ 7 个汉字」）。"""
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in s)


def _check_item(name: str, desc: str) -> None:
    if not name:
        raise ValueError("面板元素 name 不能为空")
    if _display_width(name) > 14:
        raise ValueError(f"面板元素 name 超长（显示宽度 >14）：{name!r}")
    if _display_width(desc) > 30:
        raise ValueError(f"面板元素 desc 超长（显示宽度 >30）：{desc!r}")


def command_item(name: str, desc: str = "", only_admin: bool = False) -> Dict[str, Any]:
    """command 元素：点击后把 name 填入聊天输入框。"""
    _check_item(name, desc)
    item: Dict[str, Any] = {"type": "command", "name": name}
    if desc:
        item["desc"] = desc
    if only_admin:
        item["only_admin"] = True
    return item


def link_item(name: str, url: str, desc: str = "", only_admin: bool = False) -> Dict[str, Any]:
    """link 元素：点击后浏览器打开 url（必须 https://）。"""
    _check_item(name, desc)
    if not url.startswith("https://"):
        raise ValueError(f"link 元素 url 必须 https:// 开头：{url}")
    item: Dict[str, Any] = {"type": "link", "name": name, "link": url}
    if desc:
        item["desc"] = desc
    if only_admin:
        item["only_admin"] = True
    return item


def _check_items(items: List[Dict[str, Any]]) -> None:
    if len(items) > _PANEL_ITEM_LIMIT:
        raise ValueError(f"一个面板最多 {_PANEL_ITEM_LIMIT} 个元素，当前 {len(items)}")


# ------------------ 六个 openapi 方法 ------------------

async def create_panel(
    bot: Bot,
    *,
    scope: str,
    items: List[Dict[str, Any]],
    remark: str = "",
    target_type: Optional[str] = None,
    user_openids: Optional[List[str]] = None,
    group_openids: Optional[List[str]] = None,
) -> str:
    """创建指令面板，返回 panel_id（后续修改/删除/详情用）。

    - scope：c2c（单聊）/ group（群聊）/ channel（文字子频道）/ dm（频道私信）
    - target_type：all（该场景全局）或 specific（仅指定用户/群）；
      channel/dm 只能 all；c2c/group 的 specific 分别用 user_openids / group_openids
    - remark：开发者备注，不对用户展示，最多 255 字符
    """
    if scope not in _SCOPES:
        raise ValueError(f"scope 仅支持 {sorted(_SCOPES)}，当前：{scope!r}")
    _check_items(items)

    body: Dict[str, Any] = {"scope": scope, "panel": {"items": items}}
    if remark:
        if len(remark) > 255:
            raise ValueError("remark 最多 255 字符")
        body["panel"]["remark"] = remark

    tt = target_type or ("all" if scope in ("channel", "dm") else None)
    if scope in ("channel", "dm"):
        if tt != "all":
            raise ValueError("channel/dm 场景 target_type 只能为 all")
    elif tt is not None:
        if tt not in _TARGET_TYPES:
            raise ValueError(f"target_type 仅支持 {sorted(_TARGET_TYPES)}，当前：{tt!r}")
        body["target_type"] = tt
        if tt == "specific" and scope == "group" and group_openids:
            if len(group_openids) > _TARGET_OPENID_LIMIT:
                raise ValueError(f"group_openids 一次最多 {_TARGET_OPENID_LIMIT} 个")
            body["group_openids"] = group_openids
        if tt == "specific" and scope == "c2c" and user_openids:
            if len(user_openids) > _TARGET_OPENID_LIMIT:
                raise ValueError(f"user_openids 一次最多 {_TARGET_OPENID_LIMIT} 个")
            body["user_openids"] = user_openids

    data = await qq_openapi_request(bot, "POST", "/v2/panels", json_body=body)
    panel_id = str((data or {}).get("panel_id") or "")
    if not panel_id:
        raise RuntimeError(f"创建面板成功但响应缺 panel_id：{data!r}")
    logger.info(f"[panels] 创建面板成功：id={panel_id} scope={scope} items={len(items)}")
    # 创建后异步生效：等待可见，避免调用方立即 update/delete 报 30006
    if not await _wait_panel_visible(bot, panel_id):
        logger.warning(f"[panels] 面板 {panel_id} 创建后 10s 内仍未可见（稍后会生效）")
    return panel_id


async def list_panels(
    bot: Bot,
    scope: str,
    *,
    cursor: str = "",
    limit: int = 20,
) -> Dict[str, Any]:
    """分页拉取指定场景下的面板列表（按设置时间倒序）。

    返回 {"records": [...], "next_cursor": str, "is_end": bool}；
    next_cursor 非空时传入下次调用的 cursor 继续翻页。
    """
    if scope not in _SCOPES:
        raise ValueError(f"scope 仅支持 {sorted(_SCOPES)}，当前：{scope!r}")
    if not 1 <= limit <= 50:
        raise ValueError("limit 取值 1~50")
    data = await qq_openapi_request(
        bot, "GET", "/v2/panels",
        params={"scope": scope, "cursor": cursor, "limit": limit},
    )
    return {
        "records": (data or {}).get("records") or [],
        "next_cursor": str((data or {}).get("next_cursor") or ""),
        "is_end": bool((data or {}).get("is_end", True)),
    }


async def list_all_panels(bot: Bot, scope: str) -> List[Dict[str, Any]]:
    """拉取指定场景下的全部面板（自动翻页），便于按 remark 幂等查找。"""
    records: List[Dict[str, Any]] = []
    cursor = ""
    while True:
        page = await list_panels(bot, scope, cursor=cursor, limit=50)
        records.extend(page["records"])
        if page["is_end"] or not page["next_cursor"]:
            return records
        cursor = page["next_cursor"]


async def get_panel(bot: Bot, panel_id: str) -> Dict[str, Any]:
    """查询面板详情（PanelRecord：panel_id / scope / target_type / panel / ...）。"""
    return await qq_openapi_request(bot, "GET", f"/v2/panels/{panel_id}") or {}


async def _wait_panel_visible(bot: Bot, panel_id: str, timeout: float = 10.0) -> bool:
    """面板创建后异步生效：立即 get/update/delete 可能报 30006（面板不存在）。

    轮询详情接口直到可见（实测约 5 秒内生效）。超时返回 False，
    面板之后仍会可见，不影响后续调用。
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            await get_panel(bot, panel_id)
            return True
        except Exception:  # noqa: BLE001（30006 未生效 / 网络抖动，重试）
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(1.0)


async def update_panel(
    bot: Bot,
    panel_id: str,
    *,
    items: List[Dict[str, Any]],
    remark: str = "",
) -> int:
    """修改面板内容（覆盖 items 与 remark，不影响已关联的用户/群），返回新版本号。"""
    _check_items(items)
    panel: Dict[str, Any] = {"items": items}
    if remark:
        if len(remark) > 255:
            raise ValueError("remark 最多 255 字符")
        panel["remark"] = remark
    data = await qq_openapi_request(
        bot, "PUT", f"/v2/panels/{panel_id}", json_body={"panel": panel}
    )
    version = int((data or {}).get("version") or 0)
    logger.info(f"[panels] 修改面板成功：id={panel_id} version={version} items={len(items)}")
    return version


async def update_panel_target(
    bot: Bot,
    panel_id: str,
    op: str,
    *,
    user_openids: Optional[List[str]] = None,
    group_openids: Optional[List[str]] = None,
) -> None:
    """增删面板关联的用户/群（仅 c2c/group 且 target_type=specific 的面板支持）。

    op："add" 添加 / "del" 移除；一次最多 20 个 openid。60 QPM。
    """
    if op not in ("add", "del"):
        raise ValueError(f'op 仅支持 "add" / "del"，当前：{op!r}')
    body: Dict[str, Any] = {"op": op}
    if user_openids:
        if len(user_openids) > _TARGET_OPENID_LIMIT:
            raise ValueError(f"user_openids 一次最多 {_TARGET_OPENID_LIMIT} 个")
        body["user_openids"] = user_openids
    if group_openids:
        if len(group_openids) > _TARGET_OPENID_LIMIT:
            raise ValueError(f"group_openids 一次最多 {_TARGET_OPENID_LIMIT} 个")
        body["group_openids"] = group_openids
    if not user_openids and not group_openids:
        raise ValueError("user_openids / group_openids 至少传一个")
    await qq_openapi_request(bot, "PUT", f"/v2/panels/{panel_id}/target", json_body=body)


async def delete_panel(bot: Bot, panel_id: str) -> None:
    """删除面板（删除后不再对任何用户/群生效）。"""
    await qq_openapi_request(bot, "DELETE", f"/v2/panels/{panel_id}")
    logger.info(f"[panels] 删除面板成功：id={panel_id}")


async def ensure_group_panel(
    bot: Bot,
    *,
    remark: str,
    items: List[Dict[str, Any]],
    group_openids: List[str],
) -> Optional[str]:
    """幂等维护一个群聊指定面板：按 remark 查找，存在则覆盖内容，不存在则创建。

    返回 panel_id；group_openids 为空时不做任何事返回 None。
    适合启动时调用（面板限频 10 QPM，注意调用频率）。
    """
    if not group_openids:
        return None
    _check_items(items)
    for record in await list_all_panels(bot, "group"):
        if (record.get("panel") or {}).get("remark") == remark:
            panel_id = str(record.get("panel_id") or "")
            await update_panel(bot, panel_id, items=items, remark=remark)
            # 关联差异补齐（add 幂等：已关联的重复 add 官方会忽略/报已存在，这里 best-effort）
            try:
                await update_panel_target(bot, panel_id, "add", group_openids=group_openids)
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"[panels] 补齐面板关联失败（忽略）：id={panel_id} err={exc}")
            return panel_id

    # 优先 specific（仅指定群）；平台当前对该场景可能返回 30001/30016，
    # 此时降级为 all（对 bot 所在所有群生效——单群部署下效果等同）。
    try:
        return await create_panel(
            bot, scope="group", items=items, remark=remark,
            target_type="specific", group_openids=group_openids,
        )
    except RuntimeError as exc:
        msg = str(exc)
        if "30001" not in msg and "30016" not in msg:
            raise
        logger.warning(f"[panels] specific 模式创建失败（{msg}），降级为 all 全局面板")
        return await create_panel(
            bot, scope="group", items=items, remark=remark, target_type="all",
        )


__all__ = [
    "command_item",
    "link_item",
    "create_panel",
    "list_panels",
    "list_all_panels",
    "get_panel",
    "update_panel",
    "update_panel_target",
    "delete_panel",
    "ensure_group_panel",
]
