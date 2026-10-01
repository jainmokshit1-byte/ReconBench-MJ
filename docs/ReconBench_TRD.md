# ReconBench — Technical Requirements Document (TRD)

> Converted from `ReconBench_TRD.pdf` (29 September 2026).

<!-- Page 1 -->
ReconBench — Technical Requirements
Document (TRD)
29 September 2026 · Derived from ReconBench PRD v0.1 (draft)
Contents: 1 Overview and scope · 2 PRD traceability · 3 Architecture · 4 Technology stack · 5 Functional technical
requirements · 6 Data design · 7 APIs and interfaces · 8 Non-functional requirements · 9 Security and privacy · 10
Infrastructure and deployment · 11 Observability and operations · 12 Testing and quality · 13 Delivery plan · 14
Risks, assumptions and open questions · 15 Appendix
## 1. Overview and scope
ReconBench v1 is a modular Python backend, a React web app and a headless matching engine that turn a
month of PO, GRN and invoice files into an auditable 3-way match exported to Excel. This TRD converts PRD v0.1
into buildable requirements for P0, with schema and architecture room for P1 and P2.
It is written for engineers, QA and the Finance/AP owners who sign off tolerances. Items marked proposed need
a decision; §14 lists what we still need from you.
Technical goals
- Correct by construction: decimal arithmetic, deterministic ordering, results independent of ingestion
order.
- Explainable: any verdict can be reproduced from stored inputs, config snapshot and engine version.
- Precision first: shaky data ends in Needs Review, never in a silent pass.
- Incremental: a change recomputes only the affected vendor, PO ledger or invoice.
- Portable: runs in cloud, VPC or on-prem with few moving parts.
- Engine first: matching logic lives in a UI-independent library tested against a golden set.
Out of scope for this TRD
- Microservices, multi-tenant SaaS and real-time co-editing. v1 is one workspace on one deployable.
- ERP push-back, email ingestion, multi-currency, credit notes and GSTR-2B (PRD P2). We reserve
extension points only.
- OCR is P1. The pipeline reserves its slot.
Key decisions at a glance
# Decision Why
- **D1 Modular monolith: one codebase run as API and worker**
processes
One deploy; volumes are far below what would justify services
- **D2 Matching engine is a pure library (reconbench_core) with**
no I/O, database, network or clock
Determinism and headless testing
- **D3 PostgreSQL only, including local development NUMERIC, JSONB, advisory locks and SKIP LOCKED are needed;**
a second database means second behaviour
- **D4 Postgres-backed job queue behind a port Jobs commit with the data they belong to; no Redis to run onprem**
- **D5 Raw files in S3-compatible storage, keyed by SHA-256 Free dedupe; MinIO works on-prem**
- **D6 Money and quantities use Decimal / NUMERIC; scores are**
quantized to 4 decimals
Same result on every machine and re-run
- **D7 Documents, links, config and runs are versioned and**
append-only
"Reproducible on demand" cannot be added later; the audit UI
can
- **D8 Dirty-scope recompute: vendor, then PO, then invoice Meets "only affected ledgers" without full reruns**

---

<!-- Page 2 -->
# Decision Why
- **D9 pdfplumber and pypdfium2 for PDFs; PyMuPDF only after a**
licence review
PyMuPDF is AGPL or commercially licensed
- **D10 AI sits behind a provider port, server-side only, off by**
default
Guardrails and key handling live in one place
Assumptions carried from the PRD: buyer is Benchmark (India, GST, INR); PO and GRN XML is Tally-style; most
invoice PDFs have a text layer; one invoice per PDF; one workspace; about 10,000 documents a month.
## 2. PRD traceability
Every P0 requirement in the PRD has a home module in this TRD, and §5 lists each technical requirement beside
the PRD item it serves.
PRD section Pri TRD section ID prefix
§7.1 Ingestion, ING-1 to ING-8 P0 §5.1 TR-ING
§7.1 ING-9 to ING-11 P1 §5.1 TR-ING
§7.2 Parsing and extraction (scans are P1) P0 §5.2 TR-PAR
§7.2 Normalization P0 §5.3 TR-NRM
§7.3 Vendor grouping P0 §5.4 TR-VEN
§7.4 Anchor Resolution Ladder P0 §5.5 TR-LNK
§7.5 Reconciliation S1 to S7, tolerances, reason codes P0 §5.6 TR-REC
§7.6 Review: REV-1 to REV-3 (REV-4 to REV-7 are P1) P0 / P1 §5.7 TR-REV
§7.7 Preview (click-to-highlight is P2) P0 §5.8 TR-PRV
§7.8 Excel export (PO Status, GRNI, Orphans, Audit sheets are P1) P0 §5.9 TR-EXP
§7.9 AI assist: Home card and hooks H1 to H4 P0 (M3) §5.10 TR-AI
§8 UX P0 §7 TR-API
§9 Data model P0 §6 TR-DAT
§10 Non-functional P0 §8, §9, §11 TR-NFR, TR-SEC,
TR-OPS
§11 Testing and acceptance P0 §12 TR-QA
§12 Roadmap n/a §13 n/a
§13 Risks and §14 Open questions n/a §14 n/a
Where this TRD tightens or departs from the PRD
Thirteen places where the PRD is silent, ambiguous or non-binding get a technical position here. Each is open to
challenge.
# PRD says This TRD does Why
1 Local MVP may use SQLite (Appendix
D)
PostgreSQL everywhere Ledger recompute needs row locks and
NUMERIC; a second database means
second behaviour
2 Upsert key is type + vendor + doc no.
+ date (ING-6)
The vendor in the key is the identity read
from the document (GSTIN, else
normalized name), not the resolved vendor
Vendors are resolved after parsing and can
be merged later; a merge must not create
duplicates
3 Same results regardless of ingestion
order (§7.5)
Explicit sort keys, run date passed in as an
input, scores quantized to 4 decimals,
defined tie-breaks
Ties and clocks quietly break determinism
otherwise
4 Provisional invoices consume PO
balance; silent on failed ones (S4)
The ledger consumes the billed quantity of
every invoice except Duplicate and
Rejected (proposed)
Fits "precision over recall"; Finance to
confirm
5 Verdicts reproducible on demand,
but the audit log is P1 (§10, REV-4)
Versioned documents, links and config plus
a run manifest are P0 schema
History cannot be backfilled; only the audit
screen can wait
6 Link confidences 0.99 / 0.97 / 0.95
(§7.4)
Policy constants in config, calibrated on the
golden set during the pilot
They are not measured probabilities

---

<!-- Page 3 -->
# PRD says This TRD does Why
7 Live Excel formulas beside vendor
text (§7.8)
All document text is written as text;
formulas exist only in columns we generate
Blocks formula injection and lost leading
zeros
8 CSV read with pandas (Appendix D) Every column is read as text Type inference turns PO number 0142 into
142
9 GST slab list seeded 5 / 12 / 18 / 28
(S6)
Keep the configurable/effective-dated structure.
P0 seed is 5 / 12 / 18 / 28 with null date
boundaries, per user-confirmed PRD decision
(1 Oct 2026). Do not add the extra 40% row
from this TRD departure note to the P0 seed.
10 PyMuPDF suggested for PDFs
(Appendix D)
pdfplumber and pypdfium2 first PyMuPDF is AGPL or commercially licensed
11 Security lists TLS, encryption, roles
(§10)
Adds hardening for untrusted files: XML
entity limits, ZIP limits, isolated PDF parsing
Uploaded files are the largest attack
surface
12 Duplicate detection includes fuzzy
matches (S1)
Exact duplicates stay Duplicate. Suspected
ones become Needs Review with
DUP_SUSPECTED and keep consuming
balance until a person decides
A false Duplicate blocks a real invoice, and
precision first prefers that a person decides
(OQ-5)
13 No reason code for a 0.60 to 0.80
item match, and nothing on scanned
PDFs before OCR (P1)
Adds ITEM_UNSURE and NO_TEXT_LAYER
at level Review. A scan becomes a shell
document keyed by its file hash and lands
in Needs Review (TR-PAR-9)
Every verdict needs a code, and no invoice
may vanish silently (scenario 19)
## 3. Architecture
The system is one Python codebase run as an API process and worker processes, backed by PostgreSQL and S3compatible storage, with all matching logic in a pure library that never touches I/O.
Figure 1. System architecture: matching logic sits in a library with no I/O

---

<!-- Page 4 -->
The highlighted box is the pure matching library and the dashed box is optional and external. The API only writes
rows and jobs to PostgreSQL; workers pick the jobs up and are the only callers of the AI provider.
### 3.1 Components and boundaries
Component Responsibility Talks to State
Web app (React SPA) Home, Ingestion, Mapping, Reconciliation,
Settings; virtualised tables; PDF viewer
API only None; server data cached in
the browser
API (FastAPI) Auth, uploads, queries, edits, enqueueing jobs,
signed file access
PostgreSQL, object store Stateless
Workers Parse, resolve vendors, link, rebuild ledgers,
evaluate cases, build exports, call AI
PostgreSQL, object store, AI
provider (optional)
Stateless; jobs live in
PostgreSQL
reconbench_core
library
Canonical schema, normalization, parsers,
vendor and link resolution, reconciliation,
workbook builder
Nothing outside memory None
PostgreSQL System of record, job queue, sessions,
encrypted secrets
n/a All structured data
Object store Raw uploads and export files n/a Files keyed by SHA-256
### 3.2 Library packages and the dependency rule
Package Purpose May import
core.schema Canonical models (frozen Pydantic), enums, reason codes stdlib, pydantic
core.normalize Numbers, dates, UOM, GSTIN, vendor names, PO
canonicalisation
schema
core.align Item similarity, spec-token guard, line assignment schema, normalize
core.parsers Bytes to canonical documents for XML, CSV and PDF schema, normalize
core.mapping Vendor resolution and the link ladder schema, normalize, align
core.recon Ledger, checks, statuses, payable and at-risk schema, normalize, align
core.export Result set to workbook bytes schema, recon
app HTTP, database, queue, storage, auth, AI adapters everything above
core never imports app, the database, an HTTP client, os.environ or the system clock. An import-linter contract
enforces this in CI, and the run date enters as data.
### 3.3 Processing pipeline
Job Trigger Does Idempotency key Queue
ingest_file Upload Store bytes, sniff type, expand ZIPs, create file rows SHA-256 ingest
parse_file Each source file Parse to documents and evidence, apply the criticalfield gate, upsert by natural key
File id + parser version parse
resolve_vend
ors
Files parsed Assign vendors, create merge suggestions Vendor scope + input hash mapping
link_vendor Document or
vendor change
Run the ladder for one vendor's documents Vendor id + input hash mapping
recompute_po Link or
document
change
Rebuild the ledger for one PO PO id + input hash recon
evaluate_cas
es
User run, or
Pending cases
after a new GRN
Run S1 to S7 for a scope and store results Run id + scope recon
build_export Export request Build the workbook Run id + filter hash export
ai_hook Rules unsure Call provider, validate, cache Document hash + hook +
prompt version
ai
Jobs are retried with backoff for infrastructure faults only. Parse failures are deterministic, so they fail once with
a reason and wait for a manual retry. One bad file never blocks the rest (ING-5).

---

