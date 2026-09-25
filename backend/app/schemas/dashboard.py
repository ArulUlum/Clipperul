from pydantic import BaseModel


class DashboardStatsResponse(BaseModel):
    total_videos: int
    total_clips: int
    scheduled_posts: int
    published_posts: int
    processing_jobs: int
