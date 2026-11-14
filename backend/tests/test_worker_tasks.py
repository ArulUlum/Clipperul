"""The bodies of the worker tasks that create clips, run for real with FFmpeg, Whisper, the LLM, storage and Redis
replaced by fakes: what they write to the database and storage, and how they fail."""
import json
import uuid

import pytest

from app.core.security import hash_password
from app.models.clip import Clip, ClipRender
from app.models.enums import ClipStatus, JobStatus, VideoCategory, VideoStatus
from app.models.subtitle_preset import SubtitlePreset
from app.models.transcript import Transcript, TranscriptSegment
from app.models.user import User
from app.models.video import Video
from app.models.video_processing_job import VideoProcessingJob
from app.services.ai.highlight_service import SelectedHighlight
from app.services.ai.title_service import ClipCopy, VideoCopy
from app.services.ai.transcription import TranscriptionResult, TranscriptSegmentResult
from app.services.video.analysis import SceneAnalysis
from app.services.video.exceptions import PipelineError
from app.workers.tasks import clip_render_tasks, video_tasks

SEGMENTS = [
    TranscriptSegmentResult(start=0.0, end=10.0, text="Hello world"),
    TranscriptSegmentResult(start=10.0, end=20.0, text="Second sentence here"),
    TranscriptSegmentResult(start=40.0, end=50.0, text="Another point"),
]
PRESET = {"font": "Inter", "size": 44, "position": "bottom", "color": "#FFFFFF", "highlight_color": "#FFD400"}


def _highlight(start, end, score, reason):
    return SelectedHighlight(
        start=start, end=end, raw_start=start, raw_end=end, text="spoken text", score=score, reason=reason,
        category=VideoCategory.PODCAST, confidence=0.8, hook_score=70.0,
    )


# --------------------------------------------------------------------------- process_video

@pytest.fixture()
def pipeline(db_session, mocker):
    """process_video with every external dependency faked. Returns the fakes the tests inspect."""
    user = User(email=f"w-{uuid.uuid4().hex[:6]}@example.com", hashed_password=hash_password("supersecret123"))
    db_session.add(user)
    db_session.commit()
    video = Video(
        user_id=user.id, title="V", original_filename="v.mp4", storage_key=f"videos/{user.id}/v/original.mp4",
        file_size_bytes=1, category=VideoCategory.PODCAST, status=VideoStatus.UPLOADED,
    )
    db_session.add(video)
    db_session.commit()
    job = VideoProcessingJob(video_id=video.id, status=JobStatus.PENDING, current_stage=VideoStatus.QUEUED)
    db_session.add(job)
    db_session.commit()

    mocker.patch.object(video_tasks, "SessionLocal", return_value=db_session)
    storage = mocker.patch.object(video_tasks, "storage_service")
    events = mocker.patch.object(video_tasks, "publish_video_status")
    mocker.patch.object(video_tasks.pipeline, "get_video_duration", return_value=120.0)
    mocker.patch.object(video_tasks.pipeline, "get_video_dimensions", return_value=(1920, 1080))
    for name in ("extract_audio", "trim_clip", "generate_thumbnail", "burn_subtitles"):
        mocker.patch.object(video_tasks.pipeline, name)
    mocker.patch.object(video_tasks.smart_crop, "generate_smart_cropped_clip")
    mocker.patch.object(video_tasks, "compute_audio_energy", return_value=[])
    mocker.patch.object(video_tasks, "analyze_scenes", return_value=SceneAnalysis(points=[], completed=True, analyzed_until=119.0))
    mocker.patch.object(video_tasks, "get_llm_provider")
    transcriber = mocker.MagicMock()
    transcriber.transcribe.return_value = TranscriptionResult(language="en", full_text="x", segments=SEGMENTS)
    mocker.patch.object(video_tasks, "get_transcription_provider", return_value=transcriber)
    mocker.patch.object(
        video_tasks, "find_and_select_highlights",
        return_value=[_highlight(0.0, 22.0, 90.0, "First: hello world and a second sentence."), _highlight(38.0, 52.0, 70.0, "Second: another point.")],
    )
    mocker.patch.object(
        video_tasks, "generate_clip_copy",
        return_value=VideoCopy(
            clips={0: ClipCopy(title="Hook title", caption="Why it matters. Agree?", hashtags=("#topic",))}, cta="Follow for more",
        ),
    )
    return type("Fakes", (), {"user_id": user.id, "video_id": video.id, "job_id": job.id, "storage": storage, "events": events, "transcriber": transcriber})


def _run(fakes):
    video_tasks.process_video.run(str(fakes.job_id))