<!-- Page 5 -->
### 3.4 Recompute model
A write marks a scope dirty in the same transaction, and workers coalesce dirty scopes into jobs. The PO is the
smallest safe ledger unit, because an earlier-dated invoice changes "billed before" for every later one.
Scope Marked dirty by Recomputes Serialised by
Vendor New or changed document, vendor merge,
alias change, manual link change
Step 3 content inference for that vendor's
unresolved invoices, in invoice date and
number order
Advisory lock on vendor
id
PO Linked GRN, invoice or PO revision;
opening balance
Ledger for every line of the PO Advisory lock on PO id
Invoice case Ledger change on a linked PO line, config
change, override
S1 to S7 for that invoice Run scope
Ledgers refresh automatically. Case verdicts belong to a run: mapping edits mark existing cases stale until Rerun, while a newly ingested GRN re-evaluates Pending cases by itself, as the PRD requires (proposed; see §14).
### 3.5 Consistency and failure handling
- Each stage commits atomically, and jobs are enqueued in the same transaction as the rows that need
them, so nothing is lost between a write and its follow-up.
- Two workers never touch the same PO or vendor at once; a crash releases the lock.
- Every job is safe to run twice, so an interrupted run can simply be repeated.
- API writes use optimistic concurrency (a version column and If-Match) so two reviewers cannot
silently overwrite each other.
- Long-running work never runs inside the API process.
### 3.6 Deployment shapes
Two shapes come from the same images: a single node under Docker Compose (web, API, worker, PostgreSQL,
MinIO) for the pilot and on-prem, and managed cloud or Kubernetes (managed PostgreSQL and S3) for scale.
§10 details both.
## 4. Technology stack
Python carries everything that touches documents or money, TypeScript carries the browser, and each library is
chosen for a permissive licence and as few moving parts as on-prem allows.
Layer Choice Why Notes
Language Python 3.12 or later, one pinned
minor version; TypeScript in strict
mode
Best fit for PDF work, fuzzy matching
and Decimal maths
core is type-checked with mypy
strict
API FastAPI, Pydantic v2, Uvicorn Typed contracts; OpenAPI generated for
the web client
Heavy work runs in jobs, never in a
request
Data access SQLAlchemy 2.0, Alembic Explicit transactions; reviewed
migrations
Raw SQL where clearer, such as the
ledger and bulk upserts
Database PostgreSQL 16 or later with
pg_trgm
NUMERIC, JSONB, advisory locks, SKIP
LOCKED, trigram search for pickers
No SQLite (D3)
Job queue Procrastinate on PostgreSQL, behind
a JobQueue port
Transactional enqueue, locks, retries,
periodic tasks; nothing extra to run
Swap to a Redis queue such as Arq if
the M2 load test demands it
Object store S3 API via boto3; MinIO on-prem;
local-disk adapter for tests
Same code everywhere Short-lived signed URLs
XML lxml with DTDs, entity resolution and
network access disabled
Speed and XPath; safe by configuration iterparse for large files;
defusedxml is the fallback
CSV Python csv plus charsetnormalizer; every column read as
text
Predictable typing (TR-NRM) pandas only in profiling scripts,
never in the engine
PDF text pdfplumber (pdfminer.six) for
words, lines and tables; pypdfium2
for page images
Permissive licences; coordinates for
evidence
PyMuPDF only after a licence review
(D9)

---

<!-- Page 6 -->
Layer Choice Why Notes
OCR (P1) OCRmyPDF with Tesseract first;
evaluate PaddleOCR on golden scans
OCRmyPDF adds a text layer, so the
same extractor runs
Lower base confidence for OCR text
Fuzzy matching RapidFuzz: token_set_ratio,
token_sort_ratio, cdist
Fast and deterministic Scores quantized to 4 decimals
Assignment SciPy linear_sum_assignment
(Hungarian)
Correct and fast for tens of lines Inputs pre-sorted; ties handled by
cost design (TR-REC)
Excel XlsxWriter Write-only and fast; formulas with
cached values, conditional formats,
frozen panes
Tests read back with openpyxl
Web React (current stable), Vite, Tailwind,
TanStack Table, Virtual and Query,
React Router, react-hook-form with
zod
Virtualised tables for thousands of
rows; cached server state
Chrome and Edge latest, 1366 px
minimum width
PDF viewer pdf.js (pdfjs-dist) with scripting
disabled
Runs in the browser; text selection Apache-licensed
Auth Server-side sessions in PostgreSQL,
Argon2id, HttpOnly cookies
Simple; OIDC can be added later See §9
AI Provider port with adapters for
Claude, OpenAI and Gemini via
official SDKs
Guardrails stay provider-independent Model names are settings, not code
Observability structlog JSON logs, OpenTelemetry,
Prometheus metrics
Works with any backend Sentry optional
Packaging and
CI
uv, multi-stage Docker builds,
Docker Compose, GitHub Actions or
GitLab CI
Reproducible builds Helm chart in P1
Quality tools ruff, mypy, pytest, Hypothesis,
import-linter; eslint, vitest,
Playwright
See §12 n/a
Versions, licences and library behaviour above come from the author's knowledge. Confirm each in an M0
dependency review covering licence, maintenance status and security advisories.
Provisional choices
- Queue: Procrastinate is provisional until the M2 load test; the JobQueue port keeps the swap small.
- PDF extraction: if pdfplumber misses the accuracy target on golden PDFs, add a second extractor behind
the same interface instead of rewriting the pipeline.
## 5. Functional technical requirements
Requirements are numbered TR--n and point to the PRD item they serve. P1 rows ship in v1.1 but shape the P0
schema and interfaces.
### 5.1 Ingestion service
ID Requirement PRD Pri
- **TR-ING-1 Uploads stream to object storage without buffering whole files in memory. Limits of 25 MB and**
100 pages per file apply from P0 as protection, both settings; only the password prompt is P1. The
source_file row and its ingest_file job commit in one transaction.
ING-1, ING2, ING-5
P0
- **TR-ING-2 ZIP and folder uploads stream entries and never write to disk under archive paths. Reject absolute**
paths and .., skip __MACOSX and dotfiles, and cap entry count (5,000), total uncompressed size (2
GB) and compression ratio (100:1); nested archives are rejected. Each entry becomes its own
source_file with a parent link.
ING-1 P0
- **TR-ING-3 Type and format come from content sniffing (PDF header, XML root element, CSV header**
synonyms), not the extension. A document that looks like another type raises TYPE_MISMATCH
with the suggested type; the zone the user chose stands until they accept it.
ING-2, ING3
P0
- **TR-ING-4 Multi-document XML and CSV files split into one document per voucher, PO number or GRN**
number, recording a locator (XPath or row range). One PDF is one document in P0; splitting multiinvoice PDFs is P1.
ING-4, ING10
P0

---

<!-- Page 7 -->
ID Requirement PRD Pri
- **TR-ING-5 File status machine: Staged (uploaded, waiting for Process), Queued, Parsing, then Parsed (n**
docs), Partial or Failed with reason code and message. Infrastructure faults retry three times with
backoff; parse failures do not retry and wait for a manual retry. A per-file error log downloads as
JSON lines.
ING-5 P0
- **TR-ING-6 Two levels of idempotency. Files are unique by SHA-256 (same bytes are skipped). Documents**
upsert on natural_key, a hash of type, vendor identity as read from the document, canonical
document number and date. The outcome is new, updated (a new document version) or
skipped, never a duplicate row.
ING-6 P0
- **TR-ING-7 The pool is persistent. batch is a label and filter only: a document keeps the batch of first**
ingestion, and invoices link to POs and GRNs from any batch. The suggested label is the month of
the most common invoice date, editable.
ING-7 P0
- **TR-ING-8 Warnings are document_warning rows (NO_PO_REF, NO_GSTIN, LOW_CONFIDENCE,**
UNPARSEABLE, TYPE_MISMATCH). The post-ingestion summary is a query over them plus
document counts, with a jump link per row.
ING-8 P0
- **TR-ING-9 Encrypted PDFs get status NeedsPassword. The password lives in memory for one job and is**
never stored or logged.
ING-9 P1
- **TR-ING-10 The opening-balance CSV (PO, line, received to date, billed to date) is stored as**
opening_balance rows that enter the ledger as synthetic events, so the ledger supports them
from P0.
ING-11 P1
### 5.2 Parsers
All parsers share one pure, deterministic contract: parse(bytes, hints) returns documents, warnings and a
layout signature. Every field is a FieldValue with value, confidence, source (rule, ai or manual) and evidence
(page, bounding box, snippet).
ID Requirement PRD Pri
- **TR-PAR-1 Unknown XML tags and CSV columns are kept in extras, never dropped. PDF evidence stores the**
bounding box from day one, so click-to-highlight (P2) needs no re-parse.
§7.2 P0
- **TR-PAR-2 XML uses a profile registry: a Tally-style default, a generic profile matching tag-name synonyms,**
and custom XPath maps saved per layout signature. Profiles are JSON data, not code. The parser
disables DTDs, entity expansion and network access, caps file size and streams with iterparse.
§7.2 P0
- **TR-PAR-3 CSV detects BOM, UTF-8 and Windows-1252, sniffs the delimiter, takes the header row as the first**
row matching at least 3 known synonyms, reads every cell as text, and supports one-row-per-line
files grouped by document number. A column mapping confirmed once is stored against the
layout signature (a hash of the normalized header set).
§7.2 P0
- **TR-PAR-4 Text-layer PDFs run through seven stages: classify each page as text or scanned by character**
count; extract words with coordinates; find header blocks (Seller, Bill-to, Ship-to); pair labels and
values by proximity (right of, then below); detect the line-item table from a header row with at
least 3 of description, quantity, rate, amount and HSN; merge multi-line descriptions and repeated
headers across pages; read the totals block.
§7.2 P0
- **TR-PAR-5 Extraction QA checks quantity × rate − discount ≈ amount (±₹1), sum of lines ≈ taxable value, and**
taxable + tax + round-off ≈ total (±₹1). On failure it tries alternative column readings up to a fixed
cap, then lowers confidence and flags.
§7.2 P0
- **TR-PAR-6 Field confidence starts from a base score per method, rises when validators pass (GSTIN**
checksum, arithmetic) and falls on ambiguity. Document confidence is the minimum over critical
fields. The critical-field gate marks a document needs_verification when a critical field is
missing or below 0.7.
§7.2 P0
- **TR-PAR-7 PDF work runs in a child process with wall-clock, memory and page limits, so a malformed file**
cannot hang or exhaust a worker. Target: 3 s or less for a text PDF of 5 pages or fewer on the
reference worker (§8).
§10 P0
- **TR-PAR-8 Scanned PDFs get an OCR text layer, then TR-PAR-4 to TR-PAR-6 run unchanged at a lower base**
confidence. Per-vendor layout memory stores corrected label positions against the layout
signature.
§7.2 P1
- **TR-PAR-9 Until OCR ships, a page with no text layer produces a shell document: the file, its page count and**
no fields, flagged needs_verification with reason NO_TEXT_LAYER. Its natural key falls back to the
file hash, so the invoice appears as Needs Review instead of disappearing.
§7.2 P0

---

