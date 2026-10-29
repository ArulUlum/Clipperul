"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ExternalLink, Play } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { api, ApiError } from "@/lib/api";
import useSWR from "@/lib/useSWR";
import { formatDate } from "@/lib/utils";
import type { AutoClipRun, AutoClipRunStatus, AutoClipSettings } from "@/types";

const IDLE_POLL_MS = 15_000;
const LIVE_POLL_MS = 4_000;

const STATUS_BADGE: Record<AutoClipRunStatus, { label: string; variant: "default" | "muted" | "success" | "warning" | "destructive" }> = {
  SELECTING: { label: "Choosing video", variant: "default" },
  STARTING: { label: "Choosing video", variant: "default" },
  PROCESSING: { label: "Clipping", variant: "warning" },
  PUBLISHING: { label: "Posting", variant: "warning" },
  COMPLETED: { label: "Done", variant: "success" },
  FAILED: { label: "Failed", variant: "destructive" },
  CANCELLED: { label: "Stopped", variant: "muted" },
};

interface Form {
  enabled: boolean;
  category: string;
  region: string;
  run_time: string;
  max_clips: number;
}

function RunRow({ run }: { run: AutoClipRun }) {
  const badge = STATUS_BADGE[run.status];
  const posting = run.status === "PUBLISHING" || run.status === "COMPLETED";
  return (
    <li className="space-y-1 border-t border-border py-3 first:border-t-0">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-medium">
          {run.run_date} <span className="font-normal text-muted-foreground">· {run.trigger === "manual" ? "Manual" : "Scheduled"}</span>
        </p>
        <Badge variant={badge.variant}>{badge.label}</Badge>
      </div>
      {run.source_title && (
        <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
          <span className="truncate">{run.source_title}</span>
          {run.source_url && (
            <a href={run.source_url} target="_blank" rel="noreferrer" className="shrink-0 text-primary" aria-label="Open source video">
              <ExternalLink className="h-3 w-3" />
            </a>
          )}
        </p>
      )}
      {posting && run.clips_total > 0 && (
        <p className="text-xs tabular-nums text-muted-foreground">
          {run.published} of {run.clips_total} posted{run.failed > 0 && ` · ${run.failed} failed`}
        </p>
      )}
      {run.error && <p className="text-xs text-destructive">{run.error}</p>}
      {run.note && <p className="text-xs text-muted-foreground">{run.note}</p>}
      {run.video_id && (
        <Link href={`/videos/${run.video_id}`} className="inline-block text-xs text-primary hover:underline">
          View clips
        </Link>
      )}
    </li>
  );
}

export function AutoClipCard() {
  const [intervalMs, setIntervalMs] = useState(IDLE_POLL_MS);
  const { data: settings, mutate: refetchSettings } = useSWR<AutoClipSettings>("/auto-clip", { intervalMs });
  const { data: runs, mutate: refetchRuns } = useSWR<AutoClipRun[]>("/auto-clip/runs", { intervalMs });

  const [form, setForm] = useState<Form | null>(null);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Seed the form once from the server; after that the user's edits own it.
  useEffect(() => {
    if (settings && form === null) {
      setForm({
        enabled: settings.enabled,
        category: settings.category,
        region: settings.region,
        run_time: settings.run_time,
        max_clips: settings.max_clips,
      });
    }
  }, [settings, form]);

  // Poll quickly while a run is active so its progress stays current.
  const live = settings?.in_progress ?? false;
  useEffect(() => {
    setIntervalMs(live ? LIVE_POLL_MS : IDLE_POLL_MS);
  }, [live]);

  if (!settings || !form) return null;

  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm({ ...form, [key]: value });

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      await api.put<AutoClipSettings>("/auto-clip", {
        ...form,
        region: form.region.toUpperCase(),
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
      });
      setNotice(
        form.enabled ? `Saved. It will run every day at ${form.run_time}.` : "Saved. The daily auto clip is off."
      );
      refetchSettings();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save settings.");
    } finally {
      setSaving(false);
    }
  };

  const handleRunNow = async () => {
    setRunning(true);
    setError(null);
    setNotice(null);
    try {
      await api.post("/auto-clip/run");
      setNotice("Started — it will pick today's trending video and clip it.");
      refetchSettings();
      refetchRuns();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to start a run.");
    } finally {
      setRunning(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Daily auto clip</CardTitle>
      </CardHeader>
      <CardContent className="space-y-5">
        <p className="text-sm text-muted-foreground">
          Once a day, picks a trending YouTube video in your category (never one it already used), clips it to
          1:1 with auto-track off, and posts the clips one by one to your connected YouTube accounts.
        </p>

        {!settings.can_discover && (
          <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-600 dark:text-amber-400">
            To read trending videos, connect a YouTube account on the{" "}
            <Link href="/accounts" className="underline">
              Accounts
            </Link>{" "}
            page (or set <code>YOUTUBE_API_KEY</code> on the server).
          </p>
        )}
        {settings.can_discover && settings.publish_accounts === 0 && (
          <p className="rounded-md bg-muted p-3 text-sm text-muted-foreground">
            No connected account can post yet — clips will still be created, but nothing will be published.
          </p>
        )}

        <form onSubmit={handleSave} className="space-y-4">
          <label className="flex items-center gap-2 text-sm font-medium">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => set("enabled", e.target.checked)}
              className="h-4 w-4 rounded border-border accent-primary"
            />
            Run automatically every day
          </label>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="ac-category">Category</Label>
              <Select id="ac-category" value={form.category} onChange={(e) => set("category", e.target.value)}>
                {settings.categories.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.label}
                  </option>
                ))}
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="ac-region">Country (trending in)</Label>
              <Input
                id="ac-region"
                value={form.region}
                onChange={(e) => set("region", e.target.value.toUpperCase().slice(0, 2))}
                maxLength={2}
                placeholder="ID"
                required
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="ac-time">Run at</Label>
              <Input
                id="ac-time"
                type="time"
                value={form.run_time}
                onChange={(e) => set("run_time", e.target.value)}
                required
              />
              <p className="text-xs text-muted-foreground">
                Your local time ({Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"}).
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="ac-max">Max clips per day</Label>
              <Input
                id="ac-max"
                type="number"
                min={1}
                max={8}
                value={form.max_clips}
                onChange={(e) => set("max_clips", Math.max(1, Math.min(8, Number(e.target.value) || 1)))}
                required
              />
              <p className="text-xs text-muted-foreground">
                Best-scoring first, to each account. YouTube caps how many videos a project can upload per day —
                lower this if uploads fail with a quota error.
              </p>
            </div>
          </div>

          {error && <p className="text-sm text-destructive">{error}</p>}
          {notice && <p className="text-sm text-emerald-600 dark:text-emerald-400">{notice}</p>}

          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" disabled={saving}>
              {saving ? "Saving..." : "Save"}
            </Button>
            <Button
              type="button"
              variant="outline"
              onClick={handleRunNow}
              disabled={running || settings.in_progress || !settings.can_discover}
              title={settings.in_progress ? "A run is already in progress" : undefined}
            >
              <Play className="h-3.5 w-3.5" />
              {settings.in_progress ? "Running..." : "Run now"}
            </Button>
          </div>
        </form>

        {runs && runs.length > 0 && (
          <div>
            <p className="mb-1 text-sm font-medium">Recent runs</p>
            <ul>
              {runs.map((run) => (
                <RunRow key={run.id} run={run} />
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
