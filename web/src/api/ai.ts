import { notifySessionExpired } from "../auth/session";

export interface AiGuardrailReport {
  passed: boolean;
  checks: Record<string, boolean>;
  provider: "stub";
  external_provider_called: false;
  financial_verdict_changed: false;
  status_code: string;
}

const apiBase = (import.meta.env["VITE_API_BASE_URL"] ?? "").replace(/\/$/, "");

export async function runLocalAiGuardrailTest(csrfToken: string): Promise<AiGuardrailReport> {
  const response = await fetch(`${apiBase}/api/v1/ai/test`, {
    method: "POST",
    credentials: "include",
    headers: { "X-CSRF-Token": csrfToken },
  });
  if (!response.ok) {
    let message = `AI guardrail test failed (${response.status})`;
    try {
      const body: unknown = await response.json();
      if (typeof body === "object" && body !== null && "detail" in body && typeof body.detail === "string") {
        message = body.detail;
      }
    } catch {
      // Keep the status based message when an intermediary returns non-JSON.
    }
    throw new Error(message);
  }
  return (await response.json()) as AiGuardrailReport;
}

/** The part of `GET /ai/settings` the review screens need (banners only). */
export interface AiSharingSummary {
  mode: string;
  mode_label: string;
  provider: string | null;
  model: string | null;
  /** Provider that reads page images now (null when page reading is off). */
  page_reader: "gemini" | "vllm" | null;
  page_reader_model: string | null;
  vllm_calls: string;
  external_calls: string;
  status: { code: string; label: string };
}

export type AiSuggestionState = "not_applicable" | "pending" | "available" | "expired" | "unavailable";

/**
 * One checked suggestion; `value` is already a plain decimal, ISO date or text. Text (H1)
 * suggestions quote the page text; vision suggestions give the value as printed (`quote`)
 * and its `box` on the page image (`[ymin, xmin, ymax, xmax]`, 0–1000).
 */
export interface AiFieldSuggestion {
  scope: "header" | "line";
  line_no: number | null;
  field: string;
  value: string;
  current_value: unknown;
  matches_current: boolean;
  page: number;
  quote: string;
  box: number[] | null;
}

/** A handwritten or stamped mark the vision reader saw; information only. */
export interface AiAnnotation {
  kind: string;
  text: string;
  looks_like: string;
  page: number;
}

export interface AiSuggestionsView {
  document_id: string;
  document_version: number;
  state: AiSuggestionState;
  code: string | null;
  ai_call_id: string | null;
  mode: string | null;
  label: string | null;
  model: string | null;
  suggestions: AiFieldSuggestion[];
  /** "vision" (page images) or "text" (PDF text); null before any request. */
  source: "vision" | "text" | null;
  /** False for PO/GRN: their corrections arrive with the verify screen (AC-7). */
  correctable: boolean;
  annotations: AiAnnotation[];
}

/** What a correction save sends for a value that was taken from a suggestion. */
export interface AiSuggestionRef {
  ai_call_id: string;
  scope: "header" | "line";
  line_no: number | null;
  field: string;
}

