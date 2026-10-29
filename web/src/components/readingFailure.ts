/** Plain words for the stated reasons an AI reading failed (PD-2f). */
const FAILURE_TEXT: Record<string, string> = {
  AI_READING_NOT_QUEUED: "the reading could not be started (AI reading is off or the upload's call limit was reached)",
  AI_READING_NOT_ELIGIBLE: "the AI reader can't take this file (too many pages)",
  AI_READING_PAGES_FAILED: "the page images could not be prepared",
  VISION_PAGES_FAILED: "the page images could not be prepared",
  AI_READING_ANSWER_INVALID: "the answer did not pass the checks",
  invalid_output: "the answer did not pass the checks",
  AI_READING_MERGE_FAILED: "the answer could not be turned into a version",
  AI_READING_STALLED: "nothing was reading it any more",
  error: "the company server returned an error",
  timeout: "the company server did not answer in time",
  PROVIDER_UNAVAILABLE: "the company server was unavailable",
  rate_limited: "the company server was busy",
  MODEL_UNAVAILABLE: "the model is not available on the company server",
  DAY_BUDGET_REACHED: "today's AI call limit was reached",
  MONTH_BUDGET_REACHED: "this month's AI call limit was reached",
  OPERATION_BUDGET_REACHED: "the upload's AI call limit was reached",
  QUEUE_TIMEOUT: "it waited too long in the queue",
  INPUT_TOO_LARGE: "the pages are too large for the AI reader",
  LEASE_EXPIRED: "the company server did not finish (the answer may or may not have been made)",
};

export function readingFailureText(code: string | null): string {
  if (!code) return "the reason was not recorded";
  return FAILURE_TEXT[code] ?? `the reading stopped (${code})`;
}
