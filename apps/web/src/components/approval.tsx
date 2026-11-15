import { Check, ClipboardCheck, Coins, FileText, Pencil, ShieldCheck, X } from "lucide-react";
import { useState } from "react";

import type { ApprovalDecision, PendingApproval, RunView } from "@/lib/api/client";
import { money } from "@/lib/format";

import { Notice } from "./ui";

export function Evidence({ reports = [] }: { reports: RunView["claim_verification"] }) {
  if (reports.length === 0) return <p className="muted">No portfolio claims require evidence.</p>;
  return (
    <div className="evidence-list">
      {reports.map((report, index) => (
        <details className="evidence" key={`${report.claim}-${index}`}>
          <summary title={(report.evidence_ids ?? []).join(", ")}>
            {report.supported ? <ShieldCheck size={16} /> : <X size={16} />}
            <span>{report.claim || `Claim ${index + 1}`}</span>
          </summary>
          <div className="evidence-detail">
            <p>{report.claim}</p>
            {(report.evidence_ids ?? []).length > 0 && (
              <div className="tags">{report.evidence_ids?.map((id) => <code key={id}>{id}</code>)}</div>
            )}
          </div>
        </details>
      ))}
    </div>
  );
}

export function Approval({
  pending,
  busy,
  unavailable,
  onDecision,
}: {
  pending: PendingApproval;
  busy: boolean;
  unavailable: boolean;
  onDecision: (decision: ApprovalDecision) => void;
}) {
  const [mode, setMode] = useState<"review" | "edit" | "reject">("review");
  const [text, setText] = useState(pending.submission.cover_letter);
  const [reason, setReason] = useState("");
  const { submission, preview } = pending;
  const quote = `${money(submission.bid_amount)}${submission.bid_type === "hourly" ? "/hr" : ""}`;
  const digest = pending.proposal_digest;
  const blocked = busy || unavailable;

  return (
    <section className="approval" aria-labelledby="approval-title">
      <div className="card-heading approval-heading">
        <div className="section-icon approval-icon"><ClipboardCheck size={20} /></div>
        <div><h3 id="approval-title">Review proposal</h3><p>Draft {pending.draft_version} · Waiting for your decision</p></div>
      </div>

      <div className="quote-grid" aria-label="Submission quote">
        <div><span><FileText size={14} /> Bid</span><strong>{quote}</strong></div>
        <div><span><Coins size={14} /> Connects cost</span><strong>{preview.connects_cost}</strong></div>
        <div><span>Connects balance</span><strong>{preview.balance} <span aria-label="to">→</span> {preview.balance_after}</strong></div>
      </div>
      {pending.price_quote.rationale && <p className="quote-note">{pending.price_quote.rationale}</p>}
      {submission.duration_days != null && <p className="quote-note">Estimated duration {submission.duration_days} days</p>}
      <p className="quote-note">Simulator account <code>{preview.account_id}</code></p>

      {pending.error && <Notice>{pending.error}</Notice>}
      {mode === "edit" ? (
        <form className="review-form" onSubmit={(event) => {
          event.preventDefault();
          onDecision({ action: "edit", proposal_digest: digest, text });
        }}>
          <label htmlFor="draft-edit">Revised cover letter</label>
          <textarea id="draft-edit" maxLength={5000} value={text} onChange={(event) => setText(event.target.value)} rows={10} />
          <div className="actions">
            <button type="submit" className="button primary" disabled={blocked}>Recheck draft</button>
            <button type="button" className="button secondary" onClick={() => setMode("review")} disabled={busy}>Cancel</button>
          </div>
        </form>
      ) : (
        <div className="proposal-copy">
          <h4>Cover letter</h4>
          <p>{submission.cover_letter}</p>
        </div>
      )}
      {(submission.screening_answers ?? []).length > 0 && (
        <div className="proposal-copy">
          <h4>Screening answers</h4>
          <ol>{submission.screening_answers?.map((answer, index) => <li key={`${index}-${answer}`}>{answer}</li>)}</ol>
        </div>
      )}
      <div className="approval-evidence"><h4>Claim evidence</h4><Evidence reports={pending.claim_verification} /></div>

      {mode === "reject" && (
        <form className="review-form" onSubmit={(event) => {
          event.preventDefault();
          onDecision({ action: "reject", proposal_digest: digest, reason });
        }}>
          <label htmlFor="reject-reason">Rejection reason</label>
          <textarea id="reject-reason" maxLength={2000} value={reason} onChange={(event) => setReason(event.target.value)} rows={3} />
          <div className="actions">
            <button type="submit" className="button danger" disabled={blocked}>Reject proposal</button>
            <button type="button" className="button secondary" onClick={() => setMode("review")} disabled={busy}>Cancel</button>
          </div>
        </form>
      )}

      {mode === "review" && (
        <div className="actions approval-actions">
          <button className="button primary" disabled={blocked} onClick={() => onDecision({ action: "approve", proposal_digest: digest })}>
            <Check size={17} /> Approve and submit
          </button>
          <button className="button secondary" disabled={busy} onClick={() => setMode("edit")}><Pencil size={15} /> Edit draft</button>
          <button className="button ghost" disabled={busy} onClick={() => setMode("reject")}>Reject</button>
        </div>
      )}
    </section>
  );
}
