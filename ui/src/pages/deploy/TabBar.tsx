import { TABS, type TabKey } from "./shared";

export function TabBar({ activeTab, onTabChange }: { activeTab: TabKey; onTabChange: (t: TabKey) => void }) {
  return (
    <div
      role="tablist"
      aria-label="Deploy sections"
      className="flex gap-1 mb-6 p-1 rounded-xl"
      style={{ backgroundColor: "var(--bg-inset)" }}
    >
      {TABS.map((t) => {
        const Icon = t.icon;
        const active = activeTab === t.key;
        return (
          <button
            key={t.key}
            role="tab"
            aria-selected={active}
            aria-controls={`panel-${t.key}`}
            onClick={() => onTabChange(t.key)}
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm transition-all duration-150"
            style={{
              backgroundColor: active ? "var(--bg-surface)" : "transparent",
              color: active ? "var(--accent)" : "var(--text-secondary)",
              fontWeight: active ? 600 : 400,
              boxShadow: active ? "0 1px 3px rgba(0,0,0,0.08)" : "none",
            }}
          >
            <Icon size={14} strokeWidth={active ? 2 : 1.5} />
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
