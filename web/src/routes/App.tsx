import { BrowserRouter, Navigate, Route, Routes } from "react-router";

import { AppShell } from "../components/AppShell";
import { HomePage } from "../pages/HomePage";
import { IngestionPage } from "../pages/IngestionPage";
import { LoginPage } from "../pages/LoginPage";
import { MappingPage } from "../pages/MappingPage";
import { ReconciliationPage } from "../pages/ReconciliationPage";
import { SettingsPage } from "../pages/SettingsPage";

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/home" replace />} />
          <Route path="home" element={<HomePage />} />
          <Route path="ingestion" element={<IngestionPage />} />
          <Route path="mapping" element={<MappingPage />} />
          <Route path="reconciliation" element={<ReconciliationPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/home" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
