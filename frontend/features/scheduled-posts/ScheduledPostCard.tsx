"use client";

import { useState } from "react";
import { ExternalLink, Pencil, RotateCw, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { api, ApiError } from "@/lib/api";
import { formatDate } from "@/lib/utils";
import type { ScheduledPost, ScheduledPostStatus } from "@/types";

const STATUS_BADGE: Record<ScheduledPostStatus, { label: string; variant: "default" | "muted" | "success" | "warning" | "destructive" }> = {
  SCHEDULED: { label: "Scheduled", variant: "default" },
  PROCESSING: { label: "Processing", variant: "warning" },
  UPLOADING: { label: "Uploading", variant: "warning" },
  PUBLISHED: { label: "Published", variant: "success" },
  FAILED: { label: "Failed", variant: "destructive" },
  CANCELLED: { label: "Cancelled", variant: "muted" },
};

export function ScheduledPostCard({
  post,
  onChanged,
  showThumbnail = true,
}: {
  post: ScheduledPost;
  onChanged: () => void;
  showThumbnail?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(post.title);
  const [scheduledAtLocal, setScheduledAtLocal] = useState(() => {
    const d = new Date(post.scheduled_at);
    const pad = (n: number) => n.toString().padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // PROCESSING + an error from the last attempt = it failed and is sitting out the retry
  // backoff (minutes to hours), not actively working — say so instead of looking busy.
  const waitingToRetry = post.status === "PROCESSING" && !!post.latest_error;
  const preparing = post.status === "PROCESSING" && !post.latest_error;
  const uploading = post.status === "UPLOADING";
  const badge = waitingToRetry ? { label: "Retrying", variant: "warning" as const } : STATUS_BADGE[post.status];
  const canEdit = post.status === "SCHEDULED";
  // Cancel works at any point before it's published — including mid-upload and while waiting out
  // an automatic retry (cancelling stops the retrying too). Retry is the way back afterwards.
  const canCancel = post.status === "SCHEDULED" || post.status === "PROCESSING" || post.status === "UPLOADING";
  const canRetry = post.status === "FAILED" || post.status === "CANCELLED";

  const handleSave = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.patch(`/scheduled-posts/${post.id}`, {
        title,
        scheduled_at: new Date(scheduledAtLocal).toISOString(),
      });
      setEditing(false);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save changes.");
    } finally {
      setBusy(false);
    }
  };

  const handleCancel = async () => {
    // An in-flight upload restarts from 0% if retried, so a stray click shouldn't kill it silently.
    if (post.status !== "SCHEDULED" && !window.confirm("Stop this post? You can retry it afterwards.")) return;
    setBusy(true);
    setError(null);
    try {
      await api.delete(`/scheduled-posts/${post.id}`);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to cancel post.");
    } finally {
      setBusy(false);
    }
  };

  const handleRetry = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/scheduled-posts/${post.id}/retry`);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to retry post.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardContent className="flex items-center gap-4 p-4">
        {showThumbnail &&
          (post.clip_thumbnail_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={post.clip_thumbnail_url} alt="" className="h-14 w-14 shrink-0 rounded-md object-cover" />
          ) : (
            <div className="h-14 w-14 shrink-0 rounded-md bg-muted" />
          ))}

        <div className="min-w-0 flex-1 space-y-1">
          {editing ? (
            <div className="flex flex-wrap items-center gap-2">
              <Input value={title} onChange={(e) => setTitle(e.target.value)} className="h-8 max-w-xs" />
              <Input
                type="datetime-local"
                value={scheduledAtLocal}
                onChange={(e) => setScheduledAtLocal(e.target.value)}
                className="h-8 w-auto"
              />
            </div>
          ) : (
            <p className="truncate text-sm font-medium">{post.title}</p>
          )}
          <p className="truncate text-xs text-muted-foreground">
            {post.account_name} ({post.platform}) · {formatDate(post.scheduled_at)}
          </p>
          {post.status === "FAILED" && post.latest_error && (
            <p className="truncate text-xs text-destructive">{post.latest_error}</p>
          )}
          {waitingToRetry && (
            <p className="truncate text-xs text-muted-foreground" title={post.latest_error ?? undefined}>
              Upload failed, retrying automatically — {post.latest_error}
            </p>
          )}
          {(preparing || uploading) && (
            <div className="space-y-1 pt-1">
              <Progress value={post.progress} indeterminate={preparing} />
              <p className="text-xs tabular-nums text-muted-foreground">
                {preparing ? "Preparing upload..." : `Uploading to ${post.platform} — ${post.progress}%`}
              </p>
            </div>
          )}
          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>

        <div className="flex shrink-0 items-center gap-2">
          <Badge variant={badge.variant}>{badge.label}</Badge>
          {post.status === "PUBLISHED" && post.external_url && (
            <a
              href={post.external_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
            >
              View <ExternalLink className="h-3 w-3" />
            </a>
          )}
          {canEdit && editing && (
            <Button size="sm" onClick={handleSave} disabled={busy}>
              Save
            </Button>
          )}
          {canEdit && !editing && (
            <Button size="sm" variant="outline" onClick={() => setEditing(true)} disabled={busy}>
              <Pencil className="h-3.5 w-3.5" />
            </Button>
          )}
          {canRetry && (
            <Button size="sm" variant="outline" onClick={handleRetry} disabled={busy}>
              <RotateCw className="h-3.5 w-3.5" />
              Retry
            </Button>
          )}
          {canCancel && (
            <Button
              size="sm"
              variant="outline"
              onClick={handleCancel}
              disabled={busy}
              title={post.status === "SCHEDULED" ? "Cancel" : "Cancel — stops the upload and any automatic retries"}
              aria-label="Cancel post"
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
