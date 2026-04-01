/**
 * Left sidebar navigation — Pretext-inspired warm light design.
 * Persistent on desktop, collapsible on mobile.
 */
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
      {/* Logo + Workspace */}
      <div className="px-4 pt-5 pb-3">
        <Link to="/" className="flex items-center gap-2">
          <span style={{ fontFamily: "var(--font-serif)", fontSize: 20, fontWeight: 700, color: "var(--text-primary)", letterSpacing: "-0.02em" }}>
            Waldo
          </span>
        </Link>
        <button
          className="flex items-center gap-1 mt-2 text-xs rounded-md px-2 py-1 w-full"
          style={{ color: "var(--text-secondary)", backgroundColor: "var(--bg-inset)" }}
        >
          <span className="truncate flex-1 text-left">My Workspace</span>
          <ChevronDown size={12} />
        </button>
      </div>

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
