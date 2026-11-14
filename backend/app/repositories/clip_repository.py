from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.clip import Clip, ClipRender
from app.models.enums import ClipStatus
from app.models.video import Video


class ClipRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_ready_for_user(self, user_id: UUID) -> list[Clip]:
        """READY clips across all of a user's videos — used by the schedule-post picker,
        since a clip can't be published until it has a finished render to upload."""
        return list(
            self.db.execute(
                select(Clip)
                .join(Video, Video.id == Clip.video_id)
                .where(Video.user_id == user_id, Clip.status == ClipStatus.READY)
                .order_by(Clip.created_at.desc())
            ).scalars()
        )

    def list_for_video(self, video_id: UUID) -> list[Clip]:
        return list(
            self.db.execute(
                select(Clip).where(Clip.video_id == video_id).order_by(Clip.score.desc())
            ).scalars()
        )

    def get_for_user(self, clip_id: UUID, user_id: UUID) -> Clip | None:
        return self.db.execute(
            select(Clip).join(Video, Video.id == Clip.video_id).where(Clip.id == clip_id, Video.user_id == user_id)
        ).scalar_one_or_none()

    def count_for_user(self, user_id: UUID) -> int:
        return self.db.execute(
            select(func.count(Clip.id)).join(Video, Video.id == Clip.video_id).where(Video.user_id == user_id)
        ).scalar_one()

    def update(self, clip: Clip) -> Clip:
        self.db.commit()
        self.db.refresh(clip)
        return clip

    def list_renders(self, clip_id: UUID) -> list[ClipRender]:
        return list(
            self.db.execute(
                select(ClipRender).where(ClipRender.clip_id == clip_id).order_by(ClipRender.created_at.desc())
            ).scalars()
        )

    def create_render(self, render: ClipRender) -> ClipRender:
        self.db.add(render)
        self.db.commit()
        self.db.refresh(render)
        return render

    def get_render_for_user(self, render_id: UUID, user_id: UUID) -> ClipRender | None:
        return self.db.execute(
            select(ClipRender)
            .join(Clip, Clip.id == ClipRender.clip_id)
            .join(Video, Video.id == Clip.video_id)
            .where(ClipRender.id == render_id, Video.user_id == user_id)
        ).scalar_one_or_none()
