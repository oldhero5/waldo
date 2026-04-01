import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import "./index.css";
import AppShell from "./components/AppShell";
import { AuthProvider, useAuth } from "./contexts/AuthContext";
import DemoPage from "./pages/DemoPage";
import DeployPage from "./pages/DeployPage";
import JobsPage from "./pages/JobsPage";
import LabelPage from "./pages/LabelPage";
import LoginPage from "./pages/LoginPage";
import RegisterPage from "./pages/RegisterPage";
import ReviewPage from "./pages/ReviewPage";
import TrainPage from "./pages/TrainPage";
import CollectionsPage from "./pages/CollectionsPage";
import UploadPage from "./pages/UploadPage";
import DashboardPage from "./pages/DashboardPage";
import DatasetsPage from "./pages/DatasetsPage";
import ExperimentsPage from "./pages/ExperimentsPage";

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
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/upload" element={<UploadPage />} />
        <Route path="/collections" element={<CollectionsPage />} />
        <Route path="/label/collection/:projectId" element={<LabelPage />} />
        <Route path="/label/:videoId" element={<LabelPage />} />
        <Route path="/review/:jobId" element={<ReviewPage />} />
        <Route path="/train/:jobId" element={<TrainPage />} />
        <Route path="/jobs" element={<JobsPage />} />
        <Route path="/datasets" element={<DatasetsPage />} />
        <Route path="/experiments" element={<ExperimentsPage />} />
        <Route path="/deploy" element={<DeployPage />} />
        <Route path="/demo" element={<DemoPage />} />
      </Routes>
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
