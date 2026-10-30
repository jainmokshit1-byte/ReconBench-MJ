import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DocumentDetails } from "../api/ingestion";
import { ReadingChip, ReadingStatus } from "./ReadingStatus";
import { readingFailureText } from "./readingFailure";

const retryReading = vi.fn();
const readWithRules = vi.fn();

vi.mock("../api/ingestion", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/ingestion")>();
  return {
    ...actual,
    retryReading: (...args: unknown[]) => retryReading(...args),
    readWithRules: (...args: unknown[]) => readWithRules(...args),
  };
});

afterEach(() => {
  cleanup();
  retryReading.mockReset();
  readWithRules.mockReset();
});

function details(document: Record<string, unknown>): DocumentDetails {
  return { document: { id: "doc-1", version: 7, doc_type: "invoice", ...document } };
}

describe("ReadingStatus (PD-2f)", () => {
  it("renders nothing for a ready document or a server that sends no reading state", () => {
    const { container: ready } = render(<ReadingStatus details={details({ reading_state: "ready" })} csrfToken="csrf" onSaved={vi.fn()} />);
    expect(ready.innerHTML).toBe("");
    cleanup();
    const { container: older } = render(<ReadingStatus details={details({})} csrfToken="csrf" onSaved={vi.fn()} />);
    expect(older.innerHTML).toBe("");
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