export function suggestionKey(suggestion: Pick<AiFieldSuggestion, "scope" | "line_no" | "field">): string {
  return `${suggestion.scope}:${suggestion.line_no ?? ""}:${suggestion.field}`;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

async function getJson(path: string): Promise<unknown> {
  const response = await fetch(`${apiBase}/api/v1${path}`, { credentials: "include" });
  if (response.status === 401) notifySessionExpired();
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  return response.json() as Promise<unknown>;
}

export async function getAiSharingSummary(): Promise<AiSharingSummary> {
  const value = await getJson("/ai/settings");
  if (!isObject(value) || typeof value["mode"] !== "string" || !isObject(value["status"])) {
    throw new Error("The server returned invalid AI settings.");
  }
  const strategy = value["reader_strategy"];
  const primary = value["primary_reader"];
  const reader = strategy === "single" || strategy === "shadow" ? (primary === "vllm" || primary === "gemini" ? primary : null) : null;
  const readerCard = Array.isArray(value["providers"])
    ? value["providers"].find((card: unknown) => isObject(card) && card["provider"] === reader)
    : undefined;
  return {
    mode: value["mode"],
    mode_label: text(value["mode_label"]) ?? value["mode"],
    provider: text(value["provider"]),
    model: text(value["model"]),
    page_reader: reader,
    page_reader_model: isObject(readerCard) ? text(readerCard["model"]) : null,
    vllm_calls: text(value["vllm_calls"]) ?? "disabled",
    external_calls: text(value["external_calls"]) ?? "disabled",
    status: { code: text(value["status"]["code"]) ?? "UNKNOWN", label: text(value["status"]["label"]) ?? "" },
  };
}

const suggestionStates: readonly AiSuggestionState[] = ["not_applicable", "pending", "available", "expired", "unavailable"];

function parseSuggestion(value: unknown): AiFieldSuggestion[] {
  if (!isObject(value)) return [];
  const scope = value["scope"];
  if ((scope !== "header" && scope !== "line") || typeof value["field"] !== "string" || typeof value["value"] !== "string") return [];
  return [{
    scope,
    line_no: typeof value["line_no"] === "number" ? value["line_no"] : null,
    field: value["field"],
    value: value["value"],
    current_value: value["current_value"],
    matches_current: value["matches_current"] === true,
    page: typeof value["page"] === "number" ? value["page"] : 0,
    quote: text(value["quote"]) ?? "",
    box: Array.isArray(value["box"]) && value["box"].every((item) => typeof item === "number") ? value["box"] as number[] : null,
  }];
}

function parseAnnotation(value: unknown): AiAnnotation[] {
  if (!isObject(value) || typeof value["text"] !== "string") return [];
  return [{
    kind: text(value["kind"]) ?? "other",
    text: value["text"],
    looks_like: text(value["looks_like"]) ?? "unknown",
    page: typeof value["page"] === "number" ? value["page"] : 0,
  }];
}

export async function getDocumentAiSuggestions(documentId: string): Promise<AiSuggestionsView> {
  const value = await getJson(`/documents/${encodeURIComponent(documentId)}/ai-suggestions`);
  const state = isObject(value) ? value["state"] : null;
  if (!isObject(value) || typeof value["document_version"] !== "number" || !suggestionStates.includes(state as AiSuggestionState)) {
    throw new Error("The server returned invalid AI suggestions.");
  }
  return {
    document_id: text(value["document_id"]) ?? documentId,
    document_version: value["document_version"],
    state: state as AiSuggestionState,
    code: text(value["code"]),
    ai_call_id: text(value["ai_call_id"]),
    mode: text(value["mode"]),
    label: text(value["label"]),
    model: text(value["model"]),
    suggestions: Array.isArray(value["suggestions"]) ? value["suggestions"].flatMap(parseSuggestion) : [],
    source: value["source"] === "vision" || value["source"] === "text" ? value["source"] : null,
    correctable: value["correctable"] !== false,
    annotations: Array.isArray(value["annotations"]) ? value["annotations"].flatMap(parseAnnotation) : [],
  };
}

// -- AI Assist card (Home): settings, consent preview, key, provider test, usage -------------

export type AiMode = "rules_only" | "ai_assisted" | "ai_first";
export type AiKeyStorage = "session" | "saved";

export interface AiHookDisclosure {
  hook: string;
  label: string;
  data_sent: string;
}

export interface AiStatus {
  code: string;
  label: string;
  at: string | null;
}

export type AiAutoVerifyFieldType = "doc_date" | "doc_no" | "gstin" | "line_amount" | "reference";
export interface AiAutoVerifyFieldOption {
  type: AiAutoVerifyFieldType;
  label: string;
}

export type AiProviderName = "gemini" | "vllm";
export type AiReaderStrategy = "off" | "shadow" | "single";

/** One provider card from `GET /ai/settings` (Gemini or the company vLLM server). */
export interface AiProviderCard {
  provider: AiProviderName;
  enabled: boolean;
  model: string | null;
  base_url: string | null;
  external: boolean;
  data_destination: string;
  connection: { state: string; reason_code: string | null; checked_at: string | null; connected_at: string | null };
}

/** `GET /ai/settings`; the key is never part of it, only a four-character hint. */
export interface AiSettingsView {
  /** Which provider reads page images (`single`), or none (`off`). */
  reader_strategy: AiReaderStrategy;
  primary_reader: AiProviderName | null;
  vllm_calls: string;
  providers: AiProviderCard[];
  version: number;
  mode: AiMode;
  mode_label: string;
  provider: string | null;
  model: string | null;
  key: { set: boolean; hint: string | null; storage: AiKeyStorage | null; expires_at: string | null };
  consent: { given: boolean; current: boolean; at: string | null; required_fingerprint: string | null; hooks: AiHookDisclosure[] };
  external_calls: string;
  limits: Record<string, unknown> | null;
  budget: { usd_cap_offered: boolean; token_cap_guaranteed: boolean; priced: boolean };
  key_storage_available: boolean;
  /** Page images for the vision reader: chosen per workspace, available per server. */
  vision: { enabled: boolean; available: boolean };
  auto_verify: { field_types: AiAutoVerifyFieldType[]; available_field_types: AiAutoVerifyFieldOption[] };
  status: AiStatus;
}

export interface AiConsentPreview {
  mode: AiMode;
  mode_label: string;
  model: string;
  vision: boolean;
  auto_verify_field_types: AiAutoVerifyFieldType[];
  text: string;
  fingerprint: string;
  hooks: AiHookDisclosure[];
}

export interface AiUncertainRequest {
  id: string;
  call_kind: string;
  hook: string | null;
  finished_at: string | null;
}

export interface AiUsage {
  calls_today: number;
  tokens_today: number;
  calls_this_month: number;
  tokens_this_month: number;
  calls_with_estimated_usage: number;
  usd_today: string | null;
  usd_available: boolean;
  usd_notice: string;
  token_notice: string;
  uncertain_requests: AiUncertainRequest[];
}

export interface AiRequestState {
  id: string;
  request_state: string;
  skip_code: string | null;
  error_code: string | null;
}

/** Fields the card may send; `api_key` is included only when the person typed one. */
export interface AiSettingsUpdate {
  mode?: AiMode;
  provider?: "gemini";
  model?: string;
  api_key?: string;
  key_storage?: AiKeyStorage;
  consent?: { accept: true; fingerprint: string };
  withdraw_consent?: boolean;
  vision?: boolean;
  auto_verify_field_types?: AiAutoVerifyFieldType[];
  reader_strategy?: AiReaderStrategy;
  primary_reader?: AiProviderName;
}

export class AiApiError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(message: string, status: number, code: string | null) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

/** Mirrors the server's model check so a key pasted into the model field is caught early. */
export const AI_MODEL_PATTERN = /^gemini-[a-z0-9][a-z0-9.-]{0,55}$/;

async function aiSend(path: string, init: RequestInit = {}, csrf?: string): Promise<Response> {
  const headers = new Headers(init.headers);
  if (csrf) headers.set("X-CSRF-Token", csrf);
  const response = await fetch(`${apiBase}/api/v1${path}`, { ...init, headers, credentials: "include", cache: "no-store" });
  if (response.status === 401) notifySessionExpired();
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const detail = isObject(body) && typeof body["detail"] === "string" ? body["detail"] : `Request failed (${response.status})`;
    throw new AiApiError(detail, response.status, isObject(body) ? text(body["code"]) : null);
  }
  return response;
}

