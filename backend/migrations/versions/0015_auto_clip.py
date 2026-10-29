"""daily auto-clip: settings, runs, used sources; per-video clip options; post -> run link

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-29

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

aspect_ratio = postgresql.ENUM("9:16", "1:1", "16:9", "3:4", "4:3", name="aspect_ratio", create_type=False)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.add_column("videos", sa.Column("clip_aspect_ratio", aspect_ratio, nullable=True))
    op.add_column("videos", sa.Column("auto_track", sa.Boolean(), nullable=False, server_default=sa.true()))

    op.create_table(
        "auto_clip_settings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("category", sa.String(30), nullable=False, server_default="podcast"),
        sa.Column("region", sa.String(2), nullable=False, server_default="ID"),
        sa.Column("run_time", sa.String(5), nullable=False, server_default="08:00"),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("max_clips", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("last_run_date", sa.Date(), nullable=True),
        *_timestamps(),
    )

    op.create_table(
        "auto_clip_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trigger", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="SELECTING"),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("category", sa.String(30), nullable=False),
        sa.Column("source_video_id", sa.String(20), nullable=True),
        sa.Column("source_url", sa.String(500), nullable=True),
        sa.Column("source_title", sa.String(500), nullable=True),
        sa.Column("video_id", sa.Uuid(), sa.ForeignKey("videos.id", ondelete="SET NULL"), nullable=True),
        sa.Column("remaining_candidates", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("publish_queue", sa.JSON(), nullable=False),
        sa.Column("clips_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_auto_clip_runs_user_id", "auto_clip_runs", ["user_id"])
    op.create_index("ix_auto_clip_runs_status", "auto_clip_runs", ["status"])

    op.create_table(
        "auto_clip_sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("youtube_video_id", sa.String(20), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("user_id", "youtube_video_id", name="uq_auto_clip_sources_user_video"),
    )
    op.create_index("ix_auto_clip_sources_user_id", "auto_clip_sources", ["user_id"])

    op.add_column(
        "scheduled_posts",
        sa.Column("auto_run_id", sa.Uuid(), sa.ForeignKey("auto_clip_runs.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_index("ix_scheduled_posts_auto_run_id", "scheduled_posts", ["auto_run_id"])


def downgrade() -> None:
    op.drop_index("ix_scheduled_posts_auto_run_id", table_name="scheduled_posts")
    op.drop_column("scheduled_posts", "auto_run_id")
    op.drop_table("auto_clip_sources")
    op.drop_table("auto_clip_runs")
    op.drop_table("auto_clip_settings")
    op.drop_column("videos", "auto_track")
    op.drop_column("videos", "clip_aspect_ratio")
