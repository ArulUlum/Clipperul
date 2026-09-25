"use client";

import { useState } from "react";
import { ClipCard } from "@/features/clips/ClipCard";
import { ClipPreviewModal } from "@/features/clips/ClipPreviewModal";
import useSWR from "@/lib/useSWR";
import type { Clip, Video } from "@/types";

export default function ClipsPage() {
  const { data: videos } = useSWR<Video[]>("/videos");
  const [previewClip, setPreviewClip] = useState<Clip | null>(null);

  const completedVideoIds = (videos ?? []).filter((v) => v.status === "COMPLETED").map((v) => v.id);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Clips</h1>
      {completedVideoIds.length === 0 ? (
        <p className="text-sm text-muted-foreground">No clips yet — process a video first.</p>
      ) : (
        completedVideoIds.map((videoId) => (
          <VideoClipsSection key={videoId} videoId={videoId} onPreview={setPreviewClip} />
        ))
      )}

      {previewClip && (
        <ClipPreviewModal clip={previewClip} onClose={() => setPreviewClip(null)} onSaved={() => {}} />
      )}
    </div>
  );
}

function VideoClipsSection({ videoId, onPreview }: { videoId: string; onPreview: (clip: Clip) => void }) {
  const { data: clips } = useSWR<Clip[]>(`/videos/${videoId}/clips`);
  if (!clips || clips.length === 0) return null;

  return (
    <section>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
        {clips.map((clip) => (
          <ClipCard key={clip.id} clip={clip} onPreview={onPreview} />
        ))}
      </div>
    </section>
  );
}
