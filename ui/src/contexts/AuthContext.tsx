/**
 * Auth context — manages JWT tokens, user state, and workspace selection.
 * Wraps the entire app. If no token is stored, redirects to login.
 */
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

const API = "/api/v1";

interface User {
  id: string;
  email: string;
  display_name: string;
  avatar_url: string | null;
  workspace_id: string | null;
  workspace_name: string | null;
  role: string | null;
}

interface AuthState {
  user: User | null;
  token: string | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, displayName: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState>({
  user: null,
  token: null,
  loading: true,
  login: async () => {},
  register: async () => {},
  logout: () => {},
});

export function useAuth() {
  return useContext(AuthContext);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(() => localStorage.getItem("waldo_token"));
  const [loading, setLoading] = useState(true);

  // Fetch user info when token changes
  useEffect(() => {
    if (!token) {
      setUser(null);
      setLoading(false);
      return;
    }

    let status = 0;
    fetch(`${API}/auth/me`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((res) => {
        status = res.status;
        if (!res.ok) throw new Error("Invalid token");
        return res.json();
      })
      .then((data) => {
        setUser(data);
        setLoading(false);
      })
      .catch(() => {
        if (status === 401 || status === 403) {
          // Token invalid — clear auth
          localStorage.removeItem("waldo_token");
          localStorage.removeItem("waldo_refresh");
          setToken(null);
          setUser(null);
        }
        // Network errors: keep token, just stop loading
        setLoading(false);
      });
  }, [token]);

  const login = useCallback(async (email: string, password: string) => {
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
    localStorage.setItem("waldo_token", data.access_token);
    localStorage.setItem("waldo_refresh", data.refresh_token);

    // Fetch user before updating token state so the route guard sees
    // a valid user immediately — avoids the redirect-back-to-login race.
    const meRes = await fetch(`${API}/auth/me`, {
      headers: { Authorization: `Bearer ${data.access_token}` },
    });
    if (!meRes.ok) throw new Error("Failed to fetch user");
    const me = await meRes.json();
    setUser(me);
    setToken(data.access_token);
    setLoading(false);
  }, []);

  const register = useCallback(async (email: string, password: string, displayName: string) => {
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
    localStorage.setItem("waldo_token", data.access_token);
    localStorage.setItem("waldo_refresh", data.refresh_token);

    const meRes = await fetch(`${API}/auth/me`, {
      headers: { Authorization: `Bearer ${data.access_token}` },
    });
    if (!meRes.ok) throw new Error("Failed to fetch user");
    const me = await meRes.json();
    setUser(me);
    setToken(data.access_token);
    setLoading(false);
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem("waldo_token");
    localStorage.removeItem("waldo_refresh");
    setToken(null);
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, token, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}
