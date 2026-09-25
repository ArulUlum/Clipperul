"use client";

import { ScheduledPostCard } from "@/features/scheduled-posts/ScheduledPostCard";
import useSWR from "@/lib/useSWR";
import type { ScheduledPost } from "@/types";

const HISTORY_STATUSES = new Set(["PUBLISHED", "FAILED", "CANCELLED"]);

export function PublishedView() {
  const { data: posts, mutate } = useSWR<ScheduledPost[]>("/scheduled-posts");
  const history = (posts ?? [])
    .filter((p) => HISTORY_STATUSES.has(p.status))
    .sort((a, b) => (b.published_at ?? b.scheduled_at).localeCompare(a.published_at ?? a.scheduled_at));

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Published</h1>

      <div className="space-y-2">
        {posts === undefined ? (
          <p className="text-sm text-muted-foreground">Loading...</p>
        ) : history.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing published, failed, or cancelled yet.</p>
        ) : (
          history.map((post) => <ScheduledPostCard key={post.id} post={post} onChanged={mutate} />)
        )}
      </div>
    </div>
  );
}
