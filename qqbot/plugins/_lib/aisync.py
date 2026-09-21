"""机器人 ↔ 后端 AI 联动客户端。

封装 /bot/ai/* 内部接口，承载「前端与机器人 AI 配置互通、群会话同步到前端」：
  - push_defaults()：把机器人 .env.prod 解析出的内置配置（可多套）批量同步给后端；
  - get_active_config(qq)：取该用户当前生效配置（前端自建的也在这里生效）；
  - list_configs(qq) / activate(qq, config_id)：群内查看/切换配置；
  - group_conversation()：获取/创建群会话并拿回历史消息；
  - append_messages() / reset_conversation()：持久化对话、重置归档。

网络失败时异常向上抛，由调用方决定降级策略（不阻断问答本身）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from nonebot import logger

from .client import backend_client


async def push_defaults(configs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """批量同步内置配置（.env.prod 主默认 + AI_BUILTIN_CONFIGS 多套）；返回后端 details。"""
    resp = await backend_client.post("/bot/ai/default", json={"configs": configs})
    resp.raise_for_status()
    data = resp.json()
    logger.info(f"[aisync] 内置配置已同步到后端：{data.get('details')}")
    return data


async def get_active_config(qq: str) -> Dict[str, Any]:
    """用户生效配置：{config_id, kind, api_base, api_key, model, system_prompt, searxng_url}。"""
    resp = await backend_client.get("/bot/ai/active", params={"qq": qq})
    resp.raise_for_status()
    return resp.json()


async def list_configs(qq: str) -> Dict[str, Any]:
    """配置列表：{ok, items:[{id,name,kind,model,is_builtin,is_active,...}], active_id}。"""
    resp = await backend_client.get("/bot/ai/configs", params={"qq": qq})
    resp.raise_for_status()
    return resp.json()


async def activate(qq: str, config_id: int) -> None:
    """切换配置；config_id=0 切回内置默认。"""
    resp = await backend_client.post(
        "/bot/ai/activate", json={"qq": qq, "config_id": int(config_id)}
    )
    resp.raise_for_status()


async def group_conversation(
    qq: str,
    group_id: str,
    title: Optional[str] = None,
    openid: Optional[str] = None,
    folder_name: Optional[str] = None,
    force_new: bool = False,
) -> Dict[str, Any]:
    """获取/创建 openid 组会话：details = {conversation_id, title, messages}。

    openid 缺失（OneBot 通道）时后端按 gb:<group_id> 定位；force_new 归档当前默认并新建。
    """
    resp = await backend_client.post(
        "/bot/ai/conversation",
        json={
            "qq": qq,
            "group_id": group_id,
            "title": title,
            "openid": openid,
            "folder_name": folder_name,
            "force_new": force_new,
        },
    )
    resp.raise_for_status()
    return resp.json().get("details") or {}


async def list_conversations(
    qq: str, openid: Optional[str] = None, scope: str = "current"
) -> Dict[str, Any]:
    """列会话：scope=current|all|web；details={items, current_id}。"""
    params: Dict[str, Any] = {"qq": qq, "scope": scope}
    if openid:
        params["openid"] = openid
    resp = await backend_client.get("/bot/ai/conversations", params=params)
    resp.raise_for_status()
    return resp.json().get("details") or {}


async def switch_conversation(qq: str, conversation_id: int) -> None:
    """切换当前会话（设为组内默认）。"""
    resp = await backend_client.post(
        "/bot/ai/conversation/switch",
        json={"qq": qq, "conversation_id": int(conversation_id)},
    )
    resp.raise_for_status()


async def move_conversation(
    qq: str,
    conversation_id: int,
    openid: Optional[str] = None,
    folder_name: Optional[str] = None,
) -> None:
    """把其它范围的会话移到当前 openid 组并设为默认（继续会话）。"""
    body: Dict[str, Any] = {"qq": qq, "conversation_id": int(conversation_id)}
    if openid:
        body["openid"] = openid
    if folder_name:
        body["folder_name"] = folder_name
    resp = await backend_client.post("/bot/ai/conversation/move", json=body)
    resp.raise_for_status()


async def append_messages(
    conversation_id: int, qq: str, messages: List[Dict[str, str]]
) -> None:
    """批量追加消息（一轮问答 = user + assistant 两条）。"""
    resp = await backend_client.post(
        f"/bot/ai/conversation/{int(conversation_id)}/messages",
        json={"qq": qq, "messages": messages},
    )
    resp.raise_for_status()


async def delete_conversation(qq: str, conversation_id: int) -> None:
    """删除会话（消息级联删除）。"""
    resp = await backend_client.post(
        "/bot/ai/conversation/delete",
        json={"qq": qq, "conversation_id": int(conversation_id)},
    )
    resp.raise_for_status()


async def reset_conversation(qq: str, group_id: str) -> bool:
    """归档当前群会话；返回是否真的有会话被归档。"""
    resp = await backend_client.post(
        "/bot/ai/conversation/reset",
        json={"qq": qq, "group_id": group_id},
    )
    resp.raise_for_status()
    return resp.json().get("message") == "ok"


__all__ = [
    "push_defaults",
    "get_active_config",
    "list_configs",
    "activate",
    "group_conversation",
    "list_conversations",
    "switch_conversation",
    "move_conversation",
    "append_messages",
    "reset_conversation",
    "delete_conversation",
]
