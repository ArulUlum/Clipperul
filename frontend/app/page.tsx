"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { authToken } from "@/lib/api";

export default function RootPage() {
  const router = useRouter();

  useEffect(() => {
    router.replace(authToken() ? "/dashboard" : "/login");
  }, [router]);

  return null;
}
