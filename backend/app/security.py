"""密码哈希、JWT、当前用户/当前 Bot 鉴权依赖。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import SessionLocal, get_db
from . import models


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


# ----------------------- 密码 -----------------------
# 使用 bcrypt 官方 Python 包直接操作；避免 passlib + bcrypt 5.x 的 __about__ 缺失问题。
# bcrypt 的哈希本身自带版本/盐/salt，只需要存一个 bytes/字符串即可。


def hash_password(password: str) -> str:
    """返回 str 类型的 bcrypt 哈希（$2b$...），UTF-8 编码。"""
    password_bytes = password.encode("utf-8")
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        # 旧版本可能存了奇怪编码等情况，直接返回 False，避免 500
        return False


# ----------------------- JWT -----------------------

def create_access_token(subject: str | int, settings: Settings, expires_delta: Optional[timedelta] = None) -> str:
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.jwt_access_token_expire_minutes)
    )
    to_encode = {"sub": str(subject), "exp": expire}
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, settings: Settings) -> Optional[int]:
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        sub = payload.get("sub")
        if sub is None:
            return None
        return int(sub)
    except (JWTError, ValueError):
        return None


# ----------------------- 白名单校验 -----------------------

def is_qq_in_group(qq: str, db: Session) -> bool:
    """返回该 QQ 是否存在至少一个 is_active=True 的 group_members 记录。"""
    if not qq:
        return False
    row = (
        db.query(models.GroupMember)
        .filter(models.GroupMember.qq == qq, models.GroupMember.is_active.is_(True))
        .first()
    )
    return row is not None


# ----------------------- 依赖：当前用户 -----------------------

def _promote_if_admin_qq(user_id: int, qq: str, current_role: str, settings: Settings) -> str:
    """根据 settings.admin_qq_set 判断是否需要把该用户升级为 admin。

    说明：这个函数**不操作任何 Session**，只是返回这个用户「应当具有的最终 role」。
    为了避免污染请求级事务（get_current_user 内部 commit/rollback 会影响 endpoint 的事务），
    真正写 DB 的提权动作改为用独立 Session 执行（见 _ensure_role_in_db）。

    返回：最终 role（仅会升级到 admin，不降权）。
    """
    if current_role == models.UserRole.ADMIN:
        return current_role
    if qq in settings.admin_qq_set:
        return models.UserRole.ADMIN
    return current_role


def _ensure_role_in_db(user_id: int, target_role: str) -> None:
    """用一次性独立 Session 写回 role（避免对请求主事务产生副作用）。"""
    with SessionLocal() as oneoff:
        row = oneoff.query(models.User).filter(models.User.id == user_id).first()
        if row is None or row.role == target_role:
            return
        row.role = target_role
        oneoff.commit()


def _auto_promote_check(user: models.User, settings: Settings) -> models.User:
    """给 get_current_user / login / register 使用的便捷入口：检查 + 必要时一次写入。"""
    target = _promote_if_admin_qq(user.id, user.qq, user.role, settings)
    if target != user.role:
        _ensure_role_in_db(user.id, target)
        user.role = target
    return user


def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> models.User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="未登录或登录已过期",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not token:
        raise credentials_exception
    user_id = decode_access_token(token, settings)
    if user_id is None:
        raise credentials_exception
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None:
        raise credentials_exception
    # 退群/被踢后白名单失效：已签发的 token 在下一次鉴权时立即拒绝（PRD §6.1）
    if not is_qq_in_group(user.qq, db):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="你已不在群内，登录态已失效，请重新加群后登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # 登录态下每一次都查一次 env 白名单，动态提权（不干扰当前请求事务）
    _auto_promote_check(user, settings)
    return user


def get_current_user_optional(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Optional[models.User]:
    """登录态可选，不抛异常。"""
    if not token:
        return None
    user_id = decode_access_token(token, settings)
    if user_id is None:
        return None
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None:
        return None
    # 退群后登录态同样失效：按匿名用户处理
    if not is_qq_in_group(user.qq, db):
        return None
    _auto_promote_check(user, settings)
    return user


def require_admin(
    user: models.User = Depends(get_current_user),
) -> models.User:
    """管理员专用依赖：role == 'admin' 才放行。前端据此显示「切换为管理界面」按钮。"""
    if user.role != models.UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return user


# ----------------------- 依赖：当前 Bot（X-Bot-Token 鉴权） -----------------------

class BotAuthenticated:
    """标记请求已经通过 Bot Token 校验。"""


def get_authenticated_bot(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> BotAuthenticated:
    """从 X-Bot-Token 请求头校验内部插件身份。"""
    token = request.headers.get("X-Bot-Token")
    if not token or token != settings.bot_api_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效或缺失 X-Bot-Token",
            headers={"WWW-Authenticate": "X-Bot-Token"},
        )
    return BotAuthenticated()
