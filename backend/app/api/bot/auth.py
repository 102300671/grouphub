"""/bot/auth/* —— nonebot2 插件（qqbot/plugins/auth_code.py）的内部回调端点。

端点：
  - POST /code-sent-ack    登录验证码下发成功 ack（MVP 占位）
  - POST /login-notify-ack 登录通知 ack（MVP 占位）
  - POST /verify-register  注册绑定码核销：验码 → QQ 加入白名单 → 绑定官方 openid
  - GET  /resolve-openid   openid → 真实 QQ 解析（官方通道群命令用）

所有路径都挂在 /bot/auth 前缀下（见 main.py），需要 X-Bot-Token 鉴权。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.api.auth import CODE_MAX_ATTEMPTS, _backfill_avatar, _code_attempts, _push_to_bot
from app.config import Settings, get_settings
from app.db import get_db
from app.models import utcnow
from app.security import BotAuthenticated, hash_password, get_authenticated_bot

router = APIRouter(dependencies=[Depends(get_authenticated_bot)])


@router.post("/code-sent-ack", response_model=schemas.SimpleMessageOut)
def code_sent_ack(
    payload: schemas.BotSendCodeIn,
    db: Session = Depends(get_db),
    _: BotAuthenticated = Depends(),
) -> schemas.SimpleMessageOut:
    """qqbot 成功下发验证码后可选回调（当前 MVP 仅 ack，未来可写 audit_logs）。"""
    return schemas.SimpleMessageOut(
        ok=True,
        message="ack",
        details={
            "qq": payload.qq,
            "code_prefix": payload.code[:2] + "****",
            "at": datetime.now(timezone.utc).isoformat(),
        },
    )


@router.post("/login-notify-ack", response_model=schemas.SimpleMessageOut)
def login_notify_ack(
    payload: schemas.BotNotifyLoginIn,
    db: Session = Depends(get_db),
    _: BotAuthenticated = Depends(),
) -> schemas.SimpleMessageOut:
    """qqbot 收到「用户已登录」通知后的可选回调（MVP 占位）。"""
    return schemas.SimpleMessageOut(
        ok=True,
        message="ack",
        details={"qq": payload.qq, "login_at": payload.login_at.isoformat()},
    )


# ------------------- 注册绑定码核销 -------------------

@router.post("/verify-register")
def verify_register(
    payload: schemas.BotVerifyRegisterIn,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    _: BotAuthenticated = Depends(),
) -> dict:
    """用户把注册/绑定码发给 bot → bot 核销。

    码来源两种（purpose 均可）：
      - register：站点注册流程发的码（新用户）
      - bind：老账号登录后补绑 openid 的码（OneBot 时代注册 / 「安利」自动建号）
    流程：校验码（未用、未过期；OneBot 通道校验 qq 一致）
    → QQ 加入群成员白名单（group_members upsert，is_active=True）
    → 绑定 QQ 官方 openid（qq_openid_bindings upsert，openid ↔ 真实 QQ）
    → 账号兜底建号（bind 码场景账号必然已存在）
    → 头像回填 + best-effort 触发 bot 拉取头像
    """
    now = utcnow()

    # 1. 找码：最新一条未使用的注册/绑定码
    vc = (
        db.query(models.VerificationCode)
        .filter(
            models.VerificationCode.purpose.in_(("register", "bind")),
            models.VerificationCode.code == payload.code,
            models.VerificationCode.used.is_(False),
        )
        .order_by(models.VerificationCode.created_at.desc())
        .first()
    )
    if vc is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="验证码错误或不存在")

    target_qq = vc.qq

    # 2. 防爆破：与登录验证码共用同一计数（同一 QQ 连续失败 5 次锁定）
    attempts = _code_attempts.get(target_qq, 0)
    if attempts >= CODE_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="验证码错误次数过多，请回站点重新获取",
        )

    def _reject(detail: str) -> None:
        _code_attempts[target_qq] = attempts + 1
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)

    if vc.expires_at < now:
        _reject("验证码已过期，请回站点重新获取")

    # OneBot 通道：事件自带真实 QQ，必须与发码 QQ 一致（防止用别人的码给自身 QQ 解锁白名单）
    if payload.qq is not None and payload.qq.strip() and payload.qq.strip() != target_qq:
        _reject("验证码与你的 QQ 不匹配")

    vc.used = True

    # 3. QQ 加入白名单（group_members upsert，is_active=True）
    group_id = (payload.group_id or "").strip() or "0"
    row = (
        db.query(models.GroupMember)
        .filter(models.GroupMember.qq == target_qq, models.GroupMember.group_id == group_id)
        .first()
    )
    if row is None:
        row = models.GroupMember(qq=target_qq, group_id=group_id)
        db.add(row)
    row.is_active = True
    if payload.nickname_in_group:
        row.nickname_in_group = payload.nickname_in_group
    row.last_synced_at = now

    # 4. 绑定 QQ 官方 openid（openid ↔ 真实 QQ）
    bound_openid = False
    openid = (payload.openid or "").strip()
    if openid:
        openid_type = payload.openid_type if payload.openid_type in ("group", "c2c") else "group"
        binding = (
            db.query(models.QQOpenidBinding)
            .filter(models.QQOpenidBinding.openid == openid)
            .first()
        )
        if binding is None:
            db.add(models.QQOpenidBinding(
                qq=target_qq, openid=openid, openid_type=openid_type,
                created_at=now, updated_at=now,
            ))
        else:
            binding.qq = target_qq
            binding.openid_type = openid_type
            binding.updated_at = now
        bound_openid = True

    # 5. 账号兜底建号（正常 /auth/register 已建；bind 码场景账号必然已存在）
    user = db.query(models.User).filter(models.User.qq == target_qq).first()
    account_existed = user is not None
    if user is None:
        user = models.User(
            qq=target_qq,
            password_hash=hash_password("pending-reset"),
            nickname=payload.nickname_in_group or f"群友{target_qq}",
            role=(
                models.UserRole.ADMIN if target_qq in settings.admin_qq_set else models.UserRole.MEMBER
            ),
        )
        db.add(user)
        db.flush()
    else:
        # 注册时昵称留空（占位「群友{qq}」）→ 用 bot 侧拿到的名称回填。
        # QQ 官方通道只有 QQ 用户名（拿不到群名片）；OneBot 通道是群名片/昵称。
        # 用户注册时手动填过昵称则不覆盖。
        if payload.nickname_in_group and (not user.nickname or user.nickname == f"群友{target_qq}"):
            user.nickname = payload.nickname_in_group

    # 6. 头像回填 + 触发 bot 拉取（bot 刚调过本接口，必然在线；失败降级）
    _backfill_avatar(db, user)
    db.commit()
    if not user.avatar_url:
        _push_to_bot(settings, "/bot/avatars/fetch", {"qq": target_qq})
        db.refresh(user)

    # 7. 验证成功，清风控计数
    _code_attempts.pop(target_qq, None)

    return {
        "ok": True,
        "qq": target_qq,
        "nickname": user.nickname,
        "group_id": group_id,
        "bound_openid": bound_openid,
        "account_existed": account_existed,
        "message": "验证通过：已加入白名单" + ("，并完成 openid 绑定" if bound_openid else ""),
    }


# ------------------- openid → 真实 QQ 解析 -------------------

@router.get("/resolve-openid", response_model=schemas.BotResolveOpenidOut)
def resolve_openid(
    openid: str = Query(..., min_length=8),
    openid_type: str = Query("group"),
    db: Session = Depends(get_db),
    _: BotAuthenticated = Depends(),
) -> schemas.BotResolveOpenidOut:
    """QQ 官方通道命令（如「安利」）需要把 member_openid/user_openid 解析回真实 QQ。"""
    if openid_type not in ("group", "c2c"):
        openid_type = "group"
    row = (
        db.query(models.QQOpenidBinding)
        .filter(
            models.QQOpenidBinding.openid == openid,
            models.QQOpenidBinding.openid_type == openid_type,
        )
        .first()
    )
    return schemas.BotResolveOpenidOut(ok=True, qq=row.qq if row else None)
