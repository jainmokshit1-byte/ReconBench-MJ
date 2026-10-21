import { describe, expect, it } from "vitest";

import { pdfHighlightStyle } from "./pdfEvidence";

describe("pdfHighlightStyle", () => {
  it("maps normalized vision boxes to percentages", () => {
    expect(
      pdfHighlightStyle(
        { page: 1, bbox: [100, 200, 400, 800], source: "vision", ai_call_id: "call-1" },
        [0, 0, 612, 792],
      ),
    ).toEqual({ left: "20%", top: "10%", width: "60%", height: "30%" });
  });

  it("maps PDF point boxes using the page view and clips to page edges", () => {
    expect(
      pdfHighlightStyle(
        { page: 1, bbox: [-10, 99, 210, 299], source: "ocr", ai_call_id: null },
        [0, 0, 200, 300],
      ),
    ).toEqual({ left: "0%", top: "33%", width: "100%", height: "66.6667%" });
  });

  it("ignores invalid or empty boxes", () => {
    expect(
      pdfHighlightStyle(
        { page: 1, bbox: [100, 200, 100, 300], source: "vision", ai_call_id: "call-1" },
        null,
      ),
    ).toBeNull();
    expect(
      pdfHighlightStyle(
        { page: 1, bbox: [0, 0, Number.NaN, 1], source: "vision", ai_call_id: "call-1" },
        null,
      ),
    ).toBeNull();
  });
});
