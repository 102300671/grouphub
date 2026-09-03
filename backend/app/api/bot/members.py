"""/bot/members/* —— nonebot2 插件 #1 group_member_sync 的内部 API。

所有接口都通过 X-Bot-Token 鉴权，不对外开放。
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.db import get_db
from app.models import utcnow
from app.security import BotAuthenticated, get_authenticated_bot

router = APIRouter(dependencies=[Depends(get_authenticated_bot)])


def _upsert_one_core(db: Session, group_id: str, qq: str, nickname_in_group: Optional[str]) -> models.GroupMember:
    """幂等 upsert 单条群成员：is_active=true，更新 last_synced_at / 群名片。"""
    member = (
        db.query(models.GroupMember)
        .filter(models.GroupMember.group_id == group_id, models.GroupMember.qq == qq)
        .first()
    )
    if member is None:
        member = models.GroupMember(
            group_id=group_id,
            qq=qq,
            nickname_in_group=nickname_in_group,
            is_active=True,
            first_seen_at=utcnow(),
            last_synced_at=utcnow(),
        )
        db.add(member)
    else:
        member.is_active = True
        member.last_synced_at = utcnow()
        if nickname_in_group is not None:
            member.nickname_in_group = nickname_in_group
    db.flush()
    return member


@router.post("/upsert_one", response_model=schemas.SimpleMessageOut)
def upsert_one(payload: schemas.GroupMemberIn, db: Session = Depends(get_db), _: BotAuthenticated = Depends()):
    """单条插入或更新（进群 / 群名片变更）。"""
    _upsert_one_core(db, payload.group_id, payload.qq, payload.nickname_in_group)
    db.commit()
    return schemas.SimpleMessageOut(message="ok", details={"qq": payload.qq, "group_id": payload.group_id})


@router.post("/batch_upsert", response_model=schemas.SimpleMessageOut)
def batch_upsert(payload: schemas.GroupMemberBatchIn, db: Session = Depends(get_db), _: BotAuthenticated = Depends()):
    """批量同步（全量拉取后用）。

    - 每个 members 条目 upsert 为 is_active=true
    - 若 mark_inactive_others=true，则该 group_id 下不在 payload.members 中的旧记录置 is_active=false
    """
    now = utcnow()
    qq_list: list[str] = []
    for m in payload.members:
        # group_id 统一以外层 payload.group_id 为准，避免字段冗余
        _upsert_one_core(db, payload.group_id, m.qq, m.nickname_in_group)
        qq_list.append(m.qq)

    touched = 0
    if payload.mark_inactive_others:
        inactive_rows = (
            db.query(models.GroupMember)
            .filter(
                models.GroupMember.group_id == payload.group_id,
                models.GroupMember.is_active.is_(True),
                models.GroupMember.qq.notin_(qq_list) if qq_list else models.GroupMember.qq != "__NO_MEMBER__",
            )
            .all()
        )
        for row in inactive_rows:
            row.is_active = False
            row.last_synced_at = now
            touched += 1

    db.commit()
    return schemas.SimpleMessageOut(
        message="ok",
        details={
            "group_id": payload.group_id,
            "upserted_count": len(payload.members),
            "marked_inactive_count": touched,
        },
    )


@router.post("/set_inactive", response_model=schemas.SimpleMessageOut)
def set_inactive(payload: schemas.GroupMemberSetInactiveIn, db: Session = Depends(get_db), _: BotAuthenticated = Depends()):
    """退群 / 被踢时把该成员标记为 is_active=false。"""
    member = (
        db.query(models.GroupMember)
        .filter(models.GroupMember.group_id == payload.group_id, models.GroupMember.qq == payload.qq)
        .first()
    )
    if member is None:
        # 不存在也视为成功（幂等）
        return schemas.SimpleMessageOut(message="ok (no-op: row not exists)", details={"qq": payload.qq, "group_id": payload.group_id})
    member.is_active = False
    member.last_synced_at = utcnow()
    db.commit()
    return schemas.SimpleMessageOut(message="ok", details={"qq": payload.qq, "group_id": payload.group_id})


@router.get("/status", response_model=schemas.GroupMemberStatusOut)
def status(
    qq: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
    _: BotAuthenticated = Depends(),
):
    """查询某 QQ 当前是否在任一绑定群内（调试/插件自检用）。"""
    rows = db.query(models.GroupMember).filter(models.GroupMember.qq == qq).all()
    if not rows:
        return schemas.GroupMemberStatusOut(qq=qq, in_group=False, groups=[], last_synced_at=None)
    in_group = any(r.is_active for r in rows)
    last = max((r.last_synced_at for r in rows if r.last_synced_at), default=None)
    groups = [
        schemas.GroupMemberStatusGroupItem(group_id=r.group_id, is_active=r.is_active) for r in rows
    ]
    return schemas.GroupMemberStatusOut(qq=qq, in_group=in_group, groups=groups, last_synced_at=last)


# ------------------- 头像同步 -------------------

def _sync_one_avatar(db: Session, qq: str, remote_url: str) -> dict:
    """下载 QQ 头像 → 上传 zfile /avatars/{qq}.jpg → 写 group_members / users。

    返回 {"qq", "ok", "avatar_url", "error"}。下载/上传失败不抛异常，记 error 后由上层汇总。
    """
    from app.zfile_client import get_zfile

    try:
        import httpx
        resp = httpx.get(
            remote_url,
            timeout=15.0,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        resp.raise_for_status()
        content = resp.content
        if not content:
            return {"qq": qq, "ok": False, "avatar_url": None, "error": "empty response"}
    except Exception as exc:  # noqa: BLE001 网络异常统一降级
        return {"qq": qq, "ok": False, "avatar_url": None, "error": f"download failed: {exc}"}

    ext = (remote_url.split("?")[0].rsplit(".", 1)[-1] or "jpg").lower()
    if ext not in ("jpg", "jpeg", "png", "gif", "webp"):
        ext = "jpg"
    try:
        zfile_url = get_zfile().upload_file(f"{qq}.{ext}", content, path="/avatars")
    except Exception as exc:  # noqa: BLE001
        return {"qq": qq, "ok": False, "avatar_url": None, "error": f"zfile upload failed: {exc}"}

    now = utcnow()
    member_rows = db.query(models.GroupMember).filter(models.GroupMember.qq == qq).all()
    for row in member_rows:
        row.avatar_url = zfile_url
        row.last_synced_at = now
    user = db.query(models.User).filter(models.User.qq == qq).first()
    if user is not None and not user.avatar_url:
        user.avatar_url = zfile_url
    return {"qq": qq, "ok": True, "avatar_url": zfile_url, "error": None}


@router.post("/sync_avatar", response_model=schemas.SimpleMessageOut)
def sync_avatar(payload: schemas.AvatarSyncIn, db: Session = Depends(get_db), _: BotAuthenticated = Depends()):
    """单条头像同步：下载 QQ 头像转存 zfile，并回填 group_members / users。"""
    result = _sync_one_avatar(db, payload.qq, payload.avatar_url)
    db.commit()
    return schemas.SimpleMessageOut(ok=result["ok"], message="ok" if result["ok"] else result["error"], details=result)


@router.post("/sync_avatar/batch", response_model=schemas.SimpleMessageOut)
def sync_avatar_batch(payload: schemas.AvatarSyncBatchIn, db: Session = Depends(get_db), _: BotAuthenticated = Depends()):
    """批量头像同步（全量群成员拉头像后一次性推送）。单条失败不影响其余。"""
    results = [_sync_one_avatar(db, item.qq, item.avatar_url) for item in payload.items]
    db.commit()
    ok_count = sum(1 for r in results if r["ok"])
    return schemas.SimpleMessageOut(
        ok=ok_count > 0,
        message=f"{ok_count}/{len(results)} avatars synced",
        details={"ok": ok_count, "failed": len(results) - ok_count, "results": results},
    )
