"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

/**
 * Minimal data-fetching hook (deliberately not the `swr` package — this app only needs
 * fetch + refetch, not its full caching/revalidation surface).
 */
export default function useSWR<T>(path: string | null, options?: { intervalMs?: number }) {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<unknown>(null);
  const [isLoading, setIsLoading] = useState(true);

  const revalidate = useCallback(async () => {
    if (!path) return;
    try {
      const result = await api.get<T>(path);
      setData(result);
      setError(null);
    } catch (err) {
      setError(err);
    } finally {
      setIsLoading(false);
    }
  }, [path]);

  useEffect(() => {
    setIsLoading(true);
    revalidate();

    if (options?.intervalMs) {
      const id = setInterval(revalidate, options.intervalMs);
      return () => clearInterval(id);
    }
  }, [revalidate, options?.intervalMs]);

  return { data, error, isLoading, mutate: revalidate };
}