<!-- Page 8 -->
### 5.3 Normalization
Normalizers are pure functions with property tests and fixtures taken from the PRD examples.
ID Requirement PRD Pri
- **TR-NRM-1 Numbers parse to Decimal only: Indian digit grouping, ₹ / Rs / INR prefixes, negatives in brackets**
or with a trailing minus. Storage scales are 2 decimals for amounts and 4 for rates and quantities.
No float touches money or quantity anywhere.
§7.2 P0
- **TR-NRM-2 Dates accept dd/mm/yyyy (default), ISO and 12-Sep-26. An ambiguous date takes the majority**
reading of the unambiguous dates in the same file, else dd/mm. The financial year runs 1 April to
31 March and is derived from the invoice date.
§7.2, S1 P0
- **TR-NRM-3 UOM aliases map to canonical units through an editable table (NOS/PCS/EA/UNIT to NOS, KG/KGS**
to KG, MTR/M to MTR). An unknown UOM raises a flag; optional conversions such as BOX to NOS
× n are per item.
§7.2 P0
- **TR-NRM-4 GSTIN: uppercase, strip spaces, validate pattern and checksum, then derive the state code**
(characters 1 to 2) and PAN (characters 3 to 12).
§7.2 P0
- **TR-NRM-5 Vendor names lose M/S, PVT, PRIVATE, LTD, LIMITED, LLP and punctuation. Item text is**
lowercased, stripped and tokenised; spec tokens (model numbers, GB/TB, inch, kW, size, colour)
go into a separate set used by the alignment guard.
§7.2 P0
- **TR-NRM-6 Line type is goods or charge (freight, packing, insurance, handling, round-off, discount), decided by**
an editable keyword list. Charge lines skip item and quantity checks.
§7.2 P0
- **TR-NRM-7 PO-number canonicalisation: split on non-alphanumerics, strip leading zeros from numeric**
segments, uppercase, rejoin. PO/26-27/0142, PO-26-27-142 and po 26 27 142 all become
PO2627142; BM-PO-0142 becomes BMPO142. The same function serves invoice, challan and GRN
numbers.
§7.2 P0
### 5.4 Vendor resolution
Resolution is deterministic and order-independent: it works on a sorted snapshot, and only vendors anchored by
a GSTIN or PAN can absorb fuzzy matches.
ID Requirement PRD Pri
- **TR-VEN-1 Cascade per document: GSTIN exact, same PAN, normalized-name exact, learned alias, fuzzy name**
at 0.92 or above with no conflicting PAN or GSTIN, else Unassigned. The first hit wins and the rule
that fired is stored.
§7.3 P0
- **TR-VEN-2 Two different PANs never auto-merge, whatever the name similarity. This is a hard guard in code**
and a property test.
§7.3 P0
- **TR-VEN-3 Order independence: documents are processed sorted by natural key, and fuzzy matching**
compares only against vendors anchored by a GSTIN or PAN in the snapshot taken at job start.
Ties become merge suggestions, never merges.
§7.3 P0
- **TR-VEN-4 Fuzzy scores from 0.80 up to 0.92 create vendor_merge_suggestion rows. A user merge writes**
a vendor_alias and a merged_into pointer (a soft, reversible merge), so next month it applies
on its own.
§7.3 P0
- **TR-VEN-5 Vendor counts (POs, GRNs, invoices) and link health (linked, unlinked, orphan) come from one**
indexed aggregate query. Splitting a merged vendor is P1.
§7.3, §8 P0
### 5.5 Linking: the Anchor Resolution Ladder
Linking is invoice-centric. A link joins an invoice or GRN to a PO, or an invoice to a GRN, at header or line level,
and stores method, confidence and evidence.
ID Requirement PRD Pri
- **TR-LNK-1 link rows hold the from and to documents, level (header or line), method (labeled, reverse,**
via_grn, content, manual), confidence, evidence snippet, state (auto, suggested,
confirmed, rejected) and a version. Links are append-only; a change writes a new version.
§7.4 P0
- **TR-LNK-2 Step 0, labeled reference: read PO numbers from labeled header or line fields using the editable**
synonym table (Appendix B). Confidence 0.99.
§7.4 P0
- **TR-LNK-3 Step 1, reverse lookup: tokenise the whole invoice text into alphanumeric runs, canonicalise**
sliding windows of up to 6 runs, and look each up in an in-memory index of every canonical PO
number in the pool. Confidence 0.97. Guards: a hit must sit on run boundaries; canonical length is
at least 5 unless a PO-like label is nearby; hits equal to the invoice's own number, GSTIN, IRN, eway bill number, HSN or an amount are ignored.
§7.4 P0

---

<!-- Page 9 -->
ID Requirement PRD Pri
- **TR-LNK-4 Non-exact PO matches (prefix-stripped after dropping PO, ORDER, PUR; numeric tail of at least 5**
digits that is unique in the vendor) are allowed only inside the same vendor.
§7.4 P0
- **TR-LNK-5 Step 2, via GRN or challan: a GRN that records this invoice or challan number, or an invoice that**
cites a GRN or delivery-challan number, links through the GRN to its PO. Confidence 0.95.
§7.4 P0
- **TR-LNK-6 Step 3, content inference: candidates are the vendor's POs with open balance and a PO date on or**
before the invoice date. Score = 0.40 line coverage + 0.20 quantity feasible + 0.15 rate agreement
+ 0.10 date + 0.10 GRN support + 0.05 buyer match. Suggest only if the best score is at least 0.80
and leads the runner-up by 0.15; otherwise offer the top 3. Rate is a soft signal, so a price hike
surfaces as RATE_HIGH, not as a wrong PO.
§7.4 P0
- **TR-LNK-7 Ordering for determinism: phase A resolves steps 0, 1, 2 and 4 for every document (no ledger**
needed); phase B runs step 3 per vendor in invoice date and number order, reading balances from
phase A plus earlier phase B suggestions.
§7.4, S4 P0
- **TR-LNK-8 The auto-accept threshold is 0.95 (setting). Below it the case is Needs Review with one-click**
confirm. Per-step confidences are config constants, not measured probabilities.
§7.4 P0
- **TR-LNK-9 Multi-PO invoices link at line level: a line inherits the header PO when there is exactly one,**
otherwise it is inferred among the invoice's candidate POs. GRNs use the same ladder: their PO
label, reverse lookup, invoice or challan record, content, manual.
§7.4 P0
- **TR-LNK-10 A labeled or found PO reference that is not in the pool produces NO_PO_LINK with detail**
PO_NOT_IN_POOL and the reference text, so the UI can say to upload the earlier PO file.
§7.4 P0
### 5.6 Reconciliation engine
The engine is one pure function, evaluate(snapshot, config, run_date). It performs no I/O, reads no clock
and uses no randomness.
ID Requirement PRD Pri
- **TR-REC-1 Input is an immutable snapshot (documents, lines, links, vendors, config) plus run_date, which is**
used only for ageing. Output is one CaseResult per invoice: header checks, line results, ledger
rows, status, payable and at-risk.
§7.5 P0
- **TR-REC-2 Determinism: invoices on a PO line are processed in order of invoice date, canonical invoice**
number, then natural key. Every collection is sorted by a stable key before use, similarity scores
are quantized to 4 decimals before any comparison, and alignment ties go to the lower natural
key. Ingestion order never changes an output (scenario 18).
§7.5 P0
- **TR-REC-3 S1 gate: critical fields present, vendor resolved, PO open. Duplicate = same vendor PAN, canonical**
invoice number and financial year, or same IRN. A suspected duplicate (same vendor and amount,
dates within 3 days, canonical numbers within edit distance 2) is not blocked: it becomes Needs
Review with DUP_SUSPECTED and keeps consuming balance until a person decides (proposed, OQ5). The earlier invoice by sort key is the original, and an exact duplicate consumes no balance.
S1 P0
- **TR-REC-4 S3 alignment: sim = 0.55 desc + 0.20 code + 0.15 hsn + 0.10 uom, where desc**
blends token-set and token-sort ratios and is capped at 0.60 when spec tokens conflict (16GB vs
8GB). Assignment is a Hungarian solve per invoice and PO pair. Cost is an integer: −sim in units of
0.0001, times 1,000, plus a 0 to 999 tie-break from quantity and rate proximity, so a tie-break
never outweighs a similarity difference. After the one-to-one solve, each invoice line left
unassigned takes its best PO line if it clears the thresholds, so many invoice lines may bill one PO
line. Thresholds: 0.80 or above aligned; 0.60 to 0.80 Needs Review with ITEM_UNSURE (proposed,
OQ-8); below 0.60 ITEM_NOT_ON_PO; and 0.50 when both documents have one line and the link is
explicit.
S3 P0
Quantity ledger (S4), per PO line, in Decimal:
cap = ordered × (1 + over_tolerance)
grn_cum = opening_received + Σ accepted(GRN lines aligned to this PO line)
rejected = Σ rejected(GRN lines aligned to this PO line)
billed = opening_billed
for each invoice line X on this PO line, sorted by (invoice date, invoice no., natural key):
skip if the invoice is Duplicate or Rejected # consumes nothing
B = X.qty
billed_after = billed + B
eligible = max(0, min(B, cap − billed, grn_cum − billed))
over_po = billed_after > cap # QTY_OVER_PO
shortfall = billed_after − grn_cum
if shortfall > 0:
if over_po or (rejected > 0 and shortfall ≤ rejected): QTY_OVER_GRN

---

<!-- Page 10 -->
else: GRN_PENDING
emit row(ordered, billed_before = billed, B, grn_cum, eligible, balance = ordered −
billed_after)
billed = billed_after
ID Requirement PRD Pri
- **TR-REC-5 The ledger runs as above. Accepted quantity of a GRN line is accepted, else received minus**
rejected, else received with a warning. The billed quantity of every invoice except Duplicate and
Rejected counts toward billed_before, including Needs Review and failed ones (proposed, OQ1).
S4 P0
- **TR-REC-6 Shortfall classification: when billing exceeds accepted GRN quantity, the code is QTY_OVER_GRN if**
rejected quantity covers the gap or billing already exceeds the PO cap (later receipts could not
cover it); otherwise GRN_PENDING. Pending cases age into exceptions 30 days after the invoice
date (setting); the code stays GRN_PENDING with aged = true. This follows Appendix C1 (OQ-2).
S4 P0
- **TR-REC-7 Received above ordered raises GRN_OVER_PO and the excess is not billable. Opening balances**
enter as events. A ledger built without earlier months shows a completeness warning that points
to the opening-balance import.
S4, ING-11 P0
- **TR-REC-8 S5 price and amount. Rate compares the invoice net unit rate (line amount ÷ quantity, after**
discount, before tax) with the PO rate: above tolerance is RATE_HIGH, below is RATE_LOW.
Expected taxable = Σ billed quantity × PO rate + PO-approved charges, tolerance ±1% or ±₹10,
never the PO grand total (TOTAL_MISMATCH). Cumulative invoiced taxable per PO above PO total
× (1 + tolerance) is PO_VALUE_EXCEEDED. Charge lines absent from the PO raise EXTRA_CHARGE.
S5 P0
- **TR-REC-9 S6 tax and compliance. Invoice tax rate vs PO rate (warn); computed tax = taxable × rate (±₹1); the**
rate must be in the slab list effective on the invoice date; tax type from supplier state vs place of
supply (TAX_TYPE_MISMATCH); HSN present and equal to the PO's when it has one; GSTIN format
and checksum; vendor GSTIN or PAN equals the PO vendor (VENDOR_MISMATCH), with a warning
when name similarity is below 0.92; buyer GSTIN in the entity list; DATE_ORDER (fail by default)
and LATE_DELIVERY (info); currency equal and UOM equal or convertible; payment due date =
invoice date + PO payment terms.
S6 P0
- **TR-REC-10 Checks form a registry of pure functions (context) → CheckResult(code, level,**
expected, actual, variance, tolerance, evidence). Each has a stable ID and version.
The hash of the registry and config is the run's rules_hash, and adding a check never changes
another check's output.
S5 to S7 P0
- **TR-REC-11 Every tolerance has a percentage limit and an absolute limit. Default mode or passes when either**
holds (the looser); both is a setting. Overrides resolve item, then vendor, then global. All
comparisons use Decimal.
Tolerances P0
- **TR-REC-12 S7 status, first match wins: Duplicate, Unlinked, Needs Review, Exception, Pending, Matched**
(flags), Matched. A multi-PO invoice takes its worst line. Overrides (Approved, On hold, Rejected)
are stored apart from the computed status and overlay it.
S7 P0
- **TR-REC-13 Both payable modes are supported.** Initial `payable_rate_mode` is `pay_at_po_rate`, with `payable_rate = min(invoice net rate, PO rate)`; `hold` holds payable until the vendor corrects the variance.
payable_taxable = Σ eligible quantity × payable_rate + approved charges;
at_risk = invoice taxable − payable_taxable. GST on the at-risk amount is shown
separately at the PO tax rate (proposed, OQ-4).
S7 P0
- **TR-REC-14 Each run stores engine_version, rules_hash, a config snapshot and a manifest of every input**
document version and link version with a hash. A replay command re-runs a manifest and
compares outputs (tooling P1, manifests from P0).
§10 P0
- **TR-REC-15 The snapshot is partitioned into connected components of the invoice, PO and GRN link graph.**
Components are evaluated independently and in parallel across worker processes, and memory
per component is bounded by its documents. Target: 5,000 invoices in 60 s or less on the
reference worker (§8).
§10 P0
### 5.7 Review and overrides
Human decisions are recorded as events beside the computed result, never by editing it.
ID Requirement PRD Pri
- **TR-REV-1 Case actions (confirm_link, change_link, approve with a mandatory reason, hold, reject,**
note) are rows in case_action, keyed by invoice and stamped with actor, time, reason and a
fingerprint of the case inputs. The effective status is the computed status overlaid by the latest
action.
REV-1 P0
- **TR-REV-2 Manual Recon writes manual links at confidence 1.00 for a chosen invoice, PO and GRNs, then**
runs the same engine on that component. There is no second code path.
REV-2 P0
- **TR-REV-3 Bulk actions (confirm all links at or above 0.95; approve selected Matched (flags)) run server-side**
under an idempotency key and return a per-item result. One failure never rolls back the rest.
REV-3 P0

