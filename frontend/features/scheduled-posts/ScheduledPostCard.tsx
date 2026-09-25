"use client";

import { useState } from "react";
import { ExternalLink, Pencil, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
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

export function ScheduledPostCard({ post, onChanged }: { post: ScheduledPost; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState(post.title);
  const [scheduledAtLocal, setScheduledAtLocal] = useState(() => {
    const d = new Date(post.scheduled_at);
    const pad = (n: number) => n.toString().padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const badge = STATUS_BADGE[post.status];
  const canEdit = post.status === "SCHEDULED";

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
    setBusy(true);
    try {
      await api.delete(`/scheduled-posts/${post.id}`);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to cancel post.");
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardContent className="flex items-center gap-4 p-4">
        {post.clip_thumbnail_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={post.clip_thumbnail_url} alt="" className="h-14 w-14 shrink-0 rounded-md object-cover" />
        ) : (
          <div className="h-14 w-14 shrink-0 rounded-md bg-muted" />
        )}

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
          {canEdit && (
            <Button size="sm" variant="outline" onClick={handleCancel} disabled={busy}>
              <X className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
