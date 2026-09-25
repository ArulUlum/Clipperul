"use client";

import Link from "next/link";
import useSWR from "@/lib/useSWR";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { formatDate, formatDuration } from "@/lib/utils";
import type { Video } from "@/types";

const STATUS_VARIANT: Record<Video["status"], "muted" | "warning" | "success" | "destructive"> = {
  UPLOADED: "muted",
  QUEUED: "muted",
  DOWNLOADING: "warning",
  EXTRACTING_AUDIO: "warning",
  TRANSCRIBING: "warning",
  DETECTING_CATEGORY: "warning",
  ANALYZING: "warning",
  FINDING_HIGHLIGHTS: "warning",
  GENERATING_CLIPS: "warning",
  GENERATING_SUBTITLES: "warning",
  FINALIZING: "warning",
  COMPLETED: "success",
  FAILED: "destructive",
};

export function VideoList() {
  const { data: videos, isLoading } = useSWR<Video[]>("/videos");

  if (isLoading) return <p className="text-sm text-muted-foreground">Loading videos...</p>;
  if (!videos || videos.length === 0) {
    return (
      <Card>
        <CardContent className="p-8 text-center text-sm text-muted-foreground">
          No videos yet.{" "}
          <Link href="/videos/upload" className="text-primary underline">
            Upload your first video
          </Link>{" "}
          to get started.
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {videos.map((video) => (
        <Link key={video.id} href={`/videos/${video.id}`}>
          <Card className="transition-colors hover:border-primary/40">
            <CardContent className="space-y-2 p-5">
              <div className="flex items-start justify-between gap-2">
                <h3 className="line-clamp-2 text-sm font-semibold">{video.title}</h3>
                <Badge variant={STATUS_VARIANT[video.status]}>{video.status}</Badge>
              </div>
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <Badge variant="muted">{video.category ?? "Detecting..."}</Badge>
                <span>{formatDuration(video.duration_seconds)}</span>
              </div>
              <p className="text-xs text-muted-foreground">{formatDate(video.created_at)}</p>
            </CardContent>
          </Card>
        </Link>
      ))}
    </div>
  );
}