---

<!-- Page 11 -->
ID Requirement PRD Pri
- **TR-REV-4 Cases, links and vendors carry a version. A write with a stale If-Match returns 409 with the**
current state.
REV-1 P0
- **TR-REV-5 audit_log is append-only: the application role has INSERT and SELECT only, and each row stores**
actor, action, target, before, after, reason, timestamp and the previous row's hash for tamper
evidence. Rows are written from P0 for every case action and link or vendor edit; the audit screen
is P1.
REV-4 P1
(write
s P0)
- **TR-REV-6 Inline correction of an extracted field stores a manual value, re-runs linking and evaluation for its**
component, and saves the corrected label position against the vendor layout.
REV-5 P1
- **TR-REV-7 An override whose stored input fingerprint no longer matches the latest run is flagged stale, rereview and stops overlaying the status. The fingerprint is stored from P0.**
REV-6 P1
- **TR-REV-8 Role checks run in the service layer: only a Manager may approve an Exception. P0 ships one role,**
but every action already passes the check and records the actor.
REV-7 P1
### 5.8 Preview
ID Requirement PRD Pri
- **TR-PRV-1 The file endpoint serves PDFs with HTTP range support and short-lived signed access. pdf.js**
renders them with scripting disabled.
§7.7 P0
- **TR-PRV-2 XML and CSV records render as a document card (header plus line table) from the canonical**
document, with a raw-view toggle that pretty-prints XML or shows CSV rows. Raw view is sizecapped and paged for large files.
§7.7 P0
- **TR-PRV-3 Fields below the confidence threshold are highlighted from field_evidence.confidence. The**
side-by-side Invoice, PO and GRN view loads all three from one endpoint.
§7.7 P0
- **TR-PRV-4 Click a value to highlight its source, using the stored page and bounding box converted to the**
viewer's scale on the client.
§7.7 P2
### 5.9 Excel export
Workbooks are built by core.export from stored run results, so an export always matches the screen and can
be rebuilt from history.
ID Requirement PRD Pri
- **TR-EXP-1 Export is a build_export job keyed by run and filter hash. The filter is stored as JSON on the**
export and re-applied server-side, so "export what I see" cannot drift from the screen. The file
lands in the object store behind an authenticated download link and expires after a retention
period (setting).
§7.8 P0
- **TR-EXP-2 Core sheets are Summary, Invoice Recon, Line Recon, Exceptions and Vendor Summary; PO Status,**
GRNI, Orphans and Audit follow in P1. Columns follow PRD §7.8. Summary records batch, run
time, counts by status, ₹ invoiced, payable and at-risk, engine version, tolerances used and AI on
or off.
§7.8 P0
- **TR-EXP-3 Variance, total and at-risk columns are live formulas over the value columns beside them, with**
cached results written so viewers that do not recalculate still show numbers. Engine-derived
inputs (expected, payable, quantities) are written as values.
§7.8 P0
- **TR-EXP-4 Document text (item names, vendor names, invoice numbers, narration) is always written as a**
string cell, never through type-guessing writers. A value starting with =, +, - or @ also gets Excel's
quote-prefix style. Nothing from a vendor document is ever evaluated as a formula, and PO
numbers like 0142 keep their zeros.
§7.8 P0
- **TR-EXP-5 Frozen header row, filters, a ₹ number format (grouping follows the viewer's locale; an optional**
lakh and crore custom format is a setting), and status colours by conditional formatting so they
follow later edits.
§7.8 P0
- **TR-EXP-6 Large exports use the writer's constant-memory mode and stream each sheet in stable sort order,**
so memory stays flat as rows grow.
§10 P0
- **TR-EXP-7 A read-back test opens each generated workbook and checks sheet names, headers, formula**
presence and cached values against the run. Drift fails the build.
§11 P0
### 5.10 AI assist
AI is optional, server-side and advisory: code decides every number and every pass or fail.

---

<!-- Page 12 -->
ID Requirement PRD Pri
- **TR-AI-1 A LLMProvider port (complete_json(schema, prompt, limits)) with adapters for Claude,**
OpenAI and Gemini. Model names, timeouts and concurrency limits are settings. Modes: Rules
only (default), AI-assisted, AI-first extraction.
§7.9 P0
- **TR-AI-2 Hooks H1 to H4: H1 PDF extraction fallback (critical-field failure or confidence below 0.7), H2 item**
similarity in the 0.60 to 0.80 band, H3 re-rank of the top 5 inferred POs, H4 vendor merge
suggestions. H5 (explanations, vendor email drafts) is P2. Each hook has a versioned prompt, a
JSON schema and a validator.
§7.9 P0
(M3)
- **TR-AI-3 Output must match the strict JSON schema or is discarded. Grounding: every extracted value must**
carry a snippet that occurs literally in the document text, and returned PO numbers must exist in
the pool. Otherwise the field is dropped.
§7.9 P0
- **TR-AI-4 AI never sets a quantity, total or pass and fail: the engine recomputes everything from validated**
fields. AI cannot override a spec-token conflict or a vendor and PAN conflict. AI links are tagged
ai_suggested and default to Needs Review unless auto-accept above a threshold is enabled.
§7.9 P0
- **TR-AI-5 Any failure (bad key, rate limit, timeout, invalid output) falls back to rules and is logged with a**
code. A run never blocks on AI.
§7.9 P0
- **TR-AI-6 The key is entered on Home, encrypted with AES-256-GCM under a master key from the**
environment or a KMS, masked after saving, and never returned to the browser, logged or
exported. Storage is session only (default; an encrypted server-side record with a time limit) or
saved encrypted, with a Remove key button.
§7.9 P0
- **TR-AI-7 Text passes a redactor for bank account numbers, IFSC codes, phone numbers and email**
addresses before any call (on by default). A consent flag is required and stored with the run. Calls
originate from the backend only.
§7.9 P0
- **TR-AI-8 Per-run budget cap with a live token and cost estimate; calls stop at the cap. Responses are**
cached by document hash, hook, prompt version and model, so a re-run costs nothing.
§7.9 P0
- **TR-AI-9 Each run records provider, model, prompt versions and AI on or off. ai_call_log stores hashes**
and token counts, not document text. AI on and off must give identical maths and verdicts
(scenario 20).
§7.9, §11 P0
- **TR-AI-10 Document text is untrusted input to the model. Prompts state that instructions inside documents**
are data; the model has no tools, network or database access; outputs are parsed as data only.
§13 P0
## 6. Data design
PostgreSQL holds all structured data. Documents, links, configuration and runs are versioned and append-only;
ledgers and summaries are derived and can always be rebuilt.
### 6.1 Data requirements
ID Requirement Pri
- **TR-DAT-1 Every table carries workspace_id (one fixed value in v1), and unique keys and indexes include it. Multiworkspace and row-level security can then arrive without a migration.**
P0
- **TR-DAT-2 Primary keys are UUIDv7 generated by the application (time-ordered for index locality). No sort, tie-break or**
logic depends on a key; ordering uses business keys (§5.6).
P0
- **TR-DAT-3 Money and quantity columns are NUMERIC: amounts (18,2), rates and quantities (18,4), scores (5,4). The API**
sends decimals as strings.
P0
- **TR-DAT-4 Documents, links, config and runs are append-only versions with a pointer to the current one. History cannot**
be backfilled, so this is P0 even though the audit screen is P1.
P0
- **TR-DAT-5 Derived data (PO-line ledgers, KPI summaries, vendor counts) is recomputable from primary data and never**
edited by hand.
P0
- **TR-DAT-6 Migrations use Alembic with expand-then-contract steps. Backfills run as jobs, never inside a migration**
transaction.
P0
- **TR-DAT-7 Retention is configurable: a scheduled job deletes raw files and extracted text after the period, keeping audit**
rows and run summaries. Each deletion is logged.
P1
### 6.2 Tables
Table Holds Key columns and constraints
workspace, app_user,
session
The single v1 workspace, users with roles, server- Unique email; Argon2id hash; session stored as a hash

---

<!-- Page 13 -->
Table Holds Key columns and constraints
side sessions with expiry
batch Label and period Label, period start and end
source_file One upload or ZIP entry Unique (workspace_id, sha256); zone, format,
status, error code, parent file, object key, size, page
count
document Identity of a PO, GRN or invoice Type; unique natural_key; vendor; batch;
current_version_id
document_version Immutable content of one version Document number, date, GSTIN fields, totals,
currency, confidence, extras JSONB, content_hash,
seq, business revision, source file and locator,
parser version
doc_line Lines of one document version Line number, line type, description, item code, HSN,
UOM, quantity, rate, discount, amount, tax fields, PO
reference, received, rejected and accepted quantity,
spec tokens
document_text Page text for reverse lookup, search and AI
grounding
Document version, page, text
field_evidence Evidence per field Version or line, field, value, confidence, source, page,
bounding box, snippet
document_warning Warnings and reasons Code, level, message, document
vendor, vendor_gstin,
vendor_alias
Canonical vendors, their GSTINs and learned
aliases
PAN; normalized name; merged_into pointer
vendor_merge_suggest
ion
Fuzzy pairs awaiting a user decision Pair, score, state
link Invoice or GRN to PO, invoice to GRN From and to document, level, method, confidence,
state, evidence, version, confirmed_by
opening_balance Ledger seed from an ERP export PO line, received and billed to date, as-of date
po_line_ledger Derived ledger per PO line Ordered, received accepted, rejected, billed, open,
input hash
config_version Immutable settings snapshots JSONB of tolerances, synonyms, UOM map, buyer
entities, slab list, payment terms, weights; active
pointer
recon_run One evaluation run Scope, engine_version, rules_hash, config
version, run date, manifest hash, AI metadata, counts
recon_case,
recon_line_result
Results per invoice and per invoice line Status, payable, at-risk, reason_codes text[] with
GIN index, checks JSONB, input fingerprint
case_current Pointer from invoice to its latest case Invoice id, case id
case_action,
audit_log
Overrides and the append-only audit trail Actor, action, target, before, after, reason, previousrow hash
layout_profile Per layout signature: XML profile, CSV column
mapping, PDF label corrections
Signature, kind, JSON body
dirty_scope,
processing_event, job
Recompute queue, per-document stage log, job
tables
Scope type and id; stage, duration, outcome
export_file,
ai_setting,
ai_call_log, secret
Exports, AI configuration and usage, encrypted key
material
Filter JSON, object key, expiry; token counts and
hashes only
Checks are stored as JSONB on the write-once case and line result rows, with reason codes copied into an array
column for filtering. This avoids millions of tiny rows per run while keeping every check queryable by code.
### 6.3 Versioning and reproducibility
- A re-ingested document with the same natural key and different content creates a new
document_version; identical content is skipped.
- A PO's business revision is a version too. The governing version is the highest revision, then the latest
sequence.
- link and config_version rows are never updated. A change inserts a new row and moves a pointer.

