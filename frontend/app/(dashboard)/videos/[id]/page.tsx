import { VideoDetail } from "@/features/videos/VideoDetail";

export default async function VideoDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <VideoDetail videoId={id} />;
}
