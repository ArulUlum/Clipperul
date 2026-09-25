"use client";

import Link from "next/link";
import { CalendarClock, Loader2, Scissors, Send, Video } from "lucide-react";
import { StatCard } from "@/components/shared/StatCard";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { formatDate } from "@/lib/utils";
import useSWR from "@/lib/useSWR";
import type { DashboardStats, Video as VideoType } from "@/types";

export default function DashboardPage() {
  const { data: stats } = useSWR<DashboardStats>("/dashboard/stats", { intervalMs: 15000 });
  const { data: videos } = useSWR<VideoType[]>("/videos");

  const recent = (videos ?? []).slice(0, 5);

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Dashboard</h1>
        <Link href="/videos/upload">
          <Button>Upload video</Button>
        </Link>
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <StatCard label="Total Videos" value={stats?.total_videos ?? "-"} icon={Video} />
        <StatCard label="Total Clips" value={stats?.total_clips ?? "-"} icon={Scissors} />
        <StatCard label="Scheduled Posts" value={stats?.scheduled_posts ?? "-"} icon={CalendarClock} />
        <StatCard label="Published Posts" value={stats?.published_posts ?? "-"} icon={Send} />
        <StatCard label="Processing Jobs" value={stats?.processing_jobs ?? "-"} icon={Loader2} />
      </div>

      <section>
        <h2 className="mb-3 text-lg font-semibold">Recent activity</h2>
        <Card>
          <CardContent className="divide-y divide-border p-0">
            {recent.length === 0 && (
              <p className="p-5 text-sm text-muted-foreground">No videos uploaded yet.</p>
            )}
            {recent.map((video) => (
              <Link
                key={video.id}
                href={`/videos/${video.id}`}
                className="flex items-center justify-between px-5 py-3 text-sm hover:bg-muted"
              >
                <span className="font-medium">{video.title}</span>
                <span className="text-xs text-muted-foreground">
                  {video.status} &middot; {formatDate(video.created_at)}
                </span>
              </Link>
            ))}
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
