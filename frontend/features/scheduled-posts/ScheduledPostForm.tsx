"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import useSWR from "@/lib/useSWR";
import type { SocialAccount } from "@/types";

/** Local <input type="datetime-local"> value, at least 5 minutes from now — used both as the
 * input's `min` and its initial value, so the form never starts pre-filled with a rejected time. */
function defaultScheduledAtLocal(): string {
  const d = new Date(Date.now() + 5 * 60 * 1000);
  d.setSeconds(0, 0);
  const pad = (n: number) => n.toString().padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Post/schedule form for ONE already-chosen clip (it lives inside the clip editor's "Post" tab).
 * `blockedReason` disables submitting while something makes posting unsafe — e.g. unsaved edits,
 * since a post always uploads the clip's last *rendered* version, not what's merely on screen. */
export function ScheduledPostForm({
  clipId,
  defaultTitle,
  defaultCaption,
  blockedReason,
  onCreated,
}: {
  clipId: string;
  defaultTitle: string;
  defaultCaption: string;
  blockedReason?: string | null;
  onCreated: () => void;
}) {
  const { data: accounts } = useSWR<SocialAccount[]>("/social-accounts");
  const connectedAccounts = (accounts ?? []).filter((a) => a.connected_at);

  const [accountIds, setAccountIds] = useState<string[]>([]);
  const [publishNow, setPublishNow] = useState(true);
  const [scheduledAt, setScheduledAt] = useState(defaultScheduledAtLocal());
  const [title, setTitle] = useState(defaultTitle);
  const [caption, setCaption] = useState(defaultCaption);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const toggleAccount = (id: string) => {
    setAccountIds((prev) => (prev.includes(id) ? prev.filter((a) => a !== id) : [...prev, id]));
  };

  const canSubmit = !blockedReason && accountIds.length > 0 && (publishNow || scheduledAt) && title.trim().length > 0;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    setNotice(null);
    try {
      await api.post("/scheduled-posts", {
        clip_id: clipId,
        social_account_ids: accountIds,
        ...(publishNow ? { publish_now: true } : { scheduled_at: new Date(scheduledAt).toISOString() }),
        title: title.trim(),
        caption: caption.trim() || null,
      });
      setNotice(publishNow ? "Posting started — progress shows below." : "Scheduled — it will post at the chosen time.");
      setAccountIds([]);
      setScheduledAt(defaultScheduledAtLocal());
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create post.");
    } finally {
      setSubmitting(false);
    }
  };

  if (accounts !== undefined && connectedAccounts.length === 0) {
    return (
      <p className="rounded-md bg-muted p-3 text-sm text-muted-foreground">
        Connect a social account on the{" "}
        <a href="/accounts" className="text-primary underline">
          Accounts
        </a>{" "}
        page before posting.
      </p>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {blockedReason && (
        <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-600 dark:text-amber-400">
          {blockedReason}
        </p>
      )}

      <div className="space-y-1.5">
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

      <div className="flex flex-wrap items-end gap-4">
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={publishNow}
            onChange={(e) => setPublishNow(e.target.checked)}
            className="h-4 w-4 rounded border-border accent-primary"
          />
          Post now
        </label>

        {!publishNow && (
          <div className="space-y-1.5">
            <Label htmlFor="scheduled_at">Date &amp; time</Label>
            <Input
              id="scheduled_at"
              type="datetime-local"
              value={scheduledAt}
              min={defaultScheduledAtLocal()}
              onChange={(e) => setScheduledAt(e.target.value)}
              required
              className="w-auto"
            />
          </div>
        )}
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="post-title">Post title</Label>
        <Input id="post-title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={255} required />
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="post-caption">Post caption</Label>
        <textarea
          id="post-caption"
          value={caption}
          onChange={(e) => setCaption(e.target.value)}
          rows={2}
          className="flex w-full rounded-md border border-border bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          placeholder="Optional"
        />
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}
      {notice && <p className="text-sm text-emerald-600 dark:text-emerald-400">{notice}</p>}

      <Button type="submit" disabled={!canSubmit || submitting}>
        {submitting ? (publishNow ? "Posting..." : "Scheduling...") : publishNow ? "Post now" : "Schedule post"}
      </Button>
    </form>
  );
}
