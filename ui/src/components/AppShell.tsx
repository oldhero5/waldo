/**
 * AppShell — layout wrapper with sidebar + main content + floating AI agent.
 */
import type { ReactNode } from "react";
import { useLocation } from "react-router-dom";
import AgentPanel from "./AgentPanel";
import Sidebar from "./Sidebar";

interface AppShellProps {
  children: ReactNode;
}

export default function AppShell({ children }: AppShellProps) {
  const { pathname } = useLocation();

  // Don't show the floating agent on the dedicated agent page
  const showPanel = !pathname.startsWith("/agent");

  return (
    <div className="flex min-h-screen" style={{ backgroundColor: "var(--bg-page)" }}>
      <Sidebar />
      <main className="flex-1 overflow-y-auto">
        {children}
      </main>
      {showPanel && <AgentPanel context={pathname.split("/")[1] || "dashboard"} />}
    </div>
  );
}
