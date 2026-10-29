"use client";

import { Card, CardContent } from "@/components/ui/card";
import { AutoClipCard } from "@/features/auto-clip/AutoClipCard";
import { useAuth } from "@/hooks/useAuth";

export default function SettingsPage() {
  const { user } = useAuth();

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Settings</h1>
      <Card>
        <CardContent className="space-y-4 p-6">
          <div>
            <p className="text-xs text-muted-foreground">Email</p>
            <p className="text-sm font-medium">{user?.email}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Full name</p>
            <p className="text-sm font-medium">{user?.full_name || "-"}</p>
          </div>
        </CardContent>
      </Card>

      <AutoClipCard />
    </div>
  );
}
