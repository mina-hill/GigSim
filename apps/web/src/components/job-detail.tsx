import { ArrowLeft, BadgeCheck, BriefcaseBusiness, Coins, MapPin, ScanLine, Shield, Target } from "lucide-react";

import type { ApprovalDecision, JobRecord, RunView } from "@/lib/api/client";
import { budget, domainLabel, friendly, shortDate } from "@/lib/format";

import { Approval, Evidence } from "./approval";
import { Notice, Status } from "./ui";

const percent = (value: number) => `${Math.round(value * 100)}%`;

function Analysis({ run }: { run: RunView }) {
  if (!run.fit_report && !run.risk_report && !run.decision) return null;
  return (
    <section className="detail-section">
      <h3>Bid analysis</h3>
      <div className="analysis-grid">
        {run.fit_report && (
          <article className="analysis-tile">
            <span className="tile-label"><Target size={16} /> Portfolio fit</span>
            <strong>{percent(run.fit_report.score)}</strong>
            {run.fit_report.matched_skills?.length ? <p>Matched {run.fit_report.matched_skills.join(", ")}</p> : <p>No matching skills</p>}
            {!!run.fit_report.missing_skills?.length && <p>Missing {run.fit_report.missing_skills.join(", ")}</p>}
          </article>
        )}
        {run.risk_report && (
          <article className="analysis-tile">
            <span className="tile-label"><Shield size={16} /> Job risk</span>
            <strong>{percent(run.risk_report.score)}</strong>
            <p>{run.risk_report.explanation}</p>
            {!!run.risk_report.flags?.length && <div className="tags">{run.risk_report.flags.map((flag) => <span key={flag} className="tag">{friendly(flag)}</span>)}</div>}
          </article>
        )}
      </div>
      {run.decision && <p className="decision-summary"><strong>{friendly(run.decision.action)}</strong> · {run.decision.rule_ids.map(friendly).join(", ")}</p>}
      {!!run.critique?.feedback?.length && <div className="feedback"><h4>Critic feedback</h4><ul>{run.critique.feedback.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></div>}
      {!run.pending && !!run.claim_verification?.length && <div className="feedback"><h4>Claim evidence</h4><Evidence reports={run.claim_verification} /></div>}
      {!!run.timeline?.length && (
        <details className="timeline">
          <summary>Run timeline</summary>
          <ol className="timeline-list">
            {run.timeline.map((step) => <li key={step.step}><strong>{step.nodes.map(friendly).join(", ") || "Run"}</strong> · {friendly(step.status)}</li>)}
          </ol>
        </details>
      )}
    </section>
  );
}

export function JobDetail({
  job, run, busy, unavailable, mutationError, onBack, onAnalyze, onDecision,
}: {
  job: JobRecord;
  run: RunView;
  busy: boolean;
  unavailable: boolean;
  mutationError?: string;
  onBack: () => void;
  onAnalyze: () => void;
  onDecision: (decision: ApprovalDecision) => void;
}) {
  const flags = [...(run.compliance_verdict?.reasons ?? []), ...(run.compliance_verdict?.warnings ?? [])];
  const errors = [...flags, ...(run.errors ?? [])];
  return (
    <article className="surface job-detail" aria-label="Job details">
      <div className="detail-header">
        <button className="button ghost mobile-back" onClick={onBack}><ArrowLeft size={15} /> Inbox</button>
        <div className="detail-meta"><span>{domainLabel(job.domain)}</span><span>{shortDate(job.posted_at)}</span><Status value={run.status} /></div>
        <h2>{job.title}</h2>
        <div className="detail-facts">
          <span><BriefcaseBusiness size={16} /> <b>{budget(job)}</b> {friendly(job.payment_type)}</span>
          <span><Coins size={16} /> <b>{job.connects_cost}</b> Connects</span>
          <span>{friendly(job.experience_level)}</span>
        </div>
      </div>
      <div className="detail-content">
        {mutationError && <Notice title="Action failed">{mutationError}</Notice>}
        {run.pending ? (
          <Approval key={run.pending.proposal_digest} pending={run.pending} busy={busy} unavailable={unavailable} onDecision={onDecision} />
        ) : (
          <div className="run-action">
            {run.outcome ? (
              <div className="outcome" role="status"><BadgeCheck size={21} /><div><strong>Proposal submitted</strong><p>{run.outcome.connects_spent} Connects spent in GigSim</p></div></div>
            ) : run.approval_status === "rejected" ? (
              <div className="outcome outcome--neutral" role="status"><Shield size={20} /><div><strong>Proposal rejected</strong><p>{run.approval_decision?.reason}</p></div></div>
            ) : run.status === "idle" || run.resumable ? (
              <button className="button primary" onClick={onAnalyze} disabled={busy || unavailable}>
                <ScanLine size={17} /> {busy ? "Analyzing…" : run.resumable ? "Resume analysis" : "Analyze job"}
              </button>
            ) : run.status === "running" ? <p role="status">Analysis is running…</p> : null}
          </div>
        )}
        {errors.length > 0 && (
          <Notice title={flags.length > 0 ? "Compliance review" : "Run needs attention"}>
            <ul>{errors.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul>
          </Notice>
        )}
        <section className="detail-section">
          <h3>About the job</h3>
          <p className="description">{job.description}</p>
          <div className="tags">{job.skills.map((skill, index) => <span className="tag" key={`${skill}-${index}`}>{skill}</span>)}</div>
          {job.screening_questions.length > 0 && <div className="feedback"><h4>Screening questions</h4><ol>{job.screening_questions.map((question, index) => <li key={`${question}-${index}`}>{question}</li>)}</ol></div>}
        </section>
        <Analysis run={run} />
        <section className="detail-section">
          <h3>About the client</h3>
          <div className="client-facts">
            {job.client.country && <span><MapPin size={16} /> {job.client.country}</span>}
            {job.client.payment_verified && <span className="verified"><BadgeCheck size={17} /> Payment verified</span>}
            {job.client.hire_rate != null && <span>{percent(job.client.hire_rate)} hire rate</span>}
            <span>{job.applicants} {job.applicants === 1 ? "applicant" : "applicants"}</span>
            {job.duration && <span>{job.duration}</span>}
          </div>
        </section>
      </div>
    </article>
  );
}
