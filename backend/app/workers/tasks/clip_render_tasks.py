import logging
import shutil
import tempfile
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models.clip import Clip, ClipRender
from app.models.enums import ClipStatus
from app.models.subtitle_preset import SubtitlePreset
from app.models.transcript import Transcript, TranscriptSegment
from app.models.video import Video
from app.services.ai.categories.registry import get_strategy
from app.services.storage.storage_service import storage_service
from app.services.video import pipeline, smart_crop
from app.services.video.exceptions import PipelineError
from app.services.video.subtitles import build_ass_subtitle, cues_from_segments
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _activate(db, clip: Clip, video: Video, render: ClipRender, local_path: str, tmpdir: str) -> None:
    """Makes `render` the clip's primary/shown version — regenerates the thumbnail from it (so
    the clip card never shows a thumbnail from a different subtitle state than the video it
    links to) and syncs clip.storage_key/thumbnail_key plus the clip's current subtitle config
    (subtitle_preset_id/font_size/position_x/position_y) to match this render. Keeping those in
    sync is what lets a later trim/aspect-ratio/crop-position change 'just keep' whatever
    subtitle state is currently showing, without having to re-derive it from render history."""
    thumb_local_path = str(Path(tmpdir) / "activate_thumb.jpg")
    try:
        pipeline.generate_thumbnail(local_path, thumb_local_path)
        thumb_key = f"clips/{video.user_id}/{video.id}/{clip.id}_thumb.jpg"
        storage_service.upload_file(thumb_local_path, thumb_key, content_type="image/jpeg")
        clip.thumbnail_key = thumb_key
    except PipelineError:
        logger.exception("Thumbnail regeneration failed while activating render for clip %s", clip.id)
        # Not fatal — keep the previous thumbnail rather than failing the whole activation.
    clip.storage_key = render.storage_key
    clip.subtitle_preset_id = render.subtitle_preset_id
    clip.subtitle_font_size = render.font_size
    clip.subtitle_position_x = render.position_x
    clip.subtitle_position_y = render.position_y
    db.commit()


@celery_app.task(bind=True, name="render_clip")
def render_clip_task(self, render_id: str) -> None:
    db = SessionLocal()
    tmpdir = tempfile.mkdtemp(prefix="autoclipper-render-")

    try:
        render = db.get(ClipRender, UUID(render_id))
        if render is None:
            logger.error("ClipRender %s not found", render_id)
            return
        clip = db.get(Clip, render.clip_id)
        if clip is None:
            logger.error("Clip %s not found for render %s", render.clip_id, render_id)
            return
        video = db.get(Video, clip.video_id)

        render.status = ClipStatus.RENDERING
        db.commit()

        # clip.storage_key may itself already have subtitles burned in (auto-subtitles on
        # upload) — always source from the plain (no-preset) render when one exists, or this
        # would burn a second subtitle track on top of the first.
        plain_render = db.execute(
            select(ClipRender)
            .where(ClipRender.clip_id == clip.id, ClipRender.subtitle_preset_id.is_(None), ClipRender.id != render.id)
            .order_by(ClipRender.created_at.asc())
        ).scalars().first()
        source_key = plain_render.storage_key if plain_render is not None else clip.storage_key

        clip_local_path = str(Path(tmpdir) / "clip.mp4")
        storage_service.download_to_file(source_key, clip_local_path)
        output_path = clip_local_path

        if render.subtitle_preset_id:
            preset = db.get(SubtitlePreset, render.subtitle_preset_id)
            segments = db.execute(
                select(TranscriptSegment)
                .join(Transcript, Transcript.id == TranscriptSegment.transcript_id)
                .where(
                    Transcript.video_id == video.id,
                    # Fully inside [start, end] only — a partial-overlap segment would show its
                    # whole sentence's text while the clip's padded edges only contain a sliver
                    # of that segment's actual audio, making the subtitle look out of sync.
                    TranscriptSegment.start_time >= clip.start_time,
                    TranscriptSegment.end_time <= clip.end_time,
                )
                .order_by(TranscriptSegment.start_time)
            ).scalars().all()

            cues = cues_from_segments(
                [(seg.start_time, seg.end_time, seg.text) for seg in segments], clip.start_time, clip.end_time
            )
            keywords = get_strategy(clip.category).keyword_list() if cues else []
            video_width, video_height = pipeline.get_video_dimensions(clip_local_path)
            ass_content = build_ass_subtitle(
                cues, preset.config, keywords, video_width, video_height,
                font_size_override=render.font_size, position_x_pct=render.position_x, position_y_pct=render.position_y,
            )
            ass_path = str(Path(tmpdir) / "subtitles.ass")
            Path(ass_path).write_text(ass_content, encoding="utf-8")

            output_path = str(Path(tmpdir) / "clip_subtitled.mp4")
            pipeline.burn_subtitles(clip_local_path, ass_path, output_path)

        render_key = f"clips/{video.user_id}/{video.id}/render_{render.id}.mp4"
        storage_service.upload_file(output_path, render_key, content_type="video/mp4")

        render.storage_key = render_key
        render.status = ClipStatus.READY
        db.commit()

        # A freshly created render becomes the clip's active/shown version — matches the
        # intuitive "I just made a new version, that's what I see now" expectation.
        _activate(db, clip, video, render, output_path, tmpdir)

    except Exception:
        logger.exception("Clip render failed for render %s", render_id)
        db.rollback()
        render = db.get(ClipRender, UUID(render_id))
        if render is not None:
            render.status = ClipStatus.FAILED
            db.commit()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        db.close()


