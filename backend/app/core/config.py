from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    APP_NAME: str = "Clipperul"
    ENV: Literal["development", "production", "test"] = "development"
    DEBUG: bool = True
    API_PREFIX: str = "/api"
    # Origin the frontend is served from — used to build the URL we redirect the browser back
    # to once an OAuth flow finishes (success or error).
    FRONTEND_URL: str = "http://localhost:3000"
    # Origin THIS backend is reachable at from the browser — used to build the OAuth
    # redirect_uri we send to each platform. Must exactly match (scheme+host+port+path) what's
    # registered in that platform's developer console, or the platform rejects the request.
    BACKEND_BASE_URL: str = "http://localhost:8000"

    # Security
    JWT_SECRET: str = "change-me-in-env"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    TOKEN_ENCRYPTION_KEY: str = "change-me-32-byte-fernet-key-please"

    # Database
    DATABASE_URL: str = "postgresql+psycopg://autoclipper:autoclipper@postgres:5432/autoclipper"

    # Redis / Celery
    REDIS_URL: str = "redis://redis:6379/0"
    CELERY_BROKER_URL: str = "redis://redis:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/1"

    # Storage (S3-compatible / MinIO)
    STORAGE_ENDPOINT_URL: str = "http://minio:9000"
    STORAGE_PUBLIC_ENDPOINT_URL: str = "http://localhost:9000"
    STORAGE_ACCESS_KEY: str = "autoclipper"
    STORAGE_SECRET_KEY: str = "autoclipper123"
    STORAGE_BUCKET: str = "autoclipper"
    STORAGE_REGION: str = "us-east-1"
    STORAGE_USE_SSL: bool = False
    SIGNED_URL_EXPIRE_SECONDS: int = 3600

    # Upload limits
    MAX_UPLOAD_SIZE_BYTES: int = 5 * 1024 * 1024 * 1024  # 5 GB
    ALLOWED_VIDEO_EXTENSIONS: set[str] = {".mp4", ".mov", ".mkv", ".webm", ".avi"}

    # AI providers
    AI_LLM_PROVIDER: Literal["openai", "anthropic", "ollama", "gemini"] = "openai"
    OPENAI_API_KEY: str | None = None
    OPENAI_MODEL: str = "gpt-4o-mini"
    # Optional: point the OpenAI client at any OpenAI-compatible endpoint (a router/proxy such as
    # 9router, a self-hosted gateway...). Unset = the official OpenAI API.
    OPENAI_BASE_URL: str | None = None
    ANTHROPIC_API_KEY: str | None = None
    ANTHROPIC_MODEL: str = "claude-sonnet-5"
    # Ollama running on the Windows host (outside Docker) — Docker Desktop resolves
    # host.docker.internal to the host automatically, no extra compose networking needed.
    OLLAMA_BASE_URL: str = "http://host.docker.internal:11434"
    OLLAMA_MODEL: str = "llama3.1"
    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-3.6-flash"
    WHISPER_MODEL: str = "base"
    WHISPER_DEVICE: str = "cpu"
    WHISPER_COMPUTE_TYPE: str = "int8"

    # Highlight detection
    MAX_CLIPS_PER_VIDEO: int = 8
    MIN_CLIP_DURATION_SECONDS: float = 15.0
    # Raised from 90s (1.5min) to 180s (3min) — short clips were sometimes cutting off the
    # actual hype/payoff moment before it landed.
    MAX_CLIP_DURATION_SECONDS: float = 180.0
    CLIP_PADDING_SECONDS: float = 3.0
    # The LLM is told to score every candidate honestly (including low scores) rather than
    # unilaterally deciding what's "worth" including — this threshold does the actual
    # filtering, so weak candidates don't become clips just because the model didn't omit them.
    MIN_HIGHLIGHT_SCORE: float = 40.0
    # How many candidate windows go into a single LLM scoring call. Smaller/local models
    # (Ollama) lose track partway through a long list and start recycling the same generic
    # reason across several windows instead of describing each one specifically — lowering
    # this (e.g. to 5-8) trades more LLM calls (slower) for each call having fewer windows to
    # juggle (more specific, less repetitive output). Cloud models handle the default fine.
    HIGHLIGHT_CANDIDATES_PER_LLM_CALL: int = 20

    # Social account OAuth (Phase 3) — from Google Cloud Console > APIs & Services >
    # Credentials, on an OAuth client whose "Authorized redirect URIs" includes
    # {BACKEND_BASE_URL}{API_PREFIX}/social-accounts/youtube/callback.
    YOUTUBE_CLIENT_ID: str | None = None
    YOUTUBE_CLIENT_SECRET: str | None = None
    # Optional API key (Google Cloud Console > Credentials > API key, YouTube Data API v3 enabled)
    # used by the daily auto-clip to read YouTube's "most popular" charts. Unset = it falls back
    # to the access token of the user's connected YouTube account (which has read access).
    YOUTUBE_API_KEY: str | None = None
    # From TikTok for Developers (developers.tiktok.com) > your app > Login Kit, with the
    # redirect URI registered as {TIKTOK_REDIRECT_BASE_URL or BACKEND_BASE_URL}{API_PREFIX}/
    # social-accounts/tiktok/callback. Note TikTok calls the app identifier a "Client Key", not
    # "Client ID".
    TIKTOK_CLIENT_KEY: str | None = None
    TIKTOK_CLIENT_SECRET: str | None = None
    # Optional: origin used ONLY for TikTok's redirect URI. TikTok requires https://, so local dev
    # sets this to an ngrok URL while BACKEND_BASE_URL stays http://localhost:8000 for
    # YouTube (Google accepts localhost). Unset = TikTok also uses BACKEND_BASE_URL.
    TIKTOK_REDIRECT_BASE_URL: str | None = None
    # From developers.facebook.com > your app > Instagram > API setup with Instagram business
    # login. Redirect URI must be registered as exactly
    # {BACKEND_BASE_URL}{API_PREFIX}/social-accounts/instagram/callback. Meta calls these
    # "Instagram app ID" / "Instagram app secret" (distinct from the app's Facebook App ID).
    INSTAGRAM_APP_ID: str | None = None
    INSTAGRAM_APP_SECRET: str | None = None

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    # Rate limiting
    RATE_LIMIT_AUTH: str = "10/minute"
    RATE_LIMIT_UPLOAD: str = "5/minute"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
