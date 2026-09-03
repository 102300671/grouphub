"""/admin/* —— 管理端接口（require_admin）。

前端：当 `/auth/me.role == 'admin'` 时，显示「切换到管理界面」按钮，点击后加载本路由接口即可；
  - 管理员也是普通用户，普通用户侧的内容（作品库/详情/上传/关系）在管理模式下照常显示，只是多了管理工具条。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import models
from app.config import Settings, get_settings
from app.db import get_db
from app.security import require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


# ---------- 输入输出小模型 ----------

class PatchRoleIn(BaseModel):
    role: str = Field(pattern=r"^(admin|member)$")


class PatchThresholdIn(BaseModel):
    threshold: int = Field(ge=0, le=1000)


class UserBriefOut(BaseModel):
    id: int
    qq: str
    nickname: Optional[str]
    role: str
    created_at: datetime


def _user_brief(u: models.User) -> Dict[str, Any]:
    return {
        "id": u.id,
        "qq": u.qq,
        "nickname": u.nickname,
        "role": u.role,
        "created_at": u.created_at,
    }


# ---------- 路由 ----------

@router.get("/me")
def admin_me(me: models.User = Depends(require_admin)) -> Dict[str, Any]:
    """给前端「切换到管理界面」按钮用——直接 /auth/me 已经带 role，但再加一个校验点。"""
    return _user_brief(me)


@router.get("/summary")
def summary(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    me: models.User = Depends(require_admin),
) -> Dict[str, Any]:
    """管理后台首页数字概览 + 展示配置。"""
    counts = {
        "users": db.query(models.User).count(),
        "works": db.query(models.Work).count(),
        "reviews": db.query(models.Review).count(),
        "topics": db.query(models.Topic).count(),
        "fanworks": db.query(models.Fanwork).count(),
        "group_members_active": (
            db.query(models.GroupMember).filter(models.GroupMember.is_active.is_(True)).count()
        ),
    }
    return {
        "counts": counts,
        "show_relation_threshold": settings.show_relation_threshold,
        "admin_count_in_env": len(settings.admin_qq_set),
        "current_admin_qqs": sorted(settings.admin_qq_set),
    }


# ---------- 手动触发全量群成员同步 ----------

@router.post("/trigger_member_sync")
async def trigger_member_sync(
    payload: Optional[Dict[str, Any]] = None,
    me: models.User = Depends(require_admin),
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    """管理后台「手动触发全量重同步」按钮（PRD §6.2 + F1 辅助按钮）。

    通过反向调 qqbot 的 /bot/admin/trigger_sync（X-Bot-Token 鉴权）触发 nonebot 插件执行 full_sync。
    若 qqbot 不可达（本地没起 nonebot2），降级提示管理员可到 QQ 群内 @机器人 发 `/sync_member all`。
    """
    import httpx

    group_ids = (payload or {}).get("group_ids") if isinstance(payload, dict) else None
    try:
        resp = httpx.post(
            f"{settings.qqbot_api_base.rstrip('/')}/bot/admin/trigger_sync",
            json={"group_ids": group_ids} if group_ids else {},
            headers={"X-Bot-Token": settings.bot_api_token},
            timeout=4.0,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("ok") is False:
            return {"ok": False, "message": f"qqbot 拒绝执行：{data.get('message')}"}
        return {
            "ok": True,
            "message": "已通知 qqbot 触发全量同步（异步执行，结果见 qqbot 日志）",
            "groups": data.get("groups"),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "message": (
                f"无法联系 qqbot（{exc}）。请确认 qqbot 已启动（端口 8083，DRIVER 含 ~fastapi），"
                "或到群里 @机器人 发「/sync_member all」手动触发。"
            ),
        }


# ---------- 用户管理 ----------

@router.get("/users", response_model=List[UserBriefOut])
def list_users(
    q: Optional[str] = None,
    role: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: models.User = Depends(require_admin),
) -> List[Dict[str, Any]]:
    """管理后台：用户列表。"""
    query = db.query(models.User)
    if q:
        like = f"%{q}%"
        query = query.filter((models.User.nickname.like(like)) | (models.User.qq.like(like)))
    if role in ("admin", "member"):
        query = query.filter(models.User.role == role)
    rows = query.order_by(models.User.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return [_user_brief(u) for u in rows]


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    me: models.User = Depends(require_admin),
) -> Dict[str, Any]:
    """删除账号（级联清理本地数据：关系/书评/帖子/作品/同人等）。

    安全约束：不能删自己；不能删除 ADMIN_QQS 中列出的（env 来源管理员，避免前端误操作）。
    """
    target = db.query(models.User).filter(models.User.id == user_id).first()
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    if target.id == me.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "不能删除你自己")
    from app.config import get_settings
    settings = get_settings()
    if target.qq in settings.admin_qq_set:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "该用户在 ADMIN_QQS 中受保护；需先从 .env 移除再重启，或改为直接禁用（未实现）",
        )

    db.delete(target)
    db.commit()
    return {"ok": True, "message": f"用户 id={user_id} 已删除"}


@router.patch("/users/{user_id}/role")
def change_role(
    user_id: int,
    payload: PatchRoleIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    me: models.User = Depends(require_admin),
) -> Dict[str, Any]:
    """改角色：member ↔ admin。

    强约束（经验 1425369）：不能对自己执行降权；不能把最后一个 admin 降为 member。
    """
    target = db.query(models.User).filter(models.User.id == user_id).first()
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")

    # 1. 不能动自己的权限（避免前端绕过/误操作把自己锁在门外）
    if target.id == me.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "出于安全考虑，不能修改你自己的角色；请使用另一个管理员账号操作，或直接改 ADMIN_QQS 环境变量。",
        )

    # 2. 不能把「仅剩的最后一个 admin」降级
    if target.role == models.UserRole.ADMIN and payload.role == "member":
        admins_remaining = (
            db.query(models.User)
            .filter(
                models.User.role == models.UserRole.ADMIN,
                models.User.id != target.id,
            )
            .count()
        )
        # 环境变量里还有其他 admin QQ 白名单 → 就算 DB 里只剩这一个 admin，也能被自动提权救回来，所以不卡
        if admins_remaining == 0 and len(settings.admin_qq_set) == 0:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "这是当前数据库中最后一个 admin，且 ADMIN_QQS 为空；降级将无人可管理，请先添加其他管理员。",
            )

    target.role = payload.role
    db.commit()
    db.refresh(target)
    return {"ok": True, "user": _user_brief(target)}


# ---------- 站点设置 ----------

@router.get("/settings/show_relation_threshold")
def get_relation_threshold(
    settings: Settings = Depends(get_settings),
    _: models.User = Depends(require_admin),
) -> Dict[str, Any]:
    return {"show_relation_threshold": settings.show_relation_threshold}


@router.patch("/settings/show_relation_threshold")
def set_relation_threshold(
    payload: PatchThresholdIn,
    settings: Settings = Depends(get_settings),
    me: models.User = Depends(require_admin),
) -> Dict[str, Any]:
    """运行时修改「人数展示阈值」。

    说明：因为 pydantic-settings 默认只读一次 .env，这里直接修改 settings 对象（内存中生效，重启失效）；
    如需持久化，建议后续引入 admin_settings 表。当前 MVP 阶段允许临时调优，同时推荐改 .env 永久生效。
    """
    settings.show_relation_threshold = payload.threshold  # type: ignore[attr-defined]
    return {
        "ok": True,
        "show_relation_threshold": settings.show_relation_threshold,
        "note": "本次修改仅对当前进程生效（重启后丢失）；如需永久生效，请修改 backend/.env 中的 SHOW_RELATION_THRESHOLD 并重启后端。",
    }
