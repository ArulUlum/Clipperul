import { Suspense } from "react";
import { SocialAccountsList } from "@/features/social-accounts";

export default function AccountsPage() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Accounts</h1>
      <Suspense fallback={null}>
        <SocialAccountsList />
      </Suspense>
    </div>
  );
}
