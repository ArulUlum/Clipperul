# Build phases

## Phase 1 — done
- Auth (register/login/JWT), per-user authorization on every resource
- Video upload — no category picker; category (Gaming / Podcast) is AI-detected from the
  transcript during processing (`DETECTING_CATEGORY` stage, `app/services/ai/category_detection.py`),
  with a keyword-based fallback if the LLM call fails. Pulls its option list from the
  `VideoCategoryStrategy` registry, so a newly registered category becomes a detection
  candidate automatically.
- Video upload from a YouTube/direct-file URL (yt-dlp, SSRF-validated, downloads in the worker)
- Queue-only processing (FastAPI never runs FFmpeg/Whisper inline — always via Celery)
- Pipeline: extract audio → Whisper transcript → AI category detection → category-specific AI
  highlight detection → clip generation (center-cropped to true 9:16, never letterboxed) →
  auto subtitle burn-in (opt-out checkbox at upload)
- AI provider abstraction: OpenAI, Anthropic, Gemini, and local Ollama — swap via `AI_LLM_PROVIDER` env only
- Realtime processing status via SSE
- Clip review UI: transcript viewer, clip grid, basic title/caption editing, download

## Phase 2 — partially done
- Done: subtitle burn-in via the seeded `SubtitlePreset` rows (Classic/Bold/Minimal/Gaming/Podcast).
  Auto-applied per clip during generation (category-matched preset, opt-out checkbox at upload) —
  the plain (no-subtitle) render is always kept alongside as a `ClipRender` fallback so switching
  presets later never double-burns. Manual re-render with a different preset (or none) via
  `POST /api/clips/{id}/render`, live-polled in the clip editor's "Subtitles" section.
  Known limitation: preset font names (Bangers, Montserrat-Bold, Poppins-SemiBold...) aren't bundled
  in the container — falls back to DejaVu Sans/Sans Bold, so styling is functional but not pixel-exact
  to the named font.
- Done: center-crop to true 9:16 (never letterboxed) baked into clip generation itself.
- Not implemented: face/subject-aware smart crop (current crop is always dead-center); trim/crop-
  position/aspect-ratio editing in the clip editor; AI-generated title/caption (Phase 1 uses a
  simple heuristic truncation instead)

## Phase 3 — done (Instagram gated off)
- OAuth connect flow implemented for `YouTubeProvider` (standard authorization-code flow) and
  `TikTokProvider` (PKCE) — token encryption at rest, refresh, and disconnect all wired up.
  `publish_video` is implemented for YouTube (resumable upload); TikTok's raises
  `NotImplementedError` pending TikTok's Content Posting API app review.
- `InstagramProvider` is fully implemented too (Instagram API with Instagram Login — no linked
  Facebook Page required) but is gated off in `_IMPLEMENTED_PROVIDERS`
  (`app/api/social_accounts.py`) and shows as "Coming soon" in the UI: it needs a Meta Developer
  account for `INSTAGRAM_APP_ID`/`INSTAGRAM_APP_SECRET`, which isn't available yet. Flip it back
  on by adding `SocialProvider.INSTAGRAM` to that set once those credentials exist.
- Accounts page lists connect/disconnect for every implemented provider.

## Phase 4 — done
- `POST/GET/PATCH/DELETE /api/scheduled-posts` — create (validates the clip is READY and the
  social account is owned by the caller), list, edit (only while still SCHEDULED), cancel
  (soft — flips to CANCELLED, row kept for history).
- Scheduler: a Celery-beat periodic task (`dispatch_due_scheduled_posts`, every 60s, see the
  `beat` service in docker-compose.yml) claims due `SCHEDULED` rows and hands each to
  `publish_scheduled_post`, which downloads the clip's active render and calls the provider's
  `publish_video`. Transient failures retry with backoff (1m/5m/15m/1h/6h, 5 attempts) via
  Celery's own retry/countdown; a provider that isn't implemented yet (TikTok, Instagram) fails
  immediately instead of burning retries on something that won't change. Status flows
  `SCHEDULED → PROCESSING → UPLOADING → PUBLISHED`, or `→ FAILED` / (pre-claim) `→ CANCELLED`.
- Scheduled Posts page: create form (clip + connected account + datetime + title/caption) and a
  live-polled list of upcoming posts with edit/cancel. Published page: history of
  published/failed/cancelled posts, with a link out to the platform post once published.
- Not built: a literal calendar-grid view (the list above is grouped by time, not a month/week
  grid) and per-platform publish analytics.

Extending a category or a social platform:
- New video category → add a `VideoCategoryStrategy` subclass in `backend/app/services/ai/categories/`
  and register it in `registry.py`.
- New social platform → add a `SocialMediaProvider` subclass in `backend/app/services/social/` and
  register it in `registry.py`.