---

<!-- Page 14 -->
- A run manifest lists the document version, link and config triples used, plus their hash. That is enough to
replay the run.
### 6.4 Main access paths
- document (workspace_id, type, vendor_id, doc_date) and unique (workspace_id,
natural_key).
- link (to_document_id, state) and link (from_document_id).
- recon_case (run_id, status), a GIN index on reason_codes, and case_current (invoice_id).
- doc_line (document_version_id, line_no).
- Trigram GIN indexes on vendor normalized name and canonical document number for the searchable
pickers.
- A partial index on dirty_scope where unprocessed, so workers find work in constant time.
### 6.5 Sizing (assumption)
Assuming about 10 lines per document (to validate with PRD question 6), 10,000 documents a month is about
100,000 lines and 0.7 million evidence rows. A year stays under 10 million rows, so v1 needs no partitioning. Raw
files at an average of 200 KB per PDF come to about 2 GB a month.
## 7. APIs and interfaces
The web app talks to one versioned REST API. Long work runs as jobs the client polls, and every number on
screen comes from the server.
### 7.1 API conventions
Topic Rule
Style REST over HTTPS with JSON bodies under /api/v1. FastAPI generates OpenAPI 3.1, the TypeScript client is
generated from it, and CI fails on drift.
Auth Session cookie (HttpOnly, Secure, SameSite=Lax) plus an X-CSRF-Token header on unsafe methods. 401
means no session; 403 means the role lacks the capability.
Identifiers UUIDs in paths. Business keys such as invoice or PO numbers are search filters, never path keys.
Numbers and dates Decimals travel as strings (for example 48000.00) and dates as ISO 8601. The UI formats them with Indian
grouping and never does business arithmetic.
Lists Keyset pagination (cursor, limit up to 200) with server-side filter and sort. A total count is returned only on
request (count=true).
Concurrency Mutable resources (case, link, vendor, config) return an ETag, and writes need If-Match. A stale write gets 409
with the current representation.
Idempotency Idempotency-Key on run creation, exports and bulk actions. A repeated key returns the first result for 24 hours.
Async work Endpoints that start work return 202 with a job or resource URL. No request handler does more than a few
seconds of work (TR-NFR-7).
Errors Problem Details (RFC 9457) with type, title, status, a stable code, detail and errors[] for field problems.
Versioning Additive changes stay in v1. A breaking change ships as /api/v2 beside it.
Limits JSON bodies up to 1 MB. Uploads stream under the limits in §5.1. Login and AI-test endpoints are rate limited per
user and per address.
### 7.2 Endpoints
Ingestion
Endpoint Purpose Notes
POST /batches, GET /batches, PATCH
/batches/{id}
Create, list and relabel batches Suggested label comes from the dominant
invoice date (TR-ING-7)
POST /files Upload files or a ZIP into the PO, GRN
or Invoice zone
Multipart and streamed; returns file rows in
Staged
POST /batches/{id}/process Release staged files to the queue 202; progress comes from GET /files

---

<!-- Page 15 -->
Endpoint Purpose Notes
GET /files File table: status, documents found,
warnings
Filter by batch, zone and status; ETag polling
POST /files/{id}/retry, DELETE
/files/{id}
Retry a failed file; remove a file Removing a parsed file withdraws its
documents (soft, audited) and marks
affected scopes dirty
GET /files/{id}/content, GET
/files/{id}/errors
Original bytes for preview; error log as
JSON lines
Range requests; short-lived signed access
GET /batches/{id}/summary Post-ingestion summary Counts by type, format and vendor; warnings
with document links
GET /documents, GET /documents/{id},
GET /documents/{id}/raw
Search and read canonical
documents; raw XML or CSV record
Detail includes lines, evidence, confidence
and the processing log; raw view is sizecapped and paged
Mapping
Endpoint Purpose Notes
GET /vendors, GET /vendors/{id}, GET
/vendors/{id}/documents
Vendor table, vendor detail, and its
PO, GRN and invoice rows
Counts and link health from one aggregate
query; search and has-issues filters
POST /vendors/{id}/merge Merge one vendor into another Writes an alias and a merged_into pointer;
audited
GET /vendor-merge-suggestions, POST
/vendor-merge-suggestions/{id}/decisi
on
Review fuzzy pairs Accept or reject; the decision is saved as an
alias
GET /links, POST /links, PATCH
/links/{id}
List links, create a manual link,
confirm, change or unlink
If-Match on changes; manual links are
confidence 1.00
POST /links/bulk-confirm Confirm every link at or above a
threshold (default 0.95), for a vendor
or all
Idempotency-Key; per-item results
Reconciliation
Endpoint Purpose Notes
POST /runs, GET /runs/{id} Start a run for a vendor or all; read
status, progress and counts
202 with a run id; the run stores its manifest
hash
GET /cases, GET /cases/{id} Invoice table; case detail Detail carries header checks, line grid, ledger
rows and evidence references
POST /cases/{id}/actions, POST
/cases/bulk-actions
Approve, hold, reject or note, one or
many
If-Match on one; Idempotency-Key on
bulk; approval needs a reason
POST /manual-recon Manual Recon for a chosen invoice,
PO and GRNs
Runs the same engine on that component
and returns the case
GET /kpis Home KPI cards for a period Same SQL views as the Excel Summary sheet,
so the numbers agree
POST /exports, GET /exports/{id},
GET /exports/{id}/download
Build, poll and download the
workbook
Filter stored as JSON on the export
Settings, AI and system
Endpoint Purpose Notes
GET /config, PUT /config, GET /reasoncodes
Settings; reason-code catalogue PUT writes a new immutable
config_version under If-Match
GET /ai/settings, PUT /ai/settings,
POST /ai/test, DELETE /ai/key, GET
/ai/usage
Mode, provider, model and budget;
connection test; key removal; token
and cost usage
The key is write-only and never returned,
only a masked hint
POST /auth/login, POST /auth/logout,
GET /auth/me
Session Rate limited; session rules in §9
GET /admin/errors, GET /healthz, GET
/readyz
Error dashboard data; liveness;
readiness
Health endpoints need no session and reveal
no detail

---

<!-- Page 16 -->
### 7.3 Progress and staleness
- While work is active the client polls GET /files and GET /runs/{id} every 2 seconds (setting) using
ETag, so unchanged answers cost almost nothing. Server-sent events are P1.
- Each vendor and PO scope carries a monotonic input_seq that any relevant change increments. A case
records the sequences it used, so it is stale when any of them is behind. The full input fingerprint is kept
for override staleness and replay.
### 7.4 Web app requirements
ID Requirement PRD Pri
- **TR-API-1 React SPA built with Vite and served as static files by the reverse proxy, on the same origin as the**
API (no CORS). Deep links exist for a document, a vendor and a case, so an audit trail can be
shared.
§8 P0
- **TR-API-2 Files, vendors, documents and cases are server-driven tables with filter, sort and keyset**
pagination. TanStack Virtual renders only visible rows, so 10,000 rows scroll smoothly at 1366 px
width.
§10 P0
- **TR-API-3 The UI computes no business result. Statuses, variances, payable, at-risk, ledger boxes and**
messages come from the API, and the client only formats. Money fields use an opaque
DecimalString type, so arithmetic on them does not compile.
§7.5 P0
- **TR-API-4 Reason-code messages and suggested actions come from GET /reason-codes, the same**
catalogue the Excel export uses.
§7.5 P0
- **TR-API-5 Case detail (Invoice, PO and GRN side by side, ledger box, evidence) loads in one call. The PDF**
viewer loads only the page in view.
§8 P0
- **TR-API-6 A 409 shows what changed and offers a reload. Nothing is silently overwritten. REV-1 P0**
- **TR-API-7 A stale-run banner appears when the run is behind current inputs, and Re-run is scoped to the**
affected vendors.
§8 P0
- **TR-API-8 Long-running actions show progress from job resources, survive page reloads, and are never**
cancelled by leaving the page.
ING-5 P0
- **TR-API-9 The AI key field is write-only, masked after saving, excluded from autofill and never kept in**
browser storage. AI-assisted mode cannot be saved without the consent box.
§7.9 P0
### 7.5 External interfaces
Interface Direction Contract On failure
AI providers Outbound from
workers
LLMProvider port, schema-validated JSON
(TR-AI-1)
Fall back to rules and log a code
Object store Outbound from API
and workers
S3 API, SHA-256 keys, signed URLs Transient errors retry; uploads
answer 503
Input files Inbound by upload XML and CSV by layout profile, PDFs by
extractor (§5.2)
The file fails with a reason; the
rest continue
Excel workbook Outbound file Sheets and columns per §5.9 Export shows Failed with a
message
Email ingestion, ERP pushback (P2)
Reserved DocumentSource and ErpSink ports,
interfaces only in v1
Not applicable
## 8. Non-functional requirements
Every target is measurable on a stated reference machine and has a named test. Figures from the PRD are kept
as written; the rest are marked proposed.
### 8.1 Reference environment
Role Size Used for
Reference worker 4 vCPU (x86-64, about 3 GHz), 16 GB RAM, local SSD Every performance target below
Reference database 4 vCPU, 16 GB RAM, SSD, on the same network The same targets
Pilot single node 8 vCPU, 32 GB RAM, 250 GB SSD, everything on one host Shape A in §10 (proposed)

---

<!-- Page 17 -->
Sizes are assumptions until the M2 load test confirms them.
### 8.2 Targets
ID Requirement Target Verified by
- **TR-NFR-1 Text-PDF parse 3 s or less for a document of up to 5 pages on the reference**
worker
Timed run over golden PDFs,
nightly in CI
- **TR-NFR-2 XML and CSV**
ingest
1,000 documents from Queued to Parsed in 30 s or less Synthetic 1,000-document files
- **TR-NFR-3 Reconciliation 5,000 invoices in 60 s or less, including snapshot load and**
result write
Synthetic 5,000-invoice set
- **TR-NFR-4 End to end, rules**
only
1,000 invoices ingested and reconciled in 15 min or less.
Assumes 4 parse processes and invoices averaging 3 pages or
fewer (proposed)
M2 load test
- **TR-NFR-5 UI responsiveness List API p95 at 300 ms or less and case detail p95 at 500 ms or**
less at pilot volume (proposed); 10,000-row tables stay smooth
k6 against staging; Playwright
traces
- **TR-NFR-6 Scale v1 is sized for about 10,000 documents a month. Ten times**
that is reached by adding workers and database resources,
without a rewrite
Synthetic 10x load test before
pilot exit
- **TR-NFR-7 Background**
execution
Anything that can take longer than 5 s is a job. Read endpoints
answer within 2 s at p99 (proposed)
Code-review rule; request-time
histogram
- **TR-NFR-8 Determinism The same inputs give byte-identical results in any ingestion**
order, on any machine and across Python patch versions
Property tests; CI matrix; golden
result hash
- **TR-NFR-9 Availability 99.5% during business hours on a single node (proposed). A**
killed worker loses or duplicates no work
Kill-a-worker test in CI; uptime
monitor
- **TR-NFR-10 Backup and**
recovery
RPO 24 h and RTO 4 h on a single node; point-in-time recovery
on managed PostgreSQL (proposed)
Restore drill each quarter
- **TR-NFR-11 Compatibility Latest Chrome and Edge, 1366 px minimum width, no mobile**
layout in v1
Playwright on both browsers
- **TR-NFR-12 Accessibility Tables and dialogs work by keyboard, focus is visible, and no**
status is shown by colour alone (chips carry text) (proposed)
axe checks in CI; manual pass
before pilot
- **TR-NFR-13 Localisation English UI. INR shown with Indian digit grouping and dates as**
dd/mm/yyyy
Unit tests on formatters
- **TR-NFR-14 Limits 25 MB and 100 pages per file, ZIP limits per TR-ING-2, and**
worksheets over the 1,048,576-row format limit continue on a
numbered sheet
Boundary tests
- **TR-NFR-15 Maintainability Import contract holds, mypy strict passes on core, no floatingpoint types in money paths, dependencies reviewed monthly**
CI gates
### 8.3 Scaling path to ten times the volume
Usage is bursty around month end, so the queue is the buffer: workers may lag at the peak without losing work.
Pressure First response
Parse throughput (CPU-bound) More parse worker processes, then more hosts, with concurrency set per queue
Reconciliation time More processes across link-graph components (TR-REC-15), then vendors split across hosts
Database size Partition field_evidence and document_text by month and archive old runs
Read load from exports and KPIs A read replica for those queries
Connections API through pgBouncer in transaction mode; workers connect directly, and locks are transactionscoped advisory locks
Queue limits Stay on PostgreSQL until measured otherwise; the JobQueue port allows a dedicated broker later
## 9. Security and privacy
Uploaded files and the optional AI provider are the two largest risks. The design treats every document as hostile
input and sends nothing to a model without consent and redaction.

