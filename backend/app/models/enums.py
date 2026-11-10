import enum


class VideoCategory(str, enum.Enum):
    GAMING = "gaming"
    PODCAST = "podcast"
    NEWS = "news"


class VideoStatus(str, enum.Enum):
    UPLOADED = "UPLOADED"
    QUEUED = "QUEUED"
    DOWNLOADING = "DOWNLOADING"
    EXTRACTING_AUDIO = "EXTRACTING_AUDIO"
    TRANSCRIBING = "TRANSCRIBING"
    DETECTING_CATEGORY = "DETECTING_CATEGORY"
    ANALYZING = "ANALYZING"
    FINDING_HIGHLIGHTS = "FINDING_HIGHLIGHTS"
    GENERATING_CLIPS = "GENERATING_CLIPS"
    GENERATING_SUBTITLES = "GENERATING_SUBTITLES"
    FINALIZING = "FINALIZING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class JobStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ClipStatus(str, enum.Enum):
    PENDING = "PENDING"
    RENDERING = "RENDERING"
    READY = "READY"
    FAILED = "FAILED"


class AspectRatio(str, enum.Enum):
    VERTICAL_9_16 = "9:16"
    SQUARE_1_1 = "1:1"
    PORTRAIT_3_4 = "3:4"
    LANDSCAPE_4_3 = "4:3"
    HORIZONTAL_16_9 = "16:9"


class SocialProvider(str, enum.Enum):
    YOUTUBE = "youtube"
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"


class ScheduledPostStatus(str, enum.Enum):
    SCHEDULED = "SCHEDULED"
    PROCESSING = "PROCESSING"
    UPLOADING = "UPLOADING"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class PublishJobStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
