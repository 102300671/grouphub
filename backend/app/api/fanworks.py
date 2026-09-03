"""F8 同人创作 API（见 docs/PRD §3.5）。

- GET    /fanworks/        列表（公开仅 published；?mine=1 看自己的草稿；管理员 ?all=1 看全部）
- GET    /fanworks/{id}    详情（草稿仅作者/管理员可见）
- POST   /fanworks/        发布/存草稿（需登录）
- PATCH  /fanworks/{id}    编辑（作者或管理员）
- DELETE /fanworks/{id}    删除（作者或管理员）

附件不直接走本模块：先 POST /uploads/?category=fanwork 拿到附件对象，
再放进 FanworkIn.attachments 提交。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.config import Settings, get_settings
from app.db import get_db
from app.security import get_current_user, get_current_user_optional
from app.zfile_client import COVER_EXTS, ZFilePath, UploadGuardError, ZFileError, get_zfile, validate_upload

router = APIRouter(tags=["同人创作"])

_VALID_STATUS = {models.FanworkStatus.DRAFT, models.FanworkStatus.PUBLISHED}


def _is_admin(user: Optional[models.User]) -> bool:
    return user is not None and user.role == models.UserRole.ADMIN


def _fanwork_out(fw: models.Fanwork) -> Dict[str, Any]:
    return {
        "id": fw.id,
        "work_id": fw.work_id,
        "work_title": fw.work.title if fw.work else None,
        "author": {
            "id": fw.author.id if fw.author else None,
            "qq": fw.author.qq if fw.author else None,
            "nickname": fw.author.nickname if fw.author else None,
        },
        "title": fw.title,
        "category": fw.category,
        "cover_url": fw.cover_url,
        "body": fw.body,
        "attachments": fw.attachments_json or [],
        "status": fw.status,
        "created_at": fw.created_at,
    }


def _can_view(fw: models.Fanwork, me: Optional[models.User]) -> bool:
    if fw.status == models.FanworkStatus.PUBLISHED:
        return True
    if me is None:
        return False
    return _is_admin(me) or fw.author_id == me.id


def _validate_status(value: Optional[str]) -> Optional[str]:
    if value is not None and value not in _VALID_STATUS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"status 只能是 {sorted(_VALID_STATUS)}",
        )
    return value


def _validate_work(db: Session, work_id: Optional[int]) -> None:
    if work_id is None:
        return
    w = db.query(models.Work.id).filter(models.Work.id == work_id).first()
    if w is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"关联作品 id={work_id} 不存在")


@router.get("/")
def list_fanworks(
    keyword: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    work_id: Optional[int] = Query(None),
    mine: bool = Query(False, description="只看我的（含草稿），需登录"),
    all_status: bool = Query(False, alias="all", description="管理员：看全部状态（含他人草稿）"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    me: Optional[models.User] = Depends(get_current_user_optional),
):
    """同人创作列表。"""
    q = db.query(models.Fanwork)

    if mine:
        if me is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
        q = q.filter(models.Fanwork.author_id == me.id)
    elif all_status and _is_admin(me):
        pass  # 管理员看全部状态，不加 status 过滤
    else:
        # 公开列表：published，外加本人草稿（登录时自己的草稿也能在列表看到）
        if me is not None:
            q = q.filter(
                (models.Fanwork.status == models.FanworkStatus.PUBLISHED)
                | (models.Fanwork.author_id == me.id)
            )
        else:
            q = q.filter(models.Fanwork.status == models.FanworkStatus.PUBLISHED)

    if keyword:
        like = f"%{keyword}%"
        q = q.filter(models.Fanwork.title.like(like))
    if category:
        q = q.filter(models.Fanwork.category == category)
    if work_id is not None:
        q = q.filter(models.Fanwork.work_id == work_id)

    total = q.count()
    items = (
        q.order_by(models.Fanwork.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {
        "ok": True,
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_fanwork_out(fw) for fw in items],
    }


@router.get("/{fanwork_id}")
def get_fanwork(
    fanwork_id: int,
    db: Session = Depends(get_db),
    me: Optional[models.User] = Depends(get_current_user_optional),
):
    """同人创作详情。"""
    fw = db.query(models.Fanwork).filter(models.Fanwork.id == fanwork_id).first()
    if fw is None or not _can_view(fw, me):
        # 草稿对无权用户表现为 404，避免泄露存在性
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="同人作品不存在")
    return {"ok": True, "item": _fanwork_out(fw)}


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_fanwork(
    payload: schemas.FanworkIn,
    db: Session = Depends(get_db),
    me: models.User = Depends(get_current_user),
):
    """发布同人创作或存草稿。"""
    _validate_status(payload.status)
    _validate_work(db, payload.work_id)

    fw = models.Fanwork(
        work_id=payload.work_id,
        author_id=me.id,
        title=payload.title.strip(),
        category=payload.category,
        cover_url=payload.cover_url,
        body=payload.body,
        attachments_json=payload.attachments or [],
        status=payload.status,
    )
    db.add(fw)
    db.commit()
    db.refresh(fw)
    return {"ok": True, "item": _fanwork_out(fw)}


@router.patch("/{fanwork_id}")
def update_fanwork(
    fanwork_id: int,
    payload: schemas.FanworkPatchIn,
    db: Session = Depends(get_db),
    me: models.User = Depends(get_current_user),
):
    """编辑同人创作（作者本人或管理员）。"""
    fw = db.query(models.Fanwork).filter(models.Fanwork.id == fanwork_id).first()
    if fw is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="同人作品不存在")
    if fw.author_id != me.id and not _is_admin(me):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只能编辑自己的同人作品")

    data = payload.model_dump(exclude_unset=True)
    if "status" in data:
        _validate_status(data["status"])
    if "work_id" in data:
        _validate_work(db, data["work_id"])
    if "title" in data and data["title"]:
        data["title"] = data["title"].strip()

    if "attachments" in data:
        fw.attachments_json = data.pop("attachments") or []
    for key, value in data.items():
        setattr(fw, key, value)

    db.commit()
    db.refresh(fw)
    return {"ok": True, "item": _fanwork_out(fw)}


@router.delete("/{fanwork_id}")
def delete_fanwork(
    fanwork_id: int,
    db: Session = Depends(get_db),
    me: models.User = Depends(get_current_user),
):
    """删除同人创作（作者本人或管理员）。"""
    fw = db.query(models.Fanwork).filter(models.Fanwork.id == fanwork_id).first()
    if fw is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="同人作品不存在")
    if fw.author_id != me.id and not _is_admin(me):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只能删除自己的同人作品")

    db.delete(fw)
    db.commit()
    return {"ok": True, "message": "已删除"}


@router.post("/{fanwork_id}/cover")
def upload_fanwork_cover(
    fanwork_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    me: models.User = Depends(get_current_user),
):
    """上传同人封面图（位图白名单）。传完写回 fanwork.cover_url。"""
    fw = db.query(models.Fanwork).filter(models.Fanwork.id == fanwork_id).first()
    if fw is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="同人作品不存在")
    if fw.author_id != me.id and not _is_admin(me):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只能修改自己的同人作品封面")

    file_name = file.filename or f"fanwork_{fanwork_id}"
    file_content = file.file.read()
    try:
        validate_upload(file_name, file_content, COVER_EXTS, settings.max_upload_bytes)
    except UploadGuardError as e:
        raise HTTPException(status_code=e.status, detail=e.msg)

    ext = Path(file_name).suffix.lower() or ".jpg"
    zpath = ZFilePath.general("cover", me.qq, f"fanwork_{fanwork_id}{ext}")
    try:
        zf = get_zfile()
        url = zf.upload_file(file_name, file_content, path=zpath)
    except ZFileError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"zfile 上传失败: {e.msg}")
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"zfile 连接失败: {e}")

    fw.cover_url = url
    db.commit()
    return {"ok": True, "cover_url": url}
