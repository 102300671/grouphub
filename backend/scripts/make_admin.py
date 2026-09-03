"""兜底脚本：直接把指定 QQ 升级为管理员（无需 env）。

用法（在 backend 目录下执行即可，自动使用 backend/.venv）：

    # 方案 A：用 backend 虚拟环境跑（推荐）
    cd /home/jianying/code/library/backend
    .venv/bin/python scripts/make_admin.py <QQ号> [QQ号2 ...]

    # 方案 B：撤销管理员（降回 member，前提是不能是最后一个 admin）
    .venv/bin/python scripts/make_admin.py --revoke <QQ号>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让脚本能 import app.*
BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app.db import SessionLocal, init_db  # noqa: E402
from app import models  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="把指定 QQ 升级为 / 降级出管理员")
    parser.add_argument("--revoke", action="store_true", help="改为降权（role=member）")
    parser.add_argument("qqs", nargs="+", help="目标 QQ 号（一个或多个）")
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        total_admin_before = db.query(models.User).filter(models.User.role == models.UserRole.ADMIN).count()
        for qq in args.qqs:
            u = db.query(models.User).filter(models.User.qq == qq).first()
            if u is None:
                print(f"[SKIP] QQ={qq}: 没有该用户（尚未注册或未进白名单）")
                continue
            target_role = models.UserRole.MEMBER if args.revoke else models.UserRole.ADMIN
            if u.role == target_role:
                print(f"[SKIP] QQ={qq}: 已经是 role={u.role}")
                continue
            if args.revoke:
                # 保护：不能把最后一个 admin 降掉
                left = (
                    db.query(models.User)
                    .filter(models.User.role == models.UserRole.ADMIN, models.User.id != u.id)
                    .count()
                )
                if left == 0:
                    print(f"[FAIL] QQ={qq}: 这是数据库里最后一个 admin，拒绝降权")
                    continue
            u.role = target_role
            print(f"[ OK ] QQ={qq}  nickname={u.nickname!r}  role: {u.role}")
        db.commit()
        total_admin_after = db.query(models.User).filter(models.User.role == models.UserRole.ADMIN).count()
        print(f"\nDone. admin 总数：{total_admin_before} → {total_admin_after}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
