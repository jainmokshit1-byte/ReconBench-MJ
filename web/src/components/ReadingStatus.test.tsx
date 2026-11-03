import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DocumentDetails } from "../api/ingestion";
import { ReadingChip, ReadingStatus } from "./ReadingStatus";
import { readingFailureText } from "./readingFailure";

const retryReading = vi.fn();
const readWithRules = vi.fn();
const rereadWithRules = vi.fn();

vi.mock("../api/ingestion", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/ingestion")>();
  return {
    ...actual,
    retryReading: (...args: unknown[]) => retryReading(...args),
    readWithRules: (...args: unknown[]) => readWithRules(...args),
    rereadWithRules: (...args: unknown[]) => rereadWithRules(...args),
  };
});

afterEach(() => {
  cleanup();
  retryReading.mockReset();
  readWithRules.mockReset();
  rereadWithRules.mockReset();
});

function details(document: Record<string, unknown>): DocumentDetails {
  return { document: { id: "doc-1", version: 7, doc_type: "invoice", ...document } };
}

describe("ReadingStatus (PD-2f)", () => {
  it("renders nothing when the server sends no reading state", () => {
    const { container: older } = render(<ReadingStatus details={details({})} csrfToken="csrf" onSaved={vi.fn()} />);
    expect(older.innerHTML).toBe("");
  });

  it("offers only a subtle Read again with rules for a ready document", () => {
    render(<ReadingStatus details={details({ reading_state: "ready" })} csrfToken="csrf" onSaved={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Read again with rules" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Read with rules" })).toBeNull();
    expect(screen.queryByText(/replaces the current values/)).toBeNull();
  });

  it("does not offer it for other document types", () => {
    const { container } = render(<ReadingStatus details={details({ reading_state: "ready", doc_type: "credit_note" })} csrfToken="csrf" onSaved={vi.fn()} />);
    expect(container.innerHTML).toBe("");
  });

  it("shows a waiting invoice with Read with rules only", () => {
    render(<ReadingStatus details={details({ reading_state: "waiting", reading_failure_code: null })} csrfToken="csrf" onSaved={vi.fn()} />);
    expect(screen.getByText("Waiting for reading")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(screen.getByRole("button", { name: "Read with rules" })).toBeTruthy();
  });

  it("states a failure in plain words and retries with the loaded version", async () => {
    const saved = details({ reading_state: "waiting", version: 8 });
    retryReading.mockResolvedValue(saved);
    const onSaved = vi.fn();
    render(<ReadingStatus details={details({ reading_state: "failed", reading_failure_code: "AI_READING_ANSWER_INVALID" })} csrfToken="csrf" onSaved={onSaved} />);

    expect(screen.getByText("AI reading failed: the answer did not pass the checks")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved));
    expect(retryReading).toHaveBeenCalledWith("doc-1", 7, "csrf");
    expect(readWithRules).not.toHaveBeenCalled();
  });

  it("chooses the rules reading and shows the server's refusal as an alert", async () => {
    readWithRules.mockRejectedValue(new Error("This invoice's reading changed; reload the page."));
    render(<ReadingStatus details={details({ reading_state: "failed", reading_failure_code: "error" })} csrfToken="csrf" onSaved={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Read with rules" }));

    expect((await screen.findByRole("alert")).textContent).toBe("This invoice's reading changed; reload the page.");
    expect(readWithRules).toHaveBeenCalledWith("doc-1", 7, "csrf");
  });

  it("asks to confirm, then reads again with the loaded version and refreshes the document", async () => {
    const saved = details({ reading_state: "ready", version: 8 });
    rereadWithRules.mockResolvedValue(saved);
    const onSaved = vi.fn();
    render(<ReadingStatus details={details({ reading_state: "ready" })} csrfToken="csrf" onSaved={onSaved} />);

    fireEvent.click(screen.getByRole("button", { name: "Read again with rules" }));
    expect(screen.getByText("This replaces the current values with a fresh rules reading. Earlier versions are kept.")).toBeTruthy();
    expect(rereadWithRules).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Read again" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved));
    expect(rereadWithRules).toHaveBeenCalledWith("doc-1", 7, "csrf");
    expect(retryReading).not.toHaveBeenCalled();
    expect(readWithRules).not.toHaveBeenCalled();
  });

  it("can be cancelled without calling the server, and shows a refusal as an alert", async () => {
    rereadWithRules.mockRejectedValue(new Error("The rules now find a different number of documents in this file."));
    render(<ReadingStatus details={details({ reading_state: "ready" })} csrfToken="csrf" onSaved={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Read again with rules" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: "Read again with rules" })).toBeTruthy();
    expect(rereadWithRules).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Read again with rules" }));
    fireEvent.click(screen.getByRole("button", { name: "Read again" }));
    expect((await screen.findByRole("alert")).textContent).toBe("The rules now find a different number of documents in this file.");
  });

  it("disables Read again with rules without a session", () => {
    render(<ReadingStatus details={details({ reading_state: "ready" })} csrfToken={null} onSaved={vi.fn()} />);
    expect((screen.getByRole("button", { name: "Read again with rules" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("disables both choices without a session", () => {
    render(<ReadingStatus details={details({ reading_state: "failed", reading_failure_code: "timeout" })} csrfToken={null} onSaved={vi.fn()} />);
    for (const name of ["Retry", "Read with rules"]) {
      expect((screen.getByRole("button", { name }) as HTMLButtonElement).disabled).toBe(true);
    }
  });

  it("never shows a raw code without words", () => {
    expect(readingFailureText(null)).toBe("the reason was not recorded");
    expect(readingFailureText("SOMETHING_NEW")).toBe("the reading stopped (SOMETHING_NEW)");
  });
});

describe("ReadingChip (Task C)", () => {
  it("renders chip for waiting, failed, ready, and absent states", () => {
    // waiting
    const { container: waiting } = render(
      <ReadingChip document={{ reading_state: "waiting", reading_failure_code: null }} />
    );
    expect(waiting.textContent).toContain("Waiting for reading");
    expect(waiting.querySelector(".status-chip.status-progress")).toBeTruthy();
    cleanup();

    // failed
    const { container: failed } = render(
      <ReadingChip document={{ reading_state: "failed", reading_failure_code: "AI_READING_ANSWER_INVALID" }} />
    );
    expect(failed.textContent).toContain("Reading failed: the answer did not pass the checks");
    expect(failed.querySelector(".status-chip.status-danger")).toBeTruthy();
    cleanup();

    // ready: renders nothing
    const { container: ready } = render(
      <ReadingChip document={{ reading_state: "ready", reading_failure_code: null }} />
    );
    expect(ready.innerHTML).toBe("");
    cleanup();

    // absent: renders nothing
    const { container: absent } = render(
      <ReadingChip document={{}} />
    );
    expect(absent.innerHTML).toBe("");
    cleanup();

    const { container: absentNull } = render(
      <ReadingChip document={null} />
    );
    expect(absentNull.innerHTML).toBe("");
  });
});
