"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Loader2, Pause, Play, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { ClipPostPanel } from "@/features/scheduled-posts/ClipPostPanel";
import { api, ApiError, getVideoDownloadUrl } from "@/lib/api";
import useSWR from "@/lib/useSWR";
import type { AspectRatio, Clip, ClipLayout, SubtitlePreset, TranscriptSegment } from "@/types";

const POLL_MS = 3000;
type EditorTab = "trim" | "ratio" | "crop" | "subtitles" | "details" | "post";
const TABS: { id: EditorTab; label: string }[] = [
  { id: "trim", label: "Trim" },
  { id: "ratio", label: "Ratio" },
  { id: "crop", label: "Crop" },
  { id: "subtitles", label: "Subtitles" },
  { id: "details", label: "Details" },
  { id: "post", label: "Post" },
];
const DEFAULT_FONT_SIZE = 44;
const DEFAULT_SUB_X = 50;
const DEFAULT_SUB_Y = 80;
const near = (a: number, b: number) => Math.abs(a - b) < 0.01;
const ASPECT_RATIO_OPTIONS: AspectRatio[] = ["9:16", "3:4", "1:1", "4:3"];
const ASPECT_RATIO_CSS: Record<AspectRatio, string> = {
  "9:16": "9/16", "3:4": "3/4", "1:1": "1/1", "4:3": "4/3", "16:9": "16/9",
};
// The pixel width the final render targets for each ratio — used to scale the subtitle
// font-size preview down to the (much smaller) on-screen preview box.
const ASPECT_RATIO_REF_WIDTH: Record<AspectRatio, number> = {
  "9:16": 1080, "3:4": 1080, "1:1": 1080, "4:3": 1440, "16:9": 1920,
};
const PREVIEW_WIDTH_PX = 256; // matches the w-64 preview container below

// The "news" layout, mirroring backend/app/services/video/layout.py: the crop fills the width of a
// 1080x1920 canvas, vertically centered; the title sits just above it, the subtitles just below.
const CANVAS_W = 1080;
const CANVAS_H = 1920;
const ASPECT_RATIO_SIZE: Record<AspectRatio, [number, number]> = {
  "9:16": [1080, 1920], "3:4": [1080, 1440], "1:1": [1080, 1080], "4:3": [1440, 1080], "16:9": [1920, 1080],
};
const HEADLINE_MARGIN_X = 70;
const HEADLINE_GAP = 40;
const SUBTITLE_GAP = 110;
const HEADLINE_BOX_COLOR = "#D7261E";

const newsApplies = (layout: ClipLayout, ratio: AspectRatio) => layout === "news" && ratio !== "9:16";

function newsBox(ratio: AspectRatio) {
  const [w, h] = ASPECT_RATIO_SIZE[ratio];
  const height = Math.round((CANVAS_W * h) / w / 2) * 2;
  return { height, top: Math.floor((CANVAS_H - height) / 2) };
}

const headlineFontSize = (top: number) => Math.max(40, Math.min(72, Math.floor(top / 5)));

