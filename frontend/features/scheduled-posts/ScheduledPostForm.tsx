"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import useSWR from "@/lib/useSWR";
import type { Clip, SocialAccount } from "@/types";

/** Local <input type="datetime-local"> value, at least 5 minutes from now — used both as the
 * input's `min` and its initial value, so the form never starts pre-filled with a rejected time. */
function defaultScheduledAtLocal(): string {
  const d = new Date(Date.now() + 5 * 60 * 1000);
  d.setSeconds(0, 0);
  const pad = (n: number) => n.toString().padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function ScheduledPostForm({ onCreated }: { onCreated: () => void }) {
  const { data: clips } = useSWR<Clip[]>("/clips");
  const { data: accounts } = useSWR<SocialAccount[]>("/social-accounts");
  const connectedAccounts = (accounts ?? []).filter((a) => a.connected_at);

  const [clipId, setClipId] = useState("");
  const [accountIds, setAccountIds] = useState<string[]>([]);
  const [publishNow, setPublishNow] = useState(false);
  const [scheduledAt, setScheduledAt] = useState(defaultScheduledAtLocal());
  const [title, setTitle] = useState("");
  const [caption, setCaption] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Default the post title to the clip's own title the first time a clip is picked, without
  // stomping on a title the user already typed for a previous selection.
  useEffect(() => {
    if (!title && clipId) {
      const clip = clips?.find((c) => c.id === clipId);
      if (clip) setTitle(clip.title);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clipId, clips]);

  const toggleAccount = (id: string) => {
    setAccountIds((prev) => (prev.includes(id) ? prev.filter((a) => a !== id) : [...prev, id]));
  };

  const canSubmit = clipId && accountIds.length > 0 && (publishNow || scheduledAt) && title.trim().length > 0;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      await api.post("/scheduled-posts", {
        clip_id: clipId,
        social_account_ids: accountIds,
        ...(publishNow ? { publish_now: true } : { scheduled_at: new Date(scheduledAt).toISOString() }),
        title: title.trim(),
        caption: caption.trim() || null,
      });
      setClipId("");
      setAccountIds([]);
      setPublishNow(false);
      setScheduledAt(defaultScheduledAtLocal());
      setTitle("");
      setCaption("");
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to schedule post.");
    } finally {
      setSubmitting(false);
    }
  };

  if (connectedAccounts.length === 0) {
    return (
      <Card>
        <CardContent className="p-5 text-sm text-muted-foreground">
          Connect a social account on the <a href="/accounts" className="text-primary underline">Accounts</a> page
          before scheduling a post.
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Schedule a new post</CardTitle>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="clip">Clip</Label>
            <Select id="clip" value={clipId} onChange={(e) => setClipId(e.target.value)} required>
              <option value="" disabled>
                {clips === undefined ? "Loading clips..." : clips.length === 0 ? "No ready clips yet" : "Select a clip"}
              </option>
              {(clips ?? []).map((clip) => (
                <option key={clip.id} value={clip.id}>
                  {clip.title} ({Math.round(clip.duration_seconds)}s, {clip.aspect_ratio})
                </option>
              ))}
            </Select>
          </div>

          <div className="space-y-1.5 sm:col-span-2">
            <Label>Accounts</Label>
            <p className="text-xs text-muted-foreground">
              Pick one or more — the same clip posts to every account you select.
            </p>
            <div className="flex flex-wrap gap-2">
              {connectedAccounts.map((account) => {
                const checked = accountIds.includes(account.id);
                return (
                  <button
                    key={account.id}
                    type="button"
                    onClick={() => toggleAccount(account.id)}
                    aria-pressed={checked}
                    className={cn(
                      "rounded-md border px-3 py-1.5 text-sm transition-colors",
                      checked
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-border text-muted-foreground hover:bg-muted"
                    )}
                  >
                    {account.account_name} ({account.provider})
                  </button>
                );
              })}
            </div>
          </div>

          <div className="space-y-1.5 sm:col-span-2">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={publishNow}
                onChange={(e) => setPublishNow(e.target.checked)}
                className="h-4 w-4 rounded border-border accent-primary"
              />
              Post now instead of scheduling
            </label>
          </div>

          {!publishNow && (
            <div className="space-y-1.5">
              <Label htmlFor="scheduled_at">Date &amp; time</Label>
              <Input
                id="scheduled_at"
                type="datetime-local"
                value={scheduledAt}
                min={defaultScheduledAtLocal()}
                onChange={(e) => setScheduledAt(e.target.value)}
                required={!publishNow}
              />
            </div>
          )}

          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="title">Title</Label>
            <Input id="title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={255} required />
          </div>

          <div className="space-y-1.5 sm:col-span-2">
            <Label htmlFor="caption">Caption</Label>
            <textarea
              id="caption"
              value={caption}
              onChange={(e) => setCaption(e.target.value)}
              rows={2}
              className="flex w-full rounded-md border border-border bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
              placeholder="Optional"
            />
          </div>

          {error && <p className="text-sm text-destructive sm:col-span-2">{error}</p>}

          <div className="sm:col-span-2">
            <Button type="submit" disabled={!canSubmit || submitting}>
              {submitting ? (publishNow ? "Posting..." : "Scheduling...") : publishNow ? "Post now" : "Schedule post"}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