---

<!-- Page 18 -->
### 9.1 Requirements
ID Requirement Addresses
- **TR-SEC-1 TLS 1.2 or later (1.3 preferred) ends at the reverse proxy, with HSTS. Database and objectstore connections use TLS whenever they leave a single trusted host.**
Interception
- **TR-SEC-2 Encrypted volumes or provider-managed encryption for PostgreSQL and the object store,**
and encrypted backups. AI keys get an extra AES-256-GCM layer (TR-AI-6).
Disk or backup theft
- **TR-SEC-3 Local accounts with Argon2id hashes, lockout and rate limiting on login, and server-side**
sessions rotated at login. Timeouts are 30 min idle and 12 h absolute (proposed, both
settings). Cookie flags per §7.1. OIDC single sign-on is P2.
Credential attacks,
session theft
- **TR-SEC-4 Every service method declares the capability it needs, checked against the session's role**
(§9.2). Deny by default; the server never trusts a role sent by the client.
Privilege escalation
- **TR-SEC-5 SameSite cookies plus a CSRF header; CSP with default-src 'self', no inline scripts**
and frame-ancestors 'none'; X-Content-Type-Options: nosniff; ReferrerPolicy: same-origin; CORS closed.
XSS, CSRF, clickjacking
- **TR-SEC-6 XML is parsed with DTDs, entity resolution and network access off, under a size cap and**
streaming. A document with an internal DOCTYPE subset is rejected.
XXE, entity expansion
- **TR-SEC-7 Archives follow the TR-ING-2 limits, never write under archive paths, and check ratio and**
size while streaming.
Zip bombs, path
traversal
- **TR-SEC-8 PDFs are parsed in a child process with CPU-time, memory and page limits, on workers with**
no outbound network route. The viewer disables PDF scripting, and the file endpoint sends
nosniff and Content-Security-Policy: sandbox.
Parser exploits, hangs
- **TR-SEC-9 Excel text cells follow TR-EXP-4, React escapes output, SQL is parameterised only, and**
uploaded file names are display text that never form paths.
Formula, script and SQL
injection
- **TR-SEC-10 Content sniffing always applies (TR-ING-3). An optional antivirus hook, such as ClamAV, is a**
setting and is off by default (P1).
Malware
- **TR-SEC-11 Infrastructure secrets come from the environment or a secret manager, never from images**
or the repository. Stored secrets carry a key id, so the master key can rotate through a reencryption job.
Secret leakage
- **TR-SEC-12 Logs hold identifiers, hashes and codes, not document text, and a log processor redacts**
fields named like keys or tokens. A test proves an AI key never appears in responses, logs or
exports.
Data leakage
- **TR-SEC-13 Separate database roles for migrations, the application and read-only reporting. The**
application role cannot update or delete audit_log. Containers run as non-root with a
read-only root filesystem, and API and workers use separate object-store credentials.
Blast radius, tampering
- **TR-SEC-14 audit_log rows are hash-chained, and a scheduled job verifies the chain and alerts on a**
break (P1).
Insider tampering
- **TR-SEC-15 Lockfiles for Python and JavaScript, weekly dependency updates with audit tools, a**
CycloneDX SBOM per build, image scanning, and base images pinned by digest.
Vulnerable or malicious
dependencies
- **TR-SEC-16 Document text is untrusted model input (TR-AI-10). Model output is validated data and can**
never call tools, change a status or alter the maths.
Prompt injection
- **TR-SEC-17 SAST in CI, fuzz tests on all three parsers, a ZAP baseline scan on staging, and an**
independent penetration test before real documents enter the pilot.
Undiscovered flaws
### 9.2 Roles and permissions (proposed)
P0 ships one role with every capability. P1 enforces this matrix, and the checks already exist in the service layer
(TR-REV-8).
Capability AP Executive Manager Auditor
Upload and process files Yes Yes No
Confirm or change links,
merge vendors
Yes Yes No
Run reconciliation Yes Yes No
Approve Matched (flags),
hold, reject, add notes
Yes Yes No
Approve Exceptions (REV7)
No Yes No

---

<!-- Page 19 -->
Capability AP Executive Manager Auditor
Change tolerances and
other settings
No Yes No
Manage the AI key and
mode
No Yes No
Manage users No Yes No
Export to Excel Yes Yes Yes
View documents,
evidence and the audit
trail
Yes Yes Yes
### 9.3 Privacy and compliance notes
- Documents mostly hold business data, plus some contact details such as phone numbers, emails and
bank details. The AI redactor removes bank, IFSC, phone and email values before any call (TR-AI-7).
- Confirm with Benchmark's legal or compliance team whether India's Digital Personal Data Protection Act
2023 applies to vendor contact details, and how long GST and company law require records to be kept.
Those periods are legal and Finance inputs, not engineering guesses. The design supports retention
limits (TR-DAT-7), deletion jobs and India-region hosting.
- The cloud shape uses an India region unless Benchmark decides otherwise (PRD question 9). AI calls may
leave India depending on the provider, so the consent text names the provider and links its data-use
terms beside the key field.
- Exports hold vendor and financial data. Downloads are authenticated and expire, and export creation
and download are recorded in the audit log (P1).
## 10. Infrastructure and deployment
One set of container images runs everywhere: a single Docker Compose host for the pilot and on-prem, and
managed PostgreSQL and object storage for cloud.
### 10.1 Deployment shapes
Component Shape A: single node (pilot, on-prem) Shape B: cloud or Kubernetes
Edge Caddy or nginx with automatic or supplied TLS Cloud load balancer with managed certificates
Web app Static files served by the proxy Static files from a CDN or the same proxy container
API 2 to 4 Uvicorn workers in one container Replicas behind the load balancer, scaled on CPU
Workers One container per queue group: ingest and parse
(CPU), mapping and recon (CPU), export, ai (I/O)
The same images, scaled per queue on queue age
PostgreSQL One container with a named volume and WAL archiving Managed PostgreSQL with point-in-time recovery
Object store MinIO with a volume Managed S3-compatible store with versioning
Secrets Docker secrets or a root-only env file Cloud secret manager
Observability Optional Compose profile with Prometheus, Grafana
and a log collector
Managed metrics and log services
Network Only the proxy is published; the database and MinIO
stay internal; the parse worker has no outbound route
Private subnets, security groups, and an egress allowlist for the ai worker only
### 10.2 Environments
Environment Purpose Data
Local Development with Compose (PostgreSQL, MinIO) Synthetic data and the golden set
CI Ephemeral runs: unit, property, integration and golden-set tests Synthetic data and the golden set
Staging Mirrors production; end-to-end, load and security scans Synthetic data, never production
documents
Production Real documents Real data
Real vendor documents leave production only as anonymised golden-set material that Finance has approved.

---

<!-- Page 20 -->
### 10.3 Delivery pipeline
## 1. Lint and type-check: ruff, mypy strict on core, import-linter, eslint and tsc.
## 2. Unit and property tests on core, with the coverage gate from §12.
## 3. Integration tests against PostgreSQL and MinIO in containers, including migrations on a copy of the
previous schema.
## 4. Golden-set run. Any false Matched, or a drop below the metric gates in §12, blocks the merge.
## 5. Rules-hash check. If rules_hash changed, the change must bump engine_version and attach the
golden-set diff.
## 6. Build multi-stage images, generate the SBOM, and scan images and dependencies.
## 7. Deploy to staging, then run Playwright end-to-end tests, the ZAP baseline scan and the nightly
performance run.
## 8. Manual approval, then production deploy with migrations as a separate step.
### 10.4 Release and operations rules
ID Requirement
- **TR-DEP-1 Infrastructure settings (database URL, bucket, keys) come from environment variables. Business settings (tolerances,**
synonyms, weights) live in config_version and change through the app, never through a deploy.
- **TR-DEP-2 Alembic runs as its own release step before the new API starts. Each migration stays compatible with the previous**
release for one version (expand, then contract), and backfills are jobs.
- **TR-DEP-3 On SIGTERM a worker stops taking jobs and finishes or releases its current one within 5 minutes. Jobs hold a lease, and**
a job whose worker stops heartbeating is re-queued. The M0 queue spike confirms the chosen library does this, or the
port adds it.
- **TR-DEP-4 Shape A releases with docker compose pull and up -d, health checks and a short maintenance window, and**
queued jobs resume by themselves. Shape B rolls the API and drains workers first.
- **TR-DEP-5 Images are tagged with the git SHA and a semantic version. engine_version and parser_version are recorded on**
every run and document version.
- **TR-DEP-6 Rollback means redeploying the previous images. A migration is undone only by a forward fix, because contract steps**
are held back one release.
- **TR-DEP-7 Compose files ship in P0. Terraform or OpenTofu modules and a Helm chart follow in P1.**
- **TR-DEP-8 Shape A takes a nightly PostgreSQL base backup with continuous WAL archiving (pgBackRest or WAL-G). Shape B uses**
managed point-in-time recovery. The object store is versioned and synced nightly to a second location. A restore drill
runs every quarter and before the pilot starts.
- **TR-DEP-9 Cloud resources are created in an India region by default (see §9.3).**
## 11. Observability and operations
Every document, job and run leaves a trace an operator can follow without reading the database, and every alert
names the action to take.
### 11.1 Requirements
ID Requirement PRD Pri
- **TR-OPS-1 Structured JSON logs (structlog) on every process. Each line carries request_id and, where**
relevant, job_id, file_id, document_id or run_id. Levels are configurable and default
retention is 30 days (setting). No document text or secrets (TR-SEC-12).
§10 P0
- **TR-OPS-2 A per-document processing log. Each stage (received, sniffed, parsed, QA, gated, upserted, vendor**
resolved, linked, ledger updated, evaluated) writes a processing_event with duration,
outcome, parser version and warning codes. The document detail view shows it.
§10 P0
- **TR-OPS-3 An error dashboard for admins: failed files by reason code and day, top failing layouts and**
vendors, unresolved warnings, and retry or reparse actions. It is SQL over processing_event
and source_file.
§10 P0
- **TR-OPS-4 Run metrics on recon_run: duration per stage, counts by status, straight-through rate, linkmethod mix, AI calls and cost. Home KPIs and the Excel Summary read the same views (GET**
/kpis), so their numbers agree.
§10, §2 P0
- **TR-OPS-5 Prometheus metrics: queue depth and oldest-job age per queue, job duration and failures by type, §10 P0**

---

