"use client";

import { ScheduledPostCard } from "@/features/scheduled-posts/ScheduledPostCard";
import { ScheduledPostForm } from "@/features/scheduled-posts/ScheduledPostForm";
import { useScheduledPosts } from "@/features/scheduled-posts/useScheduledPosts";

const ACTIVE_STATUSES = new Set(["SCHEDULED", "PROCESSING", "UPLOADING"]);

/** The clip editor's "Post" tab: post or schedule THIS clip, and follow what happened to
 * the posts already made from it, without leaving the editor. */
export function ClipPostPanel({
  clipId,
  defaultTitle,
  defaultCaption,
  blockedReason,
}: {
  clipId: string;
  defaultTitle: string;
  defaultCaption: string;
  blockedReason: string | null;
}) {
  // Polled: a post's status changes in the background (beat/worker claim and publish it)
  // with nothing here to push an update.
  const { data: allPosts, mutate } = useScheduledPosts();

  const posts = (allPosts ?? []).filter((p) => p.clip_id === clipId);
  const active = posts
    .filter((p) => ACTIVE_STATUSES.has(p.status))
    .sort((a, b) => a.scheduled_at.localeCompare(b.scheduled_at));
  const history = posts
    .filter((p) => !ACTIVE_STATUSES.has(p.status))
    .sort((a, b) => (b.published_at ?? b.scheduled_at).localeCompare(a.published_at ?? a.scheduled_at));

  return (
    <div className="space-y-5">
      <ScheduledPostForm
        clipId={clipId}
        defaultTitle={defaultTitle}
        defaultCaption={defaultCaption}
        blockedReason={blockedReason}
        onCreated={mutate}
      />

      {(active.length > 0 || history.length > 0) && (
        <div className="space-y-2">
          <p className="text-sm font-medium">Posts from this clip</p>
          {[...active, ...history].map((post) => (
            <ScheduledPostCard key={post.id} post={post} onChanged={mutate} showThumbnail={false} />
          ))}
        </div>
      )}
    </div>
  );
}
