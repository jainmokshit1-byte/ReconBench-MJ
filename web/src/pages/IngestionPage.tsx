import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { Link, useNavigate } from "react-router";
import {
  AlertCircle,
  Archive,
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  CircleHelp,
  FileInput,
  FileText,
  FolderUp,
  LoaderCircle,
  RefreshCw,
  TriangleAlert,
  Upload,
  X,
} from "lucide-react";

import {
  createBatch,
  displayError,
  getBatchSummary,
  getDocuments,
  getFileErrors,
  getIngestionClientConfig,
  listBatches,
  listFiles,
  processBatch,
  removeFile,
  resolveFileMismatch,
  retryFile,
  uploadFile,
} from "../api/ingestion";
import type { BatchSummary, IngestionFile, UploadZone } from "../api/ingestion";
import { PageHeader } from "../components/PageHeader";
import { CsvMappingDialog } from "../components/CsvMappingDialog";
import { IngestionPreview } from "../components/IngestionPreview";
import { useAuth } from "../auth/useAuth";

interface LocalUpload {
  id: string;
  file: File;
  zone: UploadZone;
  error: string | null;
}

interface WarningItem {
  id: string;
  title: string;
  message: string;
  fileId: string | null;
  documentId: string | null;
}

interface PreviewTarget {
  file: IngestionFile;
  documentId: string | null;
}

const BATCH_STORAGE_KEY = "reconbench.ingestion.batch";
const ZONES: { id: UploadZone; title: string; description: string }[] = [
  { id: "po", title: "Purchase orders", description: "XML, CSV, PDF or ZIP" },
  { id: "grn", title: "Goods received notes", description: "XML, CSV, PDF or ZIP" },
  { id: "invoice", title: "Invoices", description: "PDF or ZIP" },
];
const PROCESSING_STATUSES = new Set(["queued", "parsing", "staged"]);
const TERMINAL_STATUSES = new Set(["parsed", "partial", "failed", "removed"]);

function inputFiles(files: FileList | null): File[] {
  return Array.from(files ?? []);
}

// Keep the browser-side allow-list aligned with App Flow §6.3. The server still
// sniffs bytes and remains authoritative when an extension lies about content.
function validateLocalFile(file: File, zone: UploadZone, maxFileBytes: number): string | null {
  if (file.size > maxFileBytes) return `This file exceeds the configured ${Math.round(maxFileBytes / (1024 * 1024))} MB limit.`;
  const suffix = file.name.split(".").at(-1)?.toLowerCase() ?? "";
  const accepted = zone === "invoice" ? ["pdf", "zip"] : ["xml", "csv", "pdf", "zip"];
  if (!accepted.includes(suffix)) return `This upload zone accepts ${zone === "invoice" ? "PDF or ZIP" : "XML, CSV, PDF or ZIP"} files.`;
  return null;
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    staged: "Staged",
    queued: "Queued",
    parsing: "Parsing",
    parsed: "Parsed",
    partial: "Partial",
    failed: "Failed",
    removed: "Removed",
    needs_password: "Needs password",
  };
  return labels[status] ?? status.replaceAll("_", " ");
}

function statusClass(status: string): string {
  if (status === "parsed") return "status-chip status-success";
  if (status === "partial" || status === "staged") return "status-chip status-warning";
  if (status === "failed") return "status-chip status-danger";
  if (PROCESSING_STATUSES.has(status)) return "status-chip status-progress";
  return "status-chip";
}

function warningCode(file: IngestionFile): string | null {
  if (file.mismatch_state === "suggested") return "TYPE_MISMATCH";
  return file.error_code;
}

