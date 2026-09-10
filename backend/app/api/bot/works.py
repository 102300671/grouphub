"""/bot/works/* —— nonebot2 插件 #3 works_recommend / #4 works_submit 的内部 API。

  GET  /hot      热门作品（supporter+recommender+书评 数排名）
  GET  /search   关键词搜索（标题/作者）
  POST /submit   群内「安利」：按 QQ 提交作品（无账号自动建用户）

所有接口 X-Bot-Token 鉴权，不对外开放。
"""
from __future__ import annotations

import secrets
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models, schemas
from app.config import get_settings
from app.db import get_db
from app.security import BotAuthenticated, get_authenticated_bot, hash_password, is_qq_in_group

router = APIRouter(dependencies=[Depends(get_authenticated_bot)])


def _compact(w: models.Work, score: Optional[int] = None) -> Dict[str, Any]:
    """给 QQ 消息用的精简作品结构。"""
    out = {
        "id": w.id,
        "title": w.title,
        "author": w.author,
        "type": w.type,
        "uploader_nickname": w.uploader.nickname if w.uploader else None,
    }
    if score is not None:
        out["score"] = score
    return out


def _hot_scores(db: Session, work_ids: List[int]) -> Dict[int, int]:
    """批量计算热度：热度 = 关联关系角色数（supporter/recommender 每条计 1）+ 书评数。

    两条聚合查询代替逐作品 N+1：
    - user_works 按 work_id 一次性拉回，Python 侧累计 relation_roles 长度
    - reviews GROUP BY work_id 计数
    """
    scores: Dict[int, int] = {wid: 0 for wid in work_ids}
    if not work_ids:
        return scores

    rels = (
        db.query(models.UserWork.work_id, models.UserWork.relation_roles)
        .filter(models.UserWork.work_id.in_(work_ids))
        .all()
    )
    for work_id, roles in rels:
        scores[work_id] += len(roles or [])

    review_counts = (
        db.query(models.Review.work_id, func.count(models.Review.id))
        .filter(models.Review.work_id.in_(work_ids))
        .group_by(models.Review.work_id)
        .all()
    )
    for work_id, cnt in review_counts:
        scores[work_id] += int(cnt or 0)

    return scores


@router.get("/hot")
def hot_works(
    limit: int = Query(5, ge=1, le=20),
    db: Session = Depends(get_db),
    _: BotAuthenticated = Depends(),
):
    """热门作品 Top N（聚合查询，无 N+1）。"""
    works = (
        db.query(models.Work)
        .filter(models.Work.status == models.WorkStatus.PUBLISHED)
        .all()
    )
    scores = _hot_scores(db, [w.id for w in works])
    scored = sorted(works, key=lambda w: scores.get(w.id, 0), reverse=True)
    return {"ok": True, "items": [_compact(w, scores.get(w.id, 0)) for w in scored[:limit]]}


@router.get("/search")
def search_works(
    keyword: str = Query(..., min_length=1),
    limit: int = Query(10, ge=1, le=50),
    type: Optional[str] = Query(None, description="按作品类型过滤：novel/anime/movie/gallery/fanwork/other"),
    db: Session = Depends(get_db),
    _: BotAuthenticated = Depends(),
):
    """关键词搜索（标题 / 作者，MVP 用 LIKE；后续接全文检索）。

    type 可选：群机器人 /搜索 --type 传入，按 WorkType 精确过滤；非法值忽略（不过滤）。
    """
    like = f"%{keyword}%"
    query = (
        db.query(models.Work)
        .filter(models.Work.status == models.WorkStatus.PUBLISHED)
        .filter(models.Work.title.like(like) | models.Work.author.like(like))
    )
    if type:
        valid_types = {
            models.WorkType.NOVEL, models.WorkType.ANIME, models.WorkType.MOVIE,
            models.WorkType.GALLERY, models.WorkType.FANWORK, models.WorkType.OTHER,
        }
        if type in valid_types:
            query = query.filter(models.Work.type == type)
    works = query.order_by(models.Work.updated_at.desc()).limit(limit).all()
    return {"ok": True, "items": [_compact(w) for w in works]}


def _get_or_create_user(db: Session, qq: str, settings=None) -> tuple[models.User, bool]:
    """按 QQ 拿用户；不存在则自动建（同 confirm-code 逻辑：随机密码 + 群名片昵称）。

    新用户角色按 ADMIN_QQS 判定（与密码注册/验证码注册一致，避免建号即低权再等提权）。
    返回 (user, created)。QQ 不在任何绑定群 → 抛 ValueError。
    """
    user = db.query(models.User).filter(models.User.qq == qq).first()
    if user is not None:
        return user, False

    if not is_qq_in_group(qq, db):
        raise ValueError("该 QQ 不在本群，无法提交作品")

    row = (
        db.query(models.GroupMember)
        .filter(models.GroupMember.qq == qq, models.GroupMember.is_active.is_(True))
        .first()
    )
    nickname = (row.nickname_in_group if row and row.nickname_in_group else None) or f"群友{qq}"
    is_admin = bool(settings and qq in settings.admin_qq_set)
    user = models.User(
        qq=qq,
        password_hash=hash_password(secrets.token_hex(16)),
        nickname=nickname,
        role=models.UserRole.ADMIN if is_admin else models.UserRole.MEMBER,
    )
    db.add(user)
    db.flush()
    return user, True


@router.post("/submit")
def submit_work(
    payload: schemas.BotWorkSubmitIn,
    db: Session = Depends(get_db),
    settings=Depends(get_settings),
    _: BotAuthenticated = Depends(),
):
    """群内「安利」提交作品。

    - QQ 无账号 → 自动建用户（随机密码，昵称取群名片）
    - 同名作品已存在 → 不新建，返回 duplicate 提示（插件提示群友去站点标记支持者）
    - 提交者自动挂 recommender 关系
    """
    dup = db.query(models.Work).filter(models.Work.title == payload.title).first()
    if dup is not None:
        return {
            "ok": False,
            "duplicate": True,
            "work": _compact(dup),
            "message": f"已存在同名作品（ID={dup.id}），请群友到站点标记支持",
        }

    try:
        user, created = _get_or_create_user(db, payload.qq, settings)
    except ValueError as exc:
        return {"ok": False, "duplicate": False, "message": str(exc)}

    w = models.Work(
        title=payload.title,
        author=payload.author,
        type=payload.type or models.WorkType.OTHER,
        source_work_id=payload.source_work_id,
        summary=payload.summary,
        uploader_id=user.id,
        tags_json=payload.tags or [],
        status=models.WorkStatus.PUBLISHED,
    )
    db.add(w)
    db.flush()

    for link in payload.links or []:
        url = link.get("url")
        if url:
            db.add(models.WorkLink(work_id=w.id, site_name=link.get("site_name"), url=url))

    # 提交者即首 recommender
    db.add(
        models.UserWork(user_id=user.id, work_id=w.id, relation_roles=["recommender"], reading_status=None)
    )

    db.commit()
    db.refresh(w)
    return {
        "ok": True,
        "duplicate": False,
        "created_user": created,
        "work": _compact(w),
        "message": f"已入库：{w.title}（ID={w.id}）",
    }
