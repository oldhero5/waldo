/**
 * Left sidebar navigation and authenticated workspace context.
 */
import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  Home,
  Upload,
  Database,
  FlaskConical,
  Rocket,
  Settings,
  Workflow,
  Loader2,
  MessageCircle,
  CheckCircle2,
  Wand2,
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { getComparisonResult, listTrainingRuns } from "../api";
import { useAuth } from "../contexts/authState";

const NAV_ITEMS = [
  { to: "/", label: "Home", icon: Home, exact: true },
  { to: "/upload", label: "Upload", icon: Upload },
  { to: "/datasets", label: "Datasets", icon: Database },
  { to: "/playground", label: "Playground", icon: Wand2, badge: "new" },
  { to: "/workflows", label: "Workflows", icon: Workflow, badge: "beta" },
  { to: "/experiments", label: "Experiments", icon: FlaskConical },
  { to: "/deploy", label: "Deploy", icon: Rocket },
];

const BOTTOM_ITEMS = [
  { to: "/agent", label: "AI Agent", icon: MessageCircle },
  { to: "/settings", label: "Settings", icon: Settings },
];

function WorkspaceIdentity() {
  const { user } = useAuth();
  return (
    <div className="px-4 pt-5 pb-3">
      <Link to="/" className="text-xl font-semibold" style={{ color: "var(--text-primary)" }}>Waldo</Link>
      <div className="mt-3 rounded-lg px-2.5 py-2" style={{ backgroundColor: "var(--bg-inset)" }}>
        <p className="text-xs truncate font-medium" title={user?.workspace_name || undefined} style={{ color: "var(--text-secondary)" }}>
          {user?.workspace_name || "No workspace assigned"}
        </p>
        {user?.role && <p className="text-xs capitalize mt-1" style={{ color: "var(--text-muted)" }}>{user.role}</p>}
      </div>
    </div>
  );
}

function ComparisonIndicator() {
  const navigate = useNavigate();
  const [sessionId, setSessionId] = useState<string | null>(() => sessionStorage.getItem("waldo_compare_session"));
  const [done, setDone] = useState(false);

  // Listen for sessionStorage changes (from CompareDemo setting the session).
  // Same-tab `storage` events don't fire, so we use a BroadcastChannel
  // instead of a 1Hz setInterval poll.
  useEffect(() => {
    const channel = new BroadcastChannel("waldo_compare_session");
    const refresh = () => {
      setSessionId(sessionStorage.getItem("waldo_compare_session"));
      setDone(false);
    };
    channel.onmessage = refresh;
    return () => channel.close();
  }, []);

  // Poll for results
  useEffect(() => {
    if (!sessionId || done) return;
    const interval = setInterval(async () => {
      try {
        const data = await getComparisonResult(sessionId);
        if (data.status === "completed") {
          setDone(true);
        }
      } catch { /* ignore */ }
    }, 3000);
    return () => clearInterval(interval);
  }, [sessionId, done]);

  if (!sessionId) return null;

  return (
    <button
      onClick={() => {
        navigate("/deploy/test");
        // Switch to compare mode — the CompareDemo will pick up the session from sessionStorage
      }}
      className="flex items-center gap-2 mx-1 mt-2 px-3 py-2 rounded-lg text-xs font-medium w-full text-left"
      style={{
        backgroundColor: done ? "var(--success-soft)" : "var(--warning-soft)",
        color: done ? "var(--success)" : "var(--warning)",
      }}
    >
      {done ? <CheckCircle2 size={13} /> : <Loader2 size={13} className="animate-spin" />}
      <span className="truncate flex-1">
        {done ? "Comparison ready" : "Comparing..."}
      </span>
      {done && <span className="text-[9px]">View</span>}
    </button>
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
      {/* Logo + Workspace identity */}
      <WorkspaceIdentity />

      {/* Main nav */}
      <nav className="flex-1 px-2 mt-1">
        <div className="space-y-0.5">
          {NAV_ITEMS.map((item) => {
            const active = isActive(item.to, ("exact" in item ? item.exact : false));
            const Icon = item.icon;
            const badge = ("badge" in item ? item.badge : undefined);
            return (
              <Link
                key={item.to}
                to={item.to}
                aria-current={active ? "page" : undefined}
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
                {badge && (
                  <span
                    style={{
                      fontSize: 9,
                      padding: "1px 5px",
                      borderRadius: 4,
                      backgroundColor: "var(--accent-soft)",
                      color: "var(--text-secondary)",
                      marginLeft: "auto",
                    }}
                  >
                    {badge}
                  </span>
                )}
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

        {/* Active comparison task */}
        <ComparisonIndicator />
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
                aria-current={active ? "page" : undefined}
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
