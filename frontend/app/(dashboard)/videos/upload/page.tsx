import { UploadForm } from "@/features/videos/UploadForm";

export default function UploadVideoPage() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Upload video</h1>
      <UploadForm />
    </div>
  );
}
