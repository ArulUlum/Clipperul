"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ExternalLink, Instagram, Music2, Play, Square, Youtube, type LucideIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { api, ApiError } from "@/lib/api";
import useSWR from "@/lib/useSWR";
import { formatDate } from "@/lib/utils";
import type {
  AutoClipGapMode,
  AutoClipGapUnit,
  AutoClipRun,
  AutoClipRunStatus,
  AutoClipSettings,
  SocialProvider,
} from "@/types";

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

const PLATFORM_ICONS: Record<SocialProvider, { icon: LucideIcon; className: string }> = {
  youtube: { icon: Youtube, className: "text-red-600" },
  tiktok: { icon: Music2, className: "text-foreground" },
  instagram: { icon: Instagram, className: "text-pink-600" },
};

// Mirrors the backend (service.GAP_UNIT_MINUTES / MAX_GAP_MINUTES): the API enforces the same rules.
const UNIT_MINUTES: Record<AutoClipGapUnit, number> = { minute: 1, hour: 60 };
const MAX_GAP_MINUTES = 12 * 60;

const unitLabel = (n: number, unit: AutoClipGapUnit) => `${unit}${n === 1 ? "" : "s"}`;

interface Form {
  enabled: boolean;
  category: string;
  region: string;
  run_time: string;
  max_clips: number;
  platforms: SocialProvider[];
  gap_mode: AutoClipGapMode;
  gap_unit: AutoClipGapUnit;
  gap_min: number;
  gap_max: number;
}