function parseHooks(value: unknown): AiHookDisclosure[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => isObject(item) && typeof item["hook"] === "string"
    ? [{ hook: item["hook"], label: text(item["label"]) ?? item["hook"], data_sent: text(item["data_sent"]) ?? "" }]
    : []);
}

const aiModes: readonly AiMode[] = ["rules_only", "ai_assisted", "ai_first"];
const autoVerifyTypes: readonly AiAutoVerifyFieldType[] = ["doc_date", "doc_no", "gstin", "line_amount", "reference"];

function parseProviderCard(value: unknown): AiProviderCard[] {
  if (!isObject(value) || (value["provider"] !== "gemini" && value["provider"] !== "vllm")) return [];
  const connection = isObject(value["connection"]) ? value["connection"] : {};
  return [{
    provider: value["provider"],
    enabled: value["enabled"] === true,
    model: text(value["model"]),
    base_url: text(value["base_url"]),
    external: value["external"] !== false,
    data_destination: text(value["data_destination"]) ?? "",
    connection: {
      state: text(connection["state"]) ?? "not_connected",
      reason_code: text(connection["reason_code"]),
      checked_at: text(connection["checked_at"]),
      connected_at: text(connection["connected_at"]),
    },
  }];
}

/** Reader part of a consent preview, so a saved reader choice keeps its own fingerprint. */
export interface AiReaderPreview {
  reader_strategy: AiReaderStrategy;
  primary_reader: AiProviderName | null;
  vllm_model?: string | null;
  vllm_base_url?: string | null;
}

