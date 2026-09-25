"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, authToken, clearToken, setToken } from "@/lib/api";
import type { User } from "@/types";

export function useAuth() {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const loadUser = useCallback(async () => {
    if (!authToken()) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      const me = await api.get<User>("/auth/me");
      setUser(me);
    } catch {
      clearToken();
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadUser();
  }, [loadUser]);

  const login = useCallback(
    async (email: string, password: string) => {
      const res = await api.post<{ access_token: string }>("/auth/login", { email, password });
      setToken(res.access_token);
      await loadUser();
      router.push("/dashboard");
    },
    [loadUser, router]
  );

  const register = useCallback(
    async (email: string, password: string, fullName?: string) => {
      const res = await api.post<{ access_token: string }>("/auth/register", {
        email,
        password,
        full_name: fullName || undefined,
      });
      setToken(res.access_token);
      await loadUser();
      router.push("/dashboard");
    },
    [loadUser, router]
  );

  const logout = useCallback(() => {
    clearToken();
    setUser(null);
    router.push("/login");
  }, [router]);

  return { user, loading, login, register, logout };
}
