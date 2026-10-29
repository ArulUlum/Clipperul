export type VideoCategory = "gaming" | "podcast";

export type VideoStatus =
  | "UPLOADED"
  | "QUEUED"
  | "DOWNLOADING"
  | "EXTRACTING_AUDIO"
  | "TRANSCRIBING"
  | "DETECTING_CATEGORY"
  | "ANALYZING"
  | "FINDING_HIGHLIGHTS"
  | "GENERATING_CLIPS"
  | "GENERATING_SUBTITLES"
  | "FINALIZING"
  | "COMPLETED"
  | "FAILED";

export const PROCESSING_STAGES: VideoStatus[] = [
  "QUEUED",
  "DOWNLOADING",
  "EXTRACTING_AUDIO",
  "TRANSCRIBING",
  "DETECTING_CATEGORY",
  "ANALYZING",
  "FINDING_HIGHLIGHTS",
  "GENERATING_CLIPS",
  "COMPLETED",
];

export const STAGE_LABELS: Record<VideoStatus, string> = {
  UPLOADED: "Uploaded",
  QUEUED: "Queued",
  DOWNLOADING: "Downloading video",
  EXTRACTING_AUDIO: "Extracting audio",
  TRANSCRIBING: "Transcribing",
  DETECTING_CATEGORY: "Detecting category",
  ANALYZING: "Analyzing",
  FINDING_HIGHLIGHTS: "Finding highlights",
  GENERATING_CLIPS: "Generating clips",
  GENERATING_SUBTITLES: "Generating subtitles",
  FINALIZING: "Finalizing",
  COMPLETED: "Completed",
  FAILED: "Failed",
};

export interface User {
  id: string;
  email: string;
  full_name: string | null;
}

export interface Video {
  id: string;
  title: string;
  category: VideoCategory | null;
  status: VideoStatus;
  original_filename: string;
  source_url: string | null;
  subtitles_enabled: boolean;
  duration_seconds: number | null;
  file_size_bytes: number;
  created_at: string;
  updated_at: string;
}

export interface VideoDetail extends Video {
  current_stage: VideoStatus | null;
  error_code: string | null;
  error_message: string | null;
}

export interface TranscriptSegment {
  start_time: number;
  end_time: number;
  text: string;
}

export interface Transcript {
  language: string | null;
  full_text: string;
  segments: TranscriptSegment[];
}

export type ClipStatus = "PENDING" | "RENDERING" | "READY" | "FAILED";
export type AspectRatio = "9:16" | "1:1" | "3:4" | "4:3" | "16:9";

export interface Clip {
  id: string;
  video_id: string;
  title: string;
  caption: string | null;
  start_time: number;
  end_time: number;
  duration_seconds: number;
  score: number;
  hook_score: number | null;
  flow_score: number | null;
  value_score: number | null;
  trend_score: number | null;
  reason: string | null;
  category: VideoCategory;
  confidence: number;
  status: ClipStatus;
  aspect_ratio: AspectRatio;
  crop_x: number | null;
  crop_y: number | null;
  subtitle_preset_id: string | null;
  subtitle_font_size: number | null;
  subtitle_position_x: number | null;
  subtitle_position_y: number | null;
  thumbnail_url: string | null;
  created_at: string;
}

export interface SubtitlePresetConfig {
  font: string;
  size: number;
  position: string;
  color: string;
  highlight_color: string;
}

export interface SubtitlePreset {
  id: string;
  name: string;
  config: SubtitlePresetConfig;
  is_system_default: boolean;
}

export interface ClipRender {
  id: string;
  clip_id: string;
  aspect_ratio: AspectRatio;
  subtitle_preset_id: string | null;
  font_size: number | null;
  position_x: number | null;
  position_y: number | null;
  status: ClipStatus;
  video_url: string | null;
  is_active: boolean;
  created_at: string;
}

export type SocialProvider = "youtube" | "tiktok" | "instagram";

export interface SocialAccount {
  id: string;
  provider: SocialProvider;
  account_name: string;
  connected_at: string | null;
}

export type ScheduledPostStatus = "SCHEDULED" | "PROCESSING" | "UPLOADING" | "PUBLISHED" | "FAILED" | "CANCELLED";

export interface ScheduledPost {
  id: string;
  clip_id: string;
  clip_title: string;
  clip_thumbnail_url: string | null;
  social_account_id: string;
  platform: SocialProvider;
  account_name: string;
  scheduled_at: string;
  status: ScheduledPostStatus;
  progress: number;
  title: string;
  caption: string | null;
  published_at: string | null;
  external_url: string | null;
  latest_error: string | null;
  created_at: string;
}

export interface DashboardStats {
  total_videos: number;
  total_clips: number;
  scheduled_posts: number;
  published_posts: number;
  processing_jobs: number;
}
