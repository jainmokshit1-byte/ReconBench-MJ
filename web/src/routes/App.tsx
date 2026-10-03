import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation } from "react-router";

import { AuthProvider } from "../auth/AuthProvider";
import { useAuth } from "../auth/useAuth";
import { AppShell } from "../components/AppShell";
import { HomePage } from "../pages/HomePage";
import { AdminErrorsPage } from "../pages/AdminErrorsPage";
import { IngestionPage } from "../pages/IngestionPage";
import { LoginPage } from "../pages/LoginPage";
import { MappingPage } from "../pages/MappingPage";
import { ReconciliationPage } from "../pages/ReconciliationPage";
import { SettingsPage } from "../pages/SettingsPage";

export function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<RequireSession />}>
            <Route element={<AppShell />}>
              <Route index element={<Navigate to="/home" replace />} />
              <Route path="home" element={<HomePage />} />
              <Route path="ingestion" element={<IngestionPage />} />
              <Route path="mapping" element={<MappingPage />} />
              <Route path="reconciliation" element={<ReconciliationPage />} />
              <Route path="settings" element={<SettingsPage />} />
              <Route path="admin/errors" element={<AdminErrorsPage />} />
              <Route path="*" element={<Navigate to="/home" replace />} />
            </Route>
          </Route>
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}

function RequireSession() {
  const { status, user, expired } = useAuth();
  const location = useLocation();

  if (status === "loading") {
    return <main className="session-loading" role="status">Checking your session…</main>;
  }
  if (!user) {
    return (
      <Navigate
        to="/login"
        replace
        state={{
          from: { pathname: location.pathname, search: location.search, hash: location.hash },
          expired,
        }}
      />
    );
  }
  return <Outlet />;
}
