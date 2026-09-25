# Clipperul

Clipperul turns long-form video into short, vertical clips and publishes them straight to
social media. Upload a video (or paste a YouTube URL), and it transcribes the audio, finds the
moments worth clipping, cuts and crops them to 9:16, burns in subtitles, and lets you review,
edit, and schedule each clip out to YouTube, TikTok, and Instagram — including posting the same
clip to multiple accounts at once, or publishing immediately instead of scheduling.

## What it does

- **Upload** a video file directly, or paste a YouTube/direct-file URL — no local download needed.
- **Transcribe & analyze**: Whisper transcribes the audio, then an LLM (OpenAI, Anthropic, Gemini,
  or a local Ollama model — your choice) detects the video's category (Gaming or Podcast) and
  scores candidate moments against that category's own criteria (hook, flow, value, trend).
- **Generate clips**: the best non-overlapping moments are cut, center-cropped to a real 9:16 (or
  1:1, 3:4, 4:3, 16:9), and get subtitles burned in automatically using a category-matched preset.
- **Review & edit**: a tabbed clip editor (trim, aspect ratio, crop, subtitles, details) with a
  live, no-round-trip preview — adjust anything and re-render once, not per tweak.
- **Connect accounts**: OAuth to YouTube and TikTok (Instagram's adapter is built too, gated
  behind a Meta Developer account — see [PHASES.md](PHASES.md)).
- **Publish**: schedule a clip for later, publish to several accounts from one action, or post
  immediately. A background scheduler claims due posts, retries transient failures with backoff,
  and tracks each attempt's outcome per account.

Everything runs through a queue — the API never blocks on FFmpeg, Whisper, or a platform's own
upload; a Celery worker does the actual work and reports progress back over server-sent events.

## Tech stack

| Layer | Stack |
|---|---|
| Backend | FastAPI, SQLAlchemy 2.0, Alembic, PostgreSQL |
| Background jobs | Celery + Redis (worker + beat scheduler) |
| AI | OpenAI / Anthropic / Gemini / Ollama (pluggable), faster-whisper |
| Video | FFmpeg, OpenCV |
| Storage | S3-compatible object storage (MinIO in dev) |
| Frontend | Next.js 15, React 19, Tailwind CSS |
| Infra | Docker Compose |

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (runs everything below — no
  local Python/Node/FFmpeg/Postgres install needed)
- An API key from **one** AI provider: [OpenAI](https://platform.openai.com/api-keys),
  [Anthropic](https://console.anthropic.com/), or [Google AI Studio](https://aistudio.google.com/apikey)
  (Gemini) — or [Ollama](https://ollama.com/) running locally if you'd rather not use a paid API

## Installation

1. **Clone the repo**

   ```bash
   git clone https://github.com/<your-username>/clipperul.git
   cd clipperul
   ```

2. **Set up environment variables**

   ```bash
   cp .env.example .env
   ```

   Open `.env` and set `AI_LLM_PROVIDER` to `openai`, `anthropic`, `gemini`, or `ollama`, then
   fill in that provider's key (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, or
   `OLLAMA_BASE_URL`/`OLLAMA_MODEL` for a local model). Everything else has a working default for
   local development — social media OAuth (below) is optional and only needed if you want to
   actually publish clips.

3. **Start everything**

   ```bash
   docker compose up --build
   ```

   This builds and starts Postgres, Redis, MinIO (S3-compatible storage), the FastAPI backend
   (runs migrations automatically on boot), the Celery worker, the Celery beat scheduler, and the
   Next.js frontend.

4. **Open the app**

   | Service | URL |
   |---|---|
   | Frontend | http://localhost:3000 |
   | Backend API docs (Swagger) | http://localhost:8000/docs |
   | MinIO console | http://localhost:9001 (`STORAGE_ACCESS_KEY` / `STORAGE_SECRET_KEY` from `.env`) |

5. **Try it out** — register an account, upload a short video (or paste a YouTube URL), and watch
   the processing stages update live. Generated clips appear on the video's detail page once
   processing completes; open one to trim, re-crop, change subtitles, or download it.

### Connecting social accounts (optional)

Publishing to YouTube or TikTok needs OAuth credentials from that platform's own developer
console. `.env.example` has the exact redirect URI each one expects, right above its variables:

- **YouTube** — create an OAuth client at the
  [Google Cloud Console](https://console.cloud.google.com/apis/credentials), fill in
  `YOUTUBE_CLIENT_ID`/`YOUTUBE_CLIENT_SECRET`.
- **TikTok** — create an app at [developers.tiktok.com](https://developers.tiktok.com/) with
  Login Kit enabled, fill in `TIKTOK_CLIENT_KEY`/`TIKTOK_CLIENT_SECRET`. TikTok requires an
  `https://` redirect URI even in development — a tool like [ngrok](https://ngrok.com/) tunneling
  to `localhost:8000` works for local testing.

Both platforms only allow `localhost`/non-public redirect URIs for accounts you've explicitly
added as testers in their developer portal until the app passes their review process.

## Running tests

```bash
docker compose run --rm backend pytest
```

## Optional: Celery monitoring (Flower)

```bash
docker compose --profile monitoring up flower
```

Then open http://localhost:5555 to watch queued/running/failed background jobs.

## Project structure

```
backend/
  app/
    api/            FastAPI routers (auth, videos, clips, social-accounts, scheduled-posts, ...)
    models/         SQLAlchemy models
    schemas/        Pydantic request/response models
    services/
      ai/           transcription + LLM providers, per-category highlight-scoring strategies
      social/       one adapter per platform (YouTube/TikTok/Instagram), behind a shared interface
      video/        FFmpeg pipeline (extract, crop, subtitle burn-in)
      storage/      S3/MinIO wrapper
    workers/        Celery app + background tasks (processing pipeline, publishing, scheduler)
  migrations/       Alembic migrations
  tests/            pytest
frontend/
  app/              Next.js App Router pages
  features/         page-level feature components (videos, clips, social-accounts, scheduled-posts, ...)
  components/       shared UI primitives
docker-compose.yml  postgres, redis, minio, backend, worker, beat, frontend, flower
```

## Roadmap

See [PHASES.md](PHASES.md) for a detailed breakdown of what's built vs. still scaffolded, and
how to extend Clipperul with a new video category or social platform.
