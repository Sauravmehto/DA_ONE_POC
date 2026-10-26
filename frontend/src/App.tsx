import { Navigate, Outlet, Route, Routes, useParams } from "react-router-dom"

import { AuthGuard } from "@/components/auth-guard"
import { ConversationsProvider } from "@/contexts/conversations-context"
import { DatabaseProvider } from "@/contexts/database-context"
import { DashboardRedirect, WorkspaceShell } from "@/components/workspace-shell"
import { ExploreView } from "@/components/explore/explore-view"
import LoginPage from "@/pages/login"
import SignupPage from "@/pages/signup"
import InvitePage from "@/pages/invite"
import VerifyPage from "@/pages/verify"
import ForgotPasswordPage from "@/pages/forgot-password"
import ResetPage from "@/pages/reset"
import UsersPage from "@/pages/admin/users"
import ChatPage from "@/pages/chat"
import ConnectorsPage from "@/pages/connectors"
import ReportsPage from "@/pages/reports"
import DashboardsList from "@/pages/dashboards"
import DashboardDetail from "@/pages/dashboard-detail"
import ExcelSourcePage from "@/pages/excel-source"
import { AdminShell, SnowflakeToolsShell } from "@/components/admin/admin-shell"
import { SHOW_REPORT_BUILDER } from "@/lib/features"
import SemanticLayersPage from "@/pages/admin/semantic-layers"
import SemanticEditorPage from "@/pages/admin/semantic-editor"
import ReportBuilderPage from "@/pages/admin/report-builder"
import DataGroupsPage from "@/pages/admin/data-groups"

/** An old /admin/semantic/:database link, sent to where the editor lives now. */
function SemanticEditorRedirect() {
  const { database = "" } = useParams()
  return <Navigate to={`/tools/semantic/${encodeURIComponent(database)}`} replace />
}

function ProtectedShell() {
  return (
    <AuthGuard>
      <DatabaseProvider>
        <ConversationsProvider>
          <Outlet />
        </ConversationsProvider>
      </DatabaseProvider>
    </AuthGuard>
  )
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/signup" element={<SignupPage />} />
      <Route path="/forgot-password" element={<ForgotPasswordPage />} />
      {/* Emailed links carry their token in the fragment (/reset#...). The
          path forms are for links sent before that, which must keep working. */}
      <Route path="/invite" element={<InvitePage />} />
      <Route path="/invite/:token" element={<InvitePage />} />
      <Route path="/verify" element={<VerifyPage />} />
      <Route path="/verify/:token" element={<VerifyPage />} />
      <Route path="/reset" element={<ResetPage />} />
      <Route path="/reset/:token" element={<ResetPage />} />

      <Route element={<ProtectedShell />}>
        {/* Signing in lands here: the connectors, with no app around them. */}
        <Route index element={<ConnectorsPage />} />

        {/* One workspace per connection. Each has only the pages its source
            supports, and shows only what was made from that source. */}
        <Route path="snowflake" element={<WorkspaceShell kind="snowflake" />}>
          <Route index element={<ChatPage />} />
          <Route path="explore" element={<ExploreView />} />
          <Route path="dashboards" element={<DashboardsList />} />
          <Route path="dashboards/:reportId" element={<DashboardDetail />} />
          <Route path="reports" element={<ReportsPage />} />
        </Route>
        <Route
          path="sqlserver/:connectionId"
          element={<WorkspaceShell kind="sqlserver" />}
        >
          <Route index element={<ChatPage />} />
          <Route path="dashboards" element={<DashboardsList />} />
          <Route path="dashboards/:reportId" element={<DashboardDetail />} />
        </Route>
        <Route path="files" element={<WorkspaceShell kind="files" />}>
          <Route index element={<ChatPage />} />
          <Route path="library" element={<ExcelSourcePage />} />
          <Route path="dashboards" element={<DashboardsList />} />
          <Route path="dashboards/:reportId" element={<DashboardDetail />} />
        </Route>

        {/* Links from before workspaces existed. save_dashboard still hands out
            {base}/dashboards/{id}, so that one is looked up and forwarded. */}
        <Route path="dashboards/:reportId" element={<DashboardRedirect />} />
        <Route path="reports/dashboards/:reportId" element={<DashboardRedirect />} />
        <Route path="explore" element={<Navigate to="/snowflake/explore" replace />} />
        <Route path="reports" element={<Navigate to="/snowflake/reports" replace />} />
        <Route path="excel-source" element={<Navigate to="/files/library" replace />} />

        {/* Two areas outside the workspaces, each in its own tab with its own
            chrome rather than the chat sidebar. Still inside ProtectedShell —
            authentication is not optional here.
            Administration is the app's own settings, the same whatever data
            sources a client has. Snowflake tools author against Snowflake. */}
        <Route path="admin" element={<AdminShell />}>
          <Route index element={<Navigate to="users" replace />} />
          <Route path="users" element={<UsersPage />} />
          <Route path="data-groups" element={<DataGroupsPage />} />
        </Route>
        <Route path="tools" element={<SnowflakeToolsShell showReportBuilder={SHOW_REPORT_BUILDER} />}>
          <Route index element={<Navigate to="semantic" replace />} />
          <Route path="semantic" element={<SemanticLayersPage />} />
          <Route path="semantic/:database" element={<SemanticEditorPage />} />
          <Route path="report-builder" element={<ReportBuilderPage />} />
        </Route>

        {/* Where the Snowflake tools used to live, kept so old links still work. */}
        <Route path="admin/semantic" element={<Navigate to="/tools/semantic" replace />} />
        <Route path="admin/semantic/:database" element={<SemanticEditorRedirect />} />
        <Route path="admin/report-builder" element={<Navigate to="/tools/report-builder" replace />} />
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default App
