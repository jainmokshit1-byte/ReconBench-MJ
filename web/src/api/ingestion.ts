import { notifySessionExpired } from "../auth/session";

export type UploadZone = "po" | "grn" | "invoice";

export interface BatchSummary {
  id: string;
  label: string | null;
  version: number;
  period_start: string | null;
  period_end: string | null;
}

export interface IngestionFile {
  id: string;
  batch_id: string | null;
  parent_file_id: string | null;
  display_name: string;
  archive_path: string | null;
  zone: string;
  zone_uploaded: string;
  format: string;
  sniffed_type: string | null;
  mismatch_state: string;
  status: string;
  error_code: string | null;
  error_message: string | null;
  attempt_count: number;
  size_bytes: number;
  page_count: number | null;
  docs_found: number;
  docs_new: number;
  docs_updated: number;
  docs_skipped: number;
  version: number;
  retry_available?: boolean;
  retry_reason?: string | null;
  uploaded_at: string;
}

export type DocumentRow = Record<string, unknown> & { id: string };
export type DocumentDetails = Record<string, unknown>;

export interface FileListResult {
  files: IngestionFile[];
  etag: string | null;
  notModified: boolean;
}

export interface IngestionClientConfig {
  pollIntervalMs: number;
  maxFileBytes: number;
}

const baseUrl = (import.meta.env["VITE_API_BASE_URL"] ?? "").replace(/\/$/, "");

