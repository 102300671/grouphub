"""通用文件上传 API：用于评论附件、论坛附件、同人附件等非作品文件。

文件存到 zfile 的 /uploads/{category}/{uploader_qq}/ 路径下。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import User
from app.security import get_current_user
from app.zfile_client import (
    GENERAL_UPLOAD_EXTS,
    UploadGuardError,
    ZFileError,
    ZFilePath,
    validate_upload,
)
from app.storage import get_storage

router = APIRouter(tags=["文件上传"])


@router.post("/")
def upload_general_file(
    category: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    me: User = Depends(get_current_user),
):
    """上传通用文件到 zfile（同步端点：FastAPI 自动放线程池，避免 zfile 同步 httpx 阻塞事件循环）。

    Args:
        category: 文件分类（如 review / topic / fanwork / general）
        file: 上传的文件

    Returns:
        {"url": "...", "file_name": "...", "size_bytes": N}
    """
    if not category or "/" in category or "\\" in category or ".." in category:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="category 不合法（不能包含 / \\ ..）",
        )

    file_name = file.filename or "unnamed"
    file_content = file.file.read()
    try:
        validate_upload(file_name, file_content, GENERAL_UPLOAD_EXTS, settings.max_upload_bytes)
    except UploadGuardError as e:
        raise HTTPException(status_code=e.status, detail=e.msg)

    zpath = ZFilePath.general_dir(category, me.qq)

    try:
        storage = get_storage()
        url = storage.upload_file(file_name, file_content, path=zpath)
    except ZFileError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"zfile 上传失败: {e.msg}")
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"zfile 连接失败: {e}")

    return {
        "ok": True,
        "url": url,
        "file_name": file_name,
        "mime_type": file.content_type,
        "size_bytes": len(file_content),
    }
