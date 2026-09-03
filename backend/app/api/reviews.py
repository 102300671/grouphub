"""评论 API：作品详情页发评论（文本 + 可选多媒体附件）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import Review, User
from app.security import get_current_user
from app.zfile_client import (
    ATTACHMENT_EXTS,
    UploadGuardError,
    ZFileError,
    ZFilePath,
    get_zfile,
    validate_upload,
)

router = APIRouter(tags=["评论"])


class ReviewIn(BaseModel):
    rating: Optional[int] = Field(None, ge=1, le=5, description="评分 1~5（可选）")
    title: Optional[str] = Field(None, max_length=255)
    content: str = Field(..., min_length=1)
    attachments: List[Dict[str, Any]] = Field(default_factory=list)


class ReviewOut(BaseModel):
    id: int
    work_id: int
    user_id: int
    nickname: Optional[str]
    qq: Optional[str]
    rating: Optional[int]
    title: Optional[str]
    content: str
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: str


def _review_out(r: Review) -> Dict[str, Any]:
    return {
        "id": r.id,
        "work_id": r.work_id,
        "user_id": r.user_id,
        "nickname": r.user.nickname if r.user else None,
        "qq": r.user.qq if r.user else None,
        "avatar_url": r.user.avatar_url if r.user else None,
        "rating": r.rating,
        "title": r.title,
        "content": r.content,
        "attachments": r.attachments_json or [],
        "created_at": r.created_at.isoformat() if r.created_at else "",
    }


@router.get("/{work_id}/reviews")
def list_reviews(
    work_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """作品评论列表。"""
    from app.models import Work
    w = db.query(Work).filter(Work.id == work_id).first()
    if w is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="作品不存在")

    q = db.query(Review).filter(Review.work_id == work_id)
    total = q.count()
    items = q.order_by(Review.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_review_out(r) for r in items],
    }


@router.post("/{work_id}/reviews")
def create_review(
    work_id: int,
    payload: ReviewIn,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    """发表评论。"""
    from app.models import Work
    w = db.query(Work).filter(Work.id == work_id).first()
    if w is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="作品不存在")

    r = Review(
        work_id=work_id,
        user_id=me.id,
        rating=payload.rating,
        title=payload.title,
        content=payload.content,
        attachments_json=payload.attachments or [],
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return _review_out(r)


@router.post("/{work_id}/reviews/attachments")
def upload_review_attachment(
    work_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    me: User = Depends(get_current_user),
):
    """上传评论附件（同步端点，线程池执行；返回 attachment 对象，前端提交评论时带上）。"""
    file_name = file.filename or "unnamed"
    file_content = file.file.read()
    try:
        validate_upload(file_name, file_content, ATTACHMENT_EXTS, settings.max_upload_bytes)
    except UploadGuardError as e:
        raise HTTPException(status_code=e.status, detail=e.msg)

    zpath = ZFilePath.general_dir("review", me.qq)
    try:
        zf = get_zfile()
        url = zf.upload_file(file_name, file_content, path=zpath)
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
            "url": url,
            "file_name": file_name,
            "mime_type": mime,
            "size_bytes": len(file_content),
        },
    }