<!-- Page 21 -->
ID Requirement PRD Pri
parse success by format, extraction confidence distribution, critical-field gate failures, API latency
and 5xx rate, database pool use, and AI latency, failures and spend.
- **TR-OPS-6 OpenTelemetry traces from request to job, with trace context passed in job arguments. The**
exporter is optional.
§10 P1
- **TR-OPS-7 Calibration telemetry: every human confirm, change or reject of a suggested link is stored with its**
method and confidence, and every override with its reason codes. This is the dataset for tuning
link confidences and thresholds in the pilot.
§13 P0
- **TR-OPS-8 /healthz reports that the process is up. /readyz checks the database, object store, migration**
head and worker heartbeat.
§10 P0
- **TR-OPS-9 Operator CLI reconbench: rebuild-ledgers and drain-workers in P0; replay-run,**
reparse, verify-audit-chain, rotate-master-key and export-diagnostics (a redacted
bundle for debugging a layout) in P1.
§10 P0 /
P1
- **TR-OPS-10 A parser release never re-processes documents by itself. An operator starts a reparse, which**
writes new document versions only where content differs, and the golden-set diff shows the effect
first.
§10 P0
### 11.2 Alerts (proposed thresholds)
Signal Condition First action
Oldest queued job Over 15 min in ingest or parse during business hours Check worker health; add workers
Failing jobs Any job past its retry limit Open the error dashboard; fix and retry
Parse failures Over 10% of files in an hour Look for a new layout or a bad parser release
API health 5xx above 2% for 5 min, or /readyz failing Check the database and object store
Backups Last successful backup older than 26 h Fix the backup job before the next business day
Disk Above 80% on a database or object-store volume Expand, or apply retention
AI failures Above 20% of calls in an hour Check the key, limits and provider status; rules
keep running
Audit chain Verification job reports a break Treat as a security incident
Determinism canary Nightly golden-set result hash differs from the released
hash
Stop the release and investigate
### 11.3 KPI definitions
These definitions are implemented once as SQL views and shared by the Home cards and the Excel Summary
sheet.
KPI Definition
Period The top-bar selection filters invoices by invoice date. A batch is only a label (TR-ING-7).
Invoices Invoices in the period that have a current case.
Matched % Matched plus Matched (flags), as a share of invoices, using the effective status (computed status overlaid by
overrides) (proposed). Straight-through rate is Matched with no human action, as a share of invoices.
Pending, Exceptions,
Unlinked
Counts of current cases by effective status.
At-risk ₹ Sum of at_risk over current cases (TR-REC-13). Whether Duplicate and Unlinked value counts toward it is
open (OQ-6).
GRNI ₹ Sum over PO lines of the larger of zero and accepted GRN quantity minus billed quantity, times PO rate, from
the ledger as it stands now. The month-end accrual by date is the P1 GRNI report.
### 11.4 Runbooks
Runbooks live in the repository beside the code and are exercised in the M4 restore drill. Each states symptom,
checks, fix and owner. They cover:
- a stuck or slow queue
- a failed migration
- restore from backup

---

<!-- Page 22 -->
- master key rotation
- replaying a run
- reparse after a parser change
- a file that repeatedly crashes parsing
- disk full
- an AI provider outage
- an audit chain break
## 12. Testing and quality
The golden dataset comes first. Every rule, threshold and parser change is judged against hand-labelled truth,
and a single false Matched fails the build.
### 12.1 Test levels
Level What it proves Tools Runs
Unit Normalizers, guards and single checks pytest Every commit
Property-based Invariants that hold for any input (§12.4) Hypothesis Every commit; long run
nightly
Parser fixtures XML, CSV and PDF parsers against labelled samples,
including malformed files
pytest, golden files Every commit
Engine scenarios The 20 acceptance scenarios (§12.3), headless on
fixtures
pytest Every commit
Integration API, database, queue and object store together,
including migrations and idempotent re-ingest
pytest with containers Every merge
Golden-set
regression
Links, statuses, reason codes and extraction accuracy
against truth
Custom harness Every merge; nightly on
staging
End to end Ingest, map, reconcile, review and export through the
browser
Playwright Nightly and before
release
Performance The §8 targets on synthetic volume k6, seeded generator Nightly and before
release
Security SAST, parser fuzzing, dependency and image scans, ZAP
baseline
Semgrep, Hypothesis, pipaudit, Trivy, ZAP
Every merge; weekly
Export read-back Every workbook reopened and compared with its run openpyxl Every merge
### 12.2 Golden dataset and quality gates
Build the golden set first, in M0: 6 to 8 vendors, about 40 POs, 60 GRNs and 80 invoices across XML, CSV, text
PDF and scanned PDF, each with hand-labelled truth for links, statuses and reason codes (PRD §11). Real
documents are anonymised before use: names, GSTINs (regenerated with valid checksums), addresses and bank
details are replaced, while layouts, numbers and quirks stay. Two people review every truth file, one of them
from Finance.
Truth is kept as data next to each case. This example is invoice 2260 from PRD Appendix C2:
invoice: ACR/26-27/2260
expected_link: {po: BM-PO-0142, method: via_grn}
expected_status: Matched
expected_reason_codes: []
expected_ledger: {po_line: 1, ordered: 100, billed_before: 90, this: 10, grn_cum: 100,
balance: 0}
Metric Gate Measured as Enforced
False Matched 0 on the golden set; 0.5% or less
in pilot audit
Cases whose truth is not Matched but the engine
says Matched
Merge gate; pilot audit
Straight-through rate 70% or more at pilot, 85% or
more steady
Matched with no human action, as a share of
invoices
Golden-set report; pilot
KPI
Link precision and
auto-link recall
99% or more and 90% or more Precision: correct auto-accepted links over autoaccepted links. Recall: invoices auto-linked
Merge gate

---

<!-- Page 23 -->
Metric Gate Measured as Enforced
correctly over invoices with a true link
Critical-field
extraction accuracy
(text PDFs)
97% or more overall, and no
single field below 95%
(proposed)
Exact match after normalization, per critical field Merge gate from M2
Manual reconciliation
time saved
80% or more against baseline Timed pilot against the baseline measured in M0 Pilot sign-off
Speed The §8 targets Synthetic runs Nightly
### 12.3 Acceptance scenarios as executable tests
Each row of the PRD scenario matrix becomes a test named SCN-nn, so the PRD and the test suite stay one list.
Scenarios What they cover Test level First green
1 to 4, 6 to 8, 14 to 16 Quantity ledger, price, tax and multi-PO rules on
XML and CSV fixtures
Engine M1
9, 10, 12, 13 Linking ladder, PO canonicalisation, vendor identity Engine M1
5, 11, 17 Re-evaluation after a new GRN, duplicates, crossperiod pool
Engine, then pipeline M1, rerun at M2
18 Ingestion order gives identical results Property test over permutations,
then pipeline
M1, rerun at M2
19 An unreadable scan never passes silently Parser and pipeline on golden
PDFs
M2
20 AI on and off give the same maths and verdicts Integration with a stub provider
that returns garbage
M3
### 12.4 Properties that must always hold
## 1. Any permutation of files and documents gives identical case results (TR-REC-2, TR-NFR-8).
## 2. Ingesting the same files twice changes nothing.
## 3. Eligible quantity never exceeds the smaller of the PO cap and accepted receipts, and open balance is
never negative.
## 4. Raising a billed quantity never turns a failing quantity check into a passing one.
## 5. PO-number canonicalisation is idempotent, and the PRD's examples map to one value.
## 6. Different PANs never merge, whatever the names.
## 7. No floating-point value reaches a money or quantity field.
## 8. With a stub provider returning arbitrary output, maths and verdicts equal the rules-only run.
## 9. Changing one vendor's documents never changes another vendor's results.
### 12.5 Tests beyond the PRD matrix
- Hostile input: XML entity attacks, ZIP bombs, path traversal, oversized and encrypted PDFs, and CSV files
with a BOM or odd encodings. Each ends as a Failed file with a reason, never a stuck worker.
- Formula injection: vendor text starting with =, +, - or @ stays text in the export (TR-EXP-4).
- Rounding edges: amounts exactly on the ±₹1, ±₹10, 0.5% and 1% limits, in both or and both tolerance
modes.
- Data gaps: PO revisions, opening balances, and GRN quantities that are missing or ambiguous (PRD
Appendix A, gap 6).
- Concurrency: two reviewers on one case, and a run started during an ingest.
- Faults: killing a worker mid-job loses and duplicates nothing (TR-NFR-9).
- Secrets: an AI key never appears in a response, a log line or an export (TR-SEC-12).

---

<!-- Page 24 -->
### 12.6 Pilot acceptance
M4 runs one or two real months beside the manual process. Every disagreement between ReconBench and the
manual result is classified as an engine error, a manual error or a rule question. The §12.2 metrics are computed
from that comparison, and rule questions go back to Finance as tolerance or policy changes.
### 12.7 Definition of done
- The requirement has an acceptance test and it passes.
- Every new reason code has a message, a suggested action and a scenario.
- Coverage on core is at least 95% line and 90% branch, and on the application layer at least 80% line
(proposed). Mutation testing on core.recon and core.mapping follows in P1.
- New jobs have metrics, logs and an alert where one is warranted.
- The golden-set diff is unchanged, or every change is explained in the pull request.
- Migrations, runbooks and the security checklist (§9) are updated.
## 13. Delivery plan
Build the hard part first: the engine on structured files, then PDFs, then the app, then a pilot. Each phase ends at
a gate the team can test.
Figure 2. Delivery roadmap: engine first, each phase ends at a gate you can test
Each gate is pass or fail. The technical work behind each phase is in the table below.

---

<!-- Page 25 -->
### 13.1 Technical work by phase
Phase Technical deliverables Inputs needed
M0 Discovery Repository, CI skeleton and decision log. Dependency and licence review.
Golden set built and anonymised. Tally XML and CSV profile spikes. PDF
layout survey across at least 5 vendors. Spikes on queue behaviour
(transactional enqueue, leases), Hungarian tie-break determinism and
pdfplumber accuracy.
Sample XMLs and CSVs, about 20
invoice PDFs and some GRN PDFs
(PRD questions 1 and 2). Finance
sign-off on tolerances and policies
(§14).
M1 Engine core.schema, normalizers, XML and CSV parsers, vendor resolution, linking
ladder, alignment, ledger, checks, statuses and the reason-code catalogue.
Run manifests and property tests. A CLI that runs a folder of XML and CSV
files end to end and writes JSON.
Signed tolerances and policies.
Answers to OQ-1 to OQ-5.
M2 PDF and
ingestion
Text-PDF extractor with QA and the critical-field gate. PostgreSQL schema
and migrations, object store and job queue. Ingestion API with ZIP handling
and hardening, async status and the preview endpoint. Performance
benchmarks. Scanned pages flagged, not dropped (TR-PAR-9).
More real PDFs for the golden set,
including ugly ones.
M3 App React app (Home, Ingestion, Mapping, Reconciliation, Settings). Overrides,
manual recon and bulk actions. Excel export with read-back tests. AI port,
hooks H1 to H4 and key handling. Login, role checks and audit writes.
Observability baseline.
One full month of real documents
for the end-to-end demo. AI
provider choice (PRD question 10).
M4 Pilot Parallel run beside manual work. Threshold calibration from telemetry.
Penetration test, restore drill and runbooks. Pilot sign-off.
Finance reviewers' time and a
production environment (PRD
question 9).
### 13.2 Dependencies and critical path
- Sample documents gate everything. Without them the golden set and the parser profiles are guesses.
- Finance decisions on tolerances and policies gate M1 acceptance, because scenarios 4 to 7 depend on
them.
- Once M1 freezes the CaseResult schema, front-end and export work can run in parallel against a mock
API generated from OpenAPI. PDF extraction runs in parallel with the engine after M0.
- The M0 queue spike decides whether Procrastinate stays. The JobQueue port limits the cost of a change.
### 13.3 Team assumptions (proposed)
The plan assumes this minimum team. Adjust it to reality; the phase order does not change.
Role Focus Load
Tech lead, backend Engine, architecture, reviews Full time
Backend or data engineer Parsers, PDF extraction, ingestion pipeline Full time
Frontend engineer Web app, PDF viewer, tables Full time from M2, against a
mock API
QA and test-data lead Golden set, scenarios, performance and end-to-end tests Half time
Finance or AP expert Tolerances, golden-set labels, pilot review A few hours a week, more in
M0 and M4
DevOps Compose, CI, backups, observability Quarter time
## 14. Risks, assumptions and open questions
Nine technical risks could change the plan, and each has an early test. Nine design questions need an answer
before or during M1, and the PRD's own eleven questions stay open as written. Product and business risks
remain in PRD §13.
### 14.1 Technical risks
Risk Impact Mitigation Early test
PDF layout variety
defeats extraction
Low critical-field
accuracy and many
Needs Review cases
Maths-consistency QA, confidence gating, layout memory,
AI fallback (H1), and a second extractor behind the same
interface
M0 layout survey; M2
accuracy gate

