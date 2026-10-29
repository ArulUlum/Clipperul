"use client";

import { ScheduledPostCard } from "@/features/scheduled-posts/ScheduledPostCard";
import { useScheduledPosts } from "@/features/scheduled-posts/useScheduledPosts";

const ACTIVE_STATUSES = new Set(["SCHEDULED", "PROCESSING", "UPLOADING"]);

export function PublishedView() {
  // Polled: with no separate Scheduled Posts page, this is the one place that shows every post
  // across all clips — including ones still queued or mid-upload, whose status changes in the
  // background.
  const { data: posts, mutate } = useScheduledPosts();

  const upcoming = (posts ?? [])
    .filter((p) => ACTIVE_STATUSES.has(p.status))
    .sort((a, b) => a.scheduled_at.localeCompare(b.scheduled_at));
  const history = (posts ?? [])
    .filter((p) => !ACTIVE_STATUSES.has(p.status))
    .sort((a, b) => (b.published_at ?? b.scheduled_at).localeCompare(a.published_at ?? a.scheduled_at));

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Published</h1>
      <p className="-mt-3 text-sm text-muted-foreground">
        Post or schedule a clip from its editor (Clips → open a clip → Post tab).
      </p>

      {posts === undefined ? (
        <p className="text-sm text-muted-foreground">Loading...</p>
      ) : upcoming.length === 0 && history.length === 0 ? (
        <p className="text-sm text-muted-foreground">Nothing posted or scheduled yet.</p>
      ) : (
        <>
          {upcoming.length > 0 && (
            <section className="space-y-2">
              <h2 className="text-sm font-medium text-muted-foreground">Upcoming &amp; in progress</h2>
              {upcoming.map((post) => (
                <ScheduledPostCard key={post.id} post={post} onChanged={mutate} />
              ))}
            </section>
          )}
          {history.length > 0 && (
            <section className="space-y-2">
              <h2 className="text-sm font-medium text-muted-foreground">History</h2>
              {history.map((post) => (
                <ScheduledPostCard key={post.id} post={post} onChanged={mutate} />
              ))}
            </section>
          )}
        </>
      )}
    </div>
  );
}
