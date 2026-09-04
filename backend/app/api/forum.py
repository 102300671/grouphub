"""论坛 API：主题列表 / 详情 / 发帖 / 回帖（文本 + 可选多媒体附件）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import Topic, TopicPost, User
from app.security import get_current_user
from app.zfile_client import (
    ATTACHMENT_EXTS,
    UploadGuardError,
    ZFileError,
    ZFilePath,
    public_url,
    validate_upload,
)
from app.storage import get_storage

router = APIRouter(tags=["论坛"])


def _atts_out(items) -> List[Dict[str, Any]]:
    # 附件 URL 改写为同源路径，前端无需直连 zfile
    return [{**a, "url": public_url(a.get("url"))} for a in (items or [])]


class TopicIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    body: Optional[str] = Field(None, max_length=20000)
    attachments: List[Dict[str, Any]] = Field(default_factory=list)


class PostIn(BaseModel):
    content: str = Field(..., min_length=1)
    attachments: List[Dict[str, Any]] = Field(default_factory=list)


def _topic_out(t: Topic, post_count: int) -> Dict[str, Any]:
    return {
        "id": t.id,
        "creator_id": t.creator_id,
        "title": t.title,
        "cover_url": public_url(t.cover_url),
        "body": t.body,
        "attachments": _atts_out(t.attachments_json),
        "post_count": post_count,
        "created_at": t.created_at.isoformat() if t.created_at else "",
    }


def _topic_post_out(p: TopicPost) -> Dict[str, Any]:
    return {
        "id": p.id,
        "topic_id": p.topic_id,
        "user_id": p.user_id,
        "nickname": p.user.nickname if p.user else None,
        "qq": p.user.qq if p.user else None,
        "avatar_url": public_url(p.user.avatar_url) if p.user else None,
        "content": p.content,
        "attachments": _atts_out(p.attachments_json),
        "created_at": p.created_at.isoformat() if p.created_at else "",
    }


@router.get("/")
def list_topics(
    keyword: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """主题列表（含回帖数、创建者）。"""
    from sqlalchemy import func

    q = db.query(Topic, func.count(TopicPost.id)).outerjoin(TopicPost, TopicPost.topic_id == Topic.id)
    if keyword:
        q = q.filter(Topic.title.ilike(f"%{keyword}%"))
    q = q.group_by(Topic.id).order_by(Topic.created_at.desc())
    total = q.count()
    items = q.offset((page - 1) * page_size).limit(page_size).all()
    user_ids = {t.creator_id for t, _ in items}
    users = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                **_topic_out(t, cnt),
                "creator": {
                    "id": u.id,
                    "nickname": u.nickname,
                    "qq": u.qq,
                    "avatar_url": public_url(u.avatar_url),
                }
                if (u := users.get(t.creator_id))
                else None,
            }
            for t, cnt in items
        ],
    }


@router.post("/")
def create_topic(
    payload: TopicIn,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """发布新主题。"""
    t = Topic(
        creator_id=me.id,
        title=payload.title.strip(),
        body=payload.body,
        attachments_json=payload.attachments or [],
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return _topic_out(t, 0)


@router.get("/{topic_id}")
def get_topic(
    topic_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """主题详情（含回帖列表）。"""
    t = db.query(Topic).filter(Topic.id == topic_id).first()
    if t is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="主题不存在")

    pq = db.query(TopicPost).filter(TopicPost.topic_id == topic_id)
    post_total = pq.count()
    posts = pq.order_by(TopicPost.created_at.asc()).offset((page - 1) * page_size).limit(page_size).all()

    creator = db.query(User).filter(User.id == t.creator_id).first()

    out = {
        **_topic_out(t, post_total),
        "creator": {
            "id": creator.id,
            "nickname": creator.nickname,
            "qq": creator.qq,
            "avatar_url": public_url(creator.avatar_url),
        }
        if creator
        else None,
    }
    return {
        "topic": out,
        "posts": {
            "total": post_total,
            "page": page,
            "page_size": page_size,
            "items": [_topic_post_out(p) for p in posts],
        },
    }


@router.post("/{topic_id}/posts")
def create_post(
    topic_id: int,
    payload: PostIn,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """回帖（文本 + 可选附件）。"""
    t = db.query(Topic).filter(Topic.id == topic_id).first()
    if t is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="主题不存在")

    p = TopicPost(
        topic_id=topic_id,
        user_id=me.id,
        content=payload.content,
        attachments_json=payload.attachments or [],
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return _topic_post_out(p)


@router.post("/attachments")
def upload_topic_attachment(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    me: User = Depends(get_current_user),
):
    """上传论坛附件（同步端点，线程池执行；返回 attachment 对象，发帖/回帖时带上）。"""
    file_name = file.filename or "unnamed"
    file_content = file.file.read()
    try:
        validate_upload(file_name, file_content, ATTACHMENT_EXTS, settings.max_upload_bytes)
    except UploadGuardError as e:
        raise HTTPException(status_code=e.status, detail=e.msg)

    zpath = ZFilePath.general_dir("forum", me.qq)
    try:
        storage = get_storage()
        url = storage.upload_file(file_name, file_content, path=zpath)
    except ZFileError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"zfile 上传失败: {e.msg}")
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"zfile 连接失败: {e}")

    mime = file.content_type or ""
    ftype = "image" if mime.startswith("image/") else "video" if mime.startswith("video/") else "file"
    return {
        "ok": True,
        "attachment": {
            "type": ftype,
            "url": public_url(url),
            "file_name": file_name,
            "mime_type": mime,
            "size_bytes": len(file_content),
        },
    }
