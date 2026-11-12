import { AutoClipCard } from "@/features/auto-clip/AutoClipCard";

export default function AutoClipPage() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Daily auto clip</h1>
      <AutoClipCard />
    </div>
  );
}
