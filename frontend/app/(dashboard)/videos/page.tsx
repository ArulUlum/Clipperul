import Link from "next/link";
import { Button } from "@/components/ui/button";
import { VideoList } from "@/features/videos/VideoList";

export default function VideosPage() {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Videos</h1>
        <Link href="/videos/upload">
          <Button>Upload video</Button>
        </Link>
      </div>
      <VideoList />
    </div>
  );
}