function makeLocalId(): string {
  return `local-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function UploadZoneCard({
  zone,
  onFiles,
  disabled,
}: {
  zone: (typeof ZONES)[number];
  onFiles: (files: File[]) => void;
  disabled: boolean;
}) {
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    folderInput.current?.setAttribute("webkitdirectory", "");
    folderInput.current?.setAttribute("directory", "");
  }, []);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      fileInput.current?.click();
    }
  };

  return (
    <section className="ingestion-zone-card" aria-labelledby={`zone-${zone.id}`}>
      <div className="ingestion-zone-heading">
        <span className="zone-icon"><FileInput size={18} aria-hidden="true" /></span>
        <div><h2 id={`zone-${zone.id}`}>{zone.title}</h2><p>{zone.description}</p></div>
      </div>
      <div
        className={`ingestion-dropzone${dragging ? " is-dragging" : ""}`}
        role="button"
        tabIndex={disabled ? -1 : 0}
        aria-label={`Choose or drop ${zone.title.toLowerCase()} files`}
        aria-disabled={disabled}
        onKeyDown={onKeyDown}
        onClick={() => fileInput.current?.click()}
        onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
        onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
        onDragLeave={(event) => { if (event.currentTarget === event.target) setDragging(false); }}
        onDrop={(event) => { event.preventDefault(); setDragging(false); if (!disabled) onFiles(inputFiles(event.dataTransfer.files)); }}
      >
        <Upload size={18} aria-hidden="true" />
        <strong>Drop files here</strong>
        <span>or choose files from your computer</span>
      </div>
      <input
        ref={fileInput}
        className="visually-hidden-input"
        type="file"
        multiple
        accept={zone.id === "invoice" ? ".pdf,.zip" : ".xml,.csv,.pdf,.zip"}
        disabled={disabled}
        onChange={(event) => { onFiles(inputFiles(event.currentTarget.files)); event.currentTarget.value = ""; }}
        aria-label={`Choose ${zone.title.toLowerCase()} files`}
      />
      <input
        ref={folderInput}
        className="visually-hidden-input"
        type="file"
        multiple
        disabled={disabled}
        onChange={(event) => { onFiles(inputFiles(event.currentTarget.files)); event.currentTarget.value = ""; }}
        aria-label={`Choose a folder of ${zone.title.toLowerCase()}`}
      />
      <button className="text-button folder-upload-button" type="button" disabled={disabled} onClick={() => folderInput.current?.click()}>
        <FolderUp size={14} aria-hidden="true" /> Choose a folder
      </button>
    </section>
  );
}

function FileRow({
  file,
  child,
  onPreview,
  onRetry,
  onRemove,
  onMismatch,
  onMap,
  busy,
}: {
  file: IngestionFile;
  child: boolean;
  onPreview: (file: IngestionFile) => void;
  onRetry: (file: IngestionFile) => void;
  onRemove: (file: IngestionFile) => void;
  onMismatch: (file: IngestionFile, acceptSuggested: boolean) => void;
  onMap: (file: IngestionFile) => void;
  busy: boolean;
}) {
  const mismatch = file.mismatch_state === "suggested";
  const canRetry = file.status === "failed" || file.status === "partial";
  const canMap = file.format === "csv" && file.error_code === "CSV_MAPPING_REQUIRED";
  const warning = warningCode(file);
  return (
    <tr className={child ? "file-table-child-row" : ""}>
      <td>
        <div className="file-name-cell">
          {child ? <span className="zip-tree-branch" aria-hidden="true">↳</span> : file.format === "zip" ? <Archive size={15} aria-hidden="true" /> : <FileText size={15} aria-hidden="true" />}
          <span title={file.archive_path ?? file.display_name}>{file.archive_path ?? file.display_name}</span>
        </div>
      </td>
      <td><span className="file-type-label">{file.zone.toUpperCase()}</span></td>
      <td>{file.format.toUpperCase()}</td>
      <td>{formatBytes(file.size_bytes)}</td>
      <td><span className={statusClass(file.status)}>{statusLabel(file.status)}</span></td>
      <td>{file.docs_found}</td>
      <td>
        {warning ? <span className={`warning-chip${mismatch ? " warning-chip-alert" : ""}`} title={file.error_message ?? warning}>{warning.replaceAll("_", " ")}</span> : <span className="table-dash">—</span>}
      </td>
      <td>
        <div className="file-action-group">
          <button className="table-action" type="button" disabled={file.format === "zip" || busy || file.status === "removed"} onClick={() => onPreview(file)}>Preview</button>
          {canRetry ? <button className="table-action" type="button" disabled={busy} onClick={() => onRetry(file)}>Retry</button> : null}
          {canMap ? <button className="table-action action-highlight" type="button" disabled={busy} onClick={() => onMap(file)}>Map CSV</button> : null}
          {mismatch ? (
            <>
              <button className="table-action action-highlight" type="button" disabled={busy} onClick={() => onMismatch(file, true)}>Use {file.sniffed_type?.toUpperCase()}</button>
              <button className="table-action" type="button" disabled={busy} onClick={() => onMismatch(file, false)}>Keep {file.zone_uploaded.toUpperCase()}</button>
            </>
          ) : null}
          {file.status !== "removed" ? <button className="table-action table-action-danger" type="button" disabled={busy} onClick={() => onRemove(file)}>Remove</button> : null}
        </div>
      </td>
    </tr>
  );
}

function SummaryPanel({
  batchId,
  files,
  onPreview,
}: {
  batchId: string;
  files: IngestionFile[];
  onPreview: (file: IngestionFile, documentId?: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<Record<string, unknown> | null>(null);
  const [documents, setDocuments] = useState<Record<string, unknown>[]>([]);
  const [warnings, setWarnings] = useState<WarningItem[]>([]);

  const load = async () => {
    setOpen(true);
    setLoading(true);
    setError(null);
    try {
      const [batchSummary, documentRows, ...errorRows] = await Promise.all([
        getBatchSummary(batchId),
        getDocuments(batchId),
        ...files.filter((file) => file.parent_file_id === null).map((file) => getFileErrors(file.id).then((items) => ({ file, items }))),
      ]);
      setSummary(batchSummary);
      setDocuments(documentRows);
      const nextWarnings: WarningItem[] = [];
      for (const result of errorRows) {
        if (!("file" in result) || !("items" in result)) continue;
        const file = result.file as IngestionFile;
        const items = result.items as Record<string, unknown>[];
        for (const [index, item] of items.entries()) {
          const warning = typeof item["error_code"] === "string" || item["kind"] === "warning" || item["outcome"] === "error";
          if (!warning) continue;
          nextWarnings.push({
            id: `${file.id}-${index}-${String(item["error_code"] ?? item["stage"] ?? "warning")}`,
            title: String(item["error_code"] ?? item["stage"] ?? "Warning"),
            message: String(item["message"] ?? item["outcome"] ?? "Review this processing event."),
            fileId: file.id,
            documentId: typeof item["document_id"] === "string" ? item["document_id"] : null,
          });
        }
      }
      setWarnings(nextWarnings.slice(-20).reverse());
    } catch (cause) {
      setError(displayError(cause));
    } finally {
      setLoading(false);
    }
  };

  const activeFiles = files.filter((file) => file.parent_file_id === null && file.status !== "removed");
  const formatCounts = activeFiles.reduce<Record<string, number>>((counts, file) => {
    counts[file.format] = (counts[file.format] ?? 0) + 1;
    return counts;
  }, {});
  const typeCounts = documents.reduce<Record<string, number>>((counts, document) => {
    const type = typeof document["doc_type"] === "string" ? document["doc_type"] : "unknown";
    counts[type] = (counts[type] ?? 0) + 1;
    return counts;
  }, {});
  const vendorCount = new Set(documents.map((document) => document["vendor_identity"]).filter((value) => typeof value === "string")).size;

  return (
    <section className="panel ingestion-summary-panel">
      <div className="panel-heading">
        <div><div className="panel-kicker">BATCH RESULTS</div><h2>Ingestion summary</h2><p>See what was read, what needs attention, and continue to vendor and PO mapping.</p></div>
        <button className="button button-secondary" type="button" onClick={() => void load()} disabled={loading}>{loading ? "Refreshing…" : open ? "Refresh summary" : "View summary"}</button>
      </div>
      {open ? (
        <>
          {loading ? <p className="preview-message" role="status">Loading the batch totals…</p> : null}
          {error ? <p className="inline-error" role="alert">{error}</p> : null}
          {summary ? (
            <>
              <div className="summary-metrics">
                <div><span>Files</span><strong>{String(summary["file_count"] ?? activeFiles.length)}</strong></div>
                <div><span>Documents found</span><strong>{String(summary["documents_found"] ?? documents.length)}</strong></div>
                <div><span>New records</span><strong>{String(summary["documents_new"] ?? 0)}</strong></div>
                <div><span>Vendor anchors</span><strong>{String(vendorCount)}</strong></div>
              </div>
              <div className="summary-breakdown">
                <div><strong>Record types</strong><span>{Object.entries(typeCounts).map(([key, count]) => `${key.toUpperCase()} ${count}`).join(" · ") || "No parsed records yet"}</span></div>
                <div><strong>Source formats</strong><span>{Object.entries(formatCounts).map(([key, count]) => `${key.toUpperCase()} ${count}`).join(" · ") || "No uploaded files"}</span></div>
                <div><strong>Warnings</strong><span>{warnings.length} processing events need attention</span></div>
              </div>
              {warnings.length ? (
                <div className="summary-warning-list">
                  <h3>Warnings and processing issues</h3>
                  {warnings.map((warning) => (
                    <div className="summary-warning-row" key={warning.id}>
                      <div><strong>{warning.title.replaceAll("_", " ")}</strong><span>{warning.message}</span></div>
                      {warning.fileId ? <button className="table-action" type="button" onClick={() => { const file = files.find((entry) => entry.id === warning.fileId); if (file) onPreview(file, warning.documentId); }}>{warning.documentId ? "Open document evidence" : "Open source file"}</button> : null}
                    </div>
                  ))}
                </div>
              ) : <p className="summary-no-warnings"><CheckCircle2 size={15} /> No processing warnings were reported.</p>}
              <div className="summary-footer">
                <span>{Object.entries(summary["file_status_counts"] ?? {}).map(([key, count]) => `${statusLabel(key)} ${String(count)}`).join(" · ")}</span>
                <Link className="button button-primary" to={`/mapping?batch_id=${encodeURIComponent(batchId)}`}>Go to Mapping <ArrowRight size={15} /></Link>
              </div>
            </>
          ) : null}
        </>
      ) : null}
    </section>
  );
}

export function IngestionPage() {
  const { user, hasCapability } = useAuth();
  const navigate = useNavigate();
  const [batches, setBatches] = useState<BatchSummary[]>([]);
  const [batchId, setBatchId] = useState<string | null>(() => {
    try { return localStorage.getItem(BATCH_STORAGE_KEY); } catch { return null; }
  });
  const [batchLabel, setBatchLabel] = useState("");
  const [pending, setPending] = useState<LocalUpload[]>([]);
  const [files, setFiles] = useState<IngestionFile[]>([]);
  const [pollInterval, setPollInterval] = useState(2000);
  const [maxFileBytes, setMaxFileBytes] = useState(25 * 1024 * 1024);
  const [loadingBatches, setLoadingBatches] = useState(true);
  const [busy, setBusy] = useState(false);
  const [uploadProgress, setUploadProgress] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [previewTarget, setPreviewTarget] = useState<PreviewTarget | null>(null);
  const [mappingFile, setMappingFile] = useState<IngestionFile | null>(null);
  const fileEtag = useRef<string | null>(null);
  const canUpload = hasCapability("files.upload");
  const csrfToken = user?.csrf_token ?? "";

  const refreshFiles = useCallback(async (selectedBatchId: string, force = false) => {
    const result = await listFiles(selectedBatchId, force ? null : fileEtag.current);
    if (!result.notModified) setFiles(result.files);
    fileEtag.current = result.etag;
  }, []);

  useEffect(() => {
    let active = true;
    void Promise.all([listBatches(), getIngestionClientConfig()])
      .then(([batchRows, clientConfig]) => {
        if (!active) return;
        setBatches(batchRows);
        setPollInterval(clientConfig.pollIntervalMs);
        setMaxFileBytes(clientConfig.maxFileBytes);
        const preferred = batchId && batchRows.some((batch) => batch.id === batchId)
          ? batchId
          : batchRows[0]?.id ?? null;
        setBatchId(preferred);
      })
      .catch((cause: unknown) => { if (active) setError(displayError(cause)); })
      .finally(() => { if (active) setLoadingBatches(false); });
    return () => { active = false; };
    // Initial session/batch restoration only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    fileEtag.current = null;
    if (!batchId) {
      setFiles([]);
      try { localStorage.removeItem(BATCH_STORAGE_KEY); } catch { /* storage may be disabled */ }
      return;
    }
    try { localStorage.setItem(BATCH_STORAGE_KEY, batchId); } catch { /* storage may be disabled */ }
    let active = true;
    const refresh = async (force = false) => {
      try {
        const result = await listFiles(batchId, force ? null : fileEtag.current);
        if (!active) return;
        if (!result.notModified) setFiles(result.files);
        fileEtag.current = result.etag;
      } catch (cause) {
        if (active) setError(displayError(cause));
      }
    };
    void refresh(true);
    const timer = window.setInterval(() => { void refresh(); }, pollInterval);
    return () => { active = false; window.clearInterval(timer); };
  }, [batchId, pollInterval]);

  useEffect(() => {
    setBatchLabel(batchId ? batches.find((batch) => batch.id === batchId)?.label ?? "" : "");
  }, [batchId, batches]);

  const rootFiles = useMemo(() => files.filter((file) => file.parent_file_id === null), [files]);
  const childrenByParent = useMemo(() => {
    const groups = new Map<string, IngestionFile[]>();
    for (const file of files) {
      if (!file.parent_file_id) continue;
      const group = groups.get(file.parent_file_id) ?? [];
      group.push(file);
      groups.set(file.parent_file_id, group);
    }
    return groups;
  }, [files]);
  const progressRows = useMemo(() => files.filter((file) => {
    if (file.status === "removed") return false;
    if (file.format === "zip" && childrenByParent.has(file.id)) return false;
    return true;
  }), [childrenByParent, files]);
  const doneCount = progressRows.filter((file) => TERMINAL_STATUSES.has(file.status)).length;
  const progressPercent = progressRows.length ? Math.round((doneCount / progressRows.length) * 100) : 0;
  const isProcessing = files.some((file) => PROCESSING_STATUSES.has(file.status));

  const addFiles = (zone: UploadZone, incoming: File[]) => {
    if (!incoming.length) return;
    setError(null);
    setInfo(null);
    setPending((current) => [
      ...current,
      ...incoming.map((file) => ({
        id: makeLocalId(),
        file,
        zone,
        error: validateLocalFile(file, zone, maxFileBytes),
      })),
    ]);
  };

  const removePending = (id: string) => setPending((current) => current.filter((item) => item.id !== id));

  const processUploads = async () => {
    if (!csrfToken) {
      setError("Your session is missing its security token. Sign in again and retry.");
      return;
    }
    const validPending = pending.filter((item) => !item.error);
    if (!batchId && validPending.length === 0) {
      setError("Choose at least one valid file before processing.");
      return;
    }
    setBusy(true);
    setError(null);
    setInfo(null);
    let activeBatchId = batchId;
    try {
      if (!activeBatchId) {
        const created = await createBatch(batchLabel, csrfToken);
        activeBatchId = created.id;
        setBatches((current) => [created, ...current.filter((batch) => batch.id !== created.id)]);
        setBatchId(created.id);
      }
      let uploaded = 0;
      const failedIds = new Set<string>();
      for (const [index, item] of validPending.entries()) {
        setUploadProgress(`Uploading ${index + 1} of ${validPending.length}: ${item.file.name}`);
        try {
          await uploadFile(activeBatchId, item.zone, item.file, csrfToken);
          uploaded += 1;
          setPending((current) => current.filter((pendingFile) => pendingFile.id !== item.id));
        } catch (cause) {
          failedIds.add(item.id);
          setPending((current) => current.map((pendingFile) => pendingFile.id === item.id
            ? { ...pendingFile, error: displayError(cause) }
            : pendingFile));
        }
      }
      if (uploaded > 0 || rootFiles.some((file) => file.status === "staged")) {
        setUploadProgress("Starting the background processing queue…");
        await processBatch(activeBatchId, csrfToken);
        setInfo("Files are queued. Processing continues in the background if you leave this page.");
      } else if (failedIds.size > 0) {
        setError("No files uploaded successfully. Fix the highlighted upload errors and retry.");
      }
      fileEtag.current = null;
      await refreshFiles(activeBatchId, true);
    } catch (cause) {
      setError(displayError(cause));
    } finally {
      setBusy(false);
      setUploadProgress("");
    }
  };

  const runAction = async (action: () => Promise<void>, successMessage?: string) => {
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      await action();
      if (batchId) {
        await refreshFiles(batchId, true);
        if (successMessage) setInfo(successMessage);
      }
    } catch (cause) {
      setError(displayError(cause));
    } finally {
      setBusy(false);
    }
  };

  const removeServerFile = (file: IngestionFile) => {
    if (!window.confirm(`Remove ${file.display_name}? Any documents created from it will be withdrawn from this workspace.`)) return;
    void runAction(() => removeFile(file.id, csrfToken), `${file.display_name} was removed. Its extracted documents are withdrawn.`);
  };

  const resolveMismatch = (file: IngestionFile, acceptSuggested: boolean) => {
    void runAction(async () => {
      await resolveFileMismatch(file.id, acceptSuggested, csrfToken);
      if (batchId) await processBatch(batchId, csrfToken);
    }, "The type decision was saved and the file was queued again.");
  };

  const selectedBatchLabel = batchId
    ? batches.find((batch) => batch.id === batchId)?.label || "Untitled batch"
    : "New batch";

  const openPreview = (file: IngestionFile, documentId: string | null = null) => setPreviewTarget({ file, documentId });

  return (
    <>
      <PageHeader
        eyebrow="STEP 1 · BRING SOURCE RECORDS IN"
        title="Ingestion"
        description="Upload purchase orders, goods-received notes and invoices. ReconBench stores the originals, parses them in the background and keeps a review trail for each result."
        action={batchId && files.length > 0 ? <button className="button button-secondary" type="button" onClick={() => { void refreshFiles(batchId, true); }}><RefreshCw size={14} /> Refresh status</button> : undefined}
      />

      {!canUpload ? <div className="banner-warning ingestion-permission-note"><CircleHelp size={17} /><span>Your account can view ingestion status, but uploading requires the <strong>files.upload</strong> capability.</span></div> : null}
      {error ? <div className="banner-error ingestion-message" role="alert"><AlertCircle size={17} /><span>{error}</span><button type="button" aria-label="Dismiss error" onClick={() => setError(null)}><X size={15} /></button></div> : null}
      {info ? <div className="banner-info ingestion-message" role="status"><CheckCircle2 size={17} /><span>{info}</span><button type="button" aria-label="Dismiss message" onClick={() => setInfo(null)}><X size={15} /></button></div> : null}

      <section className="panel batch-control-panel" aria-label="Ingestion batch">
        <div>
          <div className="panel-kicker">UPLOAD BATCH</div>
          <h2>{batchId ? selectedBatchLabel : "Start a new batch"}</h2>
          <p>Choose a saved batch to continue its status, or create a new one for another period.</p>
        </div>
        <div className="batch-fields">
          <label className="batch-select-label">
            Batch
            <span className="batch-select-wrap">
              <select
                aria-label="Select ingestion batch"
                value={batchId ?? "__new__"}
                disabled={loadingBatches || busy}
                onChange={(event) => { setInfo(null); setError(null); setBatchId(event.target.value === "__new__" ? null : event.target.value); }}
              >
                <option value="__new__">Create a new batch</option>
                {batches.map((batch) => <option key={batch.id} value={batch.id}>{batch.label || "Untitled batch"}{batch.period_start ? ` · ${batch.period_start}` : ""}</option>)}
              </select>
              <ChevronDown size={15} aria-hidden="true" />
            </span>
          </label>
          {!batchId ? (
            <label className="batch-label-input">Optional batch name
              <input maxLength={200} value={batchLabel} onChange={(event) => setBatchLabel(event.target.value)} placeholder="e.g. April 2026 close" disabled={busy || !canUpload} />
            </label>
          ) : null}
          {loadingBatches ? <span className="batch-load-status" role="status">Loading previous batches…</span> : null}
        </div>
      </section>

      <section className="ingestion-zones-grid" aria-label="Upload zones">
        {ZONES.map((zone) => <UploadZoneCard key={zone.id} zone={zone} onFiles={(selected) => addFiles(zone.id, selected)} disabled={!canUpload || busy} />)}
      </section>

      {pending.length ? (
        <section className="panel pending-upload-panel">
          <div className="panel-heading"><div><div className="panel-kicker">READY TO SEND</div><h2>Selected files</h2><p>Files are checked against the configured upload limit before they are sent.</p></div><span className="quiet-badge">{pending.length} selected</span></div>
          <div className="pending-file-list">
            {pending.map((item) => (
              <div className={`pending-file-row${item.error ? " pending-file-rejected" : ""}`} key={item.id}>
                <span className="pending-file-icon">{item.file.name.toLowerCase().endsWith(".zip") ? <Archive size={15} /> : <FileText size={15} />}</span>
                <div><strong>{item.file.name}</strong><span>{item.zone.toUpperCase()} · {formatBytes(item.file.size)}</span>{item.error ? <small>{item.error}</small> : null}</div>
                <button className="icon-button" type="button" aria-label={`Remove ${item.file.name} from upload list`} disabled={busy} onClick={() => removePending(item.id)}><X size={15} /></button>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <div className="ingestion-process-row">
        <div className="ingestion-process-hint">
          {uploadProgress ? <><LoaderCircle size={16} className="spin-icon" /><span>{uploadProgress}</span></> : <><CircleHelp size={15} /><span>Processing happens in a background worker and remains active if you leave this page.</span></>}
        </div>
        <button className="button button-primary process-button" type="button" disabled={!canUpload || busy || (pending.length === 0 && !rootFiles.some((file) => file.status === "staged"))} onClick={() => void processUploads()}>
          {busy ? <LoaderCircle size={15} className="spin-icon" /> : <Upload size={15} />}
          {busy ? "Working…" : "Process files"}
        </button>
      </div>

      {batchId && files.length > 0 ? (
        <>
          <section className="panel ingestion-files-panel">
            <div className="panel-heading file-panel-heading">
              <div><div className="panel-kicker">BATCH FILES · {selectedBatchLabel}</div><h2>File processing</h2><p>Latest server status is refreshed every {Math.max(1, Math.round(pollInterval / 1000))} seconds. A refresh after sign-in restores saved progress.</p></div>
              <div className="file-panel-counts"><strong>{doneCount} / {progressRows.length}</strong><span>files complete</span></div>
            </div>
            <div className="batch-progress-track" role="progressbar" aria-label="Batch processing progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progressPercent}><span style={{ width: `${progressPercent}%` }} /></div>
            <div className="ingestion-table-wrap">
              <table className="ingestion-file-table">
                <thead><tr><th>Name</th><th>Type</th><th>Format</th><th>Size</th><th>Status</th><th>Docs found</th><th>Warnings</th><th>Actions</th></tr></thead>
                <tbody>
                  {rootFiles.map((file) => (
                    <Fragment key={file.id}>
                      <FileRow file={file} child={false} onPreview={openPreview} onRetry={(entry) => void runAction(() => retryFile(entry.id, csrfToken), `${entry.display_name} was queued for retry.`)} onRemove={removeServerFile} onMismatch={resolveMismatch} onMap={setMappingFile} busy={busy} />
                      {(childrenByParent.get(file.id) ?? []).map((child) => <FileRow key={child.id} file={child} child onPreview={openPreview} onRetry={(entry) => void runAction(() => retryFile(entry.id, csrfToken), `${entry.display_name} was queued for retry.`)} onRemove={removeServerFile} onMismatch={resolveMismatch} onMap={setMappingFile} busy={busy} />)}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
            {isProcessing ? <div className="processing-banner"><LoaderCircle size={15} className="spin-icon" /> Worker is processing this batch. You may safely navigate to another page.</div> : null}
          </section>
          {progressRows.length > 0 && progressRows.every((file) => TERMINAL_STATUSES.has(file.status)) ? <SummaryPanel batchId={batchId} files={files} onPreview={openPreview} /> : null}
        </>
      ) : (
        <section className="panel ingestion-empty-panel">
          <span className="empty-file-icon"><FileInput size={17} /></span>
          <div><strong>{batchId ? "No files in this batch yet" : "Your upload queue is empty"}</strong><span>Choose files above, then select Process files to send them into the ingestion pipeline.</span></div>
        </section>
      )}

      {previewTarget ? <IngestionPreview key={`${previewTarget.file.id}-${previewTarget.documentId ?? "file"}`} file={previewTarget.file} batchId={batchId} focusDocumentId={previewTarget.documentId} onClose={() => setPreviewTarget(null)} /> : null}
      {mappingFile ? <CsvMappingDialog fileId={mappingFile.id} fileName={mappingFile.display_name} csrfToken={csrfToken} onClose={() => setMappingFile(null)} onSaved={() => { setMappingFile(null); if (batchId) void refreshFiles(batchId, true); setInfo("Column map saved. The file is queued again using this layout."); }} /> : null}
      <button className="ingestion-help-anchor" type="button" onClick={() => navigate("/home")}><TriangleAlert size={13} /> Review unresolved extraction warnings after processing</button>
    </>
  );
}
