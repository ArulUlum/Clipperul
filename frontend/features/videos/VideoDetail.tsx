"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ClipCard } from "@/features/clips/ClipCard";
import { ClipPreviewModal } from "@/features/clips/ClipPreviewModal";
import { ProcessingStages } from "@/components/shared/ProcessingStages";
import { useVideoStatus } from "@/features/videos/useVideoStatus";
import useSWR from "@/lib/useSWR";
import { api, ApiError } from "@/lib/api";
import { formatDate, formatDuration } from "@/lib/utils";
import type { Clip, Transcript, VideoDetail as VideoDetailType } from "@/types";

export function VideoDetail({ videoId }: { videoId: string }) {
  const router = useRouter();
  const { data: video, mutate: refetchVideo } = useSWR<VideoDetailType>(`/videos/${videoId}`);
  const [previewClip, setPreviewClip] = useState<Clip | null>(null);
  const [deleting, setDeleting] = useState(false);

  const { stage, errorMessage } = useVideoStatus(videoId, video?.status ?? "QUEUED");
  const isProcessing = stage !== "COMPLETED" && stage !== "FAILED";

  const { data: transcript } = useSWR<Transcript>(!isProcessing ? `/videos/${videoId}/transcript` : null);
  const { data: clips, mutate: refetchClips } = useSWR<Clip[]>(!isProcessing ? `/videos/${videoId}/clips` : null);

  useEffect(() => {
    if (stage === "COMPLETED" || stage === "FAILED") {
      refetchVideo();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage]);

  const handleDelete = async () => {
    if (!confirm("Delete this video and all its generated clips? This cannot be undone.")) return;
    setDeleting(true);
    try {
      await api.delete(`/videos/${videoId}`);
      router.push("/videos");
    } catch (err) {
      alert(err instanceof ApiError ? err.message : "Failed to delete video");
      setDeleting(false);
    }
  };

  if (!video) return <p className="text-sm text-muted-foreground">Loading...</p>;

  return (
    <div className="space-y-8">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold">{video.title}</h1>
          <div className="mt-2 flex items-center gap-2">
            <Badge variant="muted">{video.category ?? "Detecting category..."}</Badge>
            <span className="text-xs text-muted-foreground">{formatDuration(video.duration_seconds)}</span>
            <span className="text-xs text-muted-foreground">{formatDate(video.created_at)}</span>
            {video.source_url && (
              <a
                href={video.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-primary underline"
              >
                Source link
              </a>
            )}
          </div>
        </div>
        <Button variant="outline" size="sm" onClick={handleDelete} disabled={deleting}>
          <Trash2 className="mr-1 h-3.5 w-3.5" />
          Delete
        </Button>
      </div>

      {isProcessing && (
        <Card>
          <CardContent className="p-6">
            <h2 className="mb-4 text-sm font-semibold">Processing video...</h2>
            <ProcessingStages currentStage={stage} />
          </CardContent>
        </Card>
      )}

      {stage === "FAILED" && (
        <Card className="border-destructive/40">
          <CardContent className="p-6">
            <h2 className="mb-1 text-sm font-semibold text-destructive">Processing failed</h2>
            <p className="text-sm text-muted-foreground">
              {errorMessage || video.error_message || "Something went wrong while processing this video."}
            </p>
          </CardContent>
        </Card>
      )}

      {!isProcessing && (
        <>
          <section>
            <h2 className="mb-3 text-lg font-semibold">
              Generated clips {clips ? `(${clips.length})` : ""}
            </h2>
            {clips && clips.length > 0 ? (
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
                {clips.map((clip) => (
                  <ClipCard key={clip.id} clip={clip} onPreview={setPreviewClip} />
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No clips were generated for this video.</p>
            )}
          </section>

          {transcript && (
            <section>
              <h2 className="mb-3 text-lg font-semibold">Transcript</h2>
              <Card>
                <CardContent className="max-h-80 space-y-2 overflow-y-auto p-5">
                  {transcript.segments.map((seg, i) => (
                    <p key={i} className="text-sm">
                      <span className="mr-2 font-mono text-xs text-muted-foreground">
                        {formatDuration(seg.start_time)}
                      </span>
                      {seg.text}
                    </p>
                  ))}
                </CardContent>
              </Card>
            </section>
          )}
        </>
      )}

      {previewClip && (
        <ClipPreviewModal
          clip={previewClip}
          onClose={() => setPreviewClip(null)}
          onSaved={() => refetchClips()}
        />
      )}
    </div>
  );
}