/** The reader settings a preview must use to describe what is saved now. */
export function currentReaderPreview(view: Pick<AiSettingsView, "reader_strategy" | "primary_reader" | "providers">): AiReaderPreview {
  const vllm = view.providers.find((card) => card.provider === "vllm");
  return {
    reader_strategy: view.reader_strategy,
    primary_reader: view.primary_reader,
    ...(view.primary_reader === "vllm" ? { vllm_model: vllm?.model ?? null, vllm_base_url: vllm?.base_url ?? null } : {}),
  };
}

function parseSettings(value: unknown): AiSettingsView {
  if (!isObject(value) || typeof value["version"] !== "number" || !aiModes.includes(value["mode"] as AiMode)
    || !isObject(value["key"]) || !isObject(value["consent"]) || !isObject(value["status"])) {
    throw new Error("The server returned invalid AI settings.");
  }
  const key = value["key"];
  const consent = value["consent"];
  const status = value["status"];
  const budget = isObject(value["budget"]) ? value["budget"] : {};
  const vision = isObject(value["vision"]) ? value["vision"] : {};
  const autoVerify = isObject(value["auto_verify"]) ? value["auto_verify"] : {};
  const storage = key["storage"];
  const autoVerifyFieldTypes = Array.isArray(autoVerify["field_types"])
    ? autoVerify["field_types"].filter((item): item is AiAutoVerifyFieldType => typeof item === "string" && autoVerifyTypes.includes(item as AiAutoVerifyFieldType))
    : [];
  const availableFieldTypes = Array.isArray(autoVerify["available_field_types"])
    ? autoVerify["available_field_types"].flatMap((item) => isObject(item) && typeof item["type"] === "string" && autoVerifyTypes.includes(item["type"] as AiAutoVerifyFieldType)
      ? [{ type: item["type"] as AiAutoVerifyFieldType, label: text(item["label"]) ?? item["type"] as AiAutoVerifyFieldType }]
      : [])
    : [];
  const readerStrategy = value["reader_strategy"];
  const primaryReader = value["primary_reader"];
  const providers = Array.isArray(value["providers"]) ? value["providers"].flatMap(parseProviderCard) : [];
  return {
    reader_strategy: readerStrategy === "shadow" || readerStrategy === "single" ? readerStrategy : "off",
    primary_reader: primaryReader === "gemini" || primaryReader === "vllm" ? primaryReader : null,
    vllm_calls: text(value["vllm_calls"]) ?? "disabled",
    providers,
    version: value["version"],
    mode: value["mode"] as AiMode,
    mode_label: text(value["mode_label"]) ?? String(value["mode"]),
    provider: text(value["provider"]),
    model: text(value["model"]),
    key: {
      set: key["set"] === true,
      hint: text(key["hint"]),
      storage: storage === "session" || storage === "saved" ? storage : null,
      expires_at: text(key["expires_at"]),
    },
    consent: {
      given: consent["given"] === true,
      current: consent["current"] === true,
      at: text(consent["at"]),
      required_fingerprint: text(consent["required_fingerprint"]),
      hooks: parseHooks(consent["hooks"]),
    },
    external_calls: text(value["external_calls"]) ?? "disabled",
    limits: isObject(value["limits"]) ? value["limits"] : null,
    budget: {
      usd_cap_offered: budget["usd_cap_offered"] === true,
      token_cap_guaranteed: budget["token_cap_guaranteed"] === true,
      priced: budget["priced"] === true,
    },
    key_storage_available: value["key_storage_available"] === true,
    vision: { enabled: vision["enabled"] === true, available: vision["available"] === true },
    auto_verify: { field_types: autoVerifyFieldTypes, available_field_types: availableFieldTypes },
    status: { code: text(status["code"]) ?? "UNKNOWN", label: text(status["label"]) ?? "", at: text(status["at"]) },
  };
}

async function settingsResult(response: Response): Promise<{ view: AiSettingsView; etag: string }> {
  const view = parseSettings(await response.json());
  return { view, etag: response.headers.get("ETag") ?? `"${view.version}"` };
}

export async function getAiSettings(): Promise<{ view: AiSettingsView; etag: string }> {
  return settingsResult(await aiSend("/ai/settings"));
}