@celery_app.task(bind=True, name="retrim_clip")
def retrim_clip_task(self, clip_id: str) -> None:
    """The general 'apply the clip's current edit state' task — re-cuts fresh from the original
    source video using whatever start_time/end_time/aspect_ratio/crop_x/crop_y/subtitle_* the
    clip row currently has, re-running smart-crop (or the manual crop override) and re-burning
    subtitles at their configured font size/position. Used by every editing action that changes
    the clip's shape or content (trim, aspect-ratio switch, crop-position override, and the
    unified apply-edits endpoint) — they all just update the relevant Clip columns and queue
    this same task, so it's always working from a fresh, single source of truth rather than
    guessing what changed. Replaces all of the clip's existing renders, since their video
    content no longer matches the new range/crop."""
    db = SessionLocal()
    tmpdir = tempfile.mkdtemp(prefix="autoclipper-retrim-")

    try:
        clip = db.get(Clip, UUID(clip_id))
        if clip is None:
            logger.error("Clip %s not found", clip_id)
            return
        video = db.get(Video, clip.video_id)
        if video is None or video.storage_key is None:
            raise PipelineError("SOURCE_MISSING", "Original video is no longer available")

        original_path = str(Path(tmpdir) / f"original{Path(video.storage_key).suffix or '.mp4'}")
        storage_service.download_to_file(video.storage_key, original_path)

        trimmed_path = str(Path(tmpdir) / "trimmed.mp4")
        pipeline.trim_clip(original_path, clip.start_time, clip.end_time, trimmed_path)

        target_w, target_h = pipeline.get_target_dimensions(clip.aspect_ratio.value)
        cropped_path = str(Path(tmpdir) / "cropped.mp4")
        if clip.crop_x is not None and clip.crop_y is not None:
            # Manual override — user picked this because smart-crop tracked the wrong subject,
            # so don't fall back to auto-tracking here even on failure.
            pipeline.crop_and_scale_positioned(trimmed_path, cropped_path, target_w, target_h, clip.crop_x, clip.crop_y)
        else:
            smart_crop.generate_smart_cropped_clip(trimmed_path, cropped_path, target_w, target_h)

        # The clip's own subtitle_preset_id/font_size/position_x/position_y are the single
        # source of truth for "what subtitle state should this clip have" — kept in sync by
        # _activate() whenever a render becomes primary, and set directly by the unified
        # apply-edits endpoint. Trim/aspect-ratio/crop-position changes don't touch these
        # fields, so re-reading them here is what makes those changes 'just keep' whatever
        # subtitle state was already showing.
        active_preset_id = clip.subtitle_preset_id
        active_font_size = clip.subtitle_font_size
        active_position_x = clip.subtitle_position_x
        active_position_y = clip.subtitle_position_y

        primary_local_path = cropped_path
        applied_preset_id = None

        if active_preset_id is not None:
            preset = db.get(SubtitlePreset, active_preset_id)
            if preset is not None:
                segments = db.execute(
                    select(TranscriptSegment)
                    .join(Transcript, Transcript.id == TranscriptSegment.transcript_id)
                    .where(
                        Transcript.video_id == video.id,
                        # Fully inside [start, end] only — see the same check in render_clip_task
                        # above for why partial-overlap segments would desync the subtitle.
                        TranscriptSegment.start_time >= clip.start_time,
                        TranscriptSegment.end_time <= clip.end_time,
                    )
                    .order_by(TranscriptSegment.start_time)
                ).scalars().all()
                cues = cues_from_segments(
                    [(seg.start_time, seg.end_time, seg.text) for seg in segments], clip.start_time, clip.end_time
                )
                if cues:
                    try:
                        keywords = get_strategy(clip.category).keyword_list()
                        video_width, video_height = pipeline.get_video_dimensions(cropped_path)
                        ass_content = build_ass_subtitle(
                            cues, preset.config, keywords, video_width, video_height,
                            font_size_override=active_font_size, position_x_pct=active_position_x,
                            position_y_pct=active_position_y,
                        )
                        ass_path = str(Path(tmpdir) / "subtitles.ass")
                        Path(ass_path).write_text(ass_content, encoding="utf-8")

                        subtitled_path = str(Path(tmpdir) / "subtitled.mp4")
                        pipeline.burn_subtitles(cropped_path, ass_path, subtitled_path)
                        primary_local_path = subtitled_path
                        applied_preset_id = preset.id
                    except PipelineError:
                        logger.exception(
                            "Subtitle re-burn failed while retrimming clip %s; using plain crop", clip.id
                        )

        thumb_local_path = str(Path(tmpdir) / "thumb.jpg")
        pipeline.generate_thumbnail(primary_local_path, thumb_local_path)

        plain_key = f"clips/{video.user_id}/{video.id}/{clip.id}_retrim_plain.mp4"
        storage_service.upload_file(cropped_path, plain_key, content_type="video/mp4")

        primary_key = plain_key
        if applied_preset_id is not None:
            primary_key = f"clips/{video.user_id}/{video.id}/{clip.id}_retrim_subtitled.mp4"
            storage_service.upload_file(primary_local_path, primary_key, content_type="video/mp4")

        thumb_key = f"clips/{video.user_id}/{video.id}/{clip.id}_thumb.jpg"
        storage_service.upload_file(thumb_local_path, thumb_key, content_type="image/jpeg")

        # Old renders point at video content outside the new [start, end] range — drop them
        # and start a fresh render set, the same shape the initial pipeline produces.
        for old_render in list(clip.renders):
            db.delete(old_render)
        db.flush()

        db.add(
            ClipRender(
                clip_id=clip.id, storage_key=plain_key, subtitle_preset_id=None,
                aspect_ratio=clip.aspect_ratio, status=ClipStatus.READY,
            )
        )
        if applied_preset_id is not None:
            db.add(
                ClipRender(
                    clip_id=clip.id, storage_key=primary_key, subtitle_preset_id=applied_preset_id,
                    font_size=active_font_size, position_x=active_position_x, position_y=active_position_y,
                    aspect_ratio=clip.aspect_ratio, status=ClipStatus.READY,
                )
            )

        clip.storage_key = primary_key
        clip.thumbnail_key = thumb_key
        clip.status = ClipStatus.READY
        db.commit()

    except PipelineError as exc:
        logger.exception("Retrim failed for clip %s: %s", clip_id, exc.code)
        db.rollback()
        clip = db.get(Clip, UUID(clip_id))
        if clip is not None:
            clip.status = ClipStatus.FAILED
            db.commit()
    except Exception:
        logger.exception("Unexpected error retrimming clip %s", clip_id)
        db.rollback()
        clip = db.get(Clip, UUID(clip_id))
        if clip is not None:
            clip.status = ClipStatus.FAILED
            db.commit()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        db.close()


@celery_app.task(bind=True, name="activate_clip_render")
def activate_clip_render_task(self, clip_id: str, render_id: str) -> None:
    """Switches the clip's primary/shown version to an EXISTING ready render — used for
    'remove subtitles' (activate the plain render) or 're-activate' an older render, without
    re-running FFmpeg to produce new video, just a thumbnail regenerate + pointer swap."""
    db = SessionLocal()
    tmpdir = tempfile.mkdtemp(prefix="autoclipper-activate-")

    try:
        clip = db.get(Clip, UUID(clip_id))
        render = db.get(ClipRender, UUID(render_id))
        if clip is None or render is None or render.clip_id != clip.id:
            logger.error("Invalid activate request: clip=%s render=%s", clip_id, render_id)
            return
        if render.status != ClipStatus.READY or render.storage_key is None:
            logger.error("Cannot activate render %s: not ready", render_id)
            return
        video = db.get(Video, clip.video_id)

        local_path = str(Path(tmpdir) / "render.mp4")
        storage_service.download_to_file(render.storage_key, local_path)
        _activate(db, clip, video, render, local_path, tmpdir)

    except Exception:
        logger.exception("Activating render %s for clip %s failed", render_id, clip_id)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        db.close()
