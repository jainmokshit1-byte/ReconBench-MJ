import { useCallback, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";

import { getAdminErrors } from "../api/settings";
import { retryFile } from "../api/ingestion";
import { PageHeader } from "../components/PageHeader";
import { useAuth } from "../auth/useAuth";

type RetryFile = { id: string; display_name: string; error_code: string | null; parse_finished_at: string | null };

function list(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.filter((row): row is Record<string, unknown> => typeof row === "object" && row !== null) : [];
}

export function AdminErrorsPage() {
  const { user, hasCapability } = useAuth();
  const canRetry = hasCapability("files.upload");
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(() => {
    setError(null);
    void getAdminErrors().then(setData).catch((cause: unknown) => setError(cause instanceof Error ? cause.message : "Error summary could not be loaded."));
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(refresh, 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  const failed = list(data?.["retry_actions"]) as RetryFile[];
  const failures = list(data?.["failed_by_reason_and_day"]);
  const layouts = list(data?.["top_failing_layouts"]);
  const vendors = list(data?.["top_failing_vendors"]);
  const warnings = list(data?.["unresolved_warnings"]);

  const retry = async (fileId: string) => {
    if (!user?.csrf_token) return;
    setBusy(fileId);
    setError(null);
    try {
      await retryFile(fileId, user.csrf_token);
      refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Retry could not be queued.");
    } finally {
      setBusy(null);
    }
  };

  return <>
    <PageHeader title="Processing errors" description="Failed source files, parser layouts, vendor labels and unresolved extraction warnings for this workspace." action={<button className="button" type="button" onClick={refresh}><RefreshCw size={14} /> Refresh</button>} />
    {error ? <div className="inline-error" role="alert">{error}</div> : null}
    <section className="panel admin-errors-panel"><h2>Failed files by reason and day</h2><Table rows={failures} columns={[["error_code", "Reason"], ["day", "Day"], ["count", "Files"]]} empty="No failed files." /></section>
    <div className="home-lower-grid admin-error-grid"><section className="panel admin-errors-panel"><h2>Failing layouts</h2><Table rows={layouts} columns={[["layout_signature", "Layout signature"], ["count", "Failures"]]} empty="No layout failures." /></section><section className="panel admin-errors-panel"><h2>Failing vendors</h2><Table rows={vendors} columns={[["vendor_name", "Printed vendor"], ["count", "Failures"]]} empty="No vendor-level failures." /></section></div>
    <section className="panel admin-errors-panel"><h2>Files eligible for retry</h2>{failed.length ? <div className="recon-table-wrap"><table className="recon-table"><thead><tr><th>Source file</th><th>Reason</th><th>Failed at</th><th>Action</th></tr></thead><tbody>{failed.map((file) => <tr key={file.id}><td>{file.display_name}</td><td>{file.error_code ?? "Unknown"}</td><td>{file.parse_finished_at ?? "—"}</td><td>{canRetry ? <button className="button" type="button" disabled={busy !== null} onClick={() => void retry(file.id)}>{busy === file.id ? "Queueing…" : "Retry"}</button> : <span>Read only</span>}</td></tr>)}</tbody></table></div> : <p>No files are waiting for retry.</p>}</section>
    <section className="panel admin-errors-panel"><h2>Unresolved extraction warnings</h2><Table rows={warnings} columns={[["code", "Code"], ["level", "Level"], ["message", "Message"], ["created_at", "Created"]]} empty="No unresolved warnings." /></section>
  </>;
}

function Table({ rows, columns, empty }: { rows: Array<Record<string, unknown>>; columns: Array<[string, string]>; empty: string }) {
  if (!rows.length) return <p>{empty}</p>;
  return <div className="recon-table-wrap"><table className="recon-table"><thead><tr>{columns.map(([, title]) => <th key={title}>{title}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={String(row["id"] ?? index)}>{columns.map(([field]) => <td key={field}>{row[field] === null || row[field] === undefined ? "—" : String(row[field])}</td>)}</tr>)}</tbody></table></div>;
}
