import { Suspense, lazy } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import AppShell from "./AppShell";
import { useAuth } from "../contexts/authState";
// Eagerly loaded — needed on first render
import DashboardPage from "../pages/DashboardPage";

// Lazy loaded — only downloaded when navigated to
const AgentPage = lazy(() => import("../pages/AgentPage"));
const CollectionsPage = lazy(() => import("../pages/CollectionsPage"));
const DatasetsPage = lazy(() => import("../pages/DatasetsPage"));
const DeployPage = lazy(() => import("../pages/DeployPage"));
const ExperimentsPage = lazy(() => import("../pages/ExperimentsPage"));
const JobsPage = lazy(() => import("../pages/JobsPage"));
const LabelPage = lazy(() => import("../pages/LabelPage"));
const PlaygroundPage = lazy(() => import("../pages/PlaygroundPage"));
const ReviewPage = lazy(() => import("../pages/ReviewPage"));
const SettingsPage = lazy(() => import("../pages/SettingsPage"));
const TrainPage = lazy(() => import("../pages/TrainPage"));
const UploadPage = lazy(() => import("../pages/UploadPage"));
const WorkflowEditorPage = lazy(() => import("../pages/WorkflowEditorPage"));
const WorkflowsPage = lazy(() => import("../pages/WorkflowsPage"));

export default function AuthenticatedRoutes() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ backgroundColor: "var(--bg-page)" }}>
        <div className="text-center">
          <p className="text-lg font-bold" style={{ color: "var(--text-primary)" }}>Waldo</p>
          <p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>Loading...</p>
        </div>
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  return (
    <AppShell key={`${user.id}:${user.workspace_id || ""}`}>
      <Suspense fallback={<div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "50vh", color: "var(--text-muted)" }}>Loading...</div>}>
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/upload" element={<UploadPage />} />
          <Route path="/collections" element={<CollectionsPage />} />
          <Route path="/datasets" element={<DatasetsPage />} />
          <Route path="/label/collection/:projectId" element={<LabelPage />} />
          <Route path="/label/:videoId" element={<LabelPage />} />
          <Route path="/playground" element={<PlaygroundPage />} />
          <Route path="/review/:jobId" element={<ReviewPage />} />
          <Route path="/train/:jobId" element={<TrainPage />} />
          <Route path="/jobs" element={<JobsPage />} />
          <Route path="/experiments" element={<ExperimentsPage />} />
          <Route path="/workflows" element={<WorkflowsPage />} />
          <Route path="/workflows/new" element={<WorkflowEditorPage />} />
          <Route path="/workflows/:workflowId" element={<WorkflowEditorPage />} />
          <Route path="/deploy" element={<DeployPage />} />
          <Route path="/deploy/:tab" element={<DeployPage />} />
          <Route path="/demo" element={<Navigate to="/deploy/test" replace />} />
          <Route path="/monitoring" element={<Navigate to="/deploy/monitor" replace />} />
          <Route path="/agent" element={<AgentPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </Suspense>
    </AppShell>
  );
}
