/**
 * Left sidebar navigation — Pretext-inspired warm light design.
 * Persistent on desktop, collapsible on mobile.
 */
import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import {
  Home,
  Upload,
  Database,
  FlaskConical,
  Rocket,
  Play,
  Settings,
  Workflow,
  ChevronDown,
  Loader2,
  BarChart3,
  MessageCircle,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { listTrainingRuns } from "../api";

const NAV_ITEMS = [
  { to: "/", label: "Home", icon: Home, exact: true },
  { to: "/upload", label: "Upload", icon: Upload },
  { to: "/datasets", label: "Datasets", icon: Database },
  { to: "/workflows", label: "Workflows", icon: Workflow },
  { to: "/experiments", label: "Experiments", icon: FlaskConical },
  { to: "/monitoring", label: "Monitoring", icon: BarChart3 },
  { to: "/deploy", label: "Deploy", icon: Rocket },
  { to: "/demo", label: "Demo", icon: Play },
];

const BOTTOM_ITEMS = [
  { to: "/agent", label: "AI Agent", icon: MessageCircle },
  { to: "/settings", label: "Settings", icon: Settings },
];

function WorkspaceSwitcher() {
  const [open, setOpen] = useState(false);
  const [workspaces, setWorkspaces] = useState<{ id: string; name: string; slug: string; role: string }[]>([]);
  const [active, setActive] = useState("My Workspace");
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");

  useEffect(() => {
    const token = localStorage.getItem("waldo_token");
    if (!token) return;
    fetch("/api/v1/workspaces", { headers: { Authorization: `Bearer ${token}` } })
      .then((r) => r.json())
      .then((data) => {
        if (Array.isArray(data) && data.length > 0) {
          setWorkspaces(data);
          setActive(data[0].name);
        }
      })
      .catch(() => {});
  }, []);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    const token = localStorage.getItem("waldo_token");
    const res = await fetch("/api/v1/workspaces", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${token || ""}` },
      body: JSON.stringify({ name: newName }),
    });
    if (res.ok) {
      const ws = await res.json();
      setWorkspaces((prev) => [...prev, ws]);
      setActive(ws.name);
      setNewName("");
      setCreating(false);
    }
  };

  return (
    <div className="px-4 pt-5 pb-3">
      <Link to="/" className="flex items-center gap-2">
        <span style={{ fontFamily: "var(--font-serif)", fontSize: 20, fontWeight: 700, color: "var(--text-primary)", letterSpacing: "-0.02em" }}>
          Waldo
        </span>
      </Link>
      <div className="relative mt-2">
        <button
          onClick={() => setOpen(!open)}
          className="flex items-center gap-1 text-xs rounded-lg px-2.5 py-2 w-full"
          style={{ color: "var(--text-secondary)", backgroundColor: "var(--bg-inset)", transition: "all 160ms ease" }}
        >
          <span className="truncate flex-1 text-left" style={{ fontWeight: 500 }}>{active}</span>
          <ChevronDown size={12} style={{ transform: open ? "rotate(180deg)" : "none", transition: "transform 160ms ease" }} />
        </button>

        {open && (
          <div
            className="absolute left-0 right-0 mt-1 rounded-xl overflow-hidden z-50"
            style={{
              backgroundColor: "var(--bg-surface)",
              border: "1px solid var(--border-default)",
              boxShadow: "0 8px 24px rgba(0,0,0,0.12)",
            }}
          >
            {workspaces.map((ws) => (
              <button
                key={ws.id}
                onClick={() => { setActive(ws.name); setOpen(false); }}
                className="w-full text-left px-3 py-2 text-xs"
                style={{
                  color: ws.name === active ? "var(--accent)" : "var(--text-primary)",
                  backgroundColor: ws.name === active ? "var(--accent-soft)" : "transparent",
                  fontWeight: ws.name === active ? 600 : 400,
                  transition: "all 100ms ease",
                }}
                onMouseEnter={(e) => { if (ws.name !== active) e.currentTarget.style.backgroundColor = "var(--bg-inset)"; }}
                onMouseLeave={(e) => { if (ws.name !== active) e.currentTarget.style.backgroundColor = "transparent"; }}
              >
                {ws.name}
                <span style={{ fontSize: 9, color: "var(--text-muted)", marginLeft: 6 }}>{ws.role}</span>
              </button>
            ))}
            <div style={{ borderTop: "1px solid var(--border-subtle)", padding: 6 }}>
              {creating ? (
                <div className="flex gap-1">
                  <input
                    autoFocus
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") handleCreate(); if (e.key === "Escape") setCreating(false); }}
                    placeholder="Workspace name"
                    className="flex-1 px-2 py-1 text-xs rounded border"
                    style={{ borderColor: "var(--border-default)", backgroundColor: "var(--bg-inset)", color: "var(--text-primary)" }}
                  />
                  <button onClick={handleCreate} className="px-2 py-1 bg-blue-600 text-white text-xs rounded">Create</button>
                </div>
              ) : (
                <button
                  onClick={() => setCreating(true)}
                  className="w-full text-left px-3 py-1.5 text-xs"
                  style={{ color: "var(--accent)" }}
                >
                  + New Workspace
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}


export default function Sidebar() {
  const loc = useLocation();

  const { data: runs } = useQuery({
    queryKey: ["training-runs"],
    queryFn: listTrainingRuns,
    refetchInterval: 5000,
  });

  const activeRun = runs?.find((r) =>
    ["queued", "preparing", "training", "validating"].includes(r.status)
  );

  const isActive = (to: string, exact?: boolean) =>
    exact ? loc.pathname === to : loc.pathname.startsWith(to);

  return (
    <aside
      className="w-56 shrink-0 flex flex-col h-screen sticky top-0 overflow-y-auto"
      style={{
        backgroundColor: "var(--bg-surface)",
        borderRight: "1px solid var(--border-subtle)",
      }}
    >
      {/* Logo + Workspace Switcher */}
      <WorkspaceSwitcher />

      {/* Main nav */}
      <nav className="flex-1 px-2 mt-1">
        <div className="space-y-0.5">
          {NAV_ITEMS.map((item) => {
            const active = isActive(item.to, item.exact);
            const Icon = item.icon;
            return (
              <Link
                key={item.to}
                to={item.to}
                className="flex items-center gap-3 px-3 py-2.5 rounded-xl text-[13px]"
                style={{
                  backgroundColor: active ? "color-mix(in srgb, var(--accent) 10%, transparent 90%)" : "transparent",
                  color: active ? "var(--accent)" : "var(--text-secondary)",
                  fontWeight: active ? 600 : 400,
                  transition: "all 160ms ease",
                  borderLeft: active ? "2px solid var(--accent)" : "2px solid transparent",
                }}
                onMouseEnter={(e) => {
                  if (!active) {
                    e.currentTarget.style.backgroundColor = "color-mix(in srgb, var(--border-subtle) 50%, transparent 50%)";
                    e.currentTarget.style.color = "var(--text-primary)";
                  }
                }}
                onMouseLeave={(e) => {
                  if (!active) {
                    e.currentTarget.style.backgroundColor = "transparent";
                    e.currentTarget.style.color = "var(--text-secondary)";
                  }
                }}
              >
                <Icon size={16} strokeWidth={active ? 2 : 1.5} />
                {item.label}
              </Link>
            );
          })}
        </div>

        {/* Active training indicator */}
        {activeRun && (
          <Link
            to={`/train/${activeRun.run_id}`}
            className="flex items-center gap-2 mx-1 mt-3 px-3 py-2 rounded-lg text-xs font-medium"
            style={{ backgroundColor: "var(--accent-soft)", color: "var(--accent)" }}
          >
            <Loader2 size={13} className="animate-spin" />
            <span className="truncate">Training {activeRun.epoch_current}/{activeRun.total_epochs}</span>
          </Link>
        )}
      </nav>

      {/* Bottom section */}
      <div className="px-2 pb-4 mt-auto" style={{ borderTop: "1px solid var(--border-subtle)" }}>
        <div className="pt-2 space-y-0.5">
          {BOTTOM_ITEMS.map((item) => {
            const active = isActive(item.to);
            const Icon = item.icon;
            return (
              <Link
                key={item.to}
                to={item.to}
                className="flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-all duration-150"
                style={{
                  color: active ? "var(--accent)" : "var(--text-muted)",
                  backgroundColor: active ? "var(--accent-soft)" : "transparent",
                }}
                onMouseEnter={(e) => {
                  if (!active) e.currentTarget.style.backgroundColor = "var(--bg-inset)";
                }}
                onMouseLeave={(e) => {
                  if (!active) e.currentTarget.style.backgroundColor = "transparent";
                }}
              >
                <Icon size={16} strokeWidth={1.6} />
                {item.label}
              </Link>
            );
          })}
        </div>
      </div>
    </aside>
  );
}
