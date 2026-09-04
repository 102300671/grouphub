"""/auth/* —— 用户侧注册 / 登录。

强规则（对齐 PRD §6.1）：
  - 注册/登录前必须校验 group_members.is_active=true
  - 退群（is_active=false）时 token 下一次鉴权会拒绝访问
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.config import Settings, get_settings
from app.db import get_db
from app.models import utcnow
from app.zfile_client import public_url
from app.security import (
    _auto_promote_check,
    create_access_token,
    get_current_user,
    hash_password,
    is_qq_in_group,
    verify_password,
)

router = APIRouter()


NOT_IN_GROUP_MSG = "你的 QQ 不在本群，请先加群后再使用本站"

# ------------------- 验证码风控（进程内内存态；MVP 单进程足够，多进程再换 DB/Redis） -------------------
CODE_TTL_MINUTES = 10          # 验证码有效期
CODE_RESEND_SECONDS = 60       # 同一 QQ 两次发码最小间隔
CODE_MAX_ATTEMPTS = 5          # 同一 QQ 连续验证失败上限，超过需重新发码

# qq -> 最近一次成功发码时间
_code_last_sent: Dict[str, datetime] = {}
# qq -> 连续验证失败次数
_code_attempts: Dict[str, int] = {}


def _push_to_bot(settings: Settings, path: str, payload: dict) -> bool:
    """反向调用 nonebot2 侧 HTTP 服务（/bot/auth/*）。失败不抛异常，返回 False。"""
    import httpx

    try:
        resp = httpx.post(
            f"{settings.qqbot_api_base.rstrip('/')}{path}",
            json=payload,
            headers={"X-Bot-Token": settings.bot_api_token},
            timeout=8.0,
        )
        resp.raise_for_status()
        # bot 侧业务失败（如无已连接 bot、私聊下发失败）HTTP 仍为 200，需再校验 body.ok
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001 非 JSON 响应视为成功（兼容未来纯 ack 端点）
            data = {}
        if isinstance(data, dict) and data.get("ok") is False:
            raise RuntimeError(f"bot 侧返回失败：{data.get('message')}")
        return True
    except Exception as exc:  # noqa: BLE001 推送失败降级，不打断用户主流程
        import logging
        logging.getLogger(__name__).warning(f"[auth] 推送 bot 侧 {path} 失败（降级）：{exc}")
        return False


def _notify_login(settings: Settings, qq: str, request: Optional[Request]) -> None:
    """登录/注册成功后 best-effort 通知 bot 侧（插件 #2 可用来在群里播报等）。"""
    ip = request.client.host if request is not None and request.client else None
    _push_to_bot(
        settings,
        "/bot/auth/notify-login",
        {"qq": qq, "login_at": datetime.now(timezone.utc).isoformat(), "ip": ip},
    )


def _user_out(user: models.User) -> Dict[str, Any]:
    return {
        "id": user.id,
        "qq": user.qq,
        "nickname": user.nickname,
        "avatar_url": public_url(user.avatar_url),
        "role": user.role,
    }


def _backfill_avatar(db: Session, user: models.User) -> None:
    """用户无头像时，从该 QQ 活跃群成员记录回填（插件 #1 同步的 zfile 直链）。"""
    if user.avatar_url:
        return
    row = (
        db.query(models.GroupMember)
        .filter(models.GroupMember.qq == user.qq, models.GroupMember.is_active.is_(True))
        .order_by(models.GroupMember.last_synced_at.desc())
        .first()
    )
    if row is not None and row.avatar_url:
        user.avatar_url = row.avatar_url


def _guess_nickname_by_qq(db: Session, qq: str) -> str | None:
    """注册时尝试用群名片作为默认昵称。"""
    row = (
        db.query(models.GroupMember)
        .filter(models.GroupMember.qq == qq, models.GroupMember.is_active.is_(True))
        .first()
    )
    return row.nickname_in_group if row and row.nickname_in_group else None


# ------------------- 注册 -------------------

@router.post("/register", response_model=schemas.AuthTokenOut)
def register(payload: schemas.RegisterIn, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    # 1. 白名单校验
    if not is_qq_in_group(payload.qq, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=NOT_IN_GROUP_MSG)

    # 2. 已存在？
    existing = db.query(models.User).filter(models.User.qq == payload.qq).first()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="该 QQ 已注册，请直接登录")

    # 3. 创建用户（昵称优先级：显式传值 > 群名片；角色：在 ADMIN_QQS 中 → admin，否则 member）
    nickname = payload.nickname or _guess_nickname_by_qq(db, payload.qq) or f"群友{payload.qq}"
    initial_role = (
        models.UserRole.ADMIN if payload.qq in settings.admin_qq_set else models.UserRole.MEMBER
    )
    user = models.User(
        qq=payload.qq,
        password_hash=hash_password(payload.password),
        nickname=nickname,
        role=initial_role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    _backfill_avatar(db, user)
    db.commit()

    # 注册后仍无头像 → 让 bot 实时拉取该 QQ 头像（best-effort，bot 离线时静默降级）
    if not user.avatar_url:
        if _push_to_bot(settings, "/bot/avatars/fetch", {"qq": payload.qq}):
            db.refresh(user)  # bot 推送成功时头像已落库，刷新对象让响应直接带上

    token = create_access_token(user.id, settings)
    return schemas.AuthTokenOut(access_token=token, user=_user_out(user))


# ------------------- 登录（密码方式） -------------------

@router.post("/login", response_model=schemas.AuthTokenOut)
def login(payload: schemas.LoginIn, request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    # 1. 白名单（即使用户老账号已存在，退群后也禁止登录）
    if not is_qq_in_group(payload.qq, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=NOT_IN_GROUP_MSG)

    user = db.query(models.User).filter(models.User.qq == payload.qq).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或密码错误")
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="账号或密码错误")

    # 登录同时检查 ADMIN_QQS，把管理员账号立刻升级（保证 /auth/login 响应里 role 就是 admin，
    # 前端拿到后就能立即渲染「切换到管理界面」按钮）
    _auto_promote_check(user, settings)

    _backfill_avatar(db, user)
    db.commit()
    _notify_login(settings, user.qq, request)

    token = create_access_token(user.id, settings)
    return schemas.AuthTokenOut(access_token=token, user=_user_out(user))


# ------------------- 验证码通道：send-code / confirm-code（MVP 保留接口，强依赖插件 #2） -------------------

@router.post("/send-code", response_model=schemas.SimpleMessageOut)
def send_code(payload: schemas.SendCodeIn, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    """生成验证码并写入 verification_codes，再反向推 nonebot2 的 /bot/auth/send-code 由 bot 私聊下发。

    若 bot 侧不可达（本地没起 nonebot2），降级为 MVP 调试模式：把 code 直接返回在响应里。
    """
    if not is_qq_in_group(payload.qq, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=NOT_IN_GROUP_MSG)

    # 频率限制：同一 QQ 两次发码间隔不得小于 CODE_RESEND_SECONDS
    now = utcnow()
    last_sent = _code_last_sent.get(payload.qq)
    if last_sent is not None and (now - last_sent).total_seconds() < CODE_RESEND_SECONDS:
        wait = CODE_RESEND_SECONDS - int((now - last_sent).total_seconds())
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"发送过于频繁，请 {wait} 秒后再试",
        )

    import random
    code = f"{random.randint(0, 999999):06d}"

    # 作废旧码：该 QQ 所有未使用的验证码标记为已用，旧码立即失效
    db.query(models.VerificationCode).filter(
        models.VerificationCode.qq == payload.qq,
        models.VerificationCode.used.is_(False),
    ).update({models.VerificationCode.used: True}, synchronize_session=False)

    vc = models.VerificationCode(
        qq=payload.qq,
        code=code,
        used=False,
        expires_at=now + timedelta(minutes=CODE_TTL_MINUTES),
        created_at=now,
    )
    db.add(vc)
    db.commit()

    _code_last_sent[payload.qq] = now
    _code_attempts.pop(payload.qq, None)

    pushed = _push_to_bot(settings, "/bot/auth/send-code", {"qq": payload.qq, "code": code})
    if pushed:
        return schemas.SimpleMessageOut(
            ok=True,
            message=f"验证码已发送到你的 QQ 私聊（来自群资源站机器人），请在 {CODE_TTL_MINUTES} 分钟内回填",
            details={"qq": payload.qq, "expires_in_minutes": CODE_TTL_MINUTES},
        )
    # 降级：bot 侧不可达，直接返回 code 便于本地联调
    return schemas.SimpleMessageOut(
        ok=True,
        message="⚠️ 机器人不在线，验证码未通过私聊下发（MVP 调试模式：请复制下方 code 回填）",
        details={"qq": payload.qq, "code": code, "expires_in_minutes": CODE_TTL_MINUTES},
    )


@router.post("/confirm-code", response_model=schemas.AuthTokenOut)
def confirm_code(payload: schemas.ConfirmCodeIn, request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    """验证码校验 → 自动注册/登录。"""
    if not is_qq_in_group(payload.qq, db):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=NOT_IN_GROUP_MSG)

    # 防爆破：连续失败超过上限，要求重新发码
    attempts = _code_attempts.get(payload.qq, 0)
    if attempts >= CODE_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="验证码错误次数过多，请重新获取验证码",
        )

    now = utcnow()
    vc = (
        db.query(models.VerificationCode)
        .filter(
            models.VerificationCode.qq == payload.qq,
            models.VerificationCode.code == payload.code,
        )
        .order_by(models.VerificationCode.created_at.desc())
        .first()
    )

    def _reject(detail: str) -> None:
        _code_attempts[payload.qq] = attempts + 1
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)

    if vc is None:
        _reject("验证码错误")
    if vc.used:
        _reject("验证码已使用或已失效，请重新获取")
    if vc.expires_at < now:
        _reject("验证码已过期")

    vc.used = True
    db.commit()

    # 验证成功，清风控计数
    _code_attempts.pop(payload.qq, None)
    _code_last_sent.pop(payload.qq, None)

    # 不存在则自动注册（角色与密码注册一致：ADMIN_QQS 内直接为 admin）
    user = db.query(models.User).filter(models.User.qq == payload.qq).first()
    if user is None:
        nickname = _guess_nickname_by_qq(db, payload.qq) or f"群友{payload.qq}"
        import secrets
        random_pw = secrets.token_hex(16)
        initial_role = (
            models.UserRole.ADMIN if payload.qq in settings.admin_qq_set else models.UserRole.MEMBER
        )
        user = models.User(
            qq=payload.qq,
            password_hash=hash_password(random_pw),
            nickname=nickname,
            role=initial_role,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        # 老用户同样保证 ADMIN_QQS 提权立即生效
        _auto_promote_check(user, settings)

    _backfill_avatar(db, user)
    db.commit()
    _notify_login(settings, user.qq, request)

    token = create_access_token(user.id, settings)
    return schemas.AuthTokenOut(access_token=token, user=_user_out(user))


# ------------------- 登出 / 我 -------------------

@router.post("/logout", response_model=schemas.SimpleMessageOut)
def logout(_: models.User = Depends(get_current_user)):
    """JWT 是无状态的；MVP 阶段只让前端丢弃 token 即可。
    后续加 sessions / token 黑名单时在这里写逻辑。
    """
    return schemas.SimpleMessageOut(ok=True, message="已退出登录（请从前端删除本地 token）")


@router.get("/me")
def me(user: models.User = Depends(get_current_user)) -> Dict[str, Any]:
    return _user_out(user)


@router.get("/user-works")
def get_user_works(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> Dict[str, Any]:
    """获取当前用户参与的所有作品（作为上传者、支持者、推荐者）。"""
    # 1. 作为上传者的作品
    uploaded_works = (
        db.query(models.Work)
        .filter(models.Work.uploader_id == user.id)
        .order_by(models.Work.updated_at.desc())
        .all()
    )
    
    # 2. 作为支持者的作品
    supporter_works = (
        db.query(models.Work)
        .join(models.UserWork, models.UserWork.work_id == models.Work.id)
        .filter(
            models.UserWork.user_id == user.id,
            models.UserWork.relation_roles.contains(["supporter"]),
        )
        .order_by(models.Work.updated_at.desc())
        .all()
    )
    
    # 3. 作为推荐者的作品
    recommender_works = (
        db.query(models.Work)
        .join(models.UserWork, models.UserWork.work_id == models.Work.id)
        .filter(
            models.UserWork.user_id == user.id,
            models.UserWork.relation_roles.contains(["recommender"]),
        )
        .order_by(models.Work.updated_at.desc())
        .all()
    )
    
    def _compact_work(w: models.Work) -> Dict[str, Any]:
        return {
            "id": w.id,
            "title": w.title,
            "type": w.type,
            "author": w.author,
            "cover_url": public_url(w.cover_url) if w.cover_url else None,
            "updated_at": w.updated_at.isoformat() if w.updated_at else None,
        }
    
    return {
        "ok": True,
        "uploaded": [_compact_work(w) for w in uploaded_works],
        "supported": [_compact_work(w) for w in supporter_works],
        "recommended": [_compact_work(w) for w in recommender_works],
    }
