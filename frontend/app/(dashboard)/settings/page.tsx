"use client";

import { Suspense } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { SocialAccountsList } from "@/features/social-accounts";
import { useAuth } from "@/hooks/useAuth";

export default function SettingsPage() {
  const { user } = useAuth();

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-semibold">Settings</h1>

      <section className="space-y-3">
        <h2 className="text-lg font-medium">Profile</h2>
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
      </section>

      <section className="space-y-3">
        <div>
          <h2 className="text-lg font-medium">Connected accounts</h2>
          <p className="text-sm text-muted-foreground">
            The accounts your clips are posted to, from the editor&apos;s Post tab and the daily auto clip.
          </p>
        </div>
        <Suspense fallback={null}>
          <SocialAccountsList />
        </Suspense>
      </section>
    </div>
  );
}
