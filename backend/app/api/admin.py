"""/admin/* —— 管理端接口（require_admin）。

前端：当 `/auth/me.role == 'admin'` 时，显示「切换到管理界面」按钮，点击后加载本路由接口即可；
  - 管理员也是普通用户，普通用户侧的内容（作品库/详情/上传/关系）在管理模式下照常显示，只是多了管理工具条。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import case
from sqlalchemy.orm import Session

from app import models, schemas
from app.config import Settings, get_settings
from app.db import get_db
from app.runtime_settings import get_runtime_values, set_runtime_setting
from app.security import require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


# ---------- 输入输出小模型 ----------

class PatchRoleIn(BaseModel):
    role: str = Field(pattern=r"^(admin|member)$")


class UserBriefOut(BaseModel):
    id: int
    qq: str
    nickname: Optional[str]
    role: str
    is_active: bool
    created_at: datetime


def _user_brief(u: models.User) -> Dict[str, Any]:
    return {
        "id": u.id,
        "qq": u.qq,
        "nickname": u.nickname,
        "role": u.role,
        "is_active": u.is_active,
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
        "users_disabled": db.query(models.User).filter(models.User.is_active.is_(False)).count(),
        "works": db.query(models.Work).count(),
        "works_pending": (
            db.query(models.Work).filter(models.Work.status == models.WorkStatus.PENDING).count()
        ),
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
        "works_require_review": settings.works_require_review,
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
    若 qqbot 不可达（本地没起 nonebot2），降级提示管理员可到 QQ 群内 @机器人 发 `/同步 成员 --all`。
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
                "或到群里 @机器人 发「/同步 成员 --all」手动触发。"
            ),
        }


# ---------- 用户管理 ----------

@router.get("/users", response_model=List[UserBriefOut])
def list_users(
    q: Optional[str] = None,
    role: Optional[str] = None,
    active: Optional[bool] = Query(None, description="按启用状态过滤：true=正常，false=已禁用"),
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
    if active is not None:
        query = query.filter(models.User.is_active.is_(active))
    rows = query.order_by(models.User.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return [_user_brief(u) for u in rows]


@router.patch("/users/{user_id}/active")
def set_user_active(
    user_id: int,
    payload: schemas.AdminPatchActiveIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    me: models.User = Depends(require_admin),
) -> Dict[str, Any]:
    """启用/禁用用户。禁用后该用户无法登录、无法访问站点 API，QQ bot 命令也会被拦。

    安全约束：不能禁用自己；不能禁用 ADMIN_QQS 白名单账号（避免锁死管理入口）。
    """
    target = db.query(models.User).filter(models.User.id == user_id).first()
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    if target.id == me.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "不能禁用你自己的账号")
    if not payload.is_active and target.qq in settings.admin_qq_set:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "该用户在 ADMIN_QQS 中受保护；需先从 .env 移除再重启后端，才能禁用。",
        )
    target.is_active = payload.is_active
    db.commit()
    db.refresh(target)
    return {
        "ok": True,
        "is_active": target.is_active,
        "user": _user_brief(target),
        "message": "账号已启用" if target.is_active else "账号已禁用（将无法登录和使用 QQ 机器人）",
    }


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
            "该用户在 ADMIN_QQS 中受保护；需先从 .env 移除再重启，或改用「禁用」功能阻止其登录。",
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


# ---------- 作品审核 ----------

@router.get("/works")
def admin_list_works(
    status_filter: Optional[str] = Query(None, alias="status", description="published/pending/draft，缺省=全部"),
    q: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _: models.User = Depends(require_admin),
) -> Dict[str, Any]:
    """管理后台：作品列表（含非发布态），供审核台使用。

    复用公开作品的 _work_out 结构；待审核作品排在前面（pending → draft → published，同态按 id 倒序）。
    """
    from app.api.works import _work_out

    query = db.query(models.Work)
    if status_filter in ("published", "pending", "draft"):
        query = query.filter(models.Work.status == status_filter)
    if q:
        query = query.filter(models.Work.title.like(f"%{q}%"))
    total = query.count()
    status_rank = case(
        (models.Work.status == models.WorkStatus.PENDING, 0),
        (models.Work.status == models.WorkStatus.DRAFT, 1),
        else_=2,
    )
    rows = (
        query.order_by(status_rank, models.Work.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    pending_total = (
        db.query(models.Work).filter(models.Work.status == models.WorkStatus.PENDING).count()
    )
    return {
        "items": [_work_out(w) for w in rows],
        "total": total,
        "pending_total": pending_total,
        "works_require_review": settings.works_require_review,
    }


@router.patch("/works/{work_id}/status")
def admin_set_work_status(
    work_id: int,
    payload: schemas.AdminWorkStatusPatchIn,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_admin),
) -> Dict[str, Any]:
    """作品审核：置为 published（通过）/ draft（驳回）/ pending（重新挂起）。"""
    from app.api.works import _work_out

    w = db.query(models.Work).filter(models.Work.id == work_id).first()
    if w is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "作品不存在")
    old_status = w.status
    w.status = payload.status
    db.commit()
    db.refresh(w)
    action_text = {
        models.WorkStatus.PUBLISHED: "已通过，作品已公开",
        models.WorkStatus.DRAFT: "已驳回（转为草稿，不再公开）",
        models.WorkStatus.PENDING: "已重新挂起为待审核",
    }.get(w.status, "状态已更新")
    return {"ok": True, "work": _work_out(w), "previous_status": old_status, "message": action_text}


# ---------- 站点设置（持久化到 admin_settings 表） ----------

@router.get("/settings")
def get_admin_settings(
    settings: Settings = Depends(get_settings),
    _: models.User = Depends(require_admin),
) -> Dict[str, Any]:
    return dict(get_runtime_values())


@router.patch("/settings")
def patch_admin_settings(
    payload: schemas.AdminSettingsPatchIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    me: models.User = Depends(require_admin),
) -> Dict[str, Any]:
    """批量修改运行时设置。

    同时写 admin_settings 表（重启不丢，启动时 load_runtime_settings 回填）和当前进程的 settings 单例。
    """
    changed: Dict[str, Any] = {}
    if payload.show_relation_threshold is not None:
        set_runtime_setting(db, "show_relation_threshold", payload.show_relation_threshold)
        changed["show_relation_threshold"] = settings.show_relation_threshold
    if payload.works_require_review is not None:
        set_runtime_setting(db, "works_require_review", payload.works_require_review)
        changed["works_require_review"] = settings.works_require_review
    return {"ok": True, "settings": dict(get_runtime_values()), "changed": changed}