function endpoint(path: string): string {
  return `${baseUrl}/api/v1${path}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function numberOrZero(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function parseFile(value: unknown): IngestionFile | null {
  if (!isRecord(value) || typeof value["id"] !== "string") return null;
  return {
    id: value["id"],
    batch_id: stringOrNull(value["batch_id"]),
    parent_file_id: stringOrNull(value["parent_file_id"]),
    display_name: typeof value["display_name"] === "string" ? value["display_name"] : "File",
    archive_path: stringOrNull(value["archive_path"]),
    zone: typeof value["zone"] === "string" ? value["zone"] : "unknown",
    zone_uploaded: typeof value["zone_uploaded"] === "string" ? value["zone_uploaded"] : "unknown",
    format: typeof value["format"] === "string" ? value["format"] : "unknown",
    sniffed_type: stringOrNull(value["sniffed_type"]),
    mismatch_state: typeof value["mismatch_state"] === "string" ? value["mismatch_state"] : "none",
    status: typeof value["status"] === "string" ? value["status"] : "unknown",
    error_code: stringOrNull(value["error_code"]),
    error_message: stringOrNull(value["error_message"]),
    attempt_count: numberOrZero(value["attempt_count"]),
    size_bytes: numberOrZero(value["size_bytes"]),
    page_count: typeof value["page_count"] === "number" ? value["page_count"] : null,
    docs_found: numberOrZero(value["docs_found"]),
    docs_new: numberOrZero(value["docs_new"]),
    docs_updated: numberOrZero(value["docs_updated"]),
    docs_skipped: numberOrZero(value["docs_skipped"]),
    version: numberOrZero(value["version"]),
    retry_available: value["retry_available"] === true,
    retry_reason: typeof value["retry_reason"] === "string" ? value["retry_reason"] : null,
    uploaded_at: typeof value["uploaded_at"] === "string" ? value["uploaded_at"] : "",
  };
}

async function request(path: string, csrfToken: string | null, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (csrfToken && init.method && init.method !== "GET") {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const response = await fetch(endpoint(path), {
    ...init,
    headers,
    credentials: "include",
  });
  if (response.status === 401) notifySessionExpired();
  if (!response.ok && response.status !== 304) {
    let message = `Request failed (${response.status})`;
    try {
      const body: unknown = await response.json();
      if (isRecord(body) && typeof body["detail"] === "string") message = body["detail"];
      else if (isRecord(body) && typeof body["title"] === "string") message = body["title"];
    } catch {
      // Keep the status-based message if the server returns a non-JSON error.
    }
    throw new Error(message);
  }
  return response;
}

async function json(response: Response): Promise<unknown> {
  return response.json() as Promise<unknown>;
}

export async function listBatches(): Promise<BatchSummary[]> {
  const value = await json(await request("/batches", null));
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry): BatchSummary[] => {
    if (!isRecord(entry) || typeof entry["id"] !== "string") return [];
    return [{
      id: entry["id"],
      label: stringOrNull(entry["label"]),
      version: numberOrZero(entry["version"]),
      period_start: stringOrNull(entry["period_start"]),
      period_end: stringOrNull(entry["period_end"]),
    }];
  });
}

export async function createBatch(label: string, csrfToken: string): Promise<BatchSummary> {
  const response = await request("/batches", csrfToken, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label: label.trim() || null }),
  });
  const value: unknown = await json(response);
  if (!isRecord(value) || typeof value["id"] !== "string") throw new Error("The server returned an invalid batch.");
  return {
    id: value["id"],
    label: stringOrNull(value["label"]),
    version: numberOrZero(value["version"]),
    period_start: stringOrNull(value["period_start"]),
    period_end: stringOrNull(value["period_end"]),
  };
}

export async function listFiles(batchId: string, etag: string | null): Promise<FileListResult> {
  const headers = etag ? { "If-None-Match": etag } : undefined;
  const response = await request(`/files?batch_id=${encodeURIComponent(batchId)}&limit=500`, null, { headers });
  if (response.status === 304) return { files: [], etag: response.headers.get("ETag") ?? etag, notModified: true };
  const value: unknown = await json(response);
  if (!Array.isArray(value)) throw new Error("The server returned an invalid file list.");
  const files = value.flatMap((entry): IngestionFile[] => {
    const parsed = parseFile(entry);
    return parsed ? [parsed] : [];
  });
  return { files, etag: response.headers.get("ETag"), notModified: false };
}

export async function getIngestionClientConfig(): Promise<IngestionClientConfig> {
  try {
    const value: unknown = await json(await request("/config", null));
    if (isRecord(value) && isRecord(value["body"]) && isRecord(value["body"]["app"]) && isRecord(value["body"]["app"]["ui"])) {
      const seconds = value["body"]["app"]["ui"]["poll_seconds"];
      const limits = value["body"]["app"]["limits"];
      const fileMb = isRecord(limits) ? limits["max_file_mb"] : null;
      return {
        pollIntervalMs:
          typeof seconds === "number" && Number.isFinite(seconds) && seconds >= 1 && seconds <= 60
            ? seconds * 1000
            : 2000,
        maxFileBytes:
          typeof fileMb === "number" && Number.isFinite(fileMb) && fileMb >= 1 && fileMb <= 4096
            ? fileMb * 1024 * 1024
            : 25 * 1024 * 1024,
      };
    }
  } catch {
    // The Plan's two-second poll and 25 MB default remain safe fallbacks.
  }
  return { pollIntervalMs: 2000, maxFileBytes: 25 * 1024 * 1024 };
}

export function fileContentUrl(fileId: string): string {
  return endpoint(`/files/${encodeURIComponent(fileId)}/content`);
}

export function displayError(error: unknown): string {
  return error instanceof Error ? error.message : "The request could not be completed.";
}

export interface UploadResult {
  file_id: string;
  /** The same bytes are already an active upload: nothing was stored or queued (PD-0b-4). */
  duplicate: boolean;
  original_display_name?: string | null;
  original_batch_id?: string | null;
}

export async function uploadFile(batchId: string, zone: UploadZone, file: File, csrfToken: string): Promise<UploadResult> {
  const safeName = file.name.replace(/[^\x20-\x7E]/g, "_").replace(/[\r\n]/g, "_");
  const response = await request(`/files?batch_id=${encodeURIComponent(batchId)}&zone=${zone}`, csrfToken, {
    method: "POST",
    headers: {
      "Content-Type": "application/octet-stream",
      "X-File-Name": safeName,
    },
    body: file,
  });
  try {
    return (await response.json()) as UploadResult;
  } catch {
    return { file_id: "", duplicate: false };
  }
}

export interface DuplicateHold {
  id: string;
  source_file_id: string;
  file_name: string;
  kind: "duplicate" | "possible";
  reason: string;
  doc_no: string | null;
  state: "open" | "discarded" | "replaced" | "kept";
  version: number;
  duplicate_of_document_id: string;
  original_doc_no: string | null;
  original_file_name: string | null;
  original_batch_id: string | null;
}

export async function listDuplicateHolds(state = "open"): Promise<DuplicateHold[]> {
  const response = await request(`/duplicate-holds?state=${encodeURIComponent(state)}`, null);
  return (await response.json()) as DuplicateHold[];
}

export async function decideDuplicateHold(
  hold: DuplicateHold,
  decision: "discard" | "replace" | "keep",
  csrfToken: string,
): Promise<void> {
  await request(`/duplicate-holds/${encodeURIComponent(hold.id)}/decision`, csrfToken, {
    method: "POST",
    headers: { "Content-Type": "application/json", "If-Match": `"${hold.version}"` },
    body: JSON.stringify({ decision }),
  });
}

export const DUPLICATE_REASON_TEXT: Record<string, string> = {
  same_vendor_number_year: "Same supplier GSTIN, invoice number and financial year.",
  number_low_confidence: "Same supplier and number, but the number was read with low confidence.",
  same_supplier_other_registration: "Same number from the same supplier under another GSTIN.",
  vendor_not_resolved: "Same number and year; the supplier could not be identified.",
  date_not_read: "Same supplier and number; the invoice date could not be read.",
  number_read_differently: "Same supplier and year; the number differs only by look-alike characters.",
  rescan_match: "The pages look like a scan of an invoice already in the app.",
};

export async function processBatch(batchId: string, csrfToken: string): Promise<void> {
  await request(`/batches/${encodeURIComponent(batchId)}/process`, csrfToken, { method: "POST" });
}

export async function getFile(fileId: string): Promise<{ file: IngestionFile; etag: string | null }> {
  const response = await request(`/files/${encodeURIComponent(fileId)}`, null);
  const parsed = parseFile(await json(response));
  if (!parsed) throw new Error("The server returned an invalid file.");
  return { file: parsed, etag: response.headers.get("ETag") };
}

export async function resolveFileMismatch(fileId: string, acceptSuggested: boolean, csrfToken: string): Promise<void> {
  const { etag } = await getFile(fileId);
  if (!etag) throw new Error("The file version could not be verified. Refresh and try again.");
  await request(`/files/${encodeURIComponent(fileId)}`, csrfToken, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", "If-Match": etag },
    body: JSON.stringify({ decision: acceptSuggested ? "accept_suggested" : "keep_zone" }),
  });
}

/** Accept a PDF's proposed split into documents, or keep it as one document of its zone. */
export async function resolvePageSplit(fileId: string, acceptSplit: boolean, csrfToken: string): Promise<void> {
  const { etag } = await getFile(fileId);
  if (!etag) throw new Error("The file version could not be verified. Refresh and try again.");
  await request(`/files/${encodeURIComponent(fileId)}/page-split`, csrfToken, {
    method: "POST",
    headers: { "Content-Type": "application/json", "If-Match": etag },
    body: JSON.stringify({ decision: acceptSplit ? "accept_split" : "keep_one" }),
  });
}

export async function retryFile(fileId: string, csrfToken: string): Promise<void> {
  await request(`/files/${encodeURIComponent(fileId)}/retry`, csrfToken, { method: "POST" });
}

export async function removeFile(fileId: string, csrfToken: string): Promise<void> {
  await request(`/files/${encodeURIComponent(fileId)}`, csrfToken, { method: "DELETE" });
}

export async function saveColumnMapping(
  fileId: string,
  columnMapping: Record<string, string>,
  csrfToken: string,
): Promise<void> {
  await request(`/files/${encodeURIComponent(fileId)}/column-mapping`, csrfToken, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ column_mapping: columnMapping }),
  });
}

export async function getFileContent(fileId: string): Promise<Response> {
  return request(`/files/${encodeURIComponent(fileId)}/content`, null);
}

export async function getDocuments(batchId: string, sourceFileId?: string): Promise<DocumentRow[]> {
  const query = new URLSearchParams({ batch_id: batchId, limit: "500" });
  if (sourceFileId) query.set("source_file_id", sourceFileId);
  const value = await json(await request(`/documents?${query.toString()}`, null));
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry): DocumentRow[] => isRecord(entry) && typeof entry["id"] === "string" ? [entry as DocumentRow] : []);
}

export async function listCurrentDocuments(docType?: UploadZone): Promise<DocumentRow[]> {
  const query = new URLSearchParams({ limit: "500" });
  if (docType) query.set("doc_type", docType);
  const value = await json(await request(`/documents?${query.toString()}`, null));
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry): DocumentRow[] => isRecord(entry) && typeof entry["id"] === "string" ? [entry as DocumentRow] : []);
}

export async function getDocumentDetails(documentId: string): Promise<DocumentDetails> {
  const value: unknown = await json(await request(`/documents/${encodeURIComponent(documentId)}`, null));
  if (!isRecord(value)) throw new Error("The server returned invalid document details.");
  return value;
}

export async function createManualInvoice(
  sourceFileId: string,
  invoice: Record<string, unknown>,
  csrfToken: string,
): Promise<DocumentDetails> {
  const response = await request(`/files/${encodeURIComponent(sourceFileId)}/manual-invoice`, csrfToken, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(invoice),
  });
  const value: unknown = await json(response);
  if (!isRecord(value)) throw new Error("The server returned invalid manually entered invoice details.");
  return value;
}

export async function correctDocument(
  documentId: string,
  version: number,
  correction: Record<string, unknown>,
  csrfToken: string,
): Promise<DocumentDetails> {
  const response = await request(`/documents/${encodeURIComponent(documentId)}/correction`, csrfToken, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      "If-Match": `"${version}"`,
    },
    body: JSON.stringify(correction),
  });
  const value: unknown = await json(response);
  if (!isRecord(value)) throw new Error("The server returned invalid corrected document details.");
  return value;
}

/** One value read by AI beside the rules (OCR) reading of the same file (PD-4d). */
export interface AiValue {
  field_name: string;
  line_id: string | null;
  line_no: number | null;
  label: string;
  value_text: string | null;
  ocr_value_text: string | null;
  agrees: boolean;
  verified: boolean;
  page: number | null;
  bbox: number[] | null;
}

export function aiValues(details: DocumentDetails): AiValue[] {
  const raw = details["ai_values"];
  if (!Array.isArray(raw)) return [];
  return raw.filter((item): item is AiValue => isRecord(item)
    && typeof item["field_name"] === "string"
    && typeof item["label"] === "string"
    && typeof item["agrees"] === "boolean"
    && typeof item["verified"] === "boolean");
}

/** Record that a person checked these values against the page; or every value that agrees with OCR. */
export async function verifyAiValues(
  documentId: string,
  version: number,
  choice: { values: { field_name: string; line_id: string | null }[] } | { all_agreeing: true },
  csrfToken: string,
): Promise<DocumentDetails> {
  const response = await request(`/documents/${encodeURIComponent(documentId)}/verify-ai-values`, csrfToken, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "If-Match": `"${version}"`,
    },
    body: JSON.stringify(choice),
  });
  const value: unknown = await json(response);
  if (!isRecord(value)) throw new Error("The server returned invalid document details.");
  return value;
}

/** Backwards-compatible name retained for invoice-only call sites. */
export const correctInvoiceDocument = correctDocument;

export async function getBatchSummary(batchId: string): Promise<Record<string, unknown>> {
  const value: unknown = await json(await request(`/batches/${encodeURIComponent(batchId)}/summary`, null));
  if (!isRecord(value)) throw new Error("The server returned an invalid batch summary.");
  return value;
}

export async function getFileErrors(fileId: string): Promise<Record<string, unknown>[]> {
  const response = await request(`/files/${encodeURIComponent(fileId)}/errors`, null);
  const body = await response.text();
  return body.split(/\r?\n/).filter(Boolean).flatMap((line): Record<string, unknown>[] => {
    try {
      const value: unknown = JSON.parse(line);
      return isRecord(value) ? [value] : [];
    } catch {
      return [];
    }
  });
}

export function isRecordValue(value: unknown): value is Record<string, unknown> {
  return isRecord(value);
}
