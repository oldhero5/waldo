/**
 * AppShell — layout wrapper with sidebar + main content area.
 * Used for all authenticated pages.
 */
import type { ReactNode } from "react";
import Sidebar from "./Sidebar";

interface AppShellProps {
  children: ReactNode;
}

export default function AppShell({ children }: AppShellProps) {
  return (
    <div className="flex min-h-screen" style={{ backgroundColor: "var(--bg-page)" }}>
      <Sidebar />
      <main className="flex-1 overflow-y-auto">
        {children}
      </main>
    </div>
  );
}