def test_a_processed_video_ends_up_with_a_transcript_clips_renders_and_uploaded_files(db_session, pipeline):
    _run(pipeline)

    video = db_session.get(Video, pipeline.video_id)
    job = db_session.get(VideoProcessingJob, pipeline.job_id)
    assert video.status == VideoStatus.COMPLETED
    assert job.status == JobStatus.COMPLETED and job.completed_at is not None

    transcript = db_session.query(Transcript).one()
    assert transcript.language == "en"
    assert [s.text for s in db_session.query(TranscriptSegment).order_by(TranscriptSegment.start_time)] == [
        "Hello world", "Second sentence here", "Another point",
    ]

    clips = db_session.query(Clip).order_by(Clip.score.desc()).all()
    assert [c.score for c in clips] == [90.0, 70.0]
    assert clips[0].title == "Hook title"  # from the LLM copy
    assert clips[0].caption == "Why it matters. Agree?\n\nFollow for more\n\n#topic #podcast #shorts"
    assert clips[1].title.startswith("Second: another point")  # no copy for this one: falls back to the reason
    assert all(c.status == ClipStatus.READY and c.category == VideoCategory.PODCAST for c in clips)
    assert db_session.query(ClipRender).count() == 2  # no subtitle preset exists: one (plain) render per clip

    uploaded = {call.args[1] for call in pipeline.storage.upload_file.call_args_list}
    prefix = f"clips/{pipeline.user_id}/{pipeline.video_id}"
    assert {f"{prefix}/clip_0.mp4", f"{prefix}/clip_0.jpg", f"{prefix}/clip_1.mp4", f"{prefix}/clip_1.jpg"} <= uploaded
    assert {c.storage_key for c in clips} == {f"{prefix}/clip_0.mp4", f"{prefix}/clip_1.mp4"}


def test_progress_events_are_published_for_every_stage_and_end_with_completed(db_session, pipeline):
    _run(pipeline)
    stages = [json.loads(call.args[1])["stage"] for call in pipeline.events.call_args_list]
    assert stages[0] == "EXTRACTING_AUDIO" and stages[-1] == "COMPLETED"
    assert {"TRANSCRIBING", "ANALYZING", "FINDING_HIGHLIGHTS", "GENERATING_CLIPS"} <= set(stages)


def test_with_a_subtitle_preset_each_clip_gets_a_subtitled_and_a_plain_render(db_session, pipeline):
    db_session.add(SubtitlePreset(name="Podcast", config=PRESET, is_system_default=True))
    db_session.commit()

    _run(pipeline)

    assert db_session.query(ClipRender).count() == 4
    clip = db_session.query(Clip).order_by(Clip.score.desc()).first()
    assert clip.subtitle_preset_id is not None
    plain = {r.storage_key for r in db_session.query(ClipRender).filter(ClipRender.subtitle_preset_id.is_(None))}
    assert plain == {f"clips/{pipeline.user_id}/{pipeline.video_id}/clip_0_plain.mp4", f"clips/{pipeline.user_id}/{pipeline.video_id}/clip_1_plain.mp4"}
    assert video_tasks.pipeline.burn_subtitles.call_count == 2


def test_no_highlights_fails_the_video_with_a_clear_code_and_creates_no_clips(db_session, pipeline, mocker):
    mocker.patch.object(video_tasks, "find_and_select_highlights", return_value=[])

    _run(pipeline)

    video, job = db_session.get(Video, pipeline.video_id), db_session.get(VideoProcessingJob, pipeline.job_id)
    assert video.status == VideoStatus.FAILED and job.status == JobStatus.FAILED
    assert job.error_code == "NO_HIGHLIGHTS_FOUND"
    assert db_session.query(Clip).count() == 0


def test_a_failing_pipeline_step_fails_the_video_with_that_steps_code(db_session, pipeline, mocker):
    mocker.patch.object(video_tasks.pipeline, "extract_audio", side_effect=PipelineError("AUDIO_EXTRACTION_FAILED", "no audio stream"))

    _run(pipeline)

    job = db_session.get(VideoProcessingJob, pipeline.job_id)
    assert (job.error_code, job.error_message) == ("AUDIO_EXTRACTION_FAILED", "no audio stream")
    assert db_session.get(Video, pipeline.video_id).status == VideoStatus.FAILED
    pipeline.transcriber.transcribe.assert_not_called()  # nothing after the failing step ran


def test_an_unexpected_error_fails_the_video_without_leaking_its_details(db_session, pipeline):
    pipeline.transcriber.transcribe.side_effect = RuntimeError("secret internal detail: /app/private/path")

    _run(pipeline)

    job = db_session.get(VideoProcessingJob, pipeline.job_id)
    assert job.error_code == "UNKNOWN_ERROR"
    assert "secret internal detail" not in job.error_message
    assert db_session.get(Video, pipeline.video_id).status == VideoStatus.FAILED


def test_incomplete_scene_analysis_is_passed_on_as_unknown_not_as_no_change(db_session, pipeline, mocker):
    mocker.patch.object(video_tasks, "analyze_scenes", return_value=SceneAnalysis(points=[], completed=False, timed_out=True, analyzed_until=60.0))
    select = video_tasks.find_and_select_highlights

    _run(pipeline)

    analysis = select.call_args.args[0]
    assert analysis.scene_analysis_complete is False and analysis.scene_analyzed_until == 60.0