export async function saveAiSettings(body: AiSettingsUpdate, etag: string, csrf: string): Promise<{ view: AiSettingsView; etag: string }> {
  return settingsResult(await aiSend("/ai/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json", "If-Match": etag },
    body: JSON.stringify(body),
  }, csrf));
}

export async function removeAiKey(etag: string, csrf: string): Promise<{ view: AiSettingsView; etag: string }> {
  return settingsResult(await aiSend("/ai/key", { method: "DELETE", headers: { "If-Match": etag } }, csrf));
}

export async function getAiConsentPreview(
  mode: Exclude<AiMode, "rules_only">,
  model: string,
  vision = false,
  autoVerifyFieldTypes: readonly AiAutoVerifyFieldType[] = [],
  reader?: AiReaderPreview,
): Promise<AiConsentPreview> {
  const query = new URLSearchParams({ mode, model });
  if (vision) query.set("vision", "true");
  if (reader) {
    query.set("reader_strategy", reader.reader_strategy);
    if (reader.primary_reader) query.set("primary_reader", reader.primary_reader);
    if (reader.vllm_model) query.set("vllm_model", reader.vllm_model);
    if (reader.vllm_base_url) query.set("vllm_base_url", reader.vllm_base_url);
  }
  for (const fieldType of autoVerifyFieldTypes) query.append("auto_verify_field_types", fieldType);
  const value: unknown = await (await aiSend(`/ai/consent-preview?${query.toString()}`)).json();
  if (!isObject(value) || typeof value["fingerprint"] !== "string" || typeof value["text"] !== "string") {
    throw new Error("The server returned an invalid consent preview.");
  }
  return {
    mode,
    mode_label: text(value["mode_label"]) ?? mode,
    model,
    vision: value["vision"] === true,
    auto_verify_field_types: autoVerifyFieldTypes.filter((item) => autoVerifyTypes.includes(item)),
    text: value["text"],
    fingerprint: value["fingerprint"],
    hooks: parseHooks(value["hooks"]),
  };
}

export async function startAiProviderTest(csrf: string, provider?: AiProviderName): Promise<string> {
  const init: RequestInit = provider
    ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ provider }) }
    : { method: "POST" };
  const value: unknown = await (await aiSend("/ai/test/provider", init, csrf)).json();
  if (!isObject(value) || typeof value["request_id"] !== "string") throw new Error("The server did not queue the provider test.");
  return value["request_id"];
}

export async function getAiRequestState(requestId: string): Promise<AiRequestState> {
  const value: unknown = await (await aiSend(`/ai/requests/${encodeURIComponent(requestId)}`)).json();
  if (!isObject(value) || typeof value["request_state"] !== "string") throw new Error("The server returned an invalid AI request.");
  return {
    id: text(value["id"]) ?? requestId,
    request_state: value["request_state"],
    skip_code: text(value["skip_code"]),
    error_code: text(value["error_code"]),
  };
}

export async function getAiUsage(): Promise<AiUsage> {
  const value: unknown = await (await aiSend("/ai/usage")).json();
  if (!isObject(value)) throw new Error("The server returned invalid AI usage.");
  const count = (item: unknown) => (typeof item === "number" ? item : 0);
  const uncertain = Array.isArray(value["uncertain_requests"]) ? value["uncertain_requests"] : [];
  return {
    calls_today: count(value["calls_today"]),
    tokens_today: count(value["tokens_today"]),
    calls_this_month: count(value["calls_this_month"]),
    tokens_this_month: count(value["tokens_this_month"]),
    calls_with_estimated_usage: count(value["calls_with_estimated_usage"]),
    usd_today: value["usd_today"] === null || value["usd_today"] === undefined ? null : String(value["usd_today"]),
    usd_available: value["usd_available"] === true,
    usd_notice: text(value["usd_notice"]) ?? "",
    token_notice: text(value["token_notice"]) ?? "",
    uncertain_requests: uncertain.flatMap((item) => isObject(item) && typeof item["id"] === "string"
      ? [{ id: item["id"], call_kind: text(item["call_kind"]) ?? "", hook: text(item["hook"]), finished_at: text(item["finished_at"]) }]
      : []),
  };
}

export async function acknowledgeAiRequest(requestId: string, csrf: string): Promise<void> {
  await aiSend(`/ai/requests/${encodeURIComponent(requestId)}/acknowledge`, { method: "POST" }, csrf);
}
