/**
 * Auth context — manages JWT tokens, user state, and authenticated workspace context.
 * Wraps the entire app. If no token is stored, redirects to login.
 */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { useQueryClient } from "@tanstack/react-query";

import { AuthContext, type User } from "./authState";

const API = "/api/v1";

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const revision = useRef(0);
  const clearSessionData = useCallback(() => {
    // Cancel first so delayed requests cannot populate the next account's cache.
    void queryClient.cancelQueries();
    queryClient.clear();
    sessionStorage.removeItem("waldo_compare_session");
    sessionStorage.removeItem("waldo_compare_meta");
    sessionStorage.removeItem("waldo_workflow_template");
  }, [queryClient]);
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(() => localStorage.getItem("waldo_token"));
  const [loading, setLoading] = useState(() => Boolean(localStorage.getItem("waldo_token")));

  // Fetch user info when token changes
  useEffect(() => {
    if (!token) return;

    const controller = new AbortController();
    const requestRevision = revision.current;
    let status = 0;
    fetch(`${API}/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
      signal: controller.signal,
    })
      .then((res) => {
        status = res.status;
        if (!res.ok) throw new Error("Invalid token");
        return res.json();
      })
      .then((data) => {
        if (controller.signal.aborted || requestRevision !== revision.current) return;
        setUser(data);
        setLoading(false);
      })
      .catch(() => {
        if (controller.signal.aborted || requestRevision !== revision.current) return;
        if (status === 401 || status === 403) {
          clearSessionData();
          // Token invalid — clear auth
          localStorage.removeItem("waldo_token");
          localStorage.removeItem("waldo_refresh");
          setToken(null);
          setUser(null);
        }
        // Network errors: keep token, just stop loading
        setLoading(false);
      });
    return () => controller.abort();
  }, [token, clearSessionData]);

  const login = useCallback(async (email: string, password: string) => {
    const requestRevision = ++revision.current;
    const res = await fetch(`${API}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Login failed");
    }
    const data = await res.json();

    // Fetch user before updating token state so the route guard sees
    // a valid user immediately — avoids the redirect-back-to-login race.
    const meRes = await fetch(`${API}/auth/me`, {
      headers: { Authorization: `Bearer ${data.access_token}` },
    });
    if (!meRes.ok) throw new Error("Failed to fetch user");
    const me = await meRes.json();
    if (requestRevision !== revision.current) throw new Error("Sign-in was interrupted");
    clearSessionData();
    localStorage.setItem("waldo_token", data.access_token);
    localStorage.setItem("waldo_refresh", data.refresh_token);
    setUser(me);
    setToken(data.access_token);
    setLoading(false);
  }, [clearSessionData]);

  const register = useCallback(async (email: string, password: string, displayName: string) => {
    const requestRevision = ++revision.current;
    const res = await fetch(`${API}/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password, display_name: displayName }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Registration failed");
    }
    const data = await res.json();

    const meRes = await fetch(`${API}/auth/me`, {
      headers: { Authorization: `Bearer ${data.access_token}` },
    });
    if (!meRes.ok) throw new Error("Failed to fetch user");
    const me = await meRes.json();
    if (requestRevision !== revision.current) throw new Error("Sign-in was interrupted");
    clearSessionData();
    localStorage.setItem("waldo_token", data.access_token);
    localStorage.setItem("waldo_refresh", data.refresh_token);
    setUser(me);
    setToken(data.access_token);
    setLoading(false);
  }, [clearSessionData]);

  const logout = useCallback(() => {
    revision.current += 1;
    clearSessionData();
    localStorage.removeItem("waldo_token");
    localStorage.removeItem("waldo_refresh");
    setToken(null);
    setUser(null);
    setLoading(false);
  }, [clearSessionData]);

  return (
    <AuthContext.Provider value={{ user, token, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}
