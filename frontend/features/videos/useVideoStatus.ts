"use client";

import { useEffect, useState } from "react";
import { subscribeToVideoStatus, type VideoStatusEvent } from "@/lib/sse";
import type { VideoStatus } from "@/types";

export function useVideoStatus(videoId: string, initialStage: VideoStatus) {
  const [stage, setStage] = useState<VideoStatus>(initialStage);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    // initialStage starts as a "QUEUED" placeholder before the video fetch resolves, then
    // flips to the real value — re-sync every time it changes (not just on mount) so a page
    // load for an already-completed/failed video doesn't get stuck showing the placeholder.
    setStage(initialStage);
    if (initialStage === "COMPLETED" || initialStage === "FAILED") return;

    const unsubscribe = subscribeToVideoStatus(videoId, (event: VideoStatusEvent) => {
      if (event.stage) setStage(event.stage as VideoStatus);
      if (event.error_message) setErrorMessage(event.error_message);
    });

    return unsubscribe;
  }, [videoId, initialStage]);

  return { stage, errorMessage };
}