---

<!-- Page 26 -->
Risk Impact Mitigation Early test
Non-determinism
from ties, ordering or
floating point
Results differ
between runs or
machines and audits
fail
Explicit sort keys, quantized scores, integer assignment
costs, Decimal, run date passed as input
Permutation property
tests; nightly result-hash
canary
Queue library does
not fit (transactional
enqueue, leases,
load)
Lost or duplicated
jobs, rework
The JobQueue port, and an outbox table written in the
same transaction as a fallback
M0 spike; M2 load test
Ledger recompute is
slow at month end
Slow re-runs and stale
screens
PO-scoped dirty recompute and link-graph parallelism (TRREC-15)
5,000-invoice benchmark
(TR-NFR-3)
Auto-link thresholds
or confidences are
mis-calibrated
A wrong link leads to
a wrong payment, or
trust erodes
Precision-first thresholds, Step 3 always confirmed,
calibration telemetry (TR-OPS-7)
Golden-set precision gate;
pilot
Hostile or malformed
files harm the
platform
Outage or breach TR-SEC-6 to 10, isolated PDF parsing, fuzzing Fuzz tests; penetration
test before the pilot
AI leaks data or
invents values
Privacy breach or
wrong data
Off by default, consent, redaction, grounding, schema
validation, a model with no tools
Stub-provider tests;
scenario 20
Tally XML and CSV
layouts vary more
than expected
Parse failures Profiles as data, custom XPath maps, samples collected in
M0
M0 profile spikes
Earlier months are
missing from the pool
Ledger overstates
open balance
Persistent pool, completeness warning, opening-balance
import (schema in P0)
Scenario 17
### 14.2 Assumptions
- The PRD's assumptions hold: buyer is Benchmark (India, GST, INR), PO and GRN XML is Tally-style, most
invoice PDFs have a text layer, one invoice is one PDF, and there is one workspace.
- Volume is about 10,000 documents a month with about 10 lines each. PDFs average 200 KB and 3 pages
or fewer. PRD question 6 will confirm this.
- The reference sizes in §8.1 are adequate. The M2 load test confirms them.
- For P0, use the user-confirmed PRD seed of 5 / 12 / 18 / 28 with null effective-date boundaries. Keep the slab list configurable and effective-dated in the data model; add date transitions only when explicitly supplied and approved. A rate difference remains a warning (TAX_RATE_DIFF).
- Vendors are Indian entities with a GSTIN or PAN. Foreign vendors stay Unassigned until multi-currency
arrives (P2).
- Users work in Chrome or Edge on desktops, and nothing in P0 requires an AI key.
### 14.3 Open design questions
ID Question Proposed default Needed by
OQ-1 Do invoices that fail (Exception, Needs Review) consume
PO balance? Later invoices pass or fail depending on it.
Yes, everything except Duplicate and Rejected
(TR-REC-5)
M1
OQ-2 Shortfall classification conflict between PRD §7.5 S4 and Appendix C1
Resolved for P0 by user (1 Oct 2026): follow Appendix C1 / TR-REC-6; billing beyond the PO cap is QTY_OVER_PO plus QTY_OVER_GRN.
No longer blocks M1
OQ-3 Should a new GRN re-evaluate Pending cases on its own
while other cases wait for Re-run?
Yes for Pending only, as the PRD says (§3.4) M1
OQ-4 Is GST on at-risk amounts shown separately at the PO tax
rate?
Yes, beside At-risk ₹ and not inside it (TR-REC13)
M1
OQ-5 How are suspected duplicates (same vendor and amount,
dates within 3 days, similar number) treated? A wrong
Duplicate blocks a real invoice; a missed one risks double
payment.
Needs Review with new code
DUP_SUSPECTED, still consuming balance until
a person decides. Exact duplicates stay
Duplicate (TR-REC-3)
M1
OQ-6 Does Duplicate or Unlinked invoice value count toward Atrisk ₹?
No; show it as separate blocked value (§11.3) M3

---

<!-- Page 27 -->
ID Question Proposed default Needed by
OQ-7 One role in P0, or login with three roles from the start?
(PRD question 8)
Login required, one role in P0, the matrix in P1
(§9.2)
M2
OQ-8 Approve three reason codes the PRD catalogue lacks:
DUP_SUSPECTED, ITEM_UNSURE (the 0.60 to 0.80
alignment band has no code) and NO_TEXT_LAYER (scans
in P0).
Add all three at level Review (Appendix B) M1
OQ-9 Which statutory retention periods and data-protection
duties apply to stored documents?
Finance and legal set the periods; retention is
configurable (TR-DAT-7)
M3
### 14.4 PRD questions that gate the plan
PRD question What it decides here Needed by
1, 2: sample XML, CSV and PDFs Parser profiles, the golden set, extraction accuracy M0
3: does the GRN carry the invoice or challan
number?
How often ladder step 2 can link M1
4: tolerances; hold or pay at PO rate Resolved for build (1 Oct 2026): PRD defaults; both modes supported; `pay_at_po_rate` default
5: one buyer GSTIN or several P0 assumes Benchmark / one workspace; actual GSTIN value(s) and count remain pending
6: monthly volumes and maximum PDF pages Sizing, limits and load-test data M2
7: do invoices carry an e-invoice IRN Strength of the duplicate key M1
8: users, roles, login OQ-7 M2
9: cloud or on-prem; may documents go to
an AI provider
Shape A or B, region and the AI consent text M2
10: which AI providers first Which adapter to build first M3
11: credit notes, service and non-PO invoices
in v1
Schema scope and the status model M1
## 15. Appendix
A. Canonical schema (draft)
Derived from PRD §7.2, §9 and Appendix A. Reconcile it against the All Fields Reference sheet in M0. Fields
marked new are the additions the PRD proposes. Every field is stored as a value with a confidence (0 to 1), a
source (rule, ai or manual) and evidence (page, bounding box, snippet).
Header fields
Field Applies to Type Notes
doc_type, doc_no,
doc_date
All enum, text, date A canonical document number is stored beside doc_no
(TR-NRM-7)
revision, status PO text, enum New. The latest revision governs the ledger; status
(open, closed, cancelled) drives PO_NOT_OPEN
vendor_name All text As printed; the normalized name is derived. Tally POs
carry a ledger name, resolved through the vendor alias
table
vendor_gstin,
buyer_gstin
All; PO and invoice text Validated, with PAN and state code derived. Both are
new on PO
ship_to, payment_terms,
delivery_date
PO text, days, date ship_to and payment_terms are new. Terms feed
the payment due date and the delivery date feeds
LATE_DELIVERY
po_refs Invoice, GRN list of text Labelled PO references found at header level, using the
PRD Appendix B synonyms
grn_refs, challan_no,
e_way_bill_no, irn
Invoice text New. irn is the strongest duplicate key
supplier_invoice_no,
challan_no
GRN text New. They let ladder step 2 link a GRN to an invoice
place_of_supply, Invoice text, ISO 4217 code New. Currency must match across documents

---

<!-- Page 28 -->
Field Applies to Type Notes
currency
taxable_total, cgst,
sgst, igst, round_off,
grand_total
Invoice, PO decimal (18,2) Inputs to the maths QA and the tax checks
extras All JSON Unknown tags and columns, never dropped
Line fields
Field Applies to Type Notes
line_no, line_type All integer, enum line_type (goods, charge) is new on invoice; charge
lines skip item and quantity checks
description, item_code,
hsn_sac
All text item_code and hsn are new on PO and on GRN lines
uom All text Canonical unit through the alias table
quantity All decimal (18,4) On a GRN with no accepted quantity it is treated as
received (PRD Appendix A, gap 6)
received_qty,
rejected_qty,
accepted_qty
GRN decimal (18,4) Accepted, else received minus rejected, else received
with a warning
rate, discount, amount PO, invoice decimal (18,4), (18,2),
(18,2)
The net unit rate is derived as amount divided by
quantity, after discount and before tax
tax_rate and tax amounts PO, invoice decimal (5,2), (18,2) Checked against the effective-dated slab list
po_ref, po_line_ref Invoice lines, GRN
lines
text po_line_ref is new on GRN. Line-level references
support multi-PO invoices
B. Reason-code levels and status effect
A case's status is a pure function of its reason codes and their levels, following the S7 precedence. Adding a code
is a data change reviewed against the golden set, not a code change. Each catalogue entry stores code, level,
stage (S1 to S7), message, suggested action and whether it can be overridden, and is served by GET /reasoncodes. Bold codes are proposed additions to the PRD catalogue (OQ-8).
Level Status effect Codes
Block Duplicate for DUP_INVOICE; Unlinked
for NO_PO_LINK
DUP_INVOICE, NO_PO_LINK
Review Needs Review LINK_INFERRED, LOW_CONFIDENCE_DATA, DUP_SUSPECTED, ITEM_UNSURE,
NO_TEXT_LAYER
Fail Exception VENDOR_MISMATCH, PO_NOT_OPEN, ITEM_NOT_ON_PO, QTY_OVER_PO,
QTY_OVER_GRN, RATE_HIGH, TOTAL_MISMATCH, MATH_ERROR,
PO_VALUE_EXCEEDED, CURRENCY_DIFF, UOM_MISMATCH, DATE_ORDER
(configurable to Warn)
Pending Pending GRN_PENDING
Warn Matched (flags) RATE_LOW, TAX_RATE_DIFF, TAX_TYPE_MISMATCH, HSN_DIFF,
GSTIN_INVALID, EXTRA_CHARGE, GRN_OVER_PO
Info None, stays Matched LATE_DELIVERY
C. Glossary
Term Meaning
Case One invoice with the PO and GRN evidence linked to it; the unit of verdict
Natural key Hash of document type, vendor identity as read, canonical number and date; makes ingestion idempotent
Layout signature Hash of a file's structure (XML tag set or CSV header set), used to remember a profile or column mapping
PO-line ledger Ordered, accepted-received, billed and open quantity per PO line; recomputed and never edited
Dirty scope A vendor or PO marked for recompute by a change
Run manifest The document, link and config versions a run used, with a hash; enough to replay the run
Rules hash Hash of the check registry and config; it changes when any rule or setting changes
Engine version Release number of reconbench_core, bumped whenever the rules hash changes

---

<!-- Page 29 -->
Term Meaning
Golden set Hand-labelled documents and truth used as the release gate
Straight-through Matched with no human action
D. Sources
Source Used for
Tally Solutions: New GST rate structure 2025 Historical context for the earlier TRD departure 9 only; it does not set the P0 seed after the user-confirmed PRD choice
ReconBench PRD v0.1 (draft), 29 Sep 2026 Every requirement, threshold and scenario in this document
Library licences, versions and behaviours in §4 come from the author's knowledge and were not looked up, so
treat them as unverified until the M0 dependency review.
