"use client";

import { BadgeCheck, CalendarClock, MessageSquare, PenLine, Send } from "lucide-react";
import { useCallback, useRef, useState } from "react";

import { api, ApiError, unwrap } from "@/lib/api/client";
import type {
  ApprovalDecision, ConversationPreview, ConversationRunView, Message, PersonaId,
} from "@/lib/api/client";
import { useResource } from "@/lib/use-resource";

import { Evidence } from "./approval";
import { Loading, Notice } from "./ui";

const INTENT_LABELS = {
  question: "Project question",
  negotiation: "Negotiation",
  interview_request: "Interview request",
  scam_signal: "Suspicious message",
};

type InterviewSlot = { starts_at: string; ends_at: string };

function ReplyApproval({
  pending, busy, unavailable, onDecision,
}: {
  pending: ConversationPreview;
  busy: boolean;
  unavailable: boolean;
  onDecision: (value: ApprovalDecision) => void;
}) {
  const [mode, setMode] = useState<"review" | "edit" | "reject">("review");
  const [text, setText] = useState(pending.draft.body);
  const [reason, setReason] = useState("");
  const digest = pending.proposal_digest;
  const disabled = busy || unavailable;

  return (
    <section className="approval" aria-labelledby="reply-approval-title">
      <div className="card-heading approval-heading">
        <div className="section-icon approval-icon"><MessageSquare size={19} aria-hidden="true" /></div>
        <div>
          <h3 id="reply-approval-title">Review reply</h3>
          <p>{INTENT_LABELS[pending.intent.category]} · Reply draft {pending.draft_version}</p>
        </div>
      </div>
      <p className="muted">{pending.intent.explanation}</p>
      {pending.error && <Notice>{pending.error}</Notice>}

      {mode === "edit" ? (
        <form className="review-form" onSubmit={(event) => {
          event.preventDefault();
          onDecision({ action: "edit", proposal_digest: digest, text });
        }}>
          <label htmlFor="revised-reply">Revised reply</label>
          <textarea id="revised-reply" rows={7} maxLength={5000} value={text} onChange={(event) => setText(event.target.value)} />
          <div className="actions">
            <button type="submit" className="button primary" disabled={disabled}>Recheck reply</button>
            <button type="button" className="button secondary" disabled={busy} onClick={() => setMode("review")}>Cancel</button>
          </div>
        </form>
      ) : <div className="proposal-copy"><h4>Message to send</h4><p>{pending.draft.body}</p></div>}

      <div className="approval-evidence"><h4>Claim evidence</h4><Evidence reports={pending.claim_verification} /></div>

      {mode === "reject" && (
        <form className="review-form" onSubmit={(event) => {
          event.preventDefault();
          onDecision({ action: "reject", proposal_digest: digest, reason });
        }}>
          <label htmlFor="reply-rejection-reason">Rejection reason</label>
          <textarea id="reply-rejection-reason" rows={3} maxLength={2000} value={reason} onChange={(event) => setReason(event.target.value)} />
          <div className="actions">
            <button type="submit" className="button danger" disabled={disabled}>Reject reply</button>
            <button type="button" className="button secondary" disabled={busy} onClick={() => setMode("review")}>Cancel</button>
          </div>
        </form>
      )}

      {mode === "review" && (
        <div className="actions">
          <button className="button primary" disabled={disabled} onClick={() => onDecision({ action: "approve", proposal_digest: digest })}>
            <Send size={15} aria-hidden="true" /> Approve and send
          </button>
          <button className="button secondary" disabled={disabled} onClick={() => setMode("edit")}><PenLine size={15} aria-hidden="true" /> Edit reply</button>
          <button className="button ghost" disabled={disabled} onClick={() => setMode("reject")}>Reject</button>
        </div>
      )}
    </section>
  );
}

