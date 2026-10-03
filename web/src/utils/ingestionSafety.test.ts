import { describe, expect, it } from "vitest";

import { PDF_RENDER_OPTIONS } from "./pdfRenderOptions";
import { parseCsvPreviewRows } from "./csvPreview";

describe("ingestion preview safety", () => {
  it("parses quoted CSV columns and keeps embedded delimiters inside their cells", () => {
    expect(parseCsvPreviewRows('"Invoice No";Vendor;Description\r\nINV-1;"North; South";"Widget, blue"\r\n')).toEqual([
      ["Invoice No", "Vendor", "Description"],
      ["INV-1", "North; South", "Widget, blue"],
    ]);
  });

  it("disables PDF JavaScript evaluation and automatic whole-file prefetch", () => {
    expect(PDF_RENDER_OPTIONS.isEvalSupported).toBe(false);
    expect(PDF_RENDER_OPTIONS.disableAutoFetch).toBe(true);
    expect(PDF_RENDER_OPTIONS.disableStream).toBe(true);
  });
});
