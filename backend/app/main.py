import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api import (
    auth,
    auto_clip,
    clips,
    dashboard,
    jobs,
    publishing,
    scheduled_posts,
    settings_api,
    social_accounts,
    subtitle_presets,
    videos,
)
from app.core.config import settings
from app.core.logging import configure_logging
from app.core.rate_limit import limiter

configure_logging(logging.DEBUG if settings.DEBUG else logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.services.storage.storage_service import storage_service

    try:
        storage_service.ensure_bucket()
    except Exception:
        logger.exception("Could not verify/create storage bucket on startup")
    yield


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"status": "error", "error_code": "INTERNAL_SERVER_ERROR", "message": "Something went wrong"},
    )


app.include_router(auth.router, prefix=settings.API_PREFIX)
app.include_router(auto_clip.router, prefix=settings.API_PREFIX)
app.include_router(videos.router, prefix=settings.API_PREFIX)
app.include_router(clips.router, prefix=settings.API_PREFIX)
app.include_router(jobs.router, prefix=settings.API_PREFIX)
app.include_router(dashboard.router, prefix=settings.API_PREFIX)
app.include_router(social_accounts.router, prefix=settings.API_PREFIX)
app.include_router(scheduled_posts.router, prefix=settings.API_PREFIX)
app.include_router(publishing.router, prefix=settings.API_PREFIX)
app.include_router(settings_api.router, prefix=settings.API_PREFIX)
app.include_router(subtitle_presets.router, prefix=settings.API_PREFIX)


@app.get("/health")
def health():
    return {"status": "ok"}