// Where subtitles go when the user hasn't moved them: just below the clip under the news layout.
function defaultSubY(layout: ClipLayout, ratio: AspectRatio): number {
  if (!newsApplies(layout, ratio)) return DEFAULT_SUB_Y;
  const { height, top } = newsBox(ratio);
  const bottom = top + height;
  return Math.round((Math.min(bottom + SUBTITLE_GAP, (bottom + CANVAS_H) / 2) / CANVAS_H) * 10000) / 100;
}

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds)) return "0:00";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export function ClipPreviewModal({
  clip,
  onClose,
  onSaved,
}: {
  clip: Clip;
  onClose: () => void;
  onSaved: (updated: Clip) => void;
}) {
  const [title, setTitle] = useState(clip.title);
  const [caption, setCaption] = useState(clip.caption || "");
  const [savingMeta, setSavingMeta] = useState(false);

  // Every field below is edited live in the browser (no server round-trip) and only sent to
  // the server once, together, when "Save edits" is clicked.
  const [trimStart, setTrimStart] = useState(clip.start_time);
  const [trimEnd, setTrimEnd] = useState(clip.end_time);
  const [aspectRatio, setAspectRatio] = useState<AspectRatio>(clip.aspect_ratio);
  const [clipLayout, setClipLayout] = useState<ClipLayout>(clip.layout ?? "full");
  const [cropOverride, setCropOverride] = useState(clip.crop_x !== null && clip.crop_y !== null);
  const [cropX, setCropX] = useState(clip.crop_x ?? 50);
  const [cropY, setCropY] = useState(clip.crop_y ?? 50);
  const [subtitlesEnabled, setSubtitlesEnabled] = useState(clip.subtitle_preset_id !== null);
  const [subtitlePresetId, setSubtitlePresetId] = useState(clip.subtitle_preset_id ?? "");
  const [fontSize, setFontSize] = useState(clip.subtitle_font_size ?? DEFAULT_FONT_SIZE);
  const [subPositionX, setSubPositionX] = useState(clip.subtitle_position_x ?? DEFAULT_SUB_X);
  const [subPositionY, setSubPositionY] = useState(
    clip.subtitle_position_y ?? defaultSubY(clip.layout ?? "full", clip.aspect_ratio)
  );

  // Switching layout or ratio moves the subtitles along to the new default spot — unless the user
  // has already placed them somewhere of their own.
  const subDefaultY = defaultSubY(clipLayout, aspectRatio);
  const prevSubDefaultY = useRef(subDefaultY);
  useEffect(() => {
    if (near(subPositionY, prevSubDefaultY.current)) setSubPositionY(subDefaultY);
    prevSubDefaultY.current = subDefaultY;
  }, [subDefaultY, subPositionY]);

  // The last server-confirmed state of this clip. The `clip` prop goes stale after a save
  // (parents don't re-pass it), so "do the on-screen edits differ from what's rendered?"
  // — which the Post tab needs — has to be measured against this instead.
  const [savedClip, setSavedClip] = useState(clip);

  const [applying, setApplying] = useState(clip.status === "RENDERING");
  const [applyError, setApplyError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<EditorTab>("trim");

  const { data: presets } = useSWR<SubtitlePreset[]>("/subtitle-presets");
  const { data: transcript } = useSWR<{ segments: TranscriptSegment[] }>(`/videos/${clip.video_id}/transcript`);

  const [sourceUrl, setSourceUrl] = useState<string | null>(null);
  const [videoDuration, setVideoDuration] = useState<number | null>(null);
  const [currentTime, setCurrentTime] = useState(clip.start_time);
  const [playing, setPlaying] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    let cancelled = false;
    getVideoDownloadUrl(clip.video_id).then((url) => {
      if (!cancelled) setSourceUrl(url);
    });
    return () => {
      cancelled = true;
    };
  }, [clip.video_id]);

  useEffect(() => {
    if (presets && presets.length > 0 && !subtitlePresetId) {
      const def = presets.find((p) => p.is_system_default) ?? presets[0];
      setSubtitlePresetId(def.id);
    }
  }, [presets, subtitlePresetId]);

  // Poll while a save is applying (async re-render in the worker).
  useEffect(() => {
    if (!applying) return;
    const id = setInterval(async () => {
      const updated = await api.get<Clip>(`/clips/${clip.id}`);
      if (updated.status === "READY") {
        setApplying(false);
        setSavedClip(updated);
        onSaved(updated);
      } else if (updated.status === "FAILED") {
        setApplying(false);
        setApplyError("Save failed. Try different settings.");
      }
    }, POLL_MS);
    return () => clearInterval(id);
  }, [applying, clip.id, onSaved]);

  const seekTo = (t: number) => {
    if (videoRef.current) videoRef.current.currentTime = t;
    setCurrentTime(t);
  };

  const handleTrimStartChange = (value: number) => {
    const clamped = Math.min(value, trimEnd - 0.1);
    setTrimStart(clamped);
    seekTo(clamped);
  };

  const handleTrimEndChange = (value: number) => {
    const clamped = Math.max(value, trimStart + 0.1);
    setTrimEnd(clamped);
    seekTo(clamped);
  };

  const handleTogglePreviewPlayback = () => {
    const video = videoRef.current;
    if (!video) return;
    if (playing) {
      video.pause();
    } else {
      if (video.currentTime < trimStart || video.currentTime >= trimEnd) video.currentTime = trimStart;
      video.play();
    }
  };

  const handleTimeUpdate = () => {
    const video = videoRef.current;
    if (!video) return;
    if (video.currentTime >= trimEnd) {
      video.pause();
      video.currentTime = trimStart;
      setCurrentTime(trimStart);
      return;
    }
    setCurrentTime(video.currentTime);
  };

  const handleSaveEdits = async () => {
    setApplyError(null);
    if (trimEnd <= trimStart) {
      setApplyError("End time must be after start time.");
      return;
    }
    setApplying(true);
    try {
      await api.post<Clip>(`/clips/${clip.id}/apply-edits`, {
        start_time: trimStart,
        end_time: trimEnd,
        aspect_ratio: aspectRatio,
        layout: clipLayout,
        ...(cropOverride ? { crop_x: cropX, crop_y: cropY } : {}),
        ...(subtitlesEnabled && subtitlePresetId
          ? {
              subtitle_preset_id: subtitlePresetId,
              subtitle_font_size: fontSize,
              subtitle_position_x: subPositionX,
              subtitle_position_y: subPositionY,
            }
          : {}),
      });
    } catch (err) {
      setApplying(false);
      setApplyError(err instanceof ApiError ? err.message : "Failed to save changes.");
    }
  };

  const handleSaveMeta = async () => {
    setSavingMeta(true);
    try {
      const updated = await api.patch<Clip>(`/clips/${clip.id}`, { title, caption });
      onSaved(updated);
      onClose();
    } finally {
      setSavingMeta(false);
    }
  };

  const savedCropOverride = savedClip.crop_x !== null && savedClip.crop_y !== null;
  const cropDirty =
    cropOverride !== savedCropOverride ||
    (cropOverride && (!near(cropX, savedClip.crop_x ?? 50) || !near(cropY, savedClip.crop_y ?? 50)));
  const subtitlesDirty =
    subtitlesEnabled !== (savedClip.subtitle_preset_id !== null) ||
    (subtitlesEnabled &&
      (subtitlePresetId !== savedClip.subtitle_preset_id ||
        !near(fontSize, savedClip.subtitle_font_size ?? DEFAULT_FONT_SIZE) ||
        !near(subPositionX, savedClip.subtitle_position_x ?? DEFAULT_SUB_X) ||
        !near(
          subPositionY,
          savedClip.subtitle_position_y ?? defaultSubY(savedClip.layout ?? "full", savedClip.aspect_ratio)
        )));
  const hasUnsavedEdits =
    !near(trimStart, savedClip.start_time) ||
    !near(trimEnd, savedClip.end_time) ||
    aspectRatio !== savedClip.aspect_ratio ||
    clipLayout !== (savedClip.layout ?? "full") ||
    cropDirty ||
    subtitlesDirty;

  // A post always uploads the clip's last RENDERED file, not what the live preview shows, so
  // posting with pending edits would silently publish the old version.
  const postBlockedReason = applying
    ? "Your edits are still rendering — wait for them to finish, then post."
    : hasUnsavedEdits
      ? "You have edits that aren't saved yet. Click “Save edits” first so the post uses the edited version."
      : savedClip.status !== "READY"
        ? "This clip isn't ready to post (its last render didn't finish)."
        : null;

  const newsActive = newsApplies(clipLayout, aspectRatio);
  const box = newsBox(aspectRatio);
  const previewScale = PREVIEW_WIDTH_PX / (newsActive ? CANVAS_W : ASPECT_RATIO_REF_WIDTH[aspectRatio]);
  const savedTitleIsHeadline = newsApplies(savedClip.layout ?? "full", savedClip.aspect_ratio);
  const activeCueText = useMemo(() => {
    if (!subtitlesEnabled || !transcript?.segments) return null;
    const cue = transcript.segments.find(
      (s) => s.start_time >= trimStart && s.end_time <= trimEnd && currentTime >= s.start_time && currentTime <= s.end_time
    );
    return cue?.text ?? null;
  }, [transcript, subtitlesEnabled, trimStart, trimEnd, currentTime]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="flex max-h-[90vh] w-full max-w-4xl gap-6 overflow-y-auto rounded-lg bg-card p-6">
        <div className="flex w-64 shrink-0 flex-col gap-2">
          <div
            className="relative w-full overflow-hidden rounded-md bg-black"
            style={{ aspectRatio: newsActive ? "9/16" : ASPECT_RATIO_CSS[aspectRatio] }}
          >
            {/* Same element in both layouts, so switching doesn't reload the video. The real render
                puts a blurred, darkened copy of the clip behind it; the preview keeps that black. */}
            <div
              className="absolute inset-x-0"
              style={
                newsActive
                  ? { top: `${(box.top / CANVAS_H) * 100}%`, height: `${(box.height / CANVAS_H) * 100}%` }
                  : { top: 0, bottom: 0 }
              }
            >
              {sourceUrl ? (
                <video
                  ref={videoRef}
                  key={sourceUrl}
                  className="h-full w-full"
                  style={{ objectFit: "cover", objectPosition: `${cropX}% ${cropY}%` }}
                  src={sourceUrl}
                  onLoadedMetadata={(e) => {
                    setVideoDuration(e.currentTarget.duration);
                    e.currentTarget.currentTime = trimStart;
                  }}
                  onTimeUpdate={handleTimeUpdate}
                  onPlay={() => setPlaying(true)}
                  onPause={() => setPlaying(false)}
                />
              ) : (
                <span className="absolute inset-0 flex items-center justify-center text-xs text-white/60">
                  Loading preview...
                </span>
              )}
            </div>
            {newsActive && title.trim() && (
              <div
                className="pointer-events-none absolute text-center font-bold leading-snug text-white"
                style={{
                  left: `${(HEADLINE_MARGIN_X / CANVAS_W) * 100}%`,
                  right: `${(HEADLINE_MARGIN_X / CANVAS_W) * 100}%`,
                  bottom: `${((CANVAS_H - (box.top - HEADLINE_GAP)) / CANVAS_H) * 100}%`,
                  fontSize: `${headlineFontSize(box.top) * previewScale}px`,
                }}
              >
                <span
                  style={{
                    background: HEADLINE_BOX_COLOR,
                    padding: `0 ${14 * previewScale}px`,
                    boxDecorationBreak: "clone",
                    WebkitBoxDecorationBreak: "clone",
                  }}
                >
                  {title}
                </span>
              </div>
            )}
            {activeCueText && (
              <div
                className="pointer-events-none absolute max-w-[90%] -translate-x-1/2 -translate-y-1/2 text-center font-bold leading-tight text-white"
                style={{
                  left: `${subPositionX}%`,
                  top: `${subPositionY}%`,
                  fontSize: `${fontSize * previewScale}px`,
                  textShadow: "0 0 3px #000, 1px 1px 2px #000, -1px -1px 2px #000",
                }}
              >
                {activeCueText}
              </div>
            )}
          </div>
          <Button type="button" variant="outline" size="sm" onClick={handleTogglePreviewPlayback} disabled={!sourceUrl}>
            {playing ? <Pause className="mr-1.5 h-3.5 w-3.5" /> : <Play className="mr-1.5 h-3.5 w-3.5" />}
            {playing ? "Pause" : "Preview trimmed range"}
          </Button>
          <p className="text-center text-xs text-muted-foreground">
            Live preview — approximate; the real render matches exactly.
          </p>
        </div>

        <div className="flex flex-1 flex-col">
          <div className="flex items-start justify-between">
            <h2 className="text-lg font-semibold">Edit clip</h2>
            <button onClick={onClose} className="text-muted-foreground hover:text-foreground">
              <X className="h-5 w-5" />
            </button>
          </div>

          <div className="mt-3 flex gap-1 border-b border-border">
            {TABS.map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors ${
                  activeTab === tab.id
                    ? "border-primary text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <div className="min-h-[220px] flex-auto shrink-0 py-4">
            {activeTab === "trim" && (
              <div className="space-y-2">
                <div className="space-y-1">
                  <div className="flex items-center justify-between text-xs text-muted-foreground">
                    <span>Start</span>
                    <span>{formatTime(trimStart)}</span>
                  </div>
                  <input
                    type="range"
                    min={0}
                    max={videoDuration ?? trimEnd}
                    step={0.1}
                    value={trimStart}
                    onChange={(e) => handleTrimStartChange(Number(e.target.value))}
                    disabled={applying || !videoDuration}
                    className="w-full"
                  />
                </div>
                <div className="space-y-1">
                  <div className="flex items-center justify-between text-xs text-muted-foreground">
                    <span>End</span>
                    <span>{formatTime(trimEnd)}</span>
                  </div>
                  <input
                    type="range"
                    min={0}
                    max={videoDuration ?? trimEnd}
                    step={0.1}
                    value={trimEnd}
                    onChange={(e) => handleTrimEndChange(Number(e.target.value))}
                    disabled={applying || !videoDuration}
                    className="w-full"
                  />
                </div>
                <p className="text-xs text-muted-foreground">Duration: {formatTime(trimEnd - trimStart)}</p>
              </div>
            )}

            {activeTab === "ratio" && (
              <div className="space-y-2">
                <p className="text-xs text-muted-foreground">Choose the output shape for this clip.</p>
                <div className="flex gap-2">
                  {ASPECT_RATIO_OPTIONS.map((ratio) => (
                    <Button
                      key={ratio}
                      type="button"
                      size="sm"
                      variant={ratio === aspectRatio ? "default" : "outline"}
                      disabled={applying}
                      onClick={() => setAspectRatio(ratio)}
                    >
                      {ratio}
                    </Button>
                  ))}
                </div>
                <p className="pt-2 text-xs text-muted-foreground">Layout</p>
                <div className="flex gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant={clipLayout === "full" ? "default" : "outline"}
                    disabled={applying}
                    onClick={() => setClipLayout("full")}
                  >
                    Full frame
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant={clipLayout === "news" ? "default" : "outline"}
                    disabled={applying}
                    onClick={() => setClipLayout("news")}
                  >
                    News: 9:16 with headline
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  {clipLayout === "news" && aspectRatio === "9:16"
                    ? "A 9:16 clip already fills the screen, so the news layout only changes 1:1, 3:4 and 4:3 clips."
                    : "News places the clip in the middle of a full-screen 9:16 video, with the title as a headline above it and the subtitles below."}
                </p>
              </div>
            )}

            {activeTab === "crop" && (
              <div className="space-y-2">
                <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={cropOverride}
                    onChange={(e) => setCropOverride(e.target.checked)}
                    disabled={applying}
                  />
                  Override manually (default: auto-track detected speaker)
                </label>
                {cropOverride && (
                  <div className="space-y-2 rounded-md bg-muted/50 p-2">
                    <div className="space-y-1">
                      <div className="flex items-center justify-between text-xs text-muted-foreground">
                        <span>Horizontal position</span>
                        <span>{cropX}%</span>
                      </div>
                      <input
                        type="range"
                        min={0}
                        max={100}
                        value={cropX}
                        onChange={(e) => setCropX(Number(e.target.value))}
                        disabled={applying}
                        className="w-full"
                      />
                    </div>
                    <div className="space-y-1">
                      <div className="flex items-center justify-between text-xs text-muted-foreground">
                        <span>Vertical position</span>
                        <span>{cropY}%</span>
                      </div>
                      <input
                        type="range"
                        min={0}
                        max={100}
                        value={cropY}
                        onChange={(e) => setCropY(Number(e.target.value))}
                        disabled={applying}
                        className="w-full"
                      />
                    </div>
                  </div>
                )}
              </div>
            )}

            {activeTab === "subtitles" && (
              <div className="space-y-2">
                <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={subtitlesEnabled}
                    onChange={(e) => setSubtitlesEnabled(e.target.checked)}
                    disabled={applying}
                  />
                  Burn subtitles into this clip
                </label>

                {subtitlesEnabled && (
                  <div className="space-y-2 rounded-md bg-muted/50 p-2">
                    <Select
                      value={subtitlePresetId}
                      onChange={(e) => setSubtitlePresetId(e.target.value)}
                      disabled={applying || !presets || presets.length === 0}
                    >
                      {(presets ?? []).map((preset) => (
                        <option key={preset.id} value={preset.id}>
                          {preset.name}
                        </option>
                      ))}
                    </Select>
                    <div className="space-y-1">
                      <div className="flex items-center justify-between text-xs text-muted-foreground">
                        <span>Font size</span>
                        <span>{fontSize}px</span>
                      </div>
                      <input
                        type="range"
                        min={8}
                        max={200}
                        value={fontSize}
                        onChange={(e) => setFontSize(Number(e.target.value))}
                        disabled={applying}
                        className="w-full"
                      />
                    </div>
                    <div className="space-y-1">
                      <div className="flex items-center justify-between text-xs text-muted-foreground">
                        <span>Horizontal position</span>
                        <span>{subPositionX}%</span>
                      </div>
                      <input
                        type="range"
                        min={0}
                        max={100}
                        value={subPositionX}
                        onChange={(e) => setSubPositionX(Number(e.target.value))}
                        disabled={applying}
                        className="w-full"
                      />
                    </div>
                    <div className="space-y-1">
                      <div className="flex items-center justify-between text-xs text-muted-foreground">
                        <span>Vertical position</span>
                        <span>{subPositionY}%</span>
                      </div>
                      <input
                        type="range"
                        min={0}
                        max={100}
                        value={subPositionY}
                        onChange={(e) => setSubPositionY(Number(e.target.value))}
                        disabled={applying}
                        className="w-full"
                      />
                    </div>
                  </div>
                )}
              </div>
            )}

            {activeTab === "details" && (
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="title">Title</Label>
                  <Input id="title" value={title} onChange={(e) => setTitle(e.target.value)} />
                  {savedTitleIsHeadline && (
                    <p className="text-xs text-muted-foreground">
                      This title is the headline on the video, so saving a new one re-renders the clip.
                    </p>
                  )}
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="caption">Caption</Label>
                  <textarea
                    id="caption"
                    value={caption}
                    onChange={(e) => setCaption(e.target.value)}
                    rows={3}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                  />
                </div>

                {clip.reason && (
                  <p className="rounded-md bg-muted p-3 text-xs text-muted-foreground">
                    <span className="font-medium text-foreground">Why AI picked this: </span>
                    {clip.reason}
                  </p>
                )}

                <div className="flex justify-end">
                  <Button onClick={handleSaveMeta} disabled={savingMeta}>
                    {savingMeta ? "Saving..." : "Save title & caption"}
                  </Button>
                </div>
              </div>
            )}

            {activeTab === "post" && (
              <ClipPostPanel
                clipId={clip.id}
                defaultTitle={title}
                defaultCaption={caption}
                blockedReason={postBlockedReason}
              />
            )}
          </div>

          <div className="flex items-center justify-between gap-2 rounded-md border border-primary/30 bg-primary/5 p-3">
            <div>
              <p className="text-sm font-medium">Save edits</p>
              <p className="text-xs text-muted-foreground">
                {applying ? "Rendering your changes..." : "Applies trim, ratio, layout, crop and subtitles together."}
              </p>
              {applyError && <p className="text-xs text-destructive">{applyError}</p>}
            </div>
            <Button type="button" onClick={handleSaveEdits} disabled={applying}>
              {applying ? <Loader2 className="h-4 w-4 animate-spin" /> : "Save edits"}
            </Button>
          </div>

          <div className="flex justify-end pt-2">
            <Button variant="outline" onClick={onClose}>
              Close
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
