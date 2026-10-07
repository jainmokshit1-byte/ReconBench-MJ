import { notifySessionExpired } from "../auth/session";

export interface MappingCounts {
  po: number;
  grn: number;
  invoice: number;
  linked: number;
  suggested: number;
  unlinked: number;
}

/** Trust in the display name (AC-4): a person's, a checked reading, an OCR reading, or none yet. */
export type VendorNameStatus = "manual" | "verified" | "provisional" | "pending";

export interface MappingVendor {
  id: string | null;
  canonical_name: string;
  name_status?: VendorNameStatus;
  version?: number;
  gstins: string[];
  pan: string | null;
  anchor: string;
  aliases?: string[];
  counts: MappingCounts;
  has_issues: boolean;
  unassigned: boolean;
}

export interface CurrentMappingLink {
  id: string;
  version_no: number;
  po_document_id: string;
  po_no: string | null;
  method: string;
  confidence: string | number;
  state: "auto" | "suggested" | "confirmed" | "rejected";
  /** Proposed by AI (H3) or confirmed from an AI proposal; never used until confirmed. */
  ai_suggested?: boolean;
  evidence: string | null;
  level: string;
  line_no: number | null;
}

export interface MappingDocument {
  id: string;
  doc_type: "po" | "grn" | "invoice";
  doc_no: string | null;
  vendor_name: string | null;
  vendor_gstin: string | null;
  doc_date: string | null;
  grand_total: string | number | null;
  currency: string | null;
  source_file_id: string;
  vendor_id: string | null;
  needs_verification: boolean;
  links: CurrentMappingLink[];
  resolution: {
    outcome: string;
    resolved_step: number | null;
    detail_code: string | null;
    cited_refs: string[];
    candidates: Array<Record<string, unknown>>;
  } | null;
}

export interface MergeSuggestion {
  id: string;
  vendor_a_id: string;
  vendor_b_id: string;
  score: string | number;
  version: number;
  vendor_a_name: string;
  vendor_a_pan: string | null;
  vendor_a_gstins: string[];
  vendor_b_name: string;
  vendor_b_pan: string | null;
  vendor_b_gstins: string[];
  pan_conflict: boolean;
  /** Advisory H4 opinion; it never merges or decides anything. */
  ai_opinion?: "same" | "different" | "unsure" | null;
  ai_reason?: string | null;
}

export interface VendorAlias {
  id: string;
  vendor_id: string;
  alias_raw: string;
  alias_norm: string;
  source: "merge" | "manual" | "learned" | "seed";
}

export class MappingApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "MappingApiError";
  }
}

const baseUrl = (import.meta.env["VITE_API_BASE_URL"] ?? "").replace(/\/$/, "");

async function requestJson<T>(path: string, csrfToken?: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (csrfToken && init.method && init.method !== "GET") headers.set("X-CSRF-Token", csrfToken);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(`${baseUrl}/api/v1${path}`, {
    ...init,
    headers,
    credentials: "include",
  });
  if (response.status === 401) notifySessionExpired();
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body: unknown = await response.json();
      if (typeof body === "object" && body !== null && "detail" in body && typeof body.detail === "string") {
        message = body.detail;
      }
    } catch {
      // Keep the status message when the server did not return a JSON problem.
    }
    throw new MappingApiError(message, response.status);
  }
  return (await response.json()) as T;
}

export async function listMappingVendors(search = "", hasIssues = false): Promise<MappingVendor[]> {
  const query = new URLSearchParams();
  if (search.trim()) query.set("search", search.trim());
  if (hasIssues) query.set("has_issues", "true");
  const suffix = query.size ? `?${query.toString()}` : "";
  return requestJson<MappingVendor[]>(`/vendors${suffix}`);
}

export async function listMappingDocuments(
  vendorId: string | null,
  docType: "po" | "grn" | "invoice" | null,
): Promise<MappingDocument[]> {
  const query = new URLSearchParams();
  if (docType) query.set("doc_type", docType);
  const suffix = query.size ? `?${query.toString()}` : "";
  const path = vendorId ? `/vendors/${encodeURIComponent(vendorId)}/documents${suffix}` : `/vendors/unassigned/documents${suffix}`;
  return requestJson<MappingDocument[]>(path);
}

export function listMergeSuggestions(): Promise<MergeSuggestion[]> {
  return requestJson<MergeSuggestion[]>("/vendor-merge-suggestions");
}

export function listVendorAliases(vendorId?: string): Promise<VendorAlias[]> {
  const query = vendorId ? `?vendor_id=${encodeURIComponent(vendorId)}` : "";
  return requestJson<VendorAlias[]>(`/vendor-aliases${query}`);
}

export function addVendorAlias(vendorId: string, alias: string, csrfToken: string): Promise<VendorAlias> {
  return requestJson<VendorAlias>("/vendor-aliases", csrfToken, {
    method: "POST",
    body: JSON.stringify({ vendor_id: vendorId, alias }),
  });
}

export function deleteVendorAlias(aliasId: string, csrfToken: string): Promise<{ deleted: boolean }> {
  return requestJson<{ deleted: boolean }>(`/vendor-aliases/${encodeURIComponent(aliasId)}`, csrfToken, {
    method: "DELETE",
  });
}

export function createMappingVendor(
  vendor: { name: string; gstin?: string | null; pan?: string | null },
  csrfToken: string,
): Promise<MappingVendor> {
  return requestJson<MappingVendor>("/vendors", csrfToken, {
    method: "POST",
    body: JSON.stringify({ name: vendor.name, gstin: vendor.gstin || null, pan: vendor.pan || null }),
  });
}

export function renameMappingVendor(vendorId: string, name: string, version: number, csrfToken: string): Promise<unknown> {
  return requestJson(`/vendors/${encodeURIComponent(vendorId)}`, csrfToken, {
    method: "PATCH",
    headers: { "If-Match": `"${version}"` },
    body: JSON.stringify({ name }),
  });
}

export function assignMappingVendor(documentId: string, vendorId: string, csrfToken: string): Promise<unknown> {
  return requestJson(`/documents/${encodeURIComponent(documentId)}/assign-vendor`, csrfToken, {
    method: "POST",
    body: JSON.stringify({ vendor_id: vendorId }),
  });
}

export function decideMergeSuggestion(
  suggestionId: string,
  decision: "accept" | "reject",
  survivorVendorId: string | null,
  version: number,
  csrfToken: string,
): Promise<unknown> {
  return requestJson(`/vendor-merge-suggestions/${encodeURIComponent(suggestionId)}/decision`, csrfToken, {
    method: "POST",
    headers: { "If-Match": `"${version}"` },
    body: JSON.stringify({ decision, survivor_vendor_id: survivorVendorId }),
  });
}

export function editMappingLink(
  linkId: string,
  action: "confirm" | "change" | "unlink",
  version: number,
  csrfToken: string,
  targetDocumentId?: string,
): Promise<CurrentMappingLink> {
  return requestJson(`/links/${encodeURIComponent(linkId)}`, csrfToken, {
    method: "PATCH",
    headers: { "If-Match": `"${version}"` },
    body: JSON.stringify({ action, to_document_id: targetDocumentId ?? null }),
  });
}

export function bulkConfirmMappingLinks(vendorId: string | null, csrfToken: string): Promise<{ confirmed: number; items: unknown[] }> {
  return requestJson("/links/bulk-confirm", csrfToken, {
    method: "POST",
    headers: { "Idempotency-Key": `mapping-bulk-${crypto.randomUUID()}` },
    body: JSON.stringify({ vendor_id: vendorId, threshold: "0.95" }),
  });
}
