/**
 * Pretext-style accordion — smooth height animation without DOM measurement.
 * Uses CSS grid trick: grid-template-rows transitions from 0fr to 1fr.
 */
import { useState, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";

interface AccordionProps {
  title: string | ReactNode;
  /** Monospace eyebrow label above the title */
  eyebrow?: string;
  /** Default open state */
  defaultOpen?: boolean;
  /** Optional count badge */
  count?: number;
  /** Optional accent color for the indicator */
  accentColor?: string;
  children: ReactNode;
  className?: string;
}

export default function Accordion({
  title,
  eyebrow,
  defaultOpen = false,
  count,
  accentColor,
  children,
  className = "",
}: AccordionProps) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className={className}>
      {/* Trigger */}
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 py-2.5 text-left group"
        style={{ transition: "color 160ms ease" }}
      >
        <ChevronRight
          size={14}
          style={{
            color: accentColor || "var(--text-muted)",
            transform: open ? "rotate(90deg)" : "rotate(0deg)",
            transition: "transform 200ms cubic-bezier(0.4, 0, 0.2, 1)",
            flexShrink: 0,
          }}
        />
        <div className="flex-1 min-w-0">
          {eyebrow && (
            <span
              className="block mb-0.5"
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 10,
                letterSpacing: "0.08em",
                textTransform: "uppercase",
                color: accentColor || "var(--text-muted)",
              }}
            >
              {eyebrow}
            </span>
          )}
          <span
            style={{
              color: "var(--text-primary)",
              fontSize: 14,
              fontWeight: 600,
              fontFamily: "var(--font-serif)",
            }}
          >
            {title}
          </span>
        </div>
        {count != null && (
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 10,
              color: "var(--text-muted)",
              backgroundColor: "var(--bg-inset)",
              padding: "2px 6px",
              borderRadius: 6,
            }}
          >
            {count}
          </span>
        )}
      </button>

      {/* Animated content — CSS grid 0fr→1fr trick */}
      <div
        style={{
          display: "grid",
          gridTemplateRows: open ? "1fr" : "0fr",
          transition: "grid-template-rows 250ms cubic-bezier(0.4, 0, 0.2, 1)",
        }}
      >
        <div style={{ overflow: "hidden" }}>
          <div style={{ paddingTop: 4, paddingBottom: open ? 8 : 0 }}>
            {children}
          </div>
        </div>
      </div>
    </div>
  );
}
