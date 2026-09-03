"""FastAPI 应用入口。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时建表、停站时收尾。"""
    init_db()
    yield


settings = get_settings()

app = FastAPI(
    title=f"{settings.site_name} —— 后端 API",
    version="0.1.0-mvp",
    description="私域群资源站（百合向）。详见 docs/PRD_群图书馆网站.md",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["通用"])
def health_check() -> dict:
    """最基础的健康检查。"""
    return {"ok": True, "site": settings.site_name}


# ------------------- 路由注册 -------------------

from .api.auth import router as auth_router  # noqa: E402
from .api.works import router as works_router  # noqa: E402
from .api.admin import router as admin_router  # noqa: E402
from .api.uploads import router as uploads_router  # noqa: E402
from .api.reviews import router as reviews_router  # noqa: E402
from .api.forum import router as forum_router  # noqa: E402
from .api.fanworks import router as fanworks_router  # noqa: E402
from .api.bot.members import router as bot_members_router  # noqa: E402
from .api.bot.auth import router as bot_auth_router  # noqa: E402
from .api.bot.works import router as bot_works_router  # noqa: E402

# 插件用内部 API，统一加 /bot 前缀
app.include_router(bot_members_router, prefix="/bot/members", tags=["[Bot 内部] 群成员白名单"])
app.include_router(bot_auth_router, prefix="/bot/auth", tags=["[Bot 内部] 验证码通道"])
app.include_router(bot_works_router, prefix="/bot/works", tags=["[Bot 内部] 作品（热门/搜索/安利）"])

# 用户侧 API
app.include_router(auth_router, prefix="/auth", tags=["认证"])
app.include_router(works_router, prefix="/works", tags=["作品库与阅读关系"])
app.include_router(uploads_router, prefix="/uploads", tags=["文件上传"])
app.include_router(reviews_router, prefix="/works", tags=["评论"])
app.include_router(forum_router, prefix="/topics", tags=["论坛"])
app.include_router(fanworks_router, prefix="/fanworks", tags=["同人创作"])

# 管理端 API（需 role=admin；前端通过 /auth/me.role 判断是否展示切换按钮）
app.include_router(admin_router, prefix="/admin", tags=["管理员"])
