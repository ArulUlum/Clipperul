# Clipperul

Clipperul turns long-form video into short, vertical clips and publishes them to social media.
Upload a video (or paste a YouTube link) and it transcribes the audio, finds the moments worth
clipping, cuts and crops them, burns in subtitles, and writes a short catchy title for each clip —
in the video's own language. You review and edit the clips, then post them from the same editor:
to one or several accounts, right now or on a schedule. It can also do the whole thing for you
once a day with the optional [daily auto clip](#daily-auto-clip-optional).

## What it does

- **Upload** a video file directly, or paste a YouTube / direct-file URL — no local download needed.
- **Transcribe & analyze**: Whisper transcribes the audio, then an LLM (OpenAI-compatible, Anthropic,
  Gemini, or a local Ollama model — your choice) detects the video's category (Gaming or Podcast)
  and scores candidate moments on hook, flow, value and trend. Categories are Gaming, Podcast and
  News — News is for whatever is being talked about right now (speeches by officials, podcasts,
  influencers) and picks the moments about where the country is heading, programs and policies,
  and critical, revealing or quotable statements, skipping ceremonial openings and host chatter.
- **Generate clips**: the best non-overlapping moments are cut and cropped to a vertical format
  (9:16 by default, 3:4 for widescreen sources; switchable to 1:1 or 4:3) with face-tracking smart
  crop, and get subtitles burned in using a category-matched preset.
- **Titles and captions in the video's language**: one LLM call per video writes, for every clip, a
  short hook-style title (around 60 characters), a caption that gives the point of the clip and ends
  with a question to the viewer (to draw comments), and 3–5 hashtags about the clip's own topic —
  plus one "follow for more" line shared by the video's clips. Everything is built only from what is
  said in the clip. An Indonesian video gets Indonesian titles and captions, and so on. Clips are
  steered toward 20–45 seconds, ending right after the payoff, since short clips that are watched to
  the end and replayed do best in short-form feeds.
- **News layout** (optional, per clip): the cropped 1:1 / 3:4 / 4:3 clip sits in the middle of a
  full-screen 9:16 video over a blurred copy of itself, with the title as a red headline just above
  it and the subtitles just below — so it fills a vertical feed instead of playing as a small box,
  and the hook is on screen from the first frame. Pick it in the editor's Ratio tab; the daily auto
  clip uses it. Changing the title of such a clip re-renders it, since the headline is burned in.
- **Review & edit** in a tabbed clip editor — **Trim, Ratio, Crop, Subtitles, Details, Post** — with
  a live, no-round-trip preview. Adjust anything, save once, and the clip is re-rendered.
- **Post from the editor** (the **Post** tab, right next to Details): pick one or more connected
  accounts, post now or schedule for later, and follow each post without leaving the page.
  - A **progress bar** shows the real upload percentage.
  - A post can be **cancelled at any point** before it's published — while scheduled, preparing,
    waiting for an automatic retry, or mid-upload. Cancelling also stops the automatic retrying.
  - Failed or cancelled posts can be **retried by hand**. Transient failures otherwise retry on
    their own with backoff.
  - Posting is blocked while you have unsaved edits, because a post uploads the last *rendered* file.
  - The **Published** page lists every post across clips: upcoming and in progress, then history.
- **Connect accounts**: YouTube connects with its own OAuth and publishes directly. **TikTok and
  Instagram connect and publish through [Zernio](https://zernio.com)** — a hosted posting API that
  uses its own approved apps, so you don't need a TikTok or Meta developer app. Set `ZERNIO_API_KEY`
  to enable it (see [below](#connecting-social-accounts-optional)); the native TikTok/Instagram
  adapters can connect accounts but can't post — see [PHASES.md](PHASES.md).

Everything heavy runs through a queue — the API never blocks on FFmpeg, Whisper, or a platform's own
upload; a Celery worker does the work and reports progress back over server-sent events.

## Tech stack

| Layer | Stack |
|---|---|
| Backend | FastAPI, SQLAlchemy 2.0, Alembic, PostgreSQL |
| Background jobs | Celery + Redis (worker + beat scheduler) |
| AI | OpenAI-compatible / Anthropic / Gemini / Ollama (pluggable), faster-whisper |
| Video | FFmpeg, OpenCV |
| Storage | S3-compatible object storage (MinIO in dev) |
| Frontend | Next.js 15, React 19, Tailwind CSS |
| Infra | Docker Compose |

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (runs everything below — no
  local Python/Node/FFmpeg/Postgres install needed)
- Access to **one** LLM: an API key from [OpenAI](https://platform.openai.com/api-keys),
  [Anthropic](https://console.anthropic.com/), or [Google AI Studio](https://aistudio.google.com/apikey)
  (Gemini); an OpenAI-compatible router/proxy; or [Ollama](https://ollama.com/) running locally if
  you'd rather not use a paid API

## Installation

1. **Clone the repo**

   ```bash
   git clone https://github.com/ArulUlum/Clipperul.git
   cd Clipperul
   ```

2. **Set up environment variables**

   ```bash
   cp .env.example .env
   ```

   Open `.env` and set `AI_LLM_PROVIDER` to `openai`, `anthropic`, `gemini`, or `ollama`, then fill in
   that provider's settings (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, or
   `OLLAMA_BASE_URL`/`OLLAMA_MODEL` for a local model). To use an OpenAI-compatible router or proxy
   instead of api.openai.com, also set `OPENAI_BASE_URL` and point `OPENAI_MODEL` at the model or
   combo name it exposes. Everything else has a working default for local development; social media
   OAuth (below) is optional and only needed to actually publish clips.

   `WHISPER_MODEL` (default `base`) trades accuracy for speed: larger models transcribe better but
   are much slower on CPU — a one-hour video can take a long time with `small` or above.

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
   processing completes; open one to trim, re-crop, change subtitles, or post it.

> **Changed `.env` later?** `docker compose restart` does *not* re-read it. Apply changes with
> `docker compose up -d`, which recreates every container whose environment changed. The worker runs
> its own copy of the settings, so it needs recreating too — `up -d` covers it.

### Connecting social accounts (optional)

Publishing needs OAuth credentials from the platform's own developer console. `.env.example` lists
the exact redirect URI each one expects, right above its variables.

- **YouTube** — create an OAuth client in the
  [Google Cloud Console](https://console.cloud.google.com/apis/credentials) (YouTube Data API v3
  enabled), register `http://localhost:8000/api/social-accounts/youtube/callback` as an authorized
  redirect URI, and set `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET`. Google allows `localhost`, so
  no tunnel is needed. While the OAuth consent screen is in *Testing* mode, add every Google account
  you'll connect as a **test user** (otherwise Google shows "Access blocked"), and expect to
  reconnect now and then — Google can expire testing-mode authorizations after about a week. The
  account needs a YouTube channel.
- **TikTok and Instagram (recommended: Zernio)** — create an account at [zernio.com](https://zernio.com),
  make an API key (Dashboard → API Keys), and set `ZERNIO_API_KEY`. "Connect TikTok" / "Connect
  Instagram" then opens Zernio's hosted connect page, and the accounts can publish from the Post tab
  and the daily auto clip. No developer app, tunnel or redirect URI is involved. Zernio's free plan
  covers the first 2 connected accounts; check its pricing for more. Things to know: a third party
  holds access to these accounts, TikTok posts go out public (Zernio's business-app connections can't
  post private videos), TikTok caps API posts at 15 per day per account, and Instagram needs a
  Business or Creator account. Remove an account in Clipperul and it's removed from Zernio too.
  Already connected accounts in Zernio's dashboard? Set `ZERNIO_PROFILE_ID` to the profile they're in
  (its "Default" profile) and "Connect" imports them instead of connecting them a second time.
- **TikTok (native, no posting)** — if you'd rather use your own app: create one at
  [developers.tiktok.com](https://developers.tiktok.com/) with Login Kit enabled, and set
  `TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET`. TikTok rejects `http://` redirect URIs even in
  development, so run a tunnel such as [ngrok](https://ngrok.com/) to `localhost:8000` and set
  `TIKTOK_REDIRECT_BASE_URL` to its `https://` address (register
  `<that address>/api/social-accounts/tiktok/callback` in the TikTok portal). This connects an
  account but can't post until TikTok approves the app's Content Posting API access. It's only used
  when `ZERNIO_API_KEY` is empty.
- **Instagram (native)** — the adapter is built but not offered: Meta only gives new apps its
  "unified login" setup, which it doesn't implement, and posting would need Meta's App Review.
  Without `ZERNIO_API_KEY`, "Connect Instagram" explains that Zernio is needed.

## Daily auto clip (optional)

**Settings → Daily auto clip.** Off by default. Once a day, at the time you choose (in your local
timezone), it:

1. picks a trending YouTube video for the category and country you selected (Podcast, Gaming,
   Entertainment, News, ...), skipping live streams and videos outside 8–75 minutes — and **never a
   video it has already used** (videos you uploaded yourself by link count as used too). **News**
   takes what's hot right now: YouTube's News & Politics chart plus searches of the last 72 hours
   for speeches by presidents and ministers, podcasts, and interviews / clarifications / influencer
   talk (Indonesian queries for `ID`, local-language or English ones elsewhere), and picks the one
   gaining the most views per hour;
2. clips it to **1:1 with auto-track off** (a fixed center crop) in the **news layout** (full-screen
   9:16 with the title as a headline);
3. posts the best clips to your connected accounts that can publish (YouTube, plus TikTok/Instagram
   connected through Zernio) — **one at a time per platform**, waiting a **random 60–90 minutes**
   between uploads to the same platform. Platforms go in parallel, so each clip appears on all of
   them at about the same time. If no connected account can post, the clips are still created and
   nothing is published.

It needs a connected YouTube account (its token is used to read the trending list) or a
`YOUTUBE_API_KEY`. "Run now" starts a run immediately and counts as that day's run. "Max clips per day"
caps how many clips are posted.

**Before you turn it on:** this downloads other people's videos and re-posts clips of them under your
accounts automatically. That can violate their copyright and YouTube's terms and may lead to claims,
strikes, or a terminated channel. Only use content you have the right to repost. Separately, YouTube
limits how many videos a Google Cloud project can upload per day — lower "Max clips per day" if
uploads start failing with a quota error.

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
    api/            FastAPI routers (auth, videos, clips, social-accounts, scheduled-posts, auto-clip, ...)
    models/         SQLAlchemy models
    schemas/        Pydantic request/response models
    services/
      ai/           transcription, LLM providers, highlight scoring, clip-title generation
      auto_clip/    trending-video discovery and the daily run engine
      social/       one adapter per platform (YouTube/TikTok/Instagram), behind a shared interface
      video/        FFmpeg pipeline (extract, crop, subtitle burn-in)
      storage/      S3/MinIO wrapper
    workers/        Celery app + tasks (processing pipeline, publishing, auto-clip tick)
  migrations/       Alembic migrations
  tests/            pytest
frontend/
  app/              Next.js App Router pages
  features/         page-level feature components (videos, clips, scheduled-posts, auto-clip, ...)
  components/       shared UI primitives
docker-compose.yml  postgres, redis, minio, backend, worker, beat, frontend, flower
```

## Roadmap

See [PHASES.md](PHASES.md) for a detailed breakdown of what's built vs. still scaffolded, and how
to extend Clipperul with a new video category or social platform.
