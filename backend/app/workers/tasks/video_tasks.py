import json
import logging
import tempfile
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.redis import publish_video_status
from app.models.clip import Clip, ClipRender
from app.models.enums import AspectRatio, ClipStatus, JobStatus, VideoCategory, VideoStatus
from app.models.subtitle_preset import SubtitlePreset
from app.models.transcript import Transcript, TranscriptSegment
from app.models.video import Video
from app.models.video_processing_job import VideoProcessingJob
from app.services.ai.categories.registry import get_strategy
from app.services.ai.category_detection import detect_video_category
from app.services.ai.glossary import correct_transcription
from app.services.ai.highlight_service import SelectedHighlight, find_and_select_highlights
from app.services.ai.llm import get_llm_provider
from app.services.ai.transcription import get_transcription_provider
from app.services.storage.storage_service import storage_service
from app.services.video import pipeline, smart_crop
from app.services.video.analysis import compute_audio_energy, detect_scene_changes
from app.services.video.analysis_types import VideoAnalysisInput
from app.services.video.downloader import download_video_from_url
from app.services.video.exceptions import PipelineError
from app.services.video.subtitles import build_ass_subtitle, cues_from_segments
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

_CATEGORY_HASHTAGS = {
    "gaming": "#gaming #shorts #gamingclips",
    "podcast": "#podcast #shorts #podcastclips",
}

_CATEGORY_PRESET_NAMES = {
    VideoCategory.GAMING: "Gaming",
    VideoCategory.PODCAST: "Podcast",
}


def _get_default_subtitle_preset(db, category: VideoCategory) -> SubtitlePreset | None:
    preset_name = _CATEGORY_PRESET_NAMES.get(category)
    if preset_name:
        preset = db.execute(select(SubtitlePreset).where(SubtitlePreset.name == preset_name)).scalar_one_or_none()
        if preset is not None:
            return preset
    return db.execute(select(SubtitlePreset).where(SubtitlePreset.is_system_default.is_(True))).scalar_one_or_none()


