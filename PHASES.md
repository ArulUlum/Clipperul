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
- Clip titles are short, hook-style titles written by the LLM (`app/services/ai/title_service.py`):
  one call per video covers all its clips, in the transcript's own detected language, ≤60 chars, built
  from what's actually said in each clip (no invented facts, no generic "The speaker explains…"
  openers, distinct per clip). If that call fails or a title is unusable, the clip falls back to the
  old truncated-reason title (`_auto_title` in `app/workers/tasks/video_tasks.py`). The same call
  (`generate_clip_copy`) writes each clip's caption (the point of the clip + one question to the
  viewer, no "Pembicara menjelaskan…" narration), 3–5 topic hashtags (generic ones like #fyp/#viral are
  dropped) and one follow CTA per video; `_compose_caption` posts them as caption, CTA, then topic tags
  + the category tag + #shorts. Without an LLM caption the highlight 'reason' + the category's stock
  hashtags stand in, as before. Added after early analytics (Nov 2026) showed ~0 comments and very low
  follow conversion on every platform. Existing clips keep their old titles and captions.
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
- **TikTok and Instagram via Zernio** (`app/services/social/zernio.py`): with `ZERNIO_API_KEY` set,
  connecting them opens Zernio's hosted connect page instead of the native OAuth, and those accounts
  can publish. One Zernio *profile* per user (found by our user id as `externalId`, nothing stored);
  the connect redirect returns to `/api/social-accounts/zernio/callback`, which trusts only the signed
  `state` and reads the accounts back from Zernio (so a tampered URL can't attach someone else's
  account). Accounts are ordinary `social_accounts` rows with `broker="zernio"` (migration 0017),
  `external_account_id` = Zernio's account id and no token of ours, so scheduling, retries,
  cancelling and the auto clip are unchanged: `get_social_provider(provider, broker)` hands a
  brokered account to `ZernioProvider`. Publishing = presigned upload of the rendered clip (progress
  fills the bar to 90%) → `POST /posts` with `publishNow` → poll the post until the platform says
  published/failed (each poll is also the cancel checkpoint). TikTok posts are public with the
  consent flags Zernio requires set; Instagram posts are Reels shared to the feed. Disconnecting
  also deletes the account on Zernio (best effort). Not built: Zernio webhooks (polling is enough),
  idempotency keys (Zernio itself rejects identical content to the same account within 24 h), and
  TikTok's "draft" mode.

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
- Posting happens from the clip editor's **Post** tab (next to Details): pick one or more
  connected accounts, post now or schedule, edit the post title/caption, and follow the posts
  made from that clip (edit/cancel while still scheduled) — no page change needed after editing.
  A post can be cancelled at any point before it's published — while scheduled, preparing,
  waiting out an automatic retry, or mid-upload (the upload stops within one chunk). Cancelling
  also ends the automatic retrying; a failed or cancelled post can then be retried by hand
  (`POST /api/scheduled-posts/{id}/retry`). Each (re)dispatch mints a `run_token` the worker
  must still match, so a retry queued before a cancel can never fire later and double-post.
  Posting is blocked while edits are unsaved/rendering, since a post uploads the clip's last
  *rendered* file. There is no separate Scheduled Posts page; the Published page lists every
  post across clips (upcoming/in-progress, then history), with a link out to the platform post.
- Not built: a literal calendar-grid view (the list above is grouped by time, not a month/week
  grid) and per-platform publish analytics.

## Daily auto clip — done (opt-in, off by default)
Settings → "Daily auto clip". Once a day (at the user's chosen local time) it picks a trending YouTube
video for the chosen category/country, clips it, and posts the clips to the user's connected accounts.
- **Discovery** (`app/services/auto_clip/trending.py`): YouTube Data API "most popular" chart per
  category (Gaming, Entertainment, News, ...) or a view-sorted search for Podcast (YouTube has no
  podcast chart). Uses `YOUTUBE_API_KEY` if set, else the user's connected YouTube account's token.
  **News** merges YouTube's News & Politics chart with searches (`_NEWS_QUERIES`, per region, medium
  and long videos): speeches by senior officials, podcasts, and interviews / clarifications /
  influencer talk. They are ranked by views per hour since upload (`rank_by_velocity`, with a 6 h age
  floor) so what is hot *now* wins; a run costs about 650 API quota units (6 searches). Clips of it
  use `NewsCategoryStrategy` (`ai/categories/news.py`), which favors the country's direction,
  programs and policies, critical, candid or revealing statements and concrete numbers from whoever
  says them, and down-weights greetings and protocol. Migration 0019 adds `news` to the
  `video_category` enum.
  Skips live streams and anything outside 8–75 min; if a pick can't be processed it falls back to the
  next trending video (up to 3 per run).
