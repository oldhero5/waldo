import { useQuery } from "@tanstack/react-query";
import { Link, useLocation } from "react-router-dom";
import {
  Upload,
  Database,
  FlaskConical,
  Rocket,
  Loader2,
} from "lucide-react";
import { listTrainingRuns } from "../api";

const links = [
  { to: "/upload", label: "Upload", icon: Upload },
  { to: "/datasets", label: "Datasets", icon: Database },
  { to: "/experiments", label: "Experiments", icon: FlaskConical },
  { to: "/deploy", label: "Deploy", icon: Rocket },
];

export default function Nav() {
  const loc = useLocation();

  const { data: runs } = useQuery({
    queryKey: ["training-runs"],
    queryFn: listTrainingRuns,
    refetchInterval: 5000,
  });

  const activeRun = runs?.find((r) =>
    ["queued", "preparing", "training", "validating"].includes(r.status)
  );

  return (
    <nav
      className="sticky top-0 z-40 backdrop-blur-xl border-b"
      style={{
        backgroundColor: "color-mix(in srgb, var(--bg-surface) 85%, transparent)",
        borderColor: "var(--border-subtle)",
      }}
    >
      <div className="max-w-7xl mx-auto px-4 sm:px-6 flex items-center h-14 gap-1">
        <Link
          to="/"
          className="font-bold text-lg tracking-tight mr-5"
          style={{ color: "var(--text-primary)" }}
        >
          Waldo
        </Link>

        {links.map((l) => {
          const active = loc.pathname.startsWith(l.to);
          const Icon = l.icon;
          return (
            <Link
              key={l.to}
              to={l.to}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm transition-all duration-150"
              style={{
                backgroundColor: active ? "var(--accent-soft)" : "transparent",
                color: active ? "var(--accent)" : "var(--text-secondary)",
                fontWeight: active ? 600 : 400,
              }}
              onMouseEnter={(e) => {
                if (!active) e.currentTarget.style.backgroundColor = "var(--bg-inset)";
              }}
              onMouseLeave={(e) => {
                if (!active) e.currentTarget.style.backgroundColor = "transparent";
              }}
            >
              <Icon size={15} strokeWidth={active ? 2.2 : 1.8} />
              {l.label}
            </Link>
          );
        })}

        <div className="flex-1" />

        {/* Active training indicator */}
        {activeRun && (
          <Link
            to={`/train/${activeRun.run_id}`}
            className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium transition-colors"
            style={{ backgroundColor: "var(--accent)", color: "#fff" }}
          >
            <Loader2 size={14} className="animate-spin" />
            Training: {activeRun.epoch_current}/{activeRun.total_epochs}
          </Link>
        )}
      </div>
    </nav>
  );
}
