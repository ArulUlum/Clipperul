"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Instagram, Music2, Trash2, Youtube, type LucideIcon } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { api, ApiError } from "@/lib/api";
import { cn, formatDate } from "@/lib/utils";
import useSWR from "@/lib/useSWR";
import type { SocialAccount, SocialProvider } from "@/types";

const PROVIDER_LABELS: Record<SocialProvider, string> = {
  youtube: "YouTube",
  tiktok: "TikTok",
  instagram: "Instagram",
};

const PROVIDER_ICONS: Record<SocialProvider, { icon: LucideIcon; className: string }> = {
  youtube: { icon: Youtube, className: "text-red-600" },
  tiktok: { icon: Music2, className: "text-foreground" },
  instagram: { icon: Instagram, className: "text-pink-600" },
};

// YouTube connects with its own OAuth; TikTok and Instagram connect through Zernio (the backend
// answers with a clear "set ZERNIO_API_KEY" message when that isn't configured yet).
const IMPLEMENTED_PROVIDERS: SocialProvider[] = ["youtube", "tiktok", "instagram"];
const COMING_SOON_PROVIDERS: SocialProvider[] = [];

export function SocialAccountsList() {
  const { data: accounts, mutate: refetch } = useSWR<SocialAccount[]>("/social-accounts");
  const searchParams = useSearchParams();
  const [connecting, setConnecting] = useState<SocialProvider | null>(null);
  const [disconnectingId, setDisconnectingId] = useState<string | null>(null);
  const [banner, setBanner] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  // The OAuth callback redirects back here with ?connected=1&provider=... or ?error=1&provider=...
  useEffect(() => {
    const provider = searchParams.get("provider") as SocialProvider | null;
    const label = provider ? PROVIDER_LABELS[provider] ?? provider : "Account";
    if (searchParams.get("connected")) {
      setBanner({ type: "success", text: `${label} connected successfully.` });
      refetch();
      window.history.replaceState({}, "", "/settings");
    } else if (searchParams.get("error")) {
      setBanner({ type: "error", text: `Failed to connect ${label}. Please try again.` });
      window.history.replaceState({}, "", "/settings");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  const handleConnect = async (provider: SocialProvider) => {
    setError(null);
    setConnecting(provider);
    try {
      const res = await api.post<{ authorization_url: string }>(`/social-accounts/${provider}/connect`);
      window.location.href = res.authorization_url;
    } catch (err) {
      setConnecting(null);
      setError(err instanceof ApiError ? err.message : `Failed to start ${PROVIDER_LABELS[provider]} connection.`);
    }
  };

  const handleDisconnect = async (account: SocialAccount) => {
    setDisconnectingId(account.id);
    try {
      await api.delete(`/social-accounts/${account.id}`);
      refetch();
    } finally {
      setDisconnectingId(null);
    }
  };

  return (
    <div className="space-y-4">
      {banner && (
        <div
          className={`rounded-md p-3 text-sm ${
            banner.type === "success" ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-destructive"
          }`}
        >
          {banner.text}
        </div>
      )}
      {error && <p className="text-sm text-destructive">{error}</p>}

      {accounts && accounts.length > 0 && (
        <div className="space-y-2">
          {accounts.map((account) => {
            const { icon: Icon, className } = PROVIDER_ICONS[account.provider];
            return (
            <Card key={account.id}>
              <CardContent className="flex items-center justify-between p-4">
                <div className="flex items-center gap-3">
                  <Icon className={cn("h-8 w-8", className)} />
                  <div>
                    <p className="text-sm font-medium">{account.account_name}</p>
                    <p className="text-xs text-muted-foreground">
                      {PROVIDER_LABELS[account.provider]}
                      {account.connected_at && ` · Connected ${formatDate(account.connected_at)}`}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Badge variant="success">Connected</Badge>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={disconnectingId === account.id}
                    onClick={() => handleDisconnect(account)}
                  >
                    <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                    {disconnectingId === account.id ? "Disconnecting..." : "Disconnect"}
                  </Button>
                </div>
              </CardContent>
            </Card>
            );
          })}
        </div>
      )}

      {IMPLEMENTED_PROVIDERS.filter(
        (provider) => !accounts?.some((a) => a.provider === provider)
      ).map((provider) => {
        const { icon: Icon } = PROVIDER_ICONS[provider];
        return (
        <Card key={provider}>
          <CardContent className="flex items-center justify-between p-4">
            <div className="flex items-center gap-3">
              <Icon className="h-8 w-8 text-muted-foreground" />
              <p className="text-sm font-medium">{PROVIDER_LABELS[provider]}</p>
            </div>
            <Button disabled={connecting === provider} onClick={() => handleConnect(provider)}>
              {connecting === provider ? "Redirecting..." : `Connect ${PROVIDER_LABELS[provider]}`}
            </Button>
          </CardContent>
        </Card>
        );
      })}

      {COMING_SOON_PROVIDERS.map((provider) => (
        <Card key={provider} className="opacity-60">
          <CardContent className="flex items-center justify-between p-4">
            <p className="text-sm font-medium">{PROVIDER_LABELS[provider]}</p>
            <Badge variant="muted">Coming soon</Badge>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