// Why the chosen wait between uploads can't be saved, or null if it's fine.
function gapProblem(f: Form): string | null {
  if (!Number.isInteger(f.gap_min) || f.gap_min < 1) return "Enter a whole number of 1 or more.";
  if (f.gap_mode === "random" && (!Number.isInteger(f.gap_max) || f.gap_max <= f.gap_min))
    return "The longer time must be more than the shorter one.";
  const longest = f.gap_mode === "random" ? f.gap_max : f.gap_min;
  if (longest * UNIT_MINUTES[f.gap_unit] > MAX_GAP_MINUTES)
    return `Uploads can be spaced at most ${MAX_GAP_MINUTES / 60} hours apart.`;
  return null;
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
      {run.status === "PUBLISHING" && run.next_publish_at && (
        <p className="text-xs text-muted-foreground">
          Next upload at {formatDate(run.next_publish_at)} · {run.remaining} left
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
  const [stopping, setStopping] = useState(false);
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
        platforms: settings.platforms,
        gap_mode: settings.gap_mode,
        gap_unit: settings.gap_unit,
        gap_min: settings.gap_min,
        gap_max: settings.gap_max,
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
  const togglePlatform = (id: SocialProvider, checked: boolean) =>
    set("platforms", checked ? [...form.platforms, id] : form.platforms.filter((p) => p !== id));
  const connectedPlatforms = settings.platform_options.filter((o) => o.connected).map((o) => o.id);

  const gapError = gapProblem(form);
  const setGapMode = (mode: AutoClipGapMode) =>
    setForm({
      ...form,
      gap_mode: mode,
      // A range needs two different numbers: start from double the single one.
      gap_max: mode === "random" && form.gap_max <= form.gap_min ? form.gap_min * 2 : form.gap_max,
    });
  const setGapUnit = (unit: AutoClipGapUnit) => {
    if (unit === form.gap_unit) return;
    // Keep the same wait when the unit changes (90 minutes -> 2 hours), never below 1.
    const convert = (n: number) => (unit === "hour" ? Math.max(1, Math.round(n / 60)) : n * 60);
    const gap_min = convert(form.gap_min);
    let gap_max = convert(form.gap_max);
    if (form.gap_mode === "random" && gap_max <= gap_min) gap_max = gap_min + 1;
    setForm({ ...form, gap_unit: unit, gap_min, gap_max });
  };
  const gapSummary =
    form.gap_mode === "static"
      ? `Each platform waits exactly ${form.gap_min} ${unitLabel(form.gap_min, form.gap_unit)} after an upload before its next one.`
      : `Each platform waits a random time between ${form.gap_min} and ${form.gap_max} ${unitLabel(form.gap_max, form.gap_unit)} after an upload before its next one, a new random time each time.`;

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (gapError) {
      setError(gapError);
      return;
    }
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      await api.put<AutoClipSettings>("/auto-clip", {
        ...form,
        // Only platforms that are still connected can be sent — a stale choice would be rejected.
        platforms: form.platforms.filter((p) => connectedPlatforms.includes(p)),
        gap_max: form.gap_mode === "random" ? form.gap_max : form.gap_min,
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

  const handleStop = async () => {
    if (
      !window.confirm(
        "Stop the current auto clip run? Nothing more will be chosen or posted, and uploads that haven't finished are cancelled. Posts already published stay up."
      )
    )
      return;
    setStopping(true);
    setError(null);
    setNotice(null);
    try {
      await api.post("/auto-clip/stop");
      setNotice("Stopped. Nothing more will be posted from this run.");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to stop the run.");
    } finally {
      setStopping(false);
      // Refresh either way: the run may have finished on its own just before the click.
      refetchSettings();
      refetchRuns();
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
      <CardContent className="space-y-5 pt-5">
        <p className="text-sm text-muted-foreground">
          Once a day, picks a trending YouTube video in your category (never one it already used), clips it to
          1:1 with auto-track off in the news layout (full-screen 9:16 with the title as a headline), and posts
          the clips to your connected accounts one at a time per platform, with the wait you choose below between
          uploads so they aren&apos;t bunched together.
        </p>

        {!settings.can_discover && (
          <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-600 dark:text-amber-400">
            To read trending videos, connect a YouTube account on the{" "}
            <Link href="/settings" className="underline">
              Settings
            </Link>{" "}
            page (or set <code>YOUTUBE_API_KEY</code> on the server).
          </p>
        )}
        {settings.can_discover && connectedPlatforms.length === 0 && (
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

          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">Post to</legend>
            <div className="flex flex-wrap gap-3">
              {settings.platform_options.map((option) => {
                const { icon: Icon, className } = PLATFORM_ICONS[option.id];
                const checked = option.connected && form.platforms.includes(option.id);
                return (
                  <label
                    key={option.id}
                    className={`flex items-center gap-2 rounded-md border border-border px-3 py-2 text-sm ${
                      option.connected ? "cursor-pointer hover:bg-muted" : "cursor-not-allowed opacity-50"
                    }`}
                    title={option.connected ? undefined : `Connect ${option.label} in Settings first`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={!option.connected}
                      onChange={(e) => togglePlatform(option.id, e.target.checked)}
                      className="h-4 w-4 rounded border-border accent-primary"
                    />
                    <Icon className={`h-4 w-4 ${option.connected ? className : "text-muted-foreground"}`} />
                    {option.label}
                    {!option.connected && <span className="text-xs text-muted-foreground">Not connected</span>}
                  </label>
                );
              })}
            </div>
            <p className="text-xs text-muted-foreground">
              {connectedPlatforms.length === 0 ? (
                <>
                  Connect an account on the{" "}
                  <Link href="/settings" className="underline">
                    Settings
                  </Link>{" "}
                  page to choose where clips are posted.
                </>
              ) : form.platforms.filter((p) => connectedPlatforms.includes(p)).length === 0 ? (
                "No platform selected — clips will still be created, but nothing will be posted."
              ) : (
                <>
                  Platforms that aren&apos;t connected can&apos;t be chosen — connect them on the{" "}
                  <Link href="/settings" className="underline">
                    Settings
                  </Link>{" "}
                  page.
                </>
              )}
            </p>
          </fieldset>

          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">Time between uploads</legend>
            <div className="inline-flex rounded-md border border-border p-1">
              {(
                [
                  ["static", "Fixed"],
                  ["random", "Random range"],
                ] as [AutoClipGapMode, string][]
              ).map(([mode, label]) => (
                <button
                  key={mode}
                  type="button"
                  onClick={() => setGapMode(mode)}
                  className={`rounded px-3 py-1.5 text-sm font-medium transition-colors ${
                    form.gap_mode === mode
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:bg-muted"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span>{form.gap_mode === "static" ? "Every" : "Between"}</span>
              <Input
                aria-label={form.gap_mode === "static" ? "Time between uploads" : "Shortest time between uploads"}
                type="number"
                min={1}
                step={1}
                value={form.gap_min || ""}
                onChange={(e) => set("gap_min", Number(e.target.value) || 0)}
                className="w-24"
              />
              {form.gap_mode === "random" && (
                <>
                  <span>and</span>
                  <Input
                    aria-label="Longest time between uploads"
                    type="number"
                    min={1}
                    step={1}
                    value={form.gap_max || ""}
                    onChange={(e) => set("gap_max", Number(e.target.value) || 0)}
                    className="w-24"
                  />
                </>
              )}
              <Select
                aria-label="Unit"
                value={form.gap_unit}
                onChange={(e) => setGapUnit(e.target.value as AutoClipGapUnit)}
                className="w-32"
              >
                <option value="minute">minutes</option>
                <option value="hour">hours</option>
              </Select>
            </div>
            <p className="text-xs text-muted-foreground">
              {gapSummary} Platforms don&apos;t wait for each other, and a platform&apos;s first upload of a run goes
              out straight away. Up to 12 hours.
            </p>
            {gapError && <p className="text-xs text-destructive">{gapError}</p>}
          </fieldset>

          {error && <p className="text-sm text-destructive">{error}</p>}
          {notice && <p className="text-sm text-emerald-600 dark:text-emerald-400">{notice}</p>}

          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" disabled={saving || gapError !== null}>
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
            {settings.in_progress && (
              <Button type="button" variant="destructive" onClick={handleStop} disabled={stopping}>
                <Square className="h-3.5 w-3.5 fill-current" />
                {stopping ? "Stopping..." : "Stop"}
              </Button>
            )}
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
