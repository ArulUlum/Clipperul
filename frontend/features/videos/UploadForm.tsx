"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Link2, UploadCloud } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { api, ApiError } from "@/lib/api";
import type { ClipLayout } from "@/types";

type UploadMode = "file" | "url";

const LAYOUT_OPTIONS: { id: ClipLayout; label: string; hint: string }[] = [
  { id: "full", label: "Full frame", hint: "The crop fills the whole clip." },
  {
    id: "news",
    label: "News",
    hint: "The clip in the middle of a full-screen 9:16 video, with its title as a headline above it.",
  },
  {
    id: "gaming",
    label: "Gaming",
    hint: "For streams with a facecam: a full-screen 9:16 video with the streamer's face (found automatically) across the top 40% and the game across the bottom 60%. Clips with no face found get the News layout.",
  },
];

export function UploadForm() {
  const router = useRouter();
  const [mode, setMode] = useState<UploadMode>("file");
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState("");
  const [subtitlesEnabled, setSubtitlesEnabled] = useState(true);
  const [clipLayout, setClipLayout] = useState<ClipLayout>("full");
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (mode === "file" && !file) {
      setError("Please choose a video file");
      return;
    }
    if (mode === "url" && !url.trim()) {
      setError("Please paste a video URL");
      return;
    }

    setUploading(true);
    try {
      let videoId: string;

      if (mode === "file" && file) {
        const form = new FormData();
        form.append("title", title || file.name);
        form.append("file", file);
        form.append("subtitles_enabled", String(subtitlesEnabled));
        form.append("clip_layout", clipLayout);
        const res = await api.postForm<{ video_id: string }>("/videos", form);
        videoId = res.video_id;
      } else {
        const res = await api.post<{ video_id: string }>("/videos/from-url", {
          title: title || url,
          url,
          subtitles_enabled: subtitlesEnabled,
          clip_layout: clipLayout,
        });
        videoId = res.video_id;
      }

      router.push(`/videos/${videoId}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="max-w-xl space-y-5">
      <div className="inline-flex rounded-md border border-border p-1">
        {(["file", "url"] as UploadMode[]).map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => setMode(m)}
            className={cn(
              "flex items-center gap-1.5 rounded px-3 py-1.5 text-sm font-medium transition-colors",
              mode === m ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted"
            )}
          >
            {m === "file" ? <UploadCloud className="h-3.5 w-3.5" /> : <Link2 className="h-3.5 w-3.5" />}
            {m === "file" ? "Upload file" : "Paste link"}
          </button>
        ))}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="title">Title</Label>
        <Input
          id="title"
          placeholder="e.g. Valorant Ranked Session #42"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </div>

      {mode === "file" ? (
        <div className="space-y-1.5">
          <Label htmlFor="file">Video file</Label>
          <label
            htmlFor="file"
            className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-muted/40 p-8 text-center hover:bg-muted"
          >
            <UploadCloud className="h-8 w-8 text-muted-foreground" />
            <span className="text-sm font-medium">{file ? file.name : "Click to choose a video"}</span>
            <span className="text-xs text-muted-foreground">MP4, MOV, MKV, WebM, or AVI</span>
          </label>
          <input
            id="file"
            type="file"
            accept="video/*"
            className="hidden"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
        </div>
      ) : (
        <div className="space-y-1.5">
          <Label htmlFor="url">Video URL</Label>
          <Input
            id="url"
            type="url"
            placeholder="https://www.youtube.com/watch?v=..."
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
          <p className="text-xs text-muted-foreground">
            YouTube links or a direct video file URL. Downloading happens in the background —
            this can take a while for long videos.
          </p>
        </div>
      )}

      <p className="text-xs text-muted-foreground">
        Clipperul watches the transcript and automatically detects whether this is Gaming, Podcast
        or News content, then picks the matching highlight-detection strategy — no need to
        choose a category.
      </p>

      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={subtitlesEnabled}
          onChange={(e) => setSubtitlesEnabled(e.target.checked)}
          className="h-4 w-4 rounded border-border accent-primary"
        />
        Auto-add subtitles to generated clips
      </label>
      <p className="-mt-3 text-xs text-muted-foreground">
        Burns in a style matched to the detected category automatically. You can still change
        the style or remove subtitles per clip afterward.
      </p>

      <div className="space-y-1.5">
        <Label>Clip layout</Label>
        <div className="inline-flex rounded-md border border-border p-1">
          {LAYOUT_OPTIONS.map((option) => (
            <button
              key={option.id}
              type="button"
              onClick={() => setClipLayout(option.id)}
              className={cn(
                "rounded px-3 py-1.5 text-sm font-medium transition-colors",
                clipLayout === option.id ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted"
              )}
            >
              {option.label}
            </button>
          ))}
        </div>
        <p className="text-xs text-muted-foreground">
          {LAYOUT_OPTIONS.find((o) => o.id === clipLayout)?.hint} You can change it per clip afterward.
        </p>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      <Button type="submit" disabled={uploading} className="w-full">
        {uploading ? "Starting..." : "Upload & start processing"}
      </Button>
    </form>
  );
}