def _emit_status(
    video: Video,
    job: VideoProcessingJob,
    db,
    *,
    stage: VideoStatus,
    job_status: JobStatus,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    video.status = stage
    job.current_stage = stage
    job.status = job_status
    job.error_code = error_code
    job.error_message = error_message
    db.commit()

    publish_video_status(
        str(video.id),
        json.dumps(
            {
                "video_id": str(video.id),
                "job_id": str(job.id),
                "stage": stage.value,
                "job_status": job_status.value,
                "error_code": error_code,
                "error_message": error_message,
            }
        ),
    )


def _auto_title(text: str, reason: str, index: int) -> str:
    source = (reason or text or "").strip()
    if not source:
        return f"Highlight #{index + 1}"
    title = source[:70].strip()
    return title[0].upper() + title[1:] if title else f"Highlight #{index + 1}"


def _auto_caption(reason: str, category: str) -> str:
    hashtags = _CATEGORY_HASHTAGS.get(category, "#shorts")
    reason = (reason or "").strip()
    return f"{reason}\n\n{hashtags}".strip()


_SIXTEEN_NINE_RATIO = 16 / 9
_SIXTEEN_NINE_TOLERANCE = 0.05


def _default_clip_aspect_ratio(original_path: str) -> AspectRatio:
    """Widescreen (~16:9) source videos default their clips to 3:4 instead of 9:16 — a less
    aggressive crop that keeps more of the original frame, since 9:16 throws away over half the
    width of a genuinely 16:9 source. Any other source shape keeps the 9:16 default."""
    try:
        src_w, src_h = pipeline.get_video_dimensions(original_path)
    except PipelineError:
        return AspectRatio.VERTICAL_9_16
    if src_h <= 0:
        return AspectRatio.VERTICAL_9_16
    if abs((src_w / src_h) - _SIXTEEN_NINE_RATIO) < _SIXTEEN_NINE_TOLERANCE:
        return AspectRatio.PORTRAIT_3_4
    return AspectRatio.VERTICAL_9_16


@celery_app.task(bind=True, name="process_video")
def process_video(self, job_id: str) -> None:
    db = SessionLocal()
    tmpdir = tempfile.mkdtemp(prefix="autoclipper-")

    try:
        job = db.get(VideoProcessingJob, UUID(job_id))
        if job is None:
            logger.error("VideoProcessingJob %s not found", job_id)
            return
        video = db.get(Video, job.video_id)
        if video is None:
            logger.error("Video %s not found for job %s", job.video_id, job_id)
            return

        # Derive the local extension from storage_key (not original_filename — for URL-sourced
        # videos that's the source URL itself, not a filename).
        original_path = str(Path(tmpdir) / f"original{Path(video.storage_key).suffix or '.mp4'}")
        audio_path = str(Path(tmpdir) / "audio.wav")

        if video.source_url:
            _emit_status(video, job, db, stage=VideoStatus.DOWNLOADING, job_status=JobStatus.RUNNING)
            download_video_from_url(video.source_url, original_path)
            video.file_size_bytes = Path(original_path).stat().st_size
            storage_service.upload_file(original_path, video.storage_key, content_type="video/mp4")
            db.commit()
        else:
            storage_service.download_to_file(video.storage_key, original_path)

        try:
            video.duration_seconds = pipeline.get_video_duration(original_path)
            db.commit()
        except PipelineError:
            pass  # non-fatal; duration can stay null

        _emit_status(video, job, db, stage=VideoStatus.EXTRACTING_AUDIO, job_status=JobStatus.RUNNING)
        pipeline.extract_audio(original_path, audio_path)

        _emit_status(video, job, db, stage=VideoStatus.TRANSCRIBING, job_status=JobStatus.RUNNING)
        transcription = correct_transcription(get_transcription_provider().transcribe(audio_path))

        transcript = Transcript(video_id=video.id, language=transcription.language, full_text=transcription.full_text)
        db.add(transcript)
        db.flush()
        for seg in transcription.segments:
            db.add(TranscriptSegment(transcript_id=transcript.id, start_time=seg.start, end_time=seg.end, text=seg.text))
        db.commit()

        if video.category is None:
            _emit_status(video, job, db, stage=VideoStatus.DETECTING_CATEGORY, job_status=JobStatus.RUNNING)
            video.category = detect_video_category(transcription.full_text, get_llm_provider())
            db.commit()

        _emit_status(video, job, db, stage=VideoStatus.ANALYZING, job_status=JobStatus.RUNNING)
        audio_energy = compute_audio_energy(audio_path)
        try:
            scene_changes = detect_scene_changes(original_path)
        except Exception:
            # Scene-change rate is one heuristic signal among several — losing it (e.g. an
            # undecodable codec) shouldn't fail the whole video, just weaken that one signal.
            logger.exception("Scene detection failed for video %s; continuing without it", video.id)
            scene_changes = []
        analysis = VideoAnalysisInput(
            video_duration=video.duration_seconds or 0.0,
            transcript_segments=transcription.segments,
            audio_energy=audio_energy,
            scene_changes=scene_changes,
            language=transcription.language,
        )

        _emit_status(video, job, db, stage=VideoStatus.FINDING_HIGHLIGHTS, job_status=JobStatus.RUNNING)
        highlights: list[SelectedHighlight] = find_and_select_highlights(analysis, video.category)
        if not highlights:
            raise PipelineError("NO_HIGHLIGHTS_FOUND", "AI could not find any highlight-worthy moments")

        _emit_status(video, job, db, stage=VideoStatus.GENERATING_CLIPS, job_status=JobStatus.RUNNING)
        default_preset = _get_default_subtitle_preset(db, video.category) if video.subtitles_enabled else None
        keywords = get_strategy(video.category).keyword_list() if default_preset is not None else []
        default_aspect_ratio = video.clip_aspect_ratio or _default_clip_aspect_ratio(original_path)

        for index, highlight in enumerate(highlights):
            trimmed_local_path = str(Path(tmpdir) / f"clip_{index}_trimmed.mp4")
            clip_local_path = str(Path(tmpdir) / f"clip_{index}.mp4")
            thumb_local_path = str(Path(tmpdir) / f"clip_{index}.jpg")

            pipeline.trim_clip(original_path, highlight.start, highlight.end, trimmed_local_path)
            target_w, target_h = pipeline.get_target_dimensions(default_aspect_ratio.value)
            if video.auto_track:
                smart_crop.generate_smart_cropped_clip(trimmed_local_path, clip_local_path, target_w, target_h)
            else:
                # Auto-track off: a fixed center crop. The clip also stores it as a manual crop
                # position below, so later re-renders (trim/ratio edits) don't switch tracking
                # back on.
                pipeline.crop_and_scale_positioned(
                    trimmed_local_path, clip_local_path, target_w, target_h, 50.0, 50.0
                )

            # Auto-subtitles: burn the category's default preset into the clip that becomes
            # the primary/downloadable version, but keep the plain cut around as a fallback
            # render so users can switch back (or pick a different preset) without reprocessing.
            primary_local_path = clip_local_path
            applied_preset_id = None

            if default_preset is not None:
                cues = cues_from_segments(
                    [(seg.start, seg.end, seg.text) for seg in transcription.segments],
                    highlight.start, highlight.end,
                )
                if cues:
                    try:
                        video_width, video_height = pipeline.get_video_dimensions(clip_local_path)
                        ass_content = build_ass_subtitle(cues, default_preset.config, keywords, video_width, video_height)
                        ass_path = str(Path(tmpdir) / f"clip_{index}.ass")
                        Path(ass_path).write_text(ass_content, encoding="utf-8")

                        subtitled_local_path = str(Path(tmpdir) / f"clip_{index}_subtitled.mp4")
                        pipeline.burn_subtitles(clip_local_path, ass_path, subtitled_local_path)
                        primary_local_path = subtitled_local_path
                        applied_preset_id = default_preset.id
                    except PipelineError:
                        logger.exception(
                            "Auto subtitle burn failed for clip %s of video %s; using plain clip", index, video.id
                        )

            pipeline.generate_thumbnail(primary_local_path, thumb_local_path)

            clip_key = f"clips/{video.user_id}/{video.id}/clip_{index}.mp4"
            thumb_key = f"clips/{video.user_id}/{video.id}/clip_{index}.jpg"
            storage_service.upload_file(primary_local_path, clip_key, content_type="video/mp4")
            storage_service.upload_file(thumb_local_path, thumb_key, content_type="image/jpeg")

            clip = Clip(
                video_id=video.id,
                title=_auto_title(highlight.text, highlight.reason, index),
                caption=_auto_caption(highlight.reason, video.category.value),
                start_time=highlight.start,
                end_time=highlight.end,
                score=highlight.score,
                hook_score=highlight.hook_score,
                flow_score=highlight.flow_score,
                value_score=highlight.value_score,
                trend_score=highlight.trend_score,
                reason=highlight.reason,
                category=video.category,
                confidence=highlight.confidence,
                status=ClipStatus.READY,
                storage_key=clip_key,
                thumbnail_key=thumb_key,
                duration_seconds=highlight.end - highlight.start,
                aspect_ratio=default_aspect_ratio,
                crop_x=None if video.auto_track else 50.0,
                crop_y=None if video.auto_track else 50.0,
                subtitle_preset_id=applied_preset_id,
            )
            db.add(clip)
            db.flush()
            db.add(
                ClipRender(
                    clip_id=clip.id, storage_key=clip_key, subtitle_preset_id=applied_preset_id,
                    aspect_ratio=default_aspect_ratio, status=ClipStatus.READY,
                )
            )

            if applied_preset_id is not None:
                plain_key = f"clips/{video.user_id}/{video.id}/clip_{index}_plain.mp4"
                storage_service.upload_file(clip_local_path, plain_key, content_type="video/mp4")
                db.add(
                    ClipRender(
                        clip_id=clip.id, storage_key=plain_key, subtitle_preset_id=None,
                        aspect_ratio=default_aspect_ratio, status=ClipStatus.READY,
                    )
                )
        db.commit()

        _emit_status(video, job, db, stage=VideoStatus.COMPLETED, job_status=JobStatus.COMPLETED)
        from datetime import datetime, timezone

        job.completed_at = datetime.now(timezone.utc)
        db.commit()

    except PipelineError as exc:
        logger.exception("Video processing failed for job %s: %s", job_id, exc.code)
        db.rollback()
        job = db.get(VideoProcessingJob, UUID(job_id))
        video = db.get(Video, job.video_id) if job else None
        if job and video:
            _emit_status(
                video, job, db,
                stage=VideoStatus.FAILED, job_status=JobStatus.FAILED,
                error_code=exc.code, error_message=exc.message,
            )
    except Exception:
        logger.exception("Unexpected error processing job %s", job_id)
        db.rollback()
        job = db.get(VideoProcessingJob, UUID(job_id))
        video = db.get(Video, job.video_id) if job else None
        if job and video:
            _emit_status(
                video, job, db,
                stage=VideoStatus.FAILED, job_status=JobStatus.FAILED,
                error_code="UNKNOWN_ERROR", error_message="An unexpected error occurred while processing this video",
            )
    finally:
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)
        db.close()
