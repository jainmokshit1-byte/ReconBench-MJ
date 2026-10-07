const opinionText: Record<string, string> = { same: "same item", different: "different item", unsure: "unsure" };

/** H2 advisory opinion for an uncertain line; it never changes the line or case result. */
export function AiLineOpinion({ value }: { value: unknown }) {
  if (typeof value !== "object" || value === null) return null;
  const opinion = value as Record<string, unknown>;
  const state = opinion["state"];
  if (state === "pending") return <small className="ai-line-opinion">AI opinion pending (advisory)</small>;
  if (state === "unavailable") return <small className="ai-line-opinion">AI opinion unavailable{typeof opinion["code"] === "string" ? ` (${opinion["code"]})` : ""}</small>;
  if (state !== "available") return null;
  const verdict = typeof opinion["opinion"] === "string" ? opinionText[opinion["opinion"]] ?? opinion["opinion"] : "no opinion";
  return <small className="ai-line-opinion"><span className="ai-advisory-tag">AI opinion · advisory</span> Likely {verdict}{typeof opinion["reason"] === "string" && opinion["reason"] ? `: ${opinion["reason"]}` : ""}</small>;
}
