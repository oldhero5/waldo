import type { ReactNode } from "react";
import Nav from "./Nav";

interface PageLayoutProps {
  children: ReactNode;
  maxWidth?: "xl" | "3xl" | "4xl" | "6xl";
  title?: string;
  subtitle?: string;
  actions?: ReactNode;
}

const widthClasses: Record<string, string> = {
  xl: "max-w-xl",
  "3xl": "max-w-3xl",
  "4xl": "max-w-4xl",
  "6xl": "max-w-6xl",
};

export default function PageLayout({
  children,
  maxWidth = "4xl",
  title,
  subtitle,
  actions,
}: PageLayoutProps) {
  return (
    <div className="min-h-screen" style={{ backgroundColor: "var(--bg-page)" }}>
      <Nav />
      <div className={`${widthClasses[maxWidth]} mx-auto mt-6 px-4 sm:px-6 pb-16`}>
        {(title || actions) && (
          <div className="flex justify-between items-start mb-6">
            <div>
              {title && (
                <h1 className="text-2xl font-bold" style={{ color: "var(--text-primary)" }}>{title}</h1>
              )}
              {subtitle && (
                <p className="text-sm mt-1" style={{ color: "var(--text-secondary)" }}>{subtitle}</p>
              )}
            </div>
            {actions && <div className="flex gap-2">{actions}</div>}
          </div>
        )}
        {children}
      </div>
    </div>
  );
}