# --------------------------------------------------------------------------- render_clip_task

@pytest.fixture()
def render_setup(db_session, mocker):
    user = User(email=f"r-{uuid.uuid4().hex[:6]}@example.com", hashed_password=hash_password("supersecret123"))
    db_session.add(user)
    db_session.commit()
    video = Video(user_id=user.id, title="V", original_filename="v.mp4", storage_key="videos/v.mp4", file_size_bytes=1, category=VideoCategory.PODCAST)
    db_session.add(video)
    db_session.commit()
    transcript = Transcript(video_id=video.id, language="en", full_text="x")
    db_session.add(transcript)
    db_session.commit()
    for s in SEGMENTS:
        db_session.add(TranscriptSegment(transcript_id=transcript.id, start_time=s.start, end_time=s.end, text=s.text))
    clip = Clip(
        video_id=video.id, title="c", start_time=0.0, end_time=22.0, score=1, category=VideoCategory.PODCAST, status=ClipStatus.READY,
        storage_key="clips/active_subtitled.mp4", thumbnail_key="clips/old.jpg", duration_seconds=22.0,
    )
    db_session.add(clip)
    preset = SubtitlePreset(name="Bold", config=PRESET)
    db_session.add(preset)
    db_session.commit()
    db_session.add(ClipRender(clip_id=clip.id, storage_key="clips/plain.mp4", status=ClipStatus.READY))
    db_session.commit()

    mocker.patch.object(clip_render_tasks, "SessionLocal", return_value=db_session)
    storage = mocker.patch.object(clip_render_tasks, "storage_service")
    for name in ("burn_subtitles", "generate_thumbnail"):
        mocker.patch.object(clip_render_tasks.pipeline, name)
    mocker.patch.object(clip_render_tasks.pipeline, "get_video_dimensions", return_value=(1080, 1080))
    return type("Setup", (), {"user_id": user.id, "video": video.id, "clip": clip.id, "preset": preset.id, "storage": storage})


def _new_render(db_session, setup, preset=True):
    render = ClipRender(clip_id=setup.clip, subtitle_preset_id=setup.preset if preset else None, status=ClipStatus.PENDING)
    db_session.add(render)
    db_session.commit()
    return render.id


def test_a_subtitle_render_burns_the_cues_onto_the_plain_render_and_becomes_the_active_file(db_session, render_setup):
    render_id = _new_render(db_session, render_setup)

    clip_render_tasks.render_clip_task.run(str(render_id))

    # sourced from the PLAIN render: burning onto the already-subtitled active file would double the subtitles
    assert render_setup.storage.download_to_file.call_args.args[0] == "clips/plain.mp4"
    clip_render_tasks.pipeline.burn_subtitles.assert_called_once()
    key = f"clips/{render_setup.user_id}/{render_setup.video}/render_{render_id}.mp4"
    render, clip = db_session.get(ClipRender, render_id), db_session.get(Clip, render_setup.clip)
    assert render.status == ClipStatus.READY and render.storage_key == key
    assert clip.storage_key == key and str(clip.subtitle_preset_id) == str(render_setup.preset)  # activated
    assert key in {c.args[1] for c in render_setup.storage.upload_file.call_args_list}


def test_a_render_without_a_preset_skips_subtitles(db_session, render_setup):
    render_id = _new_render(db_session, render_setup, preset=False)

    clip_render_tasks.render_clip_task.run(str(render_id))

    clip_render_tasks.pipeline.burn_subtitles.assert_not_called()
    assert db_session.get(ClipRender, render_id).status == ClipStatus.READY


def test_a_failed_render_is_marked_failed_and_leaves_the_clip_as_it_was(db_session, render_setup):
    render_id = _new_render(db_session, render_setup)
    render_setup.storage.download_to_file.side_effect = RuntimeError("storage down")

    clip_render_tasks.render_clip_task.run(str(render_id))

    assert db_session.get(ClipRender, render_id).status == ClipStatus.FAILED
    assert db_session.get(Clip, render_setup.clip).storage_key == "clips/active_subtitled.mp4"


# --------------------------------------------------------------------------- retrim_clip_task failure

def test_a_failed_re_cut_marks_the_clip_failed_and_keeps_its_files(db_session, render_setup, mocker):
    mocker.patch.object(clip_render_tasks.pipeline, "trim_clip", side_effect=PipelineError("CLIP_TRIM_FAILED", "bad range"))
    renders_before = db_session.query(ClipRender).count()

    clip_render_tasks.retrim_clip_task.run(str(render_setup.clip))

    assert db_session.get(Clip, render_setup.clip).status == ClipStatus.FAILED
    assert db_session.query(ClipRender).count() == renders_before
    render_setup.storage.delete.assert_not_called()  # nothing is removed when the new render never existed
