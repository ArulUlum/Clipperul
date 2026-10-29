"use client";

import { useEffect, useState } from "react";
import useSWR from "@/lib/useSWR";
import type { ScheduledPost } from "@/types";

const IDLE_POLL_MS = 10_000;
const LIVE_POLL_MS = 2_000;

/** True while a post is actively preparing/uploading — i.e. something whose progress bar the
 * user may be watching. A PROCESSING post that already has an error is just sitting out a
 * retry backoff (minutes to hours), so it doesn't count. */
function hasLivePost(posts: ScheduledPost[] | undefined): boolean {
  return (posts ?? []).some(
    (p) => p.status === "UPLOADING" || (p.status === "PROCESSING" && !p.latest_error)
  );
}

/** All of the user's scheduled posts, polled slowly when nothing is moving and every couple of
 * seconds while an upload is in flight, so its progress bar actually advances. */
export function useScheduledPosts() {
  const [intervalMs, setIntervalMs] = useState(IDLE_POLL_MS);
  const result = useSWR<ScheduledPost[]>("/scheduled-posts", { intervalMs });
  const live = hasLivePost(result.data);

  useEffect(() => {
    setIntervalMs(live ? LIVE_POLL_MS : IDLE_POLL_MS);
  }, [live]);

  return result;
}
