"""/works/* —— 作品库、上传、用户-作品关系。MVP 阶段提供最小可跑实现：

  GET    /              列表（按类型/更新时间过滤，分页）
  GET    /{work_id}     详情
  POST   /              上传作品（F3：仅名称 / 带详情）
  PATCH  /{work_id}/relation  本人绑定 supporter/recommender/阅读状态（F5）
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, unquote, urlparse

import httpx

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from app import chapters, models, schemas
from app.config import Settings, get_settings
from app.db import get_db
from app.security import get_current_user
from app.zfile_client import (
    COVER_EXTS,
    ZFilePath,
    WORK_FILE_EXTS,
    UploadGuardError,
    ZFileError,
    get_zfile,
    public_url,
    validate_upload,
)

router = APIRouter()


def _user_ref(user: models.User) -> Dict[str, Any]:
    return {"id": user.id, "nickname": user.nickname, "qq": user.qq}


def _work_out(work: models.Work) -> Dict[str, Any]:
    return {
        "id": work.id,
        "title": work.title,
        "author": work.author,
        "type": work.type,
        "source_work_id": work.source_work_id,
        "source_work_title": work.source_work.title if work.source_work else None,
        "cover_url": public_url(work.cover_url),
        "summary": work.summary,
        "uploader": _user_ref(work.uploader) if work.uploader else None,
        "tags": work.tags_json or [],
        "created_at": work.created_at,
        "updated_at": work.updated_at,
    }


def _file_out(f: models.WorkExternalFile) -> Dict[str, Any]:
    return {
        "id": f.id,
        "provider": f.provider,
        "url": public_url(f.url),
        "file_name": f.file_name,
        "mime_type": f.mime_type,
        "size_bytes": f.size_bytes,
        "uploader": _user_ref(f.uploader) if f.uploader else None,
        "created_at": f.created_at,
    }


def _can_manage_work(w: models.Work, me: models.User) -> bool:
    """上传者本人或管理员可编辑作品元信息/链接/文件。"""
    return me.role == models.UserRole.ADMIN or w.uploader_id == me.id


def _apply_links_replace(db: Session, work: models.Work, links: Optional[List[Dict[str, str]]]) -> None:
    """links 传 None 不动；传数组则整体替换（删+增）。URL 必填，site_name 可选。"""
    if links is None:
        return
    db.query(models.WorkLink).filter(models.WorkLink.work_id == work.id).delete()
    for link in links:
        url = (link.get("url") or "").strip()
        if not url:
            continue
        db.add(models.WorkLink(work_id=work.id, site_name=link.get("site_name"), url=url))
    work.links  # 触发关系刷新，确保 selectin 立即生效


# ------------------- 列表 -------------------

@router.get("/")
def list_works(
    type: Optional[str] = None,
    keyword: Optional[str] = None,
    source_work_id: Optional[int] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    q = db.query(models.Work).filter(models.Work.status == models.WorkStatus.PUBLISHED)
    if type:
        q = q.filter(models.Work.type == type)
    if source_work_id is not None:
        q = q.filter(models.Work.source_work_id == source_work_id)
    if keyword:
        like = f"%{keyword}%"
        # MVP 用 LIKE 匹配标题/作者；后续接真正全文检索
        q = q.filter(models.Work.title.like(like) | models.Work.author.like(like))

    total = q.count()
    items = (
        q.order_by(models.Work.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return {"total": total, "page": page, "page_size": page_size, "items": [_work_out(w) for w in items]}


# ------------------- 详情 -------------------

@router.get("/{work_id}")
def work_detail(
    work_id: int,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    """作品详情（同时作为 helper：外部调用时可直接传入 settings=get_settings()）。"""
    w = db.query(models.Work).filter(models.Work.id == work_id).first()
    if w is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="作品不存在")

    links = (
        db.query(models.WorkLink)
        .filter(models.WorkLink.work_id == work_id)
        .order_by(models.WorkLink.id.asc())
        .all()
    )
    external_files = (
        db.query(models.WorkExternalFile)
        .filter(models.WorkExternalFile.work_id == work_id)
        .order_by(models.WorkExternalFile.id.asc())
        .all()
    )
    relations = (
        db.query(models.UserWork)
        .filter(models.UserWork.work_id == work_id)
        .all()
    )

    supporters = []
    recommenders = []
    reading_stats: Dict[str, int] = {}
    for r in relations:
        roles = r.relation_roles or []
        user_info = {"user_id": r.user_id}
        if "supporter" in roles:
            supporters.append(user_info)
        if "recommender" in roles:
            recommenders.append(user_info)
        if r.reading_status:
            reading_stats[r.reading_status] = reading_stats.get(r.reading_status, 0) + 1

    show_relation_count = settings.show_relation_threshold
    show_supporters = len(supporters) >= show_relation_count
    show_recommenders = len(recommenders) >= show_relation_count

    result = _work_out(w)
    result["links"] = [{"id": l.id, "site_name": l.site_name, "url": l.url} for l in links]
    result["external_files"] = [_file_out(f) for f in external_files]
    result["relations"] = {
        "supporter_count": len(supporters),
        "recommender_count": len(recommenders),
        "show_supporters": show_supporters,
        "show_recommenders": show_recommenders,
        "reading_stats": reading_stats,
    }
    return result


# ------------------- 作品 PATCH（元信息 + 站外链接整体替换） -------------------

@router.patch("/{work_id}")
def update_work(
    work_id: int,
    payload: schemas.WorkPatchIn,
    db: Session = Depends(get_db),
    me: models.User = Depends(get_current_user),
):
    """编辑作品。上传者本人或管理员可改。

    传了的字段才改；links 传了即整体替换（等价前端列表「增删改」后一次性回传）。
    """
    w = db.query(models.Work).filter(models.Work.id == work_id).first()
    if w is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="作品不存在")
    if not _can_manage_work(w, me):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权编辑他人作品")

    data = payload.model_dump(exclude_unset=True)
    links_data = data.pop("links", None)
    if links_data is not None:
        _apply_links_replace(db, w, links_data)

    for key, value in data.items():
        setattr(w, key if key != "tags" else "tags_json", value if key != "tags" else (value or []))

    db.commit()
    db.refresh(w)
    # 返回最新详情，前端无需二次拉取
    return work_detail(work_id, db, settings=get_settings())


# ------------------- 上传 -------------------

@router.post("/", response_model=schemas.WorkOut)
def create_work(
    payload: schemas.WorkIn,
    db: Session = Depends(get_db),
    me: models.User = Depends(get_current_user),
):
    """F3：上传作品（人人可发）。title 必填；其他字段为空即为「仅名称」模式。"""
    # 简易去重：若标题完全相同的作品已存在，给出提示（不强制阻止，避免误杀，详见 PRD F3）
    dup = (
        db.query(models.Work)
        .filter(models.Work.title == payload.title)
        .first()
    )
    if dup is not None:
        # MVP：直接返回，提示重复，不自动合并
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"已存在同名作品（ID={dup.id}），请先前往查看并标记为支持者，或确认后再上传。",
        )

    w = models.Work(
        title=payload.title,
        author=payload.author,
        type=payload.type or models.WorkType.OTHER,
        source_work_id=payload.source_work_id,
        summary=payload.summary,
        cover_url=payload.cover_url,
        uploader_id=me.id,
        tags_json=payload.tags or [],
        status=models.WorkStatus.PUBLISHED,
    )
    db.add(w)
    db.flush()

    for link in payload.links:
        db.add(models.WorkLink(work_id=w.id, site_name=link.get("site_name"), url=link["url"]))
    for ef in payload.external_files:
        db.add(
            models.WorkExternalFile(
                work_id=w.id,
                provider=ef.get("provider", models.ExternalFileProvider.ZFILE),
                url=ef["url"],
                file_name=ef.get("file_name"),
                mime_type=ef.get("mime_type"),
                size_bytes=ef.get("size_bytes"),
                uploader_id=me.id,
            )
        )

    db.commit()
    db.refresh(w)
    return schemas.WorkOut(
        id=w.id,
        title=w.title,
        author=w.author,
        type=w.type,
        summary=w.summary,
        uploader=_user_ref(me),
        tags=w.tags_json or [],
        created_at=w.created_at,
    )


# ------------------- 用户-作品关系（F5） -------------------

@router.patch("/{work_id}/relation")
def patch_relation(
    work_id: int,
    payload: schemas.UserWorkPatchIn,
    db: Session = Depends(get_db),
    me: models.User = Depends(get_current_user),
):
    """F5：本人绑定/取消 supporter 或 recommender、更新阅读状态。"""
    w = db.query(models.Work).filter(models.Work.id == work_id).first()
    if w is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="作品不存在")

    rel = (
        db.query(models.UserWork)
        .filter(models.UserWork.work_id == work_id, models.UserWork.user_id == me.id)
        .first()
    )
    if rel is None:
        rel = models.UserWork(user_id=me.id, work_id=work_id, relation_roles=[], reading_status=None)
        db.add(rel)
        db.flush()

    roles: List[str] = list(rel.relation_roles or [])
    if payload.is_supporter is True and "supporter" not in roles:
        roles.append("supporter")
    if payload.is_supporter is False and "supporter" in roles:
        roles.remove("supporter")
    if payload.is_recommender is True and "recommender" not in roles:
        roles.append("recommender")
    if payload.is_recommender is False and "recommender" in roles:
        roles.remove("recommender")

    rel.relation_roles = roles
    if payload.reading_status is not None:
        rel.reading_status = payload.reading_status

    # 所有角色为空 且 reading_status 为空 → 删除记录，保持表干净
    if not rel.relation_roles and not rel.reading_status:
        db.delete(rel)

    db.commit()
    return {
        "ok": True,
        "work_id": work_id,
        "relation_roles": roles,
        # 记录被清空删除时 rel.reading_status 为 None；否则就是库中最新值（含未传时保留的旧值）
        "reading_status": rel.reading_status,
    }


def _upload_one_work_file(
    db: Session, settings: Settings, w: models.Work, file: UploadFile, me: models.User
) -> models.WorkExternalFile:
    """上传单个作品文件到 zfile，写回 WorkExternalFile。caller 负责权限检查。"""
    file_name = file.filename or "unnamed"
    file_content = file.file.read()
    try:
        # 作品文件（小说/番剧/电影）用更大上限，封面/附件仍用 max_upload_bytes
        validate_upload(file_name, file_content, WORK_FILE_EXTS, settings.max_work_upload_bytes)
    except UploadGuardError as e:
        raise HTTPException(status_code=e.status, detail=f"[{file_name}] {e.msg}")

    zpath = ZFilePath.work_file_dir(w.type, me.qq, w.id, w.title)
    try:
        zf = get_zfile()
        url = zf.upload_file(file_name, file_content, path=zpath)
    except ZFileError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"[{file_name}] zfile 上传失败: {e.msg}")
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"[{file_name}] zfile 连接失败: {e}")

    ef = models.WorkExternalFile(
        work_id=w.id,
        provider=models.ExternalFileProvider.ZFILE,
        url=url,
        file_name=file_name,
        mime_type=file.content_type,
        size_bytes=len(file_content),
        uploader_id=me.id,
    )
    db.add(ef)
    db.flush()
    return ef


# ------------------- 文件上传（zfile） -------------------

@router.post("/{work_id}/files")
def upload_work_file(
    work_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    me: models.User = Depends(get_current_user),
):
    """上传单个文件到 zfile 并关联到作品（连载追加一章、单文件补充等场景）。"""
    w = db.query(models.Work).filter(models.Work.id == work_id).first()
    if w is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="作品不存在")
    if not _can_manage_work(w, me):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权为他人作品上传文件")

    ef = _upload_one_work_file(db, settings, w, file, me)
    db.commit()
    db.refresh(ef)
    return {"ok": True, "file": _file_out(ef)}


@router.post("/{work_id}/files/batch")
def upload_work_files_batch(
    work_id: int,
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    me: models.User = Depends(get_current_user),
):
    """批量上传连载文件（一次入 DB，保留文件顺序）。

    关于连载处理方式（对应用户问的几种方案）：
    - 上传的每一章 **都作为独立 WorkExternalFile 存在**（默认策略：作者自己按章节增量上传）
    - 同时在列表里按创建顺序展示，阅读页按顺序章节化（见前端 detail 页渲染）
    - 下载侧提供「逐个下载」入口；如需整包下载（zip 合并）可后续扩展 /works/{id}/files/zip 代理端点
    """
    w = db.query(models.Work).filter(models.Work.id == work_id).first()
    if w is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="作品不存在")
    if not _can_manage_work(w, me):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权为他人作品上传文件")
    if not files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请至少选择一个文件")

    uploaded = []
    for f in files:
        ef = _upload_one_work_file(db, settings, w, f, me)
        uploaded.append(ef)
    db.commit()
    return {"ok": True, "count": len(uploaded), "files": [_file_out(ef) for ef in uploaded]}


@router.delete("/{work_id}/files/{file_id}")
def delete_work_file(
    work_id: int,
    file_id: int,
    db: Session = Depends(get_db),
    me: models.User = Depends(get_current_user),
):
    """删除作品关联的文件记录。权限：上传者本人或管理员。"""
    w = db.query(models.Work).filter(models.Work.id == work_id).first()
    if w is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="作品不存在")

    ef = (
        db.query(models.WorkExternalFile)
        .filter(models.WorkExternalFile.id == file_id, models.WorkExternalFile.work_id == work_id)
        .first()
    )
    if ef is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文件记录不存在")

    if ef.uploader_id != me.id and me.role != models.UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权删除他人上传的文件")

    db.delete(ef)
    db.commit()
    return {"ok": True}


# ------------------- 外站直链添加（provider=url） -------------------

def _meta_from_url(url: str) -> Dict[str, Optional[str]]:
    """尽力从直链探测 Content-Type / Content-Length；CDN 不支持则静默放行。"""
    mime_type: Optional[str] = None
    size_bytes: Optional[int] = None
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            r = client.head(url)
            if r.status_code >= 400:
                # 部分 CDN 不支持 HEAD，退回 Range GET 只取 1 字节
                r = client.get(url, headers={"Range": "bytes=0-0"})
            if r.status_code not in (200, 206):
                return {"mime_type": mime_type, "size_bytes": size_bytes}
            ctype = (r.headers.get("content-type") or "").split(";")[0].strip()
            if ctype:
                mime_type = ctype
            clen = (r.headers.get("content-length") or "").strip()
            if r.status_code == 206:
                cr = r.headers.get("content-range") or ""
                if "/" in cr and cr.split("/")[-1].strip().isdigit():
                    clen = cr.split("/")[-1].strip()
            if clen.isdigit():
                size_bytes = int(clen)
    except Exception:
        pass  # 探测失败不阻断添加
    return {"mime_type": mime_type, "size_bytes": size_bytes}


@router.post("/{work_id}/files/url")
def add_work_file_url(
    work_id: int,
    payload: schemas.WorkFileUrlIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    me: models.User = Depends(get_current_user),
):
    """把外站直链（视频 CDN / 网盘 / 对象存储等）添加为作品文件，不搬运文件本体。

    文件本体不入库；站内阅读/观看/下载仍由后端代理拉取（/raw、/chapters、/download 均按 url 直取）。
    权限与文件上传一致：上传者本人或管理员。
    """
    w = db.query(models.Work).filter(models.Work.id == work_id).first()
    if w is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="作品不存在")
    if not _can_manage_work(w, me):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权为他人作品添加文件")

    url = payload.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请填写完整的直链地址（http/https）")

    # 从直链路径推断扩展名做类型校验（与文件上传同一套白名单）
    path = unquote(urlparse(url).path)
    base = path.rsplit("/", 1)[-1]
    ext = base.rsplit(".", 1)[-1].lower() if "." in base else ""
    if ext not in WORK_FILE_EXTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"直链扩展名 .{ext or '?'} 不在允许范围内（支持图片/视频/音频/文档等）",
        )

    file_name = (payload.file_name or "").strip() or base or f"file.{ext}"

    meta = _meta_from_url(url)
    if meta["size_bytes"] is not None and meta["size_bytes"] > settings.max_work_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"直链文件大小约 {meta['size_bytes'] / 1048576:.0f}MB，超过上限 {settings.max_work_upload_mb}MB",
        )

    ef = models.WorkExternalFile(
        work_id=w.id,
        provider=models.ExternalFileProvider.URL,
        url=url,
        file_name=file_name,
        mime_type=meta["mime_type"],
        size_bytes=meta["size_bytes"],
        uploader_id=me.id,
    )
    db.add(ef)
    db.commit()
    db.refresh(ef)
    return {"ok": True, "file": _file_out(ef)}


# ------------------- 章节 / 站内阅读代理 / 下载 -------------------

def _work_and_files(db: Session, work_id: int):
    w = db.query(models.Work).filter(models.Work.id == work_id).first()
    if w is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="作品不存在")
    files = (
        db.query(models.WorkExternalFile)
        .filter(models.WorkExternalFile.work_id == work_id)
        .order_by(models.WorkExternalFile.id.asc())
        .all()
    )
    return w, files


def _ef_or_404(db: Session, work_id: int, file_id: int) -> models.WorkExternalFile:
    ef = (
        db.query(models.WorkExternalFile)
        .filter(models.WorkExternalFile.id == file_id, models.WorkExternalFile.work_id == work_id)
        .first()
    )
    if ef is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文件记录不存在")
    return ef


def _fetch_or_502(url: str) -> bytes:
    try:
        return chapters.fetch_file_bytes(url)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"从 zfile 拉取文件失败: {e}")


def _attachment_headers(file_name: str, media_type: str) -> Dict[str, str]:
    return {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(chapters.safe_filename(file_name))}",
        "Content-Type": media_type,
    }


@router.get("/{work_id}/chapters")
def work_chapters(work_id: int, db: Session = Depends(get_db)):
    """章节列表。

    - 单文件文本（txt/md）且能切出 >=2 章 → mode=split，按行内偏移切章
    - 其余情况（多文件 / 单文件非文本 / 切不出章节）→ mode=file，每个文件 = 一章
    """
    _, files = _work_and_files(db, work_id)
    if not files:
        return {"mode": "file", "chapters": []}

    if len(files) == 1:
        ef = files[0]
        if chapters.ext_of(ef.file_name) in chapters.SPLITTABLE_EXTS:
            try:
                text = chapters.decode_bytes(_fetch_or_502(ef.url))
                parts = chapters.split_text(text)
                if len(parts) >= 2:
                    return {
                        "mode": "split",
                        "chapters": [
                            {
                                "index": i,
                                "file_id": ef.id,
                                "file_name": ef.file_name,
                                "title": p["title"] or f"第 {i + 1} 部分",
                                "start": p["start"],
                                "end": p["end"],
                            }
                            for i, p in enumerate(parts)
                        ],
                    }
            except HTTPException:
                raise
            except Exception:
                pass  # 拉取/切分失败时退化为整文件一章

    return {
        "mode": "file",
        "chapters": [
            {
                "index": i,
                "file_id": ef.id,
                "file_name": ef.file_name,
                "title": ef.file_name or f"文件 {i + 1}",
                "start": 0,
                "end": None,
            }
            for i, ef in enumerate(files)
        ],
    }


@router.get("/{work_id}/files/{file_id}/raw")
def file_raw(
    work_id: int,
    file_id: int,
    db: Session = Depends(get_db),
    start: Optional[int] = Query(None, ge=0),
    end: Optional[int] = Query(None, ge=1),
):
    """代理拉取 zfile 文件内容并补正确的 Content-Type/charset（站内阅读专用）。

    - 文本：text/plain; charset=utf-8 —— 根治浏览器直开 zfile 直链乱码
    - 图片/视频/音频/PDF：inline 流式，供站内观看
    - 其它（epub/zip/docx…）：attachment 触发下载
    - start/end：文本按字符偏移切片（单文件章节化阅读）
    """
    ef = _ef_or_404(db, work_id, file_id)
    ext = chapters.ext_of(ef.file_name)
    data = _fetch_or_502(ef.url)
    media_type, disposition = chapters.content_type_for(ext)

    if ext in chapters.SPLITTABLE_EXTS and (start is not None or end is not None):
        text = chapters.decode_bytes(data)
        data = text[start or 0 : end if end is not None else len(text)].encode("utf-8")
        media_type = "text/plain; charset=utf-8"
        disposition = "inline"

    if disposition == "attachment":
        cd = f"attachment; filename*=UTF-8''{quote(chapters.safe_filename(ef.file_name))}"
    else:
        cd = "inline"
    return Response(content=data, media_type=media_type, headers={"Content-Disposition": cd})


def _parse_ids(raw: Optional[str], allow_zero: bool = False) -> List[int]:
    if not raw:
        return []
    out = []
    for part in raw.split(","):
        part = part.strip()
        if part.lstrip("-").isdigit() and int(part) >= (0 if allow_zero else 1):
            out.append(int(part))
    return out


@router.get("/{work_id}/download")
def work_download(
    work_id: int,
    db: Session = Depends(get_db),
    files: Optional[str] = Query(None, description="逗号分隔的文件 id（选章下载；多文件作品的选章=选文件）"),
    chapters_param: Optional[str] = Query(None, alias="chapters", description="逗号分隔的章节序号（仅单文本文件作品，按序抽取章节合并为 TXT）"),
):
    """作品下载。

    - 不带参数 = 下载整本：单文件直接返回该文件；多文件合并为完整文件（全文本→一个 TXT，含二进制→ZIP）
    - files=1,2 → 下载指定文件（规则同上，针对子集）
    - files=1&chapters=0,2 → 单文本文件作品抽取指定章节，按序合并成一个 TXT
    """
    w, all_files = _work_and_files(db, work_id)
    if not all_files:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="作品暂无可下载的文件")

    file_ids = _parse_ids(files)
    ch_ids = _parse_ids(chapters_param, allow_zero=True)
    selected = [f for f in all_files if f.id in file_ids] if file_ids else list(all_files)
    if not selected:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="未找到有效的文件")

    title_safe = chapters.safe_filename(w.title)

    # 单文件、未选章节 → 直接下载该文件本身（单文件下载整本 = 原样下载）
    if len(selected) == 1 and not ch_ids:
        ef = selected[0]
        data = _fetch_or_502(ef.url)
        media_type, _ = chapters.content_type_for(chapters.ext_of(ef.file_name))
        return Response(content=data, media_type=media_type, headers=_attachment_headers(ef.file_name, media_type))

    # 单文本文件 + 章节选择 → 按序抽取章节合并成一个 TXT
    if len(selected) == 1 and ch_ids:
        ef = selected[0]
        if chapters.ext_of(ef.file_name) not in chapters.SPLITTABLE_EXTS:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="该文件类型不支持按章节抽取，请下载整本")
        text = chapters.decode_bytes(_fetch_or_502(ef.url))
        parts = sorted(chapters.split_text(text), key=lambda p: p["start"])
        picked = [parts[i] for i in ch_ids if 0 <= i < len(parts)]
        if not picked:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="未找到有效的章节")
        body = "\n\n".join(text[p["start"]:p["end"]].strip() for p in picked)
        name = f"{title_safe}-选章合集.txt"
        return Response(content=body.encode("utf-8"), media_type="text/plain; charset=utf-8",
                        headers=_attachment_headers(name, "text/plain; charset=utf-8"))

    # 多文件 → 全部文本则合并为一个 TXT；含二进制则打包 ZIP
    exts = [chapters.ext_of(f.file_name) for f in selected]
    if all(e in chapters.SPLITTABLE_EXTS for e in exts):
        parts = [chapters.decode_bytes(_fetch_or_502(f.url)).strip() for f in selected]
        body = "\n\n\n".join(p for p in parts if p)
        name = f"{title_safe}-全本.txt"
        return Response(content=body.encode("utf-8"), media_type="text/plain; charset=utf-8",
                        headers=_attachment_headers(name, "text/plain; charset=utf-8"))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in selected:
            zf.writestr(chapters.safe_filename(f.file_name), _fetch_or_502(f.url))
    name = f"{title_safe}-全本.zip"
    return Response(content=buf.getvalue(), media_type="application/zip",
                    headers=_attachment_headers(name, "application/zip"))


# ------------------- 作品封面上传 -------------------

@router.post("/{work_id}/cover")
def upload_work_cover(
    work_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    me: models.User = Depends(get_current_user),
):
    """上传作品封面图（位图白名单）。传完自动写回 work.cover_url 并返回。"""
    w = db.query(models.Work).filter(models.Work.id == work_id).first()
    if w is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="作品不存在")
    if not _can_manage_work(w, me):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权修改他人作品封面")

    file_name = file.filename or f"cover_{work_id}"
    file_content = file.file.read()
    try:
        validate_upload(file_name, file_content, COVER_EXTS, settings.max_upload_bytes)
    except UploadGuardError as e:
        raise HTTPException(status_code=e.status, detail=e.msg)

    ext = Path(file_name).suffix.lower() or ".jpg"
    zpath = ZFilePath.cover(work_id, ext)
    try:
        zf = get_zfile()
        url = zf.upload_file(f"cover{ext}", file_content, path=zpath)
    except ZFileError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"zfile 上传失败: {e.msg}")
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"zfile 连接失败: {e}")

    w.cover_url = url
    db.commit()
    db.refresh(w)
    return {"ok": True, "cover_url": public_url(url)}


# ------------------- 管理员：删除作品 -------------------

@router.delete("/{work_id}")
def delete_work(
    work_id: int,
    db: Session = Depends(get_db),
    me: models.User = Depends(get_current_user),
):
    """删除作品（含链接、外部文件级联删除）。仅管理员。"""
    if me.role != models.UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅管理员可删除作品")
    w = db.query(models.Work).filter(models.Work.id == work_id).first()
    if w is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="作品不存在")
    db.delete(w)
    db.commit()
    return {"ok": True, "message": f"作品 id={work_id} 已删除"}
