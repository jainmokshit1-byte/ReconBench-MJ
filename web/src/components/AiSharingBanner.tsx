import { useEffect, useState } from "react";
import { Sparkles } from "lucide-react";

import { getAiSharingSummary } from "../api/ai";
import type { AiSharingSummary } from "../api/ai";

const providerNames: Record<string, string> = { gemini: "Google Gemini" };

function sharingText(summary: AiSharingSummary): string {
  if (summary.external_calls === "disabled") {
    return "External calls are disabled on this deployment, so no document data leaves ReconBench.";
  }
  if (summary.external_calls === "synthetic_only") {
    return "External calls are limited to built-in synthetic test fixtures; uploaded documents are not sent.";
  }
  const provider = summary.provider ? providerNames[summary.provider] ?? summary.provider : "the AI provider";
  const model = summary.model ? ` (${summary.model})` : "";
  return `Eligible document text is redacted and sent to ${provider}${model}.`;
}

/** Shown on review screens whenever AI Assist is on; nothing is shown in rules-only mode. */
export function AiSharingBanner() {
  const [summary, setSummary] = useState<AiSharingSummary | null>(null);

  useEffect(() => {
    let active = true;
    getAiSharingSummary()
      .then((value) => { if (active) setSummary(value); })
      .catch(() => { if (active) setSummary(null); });
    return () => { active = false; };
  }, []);

  if (!summary || summary.mode === "rules_only") return null;
  return (
    <div className="ai-sharing-banner" role="note" aria-label="AI external sharing notice">
      <Sparkles size={15} aria-hidden="true" />
      <div>
        <strong>AI Assist is on: {summary.mode_label}</strong>
        <span>{sharingText(summary)} AI output is advisory; nothing changes a document, link or verdict until a person confirms it.</span>
        {summary.status.label ? <small>Status: {summary.status.label}</small> : null}
      </div>
    </div>
  );
}
