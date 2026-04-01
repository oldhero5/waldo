import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import "./index.css";
import DemoPage from "./pages/DemoPage";
import DeployPage from "./pages/DeployPage";
import JobsPage from "./pages/JobsPage";
import LabelPage from "./pages/LabelPage";
import ReviewPage from "./pages/ReviewPage";
import TrainPage from "./pages/TrainPage";
import CollectionsPage from "./pages/CollectionsPage";
import UploadPage from "./pages/UploadPage";
import DashboardPage from "./pages/DashboardPage";
import DatasetsPage from "./pages/DatasetsPage";
import ExperimentsPage from "./pages/ExperimentsPage";

const queryClient = new QueryClient();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
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
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>
);