function DraftReply({ busy, unavailable, resumable, onStart }: {
  busy: boolean;
  unavailable: boolean;
  resumable: boolean;
  onStart: (slots: InterviewSlot[]) => void;
}) {
  const [startsAt, setStartsAt] = useState("");
  const [endsAt, setEndsAt] = useState("");
  const [error, setError] = useState("");

  return (
    <form className="review-form" onSubmit={(event) => {
      event.preventDefault();
      setError("");
      if (resumable || (!startsAt && !endsAt)) {
        onStart([]);
      } else if (!startsAt || !endsAt || !(Date.parse(endsAt) > Date.parse(startsAt))) {
        setError("Enter a start and an end time. The end must be later than the start.");
      } else {
        onStart([{ starts_at: new Date(startsAt).toISOString(), ends_at: new Date(endsAt).toISOString() }]);
      }
    }}>
      {error && <Notice>{error}</Notice>}
      {!resumable && (
        <details className="reply-availability">
          <summary><CalendarClock size={15} aria-hidden="true" /> Offer an interview time</summary>
          <div className="reply-availability-fields">
            <label htmlFor="slot-start">Start (local time)<input id="slot-start" type="datetime-local" value={startsAt} onChange={(event) => setStartsAt(event.target.value)} /></label>
            <label htmlFor="slot-end">End (local time)<input id="slot-end" type="datetime-local" value={endsAt} onChange={(event) => setEndsAt(event.target.value)} /></label>
          </div>
        </details>
      )}
      <div className="actions">
        <button type="submit" className="button primary" disabled={busy || unavailable}>
          <PenLine size={16} aria-hidden="true" /> {busy ? "Preparing reply…" : resumable ? "Resume reply" : "Draft reply"}
        </button>
      </div>
    </form>
  );
}

export function ConversationReview({
  persona, threadId, latest, refresh, unavailable, onRefresh,
}: {
  persona: PersonaId;
  threadId: string;
  latest?: Message;
  refresh: number;
  unavailable: boolean;
  onRefresh: () => void;
}) {
  const [result, setResult] = useState<{ run: ConversationRunView; revision: number }>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const loadRun = useCallback((signal: AbortSignal) => api.GET("/api/v1/messages/{thread_id}/run", {
    signal, params: { path: { thread_id: threadId }, query: { persona } },
  }).then(unwrap), [persona, threadId]);
  const remote = useResource(`${persona}:${threadId}`, refresh, loadRun);
  const run = result && (remote.revision ?? -1) < result.revision ? result.run : remote.data;

  async function mutate(payload: { availability: InterviewSlot[] } | ApprovalDecision) {
    if (inFlight.current || unavailable) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    const params = { path: { thread_id: threadId }, query: { persona } };
    try {
      const run = "action" in payload
        ? unwrap(await api.POST("/api/v1/messages/{thread_id}/decision", { params, body: payload }))
        : unwrap(await api.POST("/api/v1/messages/{thread_id}/run", { params, body: payload }));
      setResult({ run, revision: refresh + 1 });
      onRefresh();
    } catch (error: unknown) {
      setError(error instanceof Error ? error.message : "The reply action could not be completed.");
      if (error instanceof ApiError && [409, 503].includes(error.status)) onRefresh();
    } finally {
      setBusy(false);
      inFlight.current = false;
    }
  }

  if (!run) {
    if (unavailable) return null;
    if (remote.error) return <Notice title="Could not load reply review">{remote.error}</Notice>;
    return <Loading>Loading reply review…</Loading>;
  }

  const handledId = run.outcome || run.approval_status === "rejected" || run.status === "skipped"
    ? run.incoming?.id : undefined;
  const canDraft = !run.pending && (run.resumable || (
    latest?.sender === "client" && latest.id !== handledId && run.status !== "running"
  ));
  const problems = [...new Set([...(run.errors ?? []), ...(run.compliance_verdict?.reasons ?? [])])];

  return (
    <div className="conversation-review">
      {error && <Notice title="Reply action failed">{error}</Notice>}
      {remote.error && !unavailable && <Notice title="Could not refresh reply review">{remote.error}</Notice>}
      {problems.length > 0 && <Notice title={run.intent?.category === "scam_signal" ? "Suspicious message" : "Reply needs attention"}>
        <ul>{problems.map((problem) => <li key={problem}>{problem}</li>)}</ul>
      </Notice>}
      {run.pending && <ReplyApproval key={run.pending.proposal_digest} pending={run.pending} busy={busy} unavailable={unavailable} onDecision={(decision) => void mutate(decision)} />}
      {run.outcome && <div className="outcome" role="status"><BadgeCheck size={20} aria-hidden="true" /><strong>Reply sent</strong></div>}
      {run.approval_status === "rejected" && <div className="outcome outcome--neutral" role="status"><strong>Reply rejected</strong><p>{run.approval_decision?.reason}</p></div>}
      {canDraft && <DraftReply key={latest?.id ?? "resume"} busy={busy} unavailable={unavailable} resumable={run.resumable ?? false} onStart={(availability) => void mutate({ availability })} />}
      {!canDraft && run.status === "running" && <p role="status">Preparing reply…</p>}
    </div>
  );
}
