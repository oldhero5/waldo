import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, Suspense, lazy } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import "./index.css";
import AppShell from "./components/AppShell";
import { AuthProvider, useAuth } from "./contexts/AuthContext";

// Eagerly loaded — needed on first render
import LoginPage from "./pages/LoginPage";
import RegisterPage from "./pages/RegisterPage";
import DashboardPage from "./pages/DashboardPage";

// Lazy loaded — only downloaded when navigated to
const AgentPage = lazy(() => import("./pages/AgentPage"));
const CollectionsPage = lazy(() => import("./pages/CollectionsPage"));
const DatasetsPage = lazy(() => import("./pages/DatasetsPage"));
const DeployPage = lazy(() => import("./pages/DeployPage"));
const ExperimentsPage = lazy(() => import("./pages/ExperimentsPage"));
const JobsPage = lazy(() => import("./pages/JobsPage"));
const LabelPage = lazy(() => import("./pages/LabelPage"));
const PlaygroundPage = lazy(() => import("./pages/PlaygroundPage"));
const ReviewPage = lazy(() => import("./pages/ReviewPage"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));
const TrainPage = lazy(() => import("./pages/TrainPage"));
const UploadPage = lazy(() => import("./pages/UploadPage"));
const WorkflowEditorPage = lazy(() => import("./pages/WorkflowEditorPage"));
const WorkflowsPage = lazy(() => import("./pages/WorkflowsPage"));

const queryClient = new QueryClient();

function AuthenticatedRoutes() {
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
    <AppShell>
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

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/register" element={<RegisterPage />} />
            <Route path="/*" element={<AuthenticatedRoutes />} />
          </Routes>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>
);
