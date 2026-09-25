"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-08

"""
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# create_type=False on every enum used as a column type below — each type is created exactly
# once, explicitly, in upgrade() via enum_type.create(bind). Without create_type=False, the
# PG dialect's own op.create_table() DDL compiler ALSO tries to CREATE TYPE for the first table
# that uses it, which fails with "type ... already exists" (confirmed against a live Postgres).
video_category = postgresql.ENUM("gaming", "podcast", name="video_category", create_type=False)
video_status = postgresql.ENUM(
    "UPLOADED", "QUEUED", "EXTRACTING_AUDIO", "TRANSCRIBING", "ANALYZING", "FINDING_HIGHLIGHTS",
    "GENERATING_CLIPS", "GENERATING_SUBTITLES", "FINALIZING", "COMPLETED", "FAILED",
    name="video_status", create_type=False,
)
job_status = postgresql.ENUM("PENDING", "RUNNING", "COMPLETED", "FAILED", name="job_status", create_type=False)
clip_status = postgresql.ENUM(
    "PENDING", "RENDERING", "READY", "FAILED", name="clip_status", create_type=False
)
aspect_ratio = postgresql.ENUM("9:16", "1:1", "16:9", name="aspect_ratio", create_type=False)
social_provider = postgresql.ENUM(
    "youtube", "tiktok", "instagram", name="social_provider", create_type=False
)
scheduled_post_status = postgresql.ENUM(
    "SCHEDULED", "PROCESSING", "UPLOADING", "PUBLISHED", "FAILED", "CANCELLED",
    name="scheduled_post_status", create_type=False,
)
publish_job_status = postgresql.ENUM(
    "PENDING", "RUNNING", "SUCCEEDED", "FAILED", name="publish_job_status", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in (
        video_category, video_status, job_status, clip_status,
        aspect_ratio, social_provider, scheduled_post_status, publish_job_status,
    ):
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "videos",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("category", video_category, nullable=False),
        sa.Column("original_filename", sa.String(500), nullable=False),
        sa.Column("storage_key", sa.String(1000), nullable=False),
        sa.Column("duration_seconds", sa.Float, nullable=True),
        sa.Column("file_size_bytes", sa.Integer, nullable=False),
        sa.Column("status", video_status, nullable=False, server_default="UPLOADED"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_videos_user_id", "videos", ["user_id"])

    op.create_table(
        "video_processing_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", job_status, nullable=False, server_default="PENDING"),
        sa.Column("current_stage", video_status, nullable=False, server_default="QUEUED"),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_video_processing_jobs_video_id", "video_processing_jobs", ["video_id"])

    op.create_table(
        "transcripts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("language", sa.String(20), nullable=True),
        sa.Column("full_text", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_transcripts_video_id", "transcripts", ["video_id"])

    op.create_table(
        "transcript_segments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("transcript_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("start_time", sa.Float, nullable=False),
        sa.Column("end_time", sa.Float, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
    )
    op.create_index("ix_transcript_segments_transcript_id", "transcript_segments", ["transcript_id"])

    op.create_table(
        "subtitle_presets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("config", sa.JSON, nullable=False),
        sa.Column("is_system_default", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "clips",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("caption", sa.Text, nullable=True),
        sa.Column("start_time", sa.Float, nullable=False),
        sa.Column("end_time", sa.Float, nullable=False),
        sa.Column("score", sa.Float, nullable=False, server_default="0"),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("category", video_category, nullable=False),
        sa.Column("confidence", sa.Float, nullable=False, server_default="0"),
        sa.Column("status", clip_status, nullable=False, server_default="PENDING"),
        sa.Column("storage_key", sa.String(1000), nullable=True),
        sa.Column("thumbnail_key", sa.String(1000), nullable=True),
        sa.Column("duration_seconds", sa.Float, nullable=False, server_default="0"),
        sa.Column("aspect_ratio", aspect_ratio, nullable=False, server_default="9:16"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_clips_video_id", "clips", ["video_id"])

    op.create_table(
        "clip_renders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("clip_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clips.id", ondelete="CASCADE"), nullable=False),
        sa.Column("aspect_ratio", aspect_ratio, nullable=False, server_default="9:16"),
        sa.Column("subtitle_preset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("subtitle_presets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("storage_key", sa.String(1000), nullable=True),
        sa.Column("status", clip_status, nullable=False, server_default="PENDING"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_clip_renders_clip_id", "clip_renders", ["clip_id"])

    op.create_table(
        "social_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", social_provider, nullable=False),
        sa.Column("account_name", sa.String(255), nullable=False),
        sa.Column("external_account_id", sa.String(255), nullable=False),
        sa.Column("encrypted_access_token", sa.Text, nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text, nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_social_accounts_user_id", "social_accounts", ["user_id"])

    op.create_table(
        "scheduled_posts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("clip_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clips.id", ondelete="CASCADE"), nullable=False),
        sa.Column("social_account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("social_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("platform", social_provider, nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", scheduled_post_status, nullable=False, server_default="SCHEDULED"),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("caption", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_scheduled_posts_clip_id", "scheduled_posts", ["clip_id"])
    op.create_index("ix_scheduled_posts_social_account_id", "scheduled_posts", ["social_account_id"])
    op.create_index("ix_scheduled_posts_scheduled_at", "scheduled_posts", ["scheduled_at"])

    op.create_table(
        "publish_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("scheduled_post_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("scheduled_posts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", publish_job_status, nullable=False, server_default="PENDING"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_publish_jobs_scheduled_post_id", "publish_jobs", ["scheduled_post_id"])

    # Seed default subtitle presets (Phase 2 applies these; the rows exist from day one).
    subtitle_presets_table = sa.table(
        "subtitle_presets",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.String),
        sa.column("config", sa.JSON),
        sa.column("is_system_default", sa.Boolean),
    )
    op.bulk_insert(
        subtitle_presets_table,
        [
            {"id": uuid.uuid4(), "name": "Classic", "config": {"font": "Inter", "size": 44, "position": "bottom", "color": "#FFFFFF", "highlight_color": "#FFD400"}, "is_system_default": True},
            {"id": uuid.uuid4(), "name": "Bold", "config": {"font": "Montserrat-Bold", "size": 56, "position": "center", "color": "#FFFFFF", "highlight_color": "#FF3B30"}, "is_system_default": False},
            {"id": uuid.uuid4(), "name": "Minimal", "config": {"font": "Inter", "size": 36, "position": "bottom", "color": "#FFFFFF", "highlight_color": "#FFFFFF"}, "is_system_default": False},
            {"id": uuid.uuid4(), "name": "Gaming", "config": {"font": "Bangers", "size": 60, "position": "middle-bottom", "color": "#FFFFFF", "highlight_color": "#39FF14"}, "is_system_default": False},
            {"id": uuid.uuid4(), "name": "Podcast", "config": {"font": "Poppins-SemiBold", "size": 42, "position": "bottom", "color": "#FFFFFF", "highlight_color": "#00C2FF"}, "is_system_default": False},
        ],
    )


def downgrade() -> None:
    op.drop_table("publish_jobs")
    op.drop_table("scheduled_posts")
    op.drop_table("social_accounts")
    op.drop_table("clip_renders")
    op.drop_table("clips")
    op.drop_table("subtitle_presets")
    op.drop_table("transcript_segments")
    op.drop_table("transcripts")
    op.drop_table("video_processing_jobs")
    op.drop_table("videos")
    op.drop_table("users")

    bind = op.get_bind()
    for enum_type in (
        publish_job_status, scheduled_post_status, social_provider,
        aspect_ratio, clip_status, job_status, video_status, video_category,
    ):
        enum_type.drop(bind, checkfirst=True)
