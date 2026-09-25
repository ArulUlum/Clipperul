"use client";

import { useState } from "react";
import { Download, Pencil, Play } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { getClipDownloadUrl } from "@/lib/api";
import { cn } from "@/lib/utils";
import { formatDate, formatDuration } from "@/lib/utils";
import type { Clip } from "@/types";

const STATUS_VARIANT: Record<Clip["status"], "muted" | "warning" | "success" | "destructive"> = {
  PENDING: "muted",
  RENDERING: "warning",
  READY: "success",
  FAILED: "destructive",
};

function scoreColorClass(score: number): string {
  if (score >= 75) return "bg-emerald-500";
  if (score >= 50) return "bg-amber-500";
  return "bg-red-500";
}

function ViralScoreBadge({ score }: { score: number }) {
  return (
    <span
      className={cn(
        "absolute left-2 top-2 rounded-full px-2 py-0.5 text-xs font-bold text-white shadow",
        scoreColorClass(score)
      )}
      title="Viral score — how likely this clip is to hook viewers, based on hook, flow, value, and trend fit"
    >
      🔥 {Math.round(score)}
    </span>
  );
}

const SUB_SCORE_LABELS: { key: keyof Pick<Clip, "hook_score" | "flow_score" | "value_score" | "trend_score">; label: string }[] = [
  { key: "hook_score", label: "Hook" },
  { key: "flow_score", label: "Flow" },
  { key: "value_score", label: "Value" },
  { key: "trend_score", label: "Trend" },
];

function ViralScoreBreakdown({ clip }: { clip: Clip }) {
  const hasBreakdown = SUB_SCORE_LABELS.some(({ key }) => clip[key] != null);
  if (!hasBreakdown) return null;

  return (
    <div className="grid grid-cols-4 gap-1.5">
      {SUB_SCORE_LABELS.map(({ key, label }) => {
        const value = clip[key];
        return (
          <div key={key} className="space-y-0.5">
            <div className="h-1 overflow-hidden rounded-full bg-muted">
              {value != null && (
                <div className={cn("h-full rounded-full", scoreColorClass(value))} style={{ width: `${value}%` }} />
              )}
            </div>
            <p className="text-[10px] text-muted-foreground">
              {label} {value != null ? Math.round(value) : "-"}
            </p>
          </div>
        );
      })}
    </div>
  );
}

export function ClipCard({ clip, onPreview }: { clip: Clip; onPreview: (clip: Clip) => void }) {
  const [downloading, setDownloading] = useState(false);

  const handleDownload = async () => {
    setDownloading(true);
    try {
      const url = await getClipDownloadUrl(clip.id);
      window.location.href = url;
    } finally {
      setDownloading(false);
    }
  };

  return (
    <Card className="overflow-hidden">
      <button
        onClick={() => onPreview(clip)}
        className="group relative flex aspect-[9/16] w-full items-center justify-center overflow-hidden bg-muted"
      >
        {clip.thumbnail_url ? (
          // eslint-disable-next-line @next/next/no-img-element -- presigned URL, not a static/local asset
          <img
            src={clip.thumbnail_url}
            alt={clip.title}
            className="h-full w-full object-cover transition-transform group-hover:scale-105"
          />
        ) : (
          <Play className="h-10 w-10 text-muted-foreground transition-transform group-hover:scale-110" />
        )}
        {clip.thumbnail_url && (
          <div className="absolute inset-0 flex items-center justify-center opacity-0 transition-opacity group-hover:opacity-100">
            <Play className="h-10 w-10 text-white drop-shadow" />
          </div>
        )}
        <ViralScoreBadge score={clip.score} />
        <span className="absolute bottom-2 right-2 rounded bg-black/70 px-1.5 py-0.5 text-xs text-white">
          {formatDuration(clip.duration_seconds)}
        </span>
      </button>
      <CardContent className="space-y-2 p-4">
        <div className="flex items-start justify-between gap-2">
          <h3 className="line-clamp-2 text-sm font-semibold">{clip.title}</h3>
          <Badge variant={STATUS_VARIANT[clip.status]}>{clip.status}</Badge>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Badge variant="muted">{clip.category}</Badge>
        </div>
        <ViralScoreBreakdown clip={clip} />
        {clip.reason && <p className="line-clamp-2 text-xs text-muted-foreground">{clip.reason}</p>}
        <p className="text-xs text-muted-foreground">{formatDate(clip.created_at)}</p>

        <div className="flex gap-2 pt-2">
          <Button variant="outline" size="sm" onClick={() => onPreview(clip)}>
            <Pencil className="mr-1 h-3.5 w-3.5" />
            Preview
          </Button>
          <Button variant="outline" size="sm" disabled={downloading} onClick={handleDownload}>
            <Download className="mr-1 h-3.5 w-3.5" />
            Download
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
