"""管理员运行时设置：DB(admin_settings) 持久化 + 内存(settings 单例)同步。

- 启动时 load_runtime_settings() 把库中值覆盖到 get_settings() 单例；
- 管理后台 set_runtime_setting() 同时写库与内存，重启不丢；
- 未在库中配置时沿用 .env / 内置默认。
"""
from __future__ import annotations

from typing import Any, Callable, Dict

from sqlalchemy.orm import Session

from . import models
from .config import Settings, get_settings

# key -> (settings 属性名, 类型转换函数)
RUNTIME_KEYS: Dict[str, tuple[str, Callable[[str], Any]]] = {
    "show_relation_threshold": ("show_relation_threshold", int),
    "works_require_review": ("works_require_review", lambda v: v == "1"),
}


def _coerce(attr: str, value: str) -> Any:
    for _key, spec in RUNTIME_KEYS.items():
        if spec[0] == attr:
            return spec[1](value)
    raise KeyError(attr)


def load_runtime_settings(db: Session, settings: Settings | None = None) -> None:
    """启动时调用：把 admin_settings 中的值应用到 settings 单例。"""
    settings = settings or get_settings()
    rows = db.query(models.AdminSetting).all()
    for row in rows:
        spec = RUNTIME_KEYS.get(row.key)
        if spec is None:
            continue
        try:
            setattr(settings, spec[0], spec[1](row.value))
        except (ValueError, TypeError):
            continue


def get_runtime_values(settings: Settings | None = None) -> Dict[str, Any]:
    """供 API 返回当前生效值。"""
    settings = settings or get_settings()
    return {key: getattr(settings, spec[0]) for key, spec in RUNTIME_KEYS.items()}


def set_runtime_setting(db: Session, attr: str, value: Any) -> Any:
    """持久化并同步到内存。attr 必须在 RUNTIME_KEYS 中，否则 KeyError。"""
    key = next((k for k, spec in RUNTIME_KEYS.items() if spec[0] == attr), None)
    if key is None:
        raise KeyError(attr)

    settings = get_settings()
    # 按目标类型校验/规整（bool 特判：直接接受 bool）
    expected_type = type(getattr(settings, attr))
    if expected_type is bool:
        value = bool(value)
        stored = "1" if value else "0"
    else:
        value = expected_type(value)
        stored = str(value)

    row = db.query(models.AdminSetting).filter(models.AdminSetting.key == key).first()
    if row is None:
        row = models.AdminSetting(key=key, value=stored)
        db.add(row)
    else:
        row.value = stored
    db.commit()

    setattr(settings, attr, value)
    return value
