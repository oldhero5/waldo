/**
 * Settings — workspace management, user profile, API keys.
 */
import { useState } from "react";
import { Settings, Key, Users, User, Shield, LogOut } from "lucide-react";
import { useAuth } from "../contexts/AuthContext";

type Tab = "profile" | "team" | "api_keys";

export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>("profile");
  const { user, logout } = useAuth();

  const tabs: { key: Tab; label: string; icon: typeof User }[] = [
    { key: "profile", label: "Profile", icon: User },
    { key: "team", label: "Team", icon: Users },
    { key: "api_keys", label: "API Keys", icon: Key },
  ];

  return (
    <div className="max-w-3xl mx-auto mt-6 px-4 sm:px-6 pb-16">
      <h1 className="text-2xl font-bold flex items-center gap-2 mb-6" style={{ color: "var(--text-primary)" }}>
        <Settings size={24} />
        Settings
      </h1>

      {/* Tab nav */}
      <div className="flex gap-1 mb-6" style={{ borderBottom: "1px solid var(--border-subtle)" }}>
        {tabs.map((t) => {
          const active = tab === t.key;
          const Icon = t.icon;
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className="flex items-center gap-1.5 px-4 py-2.5 text-sm transition-colors -mb-px"
              style={{
                color: active ? "var(--accent)" : "var(--text-secondary)",
                borderBottom: active ? "2px solid var(--accent)" : "2px solid transparent",
                fontWeight: active ? 600 : 400,
              }}
            >
              <Icon size={15} />
              {t.label}
            </button>
          );
        })}
      </div>

      {/* Profile tab */}
      {tab === "profile" && (
        <div className="space-y-6">
          <div className="surface p-5">
            <h2 className="font-semibold text-sm mb-4" style={{ color: "var(--text-primary)" }}>Your Profile</h2>
            <div className="space-y-4">
              <div>
                <label className="text-xs block mb-1" style={{ color: "var(--text-secondary)" }}>Display Name</label>
                <input
                  type="text"
                  defaultValue={user?.display_name || ""}
                  className="w-full px-3 py-2 rounded-lg border text-sm"
                  style={{ borderColor: "var(--border-default)", backgroundColor: "var(--bg-inset)", color: "var(--text-primary)" }}
                />
              </div>
              <div>
                <label className="text-xs block mb-1" style={{ color: "var(--text-secondary)" }}>Email</label>
                <input
                  type="email"
                  defaultValue={user?.email || ""}
                  disabled
                  className="w-full px-3 py-2 rounded-lg border text-sm opacity-60"
                  style={{ borderColor: "var(--border-default)", backgroundColor: "var(--bg-inset)", color: "var(--text-primary)" }}
                />
              </div>
              <div className="flex justify-between items-center pt-2">
                <button className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700">
                  Save Changes
                </button>
                <button
                  onClick={logout}
                  className="flex items-center gap-1.5 px-3 py-2 text-red-500 text-sm hover:bg-red-50 rounded-lg"
                >
                  <LogOut size={14} />
                  Sign Out
                </button>
              </div>
            </div>
          </div>

          <div className="surface p-5">
            <h2 className="font-semibold text-sm mb-2" style={{ color: "var(--text-primary)" }}>Workspace</h2>
            <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
              {user?.workspace_name || "Default Workspace"}
            </p>
            <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>
              Role: <span className="capitalize font-medium">{user?.role || "admin"}</span>
            </p>
          </div>
        </div>
      )}

      {/* Team tab */}
      {tab === "team" && (
        <div className="surface p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-semibold text-sm" style={{ color: "var(--text-primary)" }}>Team Members</h2>
            <button className="px-3 py-1.5 bg-blue-600 text-white rounded-lg text-xs hover:bg-blue-700">
              Invite Member
            </button>
          </div>

          <div className="rounded-lg overflow-hidden" style={{ border: "1px solid var(--border-subtle)" }}>
            <div className="flex items-center justify-between px-4 py-3" style={{ backgroundColor: "var(--bg-inset)" }}>
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-full flex items-center justify-center bg-blue-100">
                  <User size={14} className="text-blue-600" />
                </div>
                <div>
                  <p className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>{user?.display_name}</p>
                  <p className="text-xs" style={{ color: "var(--text-muted)" }}>{user?.email}</p>
                </div>
              </div>
              <span className="text-xs px-2 py-1 rounded capitalize" style={{ backgroundColor: "var(--bg-surface)", color: "var(--text-secondary)" }}>
                {user?.role || "admin"}
              </span>
            </div>
          </div>

          <p className="text-xs mt-4" style={{ color: "var(--text-muted)" }}>
            Invite team members to collaborate on labeling, training, and deployment.
            Assign roles: Admin (full access), Annotator (label data), Reviewer (review annotations), Viewer (read-only).
          </p>
        </div>
      )}

      {/* API Keys tab */}
      {tab === "api_keys" && (
        <div className="space-y-4">
          <div className="surface p-5">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="font-semibold text-sm" style={{ color: "var(--text-primary)" }}>API Keys</h2>
                <p className="text-xs mt-0.5" style={{ color: "var(--text-muted)" }}>
                  Use API keys for programmatic access to the Waldo API.
                </p>
              </div>
              <button className="px-3 py-1.5 bg-blue-600 text-white rounded-lg text-xs hover:bg-blue-700">
                Create Key
              </button>
            </div>

            <div className="text-center py-8">
              <Shield size={32} className="mx-auto mb-2" style={{ color: "var(--text-muted)" }} />
              <p className="text-sm" style={{ color: "var(--text-secondary)" }}>No API keys yet</p>
              <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>
                Create an API key to authenticate with <code className="font-mono">Authorization: Bearer wld_...</code>
              </p>
            </div>
          </div>

          <div className="surface p-5">
            <h2 className="font-semibold text-sm mb-3" style={{ color: "var(--text-primary)" }}>Usage Example</h2>
            <pre
              className="text-xs p-3 rounded-lg overflow-x-auto font-mono"
              style={{ backgroundColor: "var(--bg-inset)", color: "var(--text-secondary)" }}
            >
{`# Predict with API key
curl -X POST http://localhost:8000/api/v1/predict/image \\
  -H "Authorization: Bearer wld_your_key_here" \\
  -F "file=@image.jpg"

# Python
import requests
r = requests.post(
    "http://localhost:8000/api/v1/predict/image",
    headers={"Authorization": "Bearer wld_your_key_here"},
    files={"file": open("image.jpg", "rb")}
)
print(r.json())`}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}
