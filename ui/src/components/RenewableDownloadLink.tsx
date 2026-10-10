import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { renewJobDownload } from "../api";
import { useAuth } from "../contexts/authState";

interface Props { jobId: string; url: string; children: ReactNode; className?: string; style?: CSSProperties }

export default function RenewableDownloadLink({ jobId, url, children, className, style }: Props) {
  const { user, token } = useAuth();
  const operation = useMemo(() => ({ jobId, url, token, userId: user?.id, workspaceId: user?.workspace_id, active: false, pending: false }), [jobId, url, token, user?.id, user?.workspace_id]);
  const [state, setState] = useState({ operation, pending: false, error: null as string | null });
  useEffect(() => {
    operation.active = true;
    return () => { operation.active = false; };
  }, [operation]);
  return <>
    <a href={url} className={className} style={style} aria-disabled={state.operation === operation && state.pending}
      onClick={async (event) => {
        event.preventDefault();
        if (!operation.active || operation.pending) return;
        operation.pending = true;
        setState({ operation, pending: true, error: null });
        try {
          const renewedUrl = await renewJobDownload(jobId, url);
          if (!operation.active) return;
          const anchor = document.createElement("a");
          anchor.href = renewedUrl;
          anchor.download = "";
          document.body.appendChild(anchor);
          anchor.click();
          anchor.remove();
        } catch (error) {
          if (!operation.active) return;
          setState({ operation, pending: false, error: error instanceof Error ? error.message : "Could not download this export." });
        } finally {
          operation.pending = false;
          if (operation.active) setState((previous) => ({ ...previous, pending: false }));
        }
      }}>
      {children}
    </a>
    {state.operation === operation && state.error && <span role="alert" className="ml-2 text-sm" style={{ color: "var(--danger)" }}>{state.error}</span>}
  </>;
}