- **Never the same link twice:** every picked video is recorded per user (`auto_clip_sources`, unique),
  and videos the user uploaded by URL themselves count as used too.
- **Output:** 1:1, auto-track (face tracking) off = fixed center crop, in the news layout — via
  per-video options `Video.clip_aspect_ratio` / `Video.auto_track` / `Video.clip_layout`, honored by
  `process_video`; the clips also store the center crop so later re-renders don't re-enable tracking.
- **News layout** (`app/services/video/layout.py`, migration 0020 adds `clips.layout` and
  `videos.clip_layout`): the crop scaled to 1080 wide and centered on a 1080x1920 canvas over a
  blurred, darkened copy of itself, the title as a headline (ASS, red box) bottom-anchored 40 px above
  the clip, subtitles defaulting to 110 px below it — clear of the feeds' own UI at the very top and
  bottom. One extra FFmpeg pass (~10 s for a 40 s clip). The composed video *is* the plain render, so
  subtitle presets, trims and crops work on it unchanged; a 9:16 crop is left alone. Since the title
  is burned in, a title change re-renders such a clip. The editor previews it (Ratio tab → Layout),
  mirroring the geometry in TypeScript.
- **Gaming layout** (`app/services/video/gaming_layout.py`, migration 0021 adds `clips.facecam_*`):
  Haar face detection at 1 fps on the trimmed clip, detections grouped by position, and the group seen
  in the most frames (at least a quarter of them) is the facecam. The canvas is split 40/60 with no
  margins: the face crop has the face area's shape (1080:768), 2.2x the face height tall, with the face
  in its upper third (a facecam's face is usually near its top, so a centered crop would reach past the
  cam into the game); the game crop has the game area's shape (1080:1152, nearly square), full height,
  centered or slid by `crop_x`. Both are scaled to full width and stacked (`vstack`) in one FFmpeg
  pass, from the uncropped frame; subtitles default to the seam (40%). The facecam is stored on the clip (re-used by later renders; the editor can move it
  or clear it to detect again); none found = `layout.effective()` renders the clip in the news layout.
  Uploads take a `clip_layout`; auto-clip runs in the Gaming category use this layout.
- **Publishing:** best-scoring clips first (capped by "max clips per day"), to every connected account
  that can publish (YouTube, and TikTok/Instagram via Zernio — `SocialMediaProvider.can_publish`),
  **one upload at a time per platform**: a platform's next upload starts only once its previous post
  is published/failed/cancelled, and then only after a random 60–90 min gap (`PUBLISH_GAP_*` in
  `service.py`). Platforms don't wait for each other, so a clip goes to YouTube, TikTok and Instagram
  together while each platform still sees spaced-out posts (5 clips take ~5 h, not 5 × accounts). The
  deadlines are stored per platform in `auto_clip_runs.next_publish_by_platform`, so restarts don't
  reset them (`next_publish_at`, shown in the UI, is the soonest of them). A platform's first upload
  of a run is immediate. No such account = clips are created and nothing is posted.
- **Engine:** `auto_clip_tick` (beat, every 60 s) moves every run one step from database state, so a
  restart or missed tick loses nothing. Runs stuck >48 h are stopped so they can't block later days.
  "Run now" starts a run immediately and counts as today's run. Turning the feature off stops scheduled
  runs in progress.
- Heads-up: YouTube limits how many videos a Cloud project can upload per day; and re-posting other
  people's videos can violate their copyright and YouTube's terms.

Extending a category or a social platform:
- New video category → add a `VideoCategoryStrategy` subclass in `backend/app/services/ai/categories/`
  and register it in `registry.py`.
- New social platform → add a `SocialMediaProvider` subclass in `backend/app/services/social/` and
  register it in `registry.py`.
