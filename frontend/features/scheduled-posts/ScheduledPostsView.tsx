"use client";

import { ScheduledPostCard } from "@/features/scheduled-posts/ScheduledPostCard";
import { ScheduledPostForm } from "@/features/scheduled-posts/ScheduledPostForm";
import useSWR from "@/lib/useSWR";
import type { ScheduledPost } from "@/types";

const ACTIVE_STATUSES = new Set(["SCHEDULED", "PROCESSING", "UPLOADING"]);

export function ScheduledPostsView() {
  // Polls rather than one-shot fetches — a post's status changes in the background (the
  // beat/worker pair claims and publishes it) with nothing on this page to push an update.
  const { data: posts, mutate } = useSWR<ScheduledPost[]>("/scheduled-posts", { intervalMs: 10_000 });
  const active = (posts ?? []).filter((p) => ACTIVE_STATUSES.has(p.status));

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Scheduled Posts</h1>
      <ScheduledPostForm onCreated={mutate} />

      <div className="space-y-2">
        {posts === undefined ? (
          <p className="text-sm text-muted-foreground">Loading...</p>
        ) : active.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing scheduled yet.</p>
        ) : (
          active.map((post) => <ScheduledPostCard key={post.id} post={post} onChanged={mutate} />)
        )}
      </div>
    </div>
  );
}
