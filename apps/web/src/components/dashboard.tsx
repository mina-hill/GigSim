"use client";

import {
  Activity, ArrowLeft, ArrowRight, ArrowUpRight, BriefcaseBusiness, ChevronDown,
  ChevronRight, CircleCheck, Columns3, FlaskConical, Inbox, MessageSquare, RefreshCw, Search,
} from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError, unwrap } from "@/lib/api/client";
import type { ApprovalDecision, JobCard, PersonaId, RunView } from "@/lib/api/client";
import { budget, domainLabel, friendly, money, shortDate } from "@/lib/format";
import { useDebouncedSearch, useResource } from "@/lib/use-resource";

import { ConversationReview } from "./conversation-review";
import { JobDetail } from "./job-detail";
import { Loading, Notice, Status } from "./ui";

const VIEWS = [
  { id: "inbox", label: "Job inbox", icon: Inbox, description: "Find opportunities and review how well they match your portfolio." },
  { id: "pipeline", label: "Pipeline", icon: Columns3, description: "Track your simulated proposals and the Connects you spent." },
  { id: "messages", label: "Messages", icon: MessageSquare, description: "" },
] as const;
const PREFERRED_DOMAIN: Record<PersonaId, string> = {
  fullstack: "fullstack_web", ai_ml: "ai_ml", data_analyst: "data_analysis",
};
const PAGE_SIZE = 50;
const FILTERS = [
  { id: "all", label: "All jobs" },
  { id: "awaiting_approval", label: "Needs review" },
  { id: "skipped", label: "Skipped" },
] as const;
type View = (typeof VIEWS)[number]["id"];
type Filter = (typeof FILTERS)[number]["id"];

const loadPersonas = (signal: AbortSignal) => api.GET("/api/v1/personas", { signal }).then(unwrap);
const loadHealth = (signal: AbortSignal) => api.GET("/health/deps", { signal }).then(unwrap);

function PersonaInitial({ name }: { name: string }) {
  return <span className="avatar" aria-hidden="true">{name.trim().split(/\s+/).slice(0, 2).map((word) => word[0]).join("") || "GB"}</span>;
}

function JobListItem({ job, active, onSelect }: { job: JobCard; active: boolean; onSelect: () => void }) {
  return (
    <button className={`job-card${active ? " job-card--active" : ""}`} aria-current={active ? "true" : undefined} onClick={onSelect}>
      <div className="job-card-meta"><span>{domainLabel(job.domain)}</span><span>{shortDate(job.posted_at)}</span></div>
      <strong>{job.title}</strong>
      <div className="job-card-budget"><span>{budget(job)}</span><span>{friendly(job.payment_type)}</span></div>
      <div className="job-card-bottom"><Status value={job.run_status} />{job.fit_score != null && <span>{Math.round(job.fit_score * 100)}% fit</span>}</div>
    </button>
  );
}

function MessagesEmpty({ persona }: { persona: PersonaId }) {
  return (
    <section className="surface messages-empty" aria-label="Conversations">
      <div className="messages-empty-copy">
        <h2>No client replies yet</h2>
        <p>When a simulated client responds to a proposal, you’ll find the conversation here.</p>
        <Link className="button primary" href={{ pathname: "/", query: { persona, view: "pipeline" } }} scroll={false}>
          View proposal pipeline <ArrowRight size={16} aria-hidden="true" />
        </Link>
      </div>
      <div className="conversation-art" aria-hidden="true">
        <div className="conversation-orbit" />
        <span className="conversation-spark conversation-spark--large" />
        <span className="conversation-spark conversation-spark--small" />
        <div className="conversation-bubble conversation-bubble--back"><MessageSquare size={32} strokeWidth={1.5} /></div>
        <div className="conversation-bubble conversation-bubble--front"><span /><span /><span /></div>
      </div>
    </section>
  );
}

export function Dashboard() {
  const router = useRouter();
  const params = useSearchParams();
  const [refresh, setRefresh] = useState(0);
  const [healthRevision, setHealthRevision] = useState(0);
  const [search, setSearch] = useState("");
  const [cursor, setCursor] = useState(0);
  const [filter, setFilter] = useState<Filter>("all");
  const [result, setResult] = useState<{ key: string; run?: RunView; error?: string; errorStatus?: number; revision: number }>();
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const inFlight = useRef(false);
  const query = useDebouncedSearch(search);
  const requestedPersona = params.get("persona");
  const preferredPersona: PersonaId = requestedPersona === "ai_ml" || requestedPersona === "data_analyst" ? requestedPersona : "fullstack";
  const view = VIEWS.find((item) => item.id === params.get("view")) ?? VIEWS[0];
  const selectedJob = params.get("job");
  const selectedThread = params.get("thread");

  useEffect(() => {
    const interval = window.setInterval(() => setHealthRevision((value) => value + 1), 12_000);
    return () => window.clearInterval(interval);
  }, []);

  const personas = useResource("personas", refresh, loadPersonas);
  const selectedPersona = personas.data?.find((item) => item.id === preferredPersona) ?? personas.data?.[0];
  const persona = selectedPersona?.id ?? preferredPersona;
  const personaOptions = personas.data ?? [];
  const health = useResource("health", healthRevision, loadHealth);

  const fetchJobs = useCallback((signal: AbortSignal) => api.GET("/api/v1/jobs", {
    signal, params: { query: { persona, q: query || undefined, limit: PAGE_SIZE, cursor } },
  }).then(unwrap), [persona, query, cursor]);
  const jobs = useResource(view.id === "inbox" ? `${persona}:${query}:${cursor}` : null, refresh, fetchJobs);
  const rows = jobs.data ?? [];
  const visibleJobs = filter === "all" ? rows : rows.filter((job) => job.run_status === filter);
  const defaultJob = visibleJobs.find((job) => job.domain === PREFERRED_DOMAIN[persona]) ?? visibleJobs[0];
  const jobId = view.id === "inbox" ? selectedJob ?? defaultJob?.job_id ?? null : null;
  const jobKey = jobId ? `${persona}:${jobId}` : null;

  const fetchJob = useCallback((signal: AbortSignal) => api.GET("/api/v1/jobs/{job_id}", {
    signal, params: { path: { job_id: jobId! }, query: { persona } },
  }).then(unwrap), [jobId, persona]);
  const detail = useResource(jobKey, refresh, fetchJob);
  const localResult = result?.key === jobKey ? result : undefined;
  const run = localResult?.run && (detail.revision ?? -1) < localResult.revision ? localResult.run : detail.data?.run;

  useEffect(() => {
    if (run?.status !== "running" || detail.loading || busyKey !== null) return;
    const poll = window.setTimeout(() => setRefresh((value) => value + 1), 3000);
    return () => window.clearTimeout(poll);
  }, [run?.status, detail.loading, detail.revision, busyKey]);

  const fetchPipeline = useCallback((signal: AbortSignal) => api.GET("/api/v1/pipeline", {
    signal, params: { query: { persona } },
  }).then(unwrap), [persona]);
  const pipeline = useResource(view.id === "pipeline" ? persona : null, refresh, fetchPipeline);

  const fetchThreads = useCallback((signal: AbortSignal) => api.GET("/api/v1/messages", {
    signal, params: { query: { persona } },
  }).then(unwrap), [persona]);
  const threads = useResource(view.id === "messages" ? persona : null, refresh, fetchThreads);
  const activeThread = threads.data?.find((thread) => thread.thread_id === selectedThread) ?? threads.data?.[0];
  const threadId = activeThread?.thread_id;
  const fetchMessages = useCallback((signal: AbortSignal) => api.GET("/api/v1/messages/{thread_id}", {
    signal, params: { path: { thread_id: threadId! }, query: { persona } },
  }).then(unwrap), [persona, threadId]);
  const messages = useResource(view.id === "messages" && threadId ? `${persona}:${threadId}` : null, refresh, fetchMessages);
  const outage = [jobs, detail, pipeline, threads, messages, localResult].some((resource) => resource?.errorStatus === 503);
  const unavailable = outage || Boolean(health.error || health.data?.gigsim.status === "down");

  useEffect(() => {
    if (!outage) return;
    const timeout = window.setTimeout(() => {
      setResult((previous) => previous?.errorStatus === 503 ? undefined : previous);
      setRefresh((value) => value + 1);
    }, 8000);
    return () => window.clearTimeout(timeout);
  }, [outage, refresh]);

  function go(to: View, options: { persona?: PersonaId; job?: string; thread?: string } = {}) {
    const next = new URLSearchParams({ persona: options.persona ?? persona });
    if (to !== "inbox") next.set("view", to);
    if (options.job) next.set("job", options.job);
    if (options.thread) next.set("thread", options.thread);
    router.push(`/?${next}`, { scroll: false });
  }

  function refreshData() {
    setResult(undefined);
    setRefresh((value) => value + 1);
    setHealthRevision((value) => value + 1);
  }

  async function mutate(decision?: ApprovalDecision) {
    if (jobId === null || jobKey === null || unavailable || inFlight.current) return;
    inFlight.current = true;
    setBusyKey(jobKey);
    setResult(undefined);
    const options = { params: { path: { job_id: jobId }, query: { persona } } };
    const revision = refresh + 1;
    try {
      const updated = decision
        ? unwrap(await api.POST("/api/v1/jobs/{job_id}/decision", { ...options, body: decision }))
        : unwrap(await api.POST("/api/v1/jobs/{job_id}/run", options));
      setResult({ key: jobKey, run: updated, revision });
    } catch (error: unknown) {
      setResult({
        key: jobKey,
        error: error instanceof Error ? error.message : "Action could not be completed.",
        errorStatus: error instanceof ApiError ? error.status : undefined,
        revision,
      });
    } finally {
      setRefresh((value) => value + 1);
      setBusyKey(null);
      inFlight.current = false;
    }
  }

  return (
    <div className="dashboard">
      <a href="#content" className="skip-link">Skip to workspace</a>
      <aside className="sidebar">
        <button className="brand" onClick={() => go("inbox")} aria-label="GigBid home"><span className="brand-mark"><ArrowUpRight size={22} strokeWidth={1.8} aria-hidden="true" /></span>GigBid</button>
        <nav className="navigation" aria-label="Workspace">
          {VIEWS.map(({ id, label, icon: Icon }) => (
            <button key={id} className={view.id === id ? "nav-item nav-item--active" : "nav-item"} aria-current={view.id === id ? "page" : undefined} onClick={() => go(id)}>
              <Icon size={18} strokeWidth={1.7} aria-hidden="true" /><span>{label}</span>{view.id === id && <ChevronRight size={14} className="nav-chevron" aria-hidden="true" />}
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="sim-caption"><FlaskConical size={15} aria-hidden="true" /><span>GigSim simulator</span></div>
          <div className="sim-status"><span className={unavailable ? "status-light status-light--down" : health.data ? "status-light" : "status-light status-light--checking"} />{unavailable ? "Simulator offline" : health.data ? "Simulator online" : "Checking simulator"}</div>
        </div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb"><BriefcaseBusiness size={17} /><span>Workspace</span><ChevronRight size={14} /><strong>{view.label}</strong></div>
          <div className="persona-control">
            <PersonaInitial name={selectedPersona?.name ?? "GigBid"} />
            <div><label htmlFor="persona">Active persona</label>
              <div className="select-control">
                <select id="persona" value={persona} disabled={personaOptions.length === 0} onChange={(event) => {
                  setCursor(0);
                  setSearch("");
                  setFilter("all");
                  go(view.id, { persona: event.target.value as PersonaId });
                }}>
                  {personaOptions.length === 0 ? <option value={persona}>Loading personas…</option> : personaOptions.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select>
                <ChevronDown size={15} aria-hidden="true" />
              </div>
            </div>
          </div>
        </header>

        <main id="content" className="content">
          <div className="page-heading">
            <div><h1>{view.label}</h1>{view.description && <p>{view.description}</p>}</div>
            <button className="button secondary refresh" onClick={refreshData}><RefreshCw size={16} /> Refresh</button>
          </div>
          {unavailable && <Notice title="Marketplace unavailable"><p>GigSim is offline. New analysis and marketplace actions are paused.</p></Notice>}
          {personas.error && !unavailable && <Notice title="Could not load personas">{personas.error}</Notice>}

          {view.id === "inbox" && (
            <div className={`inbox-layout${selectedJob ? " inbox-layout--job" : ""}`}>
              <section className="surface inbox-list" aria-label="Jobs">
                <div className="inbox-tools">
                  <div className="list-title"><h2>Opportunities</h2>{jobs.data && <span>{rows.length} {rows.length === 1 ? "job" : "jobs"}</span>}</div>
                  <label className="search"><Search size={16} aria-hidden="true" /><input type="search" placeholder="Search jobs" aria-label="Search jobs" value={search} onChange={(event) => { setSearch(event.target.value); setCursor(0); }} /></label>
                  <div className="filters" aria-label="Filter jobs">{FILTERS.map(({ id, label }) => <button key={id} aria-pressed={filter === id} onClick={() => setFilter(id)}>{label}</button>)}</div>
                </div>
                {jobs.error && !unavailable && <Notice title="Could not load jobs">{jobs.error}</Notice>}
                {jobs.loading && !jobs.data && <Loading>Loading jobs…</Loading>}
                {jobs.data && visibleJobs.length === 0 && <div className="empty-state"><Inbox size={24} /><p>No jobs match this view.</p></div>}
                <div className="job-list">{visibleJobs.map((job) => <JobListItem key={job.job_id} job={job} active={jobId === job.job_id} onSelect={() => go("inbox", { job: job.job_id })} />)}</div>
                {(cursor > 0 || rows.length === PAGE_SIZE) && <div className="pagination"><button className="button ghost" disabled={cursor === 0 || jobs.loading} onClick={() => setCursor(cursor - PAGE_SIZE)}><ArrowLeft size={15} /> Previous</button><button className="button ghost" disabled={rows.length < PAGE_SIZE || jobs.loading} onClick={() => setCursor(cursor + PAGE_SIZE)}>Next <ArrowRight size={15} /></button></div>}
              </section>
              <div className="detail-pane">
                {detail.data && run ? (
                  <JobDetail key={jobKey} job={detail.data} run={run} busy={busyKey !== null} unavailable={unavailable} mutationError={localResult?.error} onBack={() => go("inbox")} onAnalyze={() => void mutate()} onDecision={(decision) => void mutate(decision)} />
                ) : (
                  <div className="surface detail-placeholder">
                    {selectedJob && <button className="button ghost mobile-back" onClick={() => go("inbox")}><ArrowLeft size={15} /> Inbox</button>}
                    {detail.error ? <Notice title="Could not load job">{detail.error}</Notice> : detail.loading ? <Loading>Loading job details…</Loading> : <div className="empty-state"><BriefcaseBusiness size={24} /><p>Select a job to see its details.</p></div>}
                  </div>
                )}
              </div>
            </div>
          )}

          {view.id === "pipeline" && (
            <section className="surface data-panel" aria-label="Proposal pipeline">
              <div className="data-panel-heading"><Columns3 size={19} /><h2>Proposals</h2></div>
              {pipeline.error && !unavailable && <Notice title="Could not load proposals">{pipeline.error}</Notice>}
              {pipeline.loading && !pipeline.data && <Loading>Loading proposals…</Loading>}
              {pipeline.data?.length === 0 && !pipeline.error && !unavailable && (
                <div className="empty-state pipeline-empty">
                  <span className="empty-icon"><Activity size={23} aria-hidden="true" /></span>
                  <strong>No proposals yet</strong>
                  <p>Approved proposals appear here after submission.</p>
                  <button className="button secondary" onClick={() => go("inbox")}>Open job inbox <ArrowRight size={15} aria-hidden="true" /></button>
                </div>
              )}
              {pipeline.data?.map((item) => (
                <article className="proposal-row" key={item.proposal_id}>
                  <div><strong>{friendly(item.stage)}</strong><p>{item.job_id} · {shortDate(item.submitted_at ?? item.created_at)}</p></div>
                  <div><span>{money(item.bid_amount)}{item.bid_type === "hourly" ? "/hr" : ""}</span><p>{item.connects_spent} Connects spent</p></div>
                  <button className="button secondary" onClick={() => go("inbox", { job: item.job_id })}>Open job <ChevronRight size={15} /></button>
                </article>
              ))}
            </section>
          )}

          {view.id === "messages" && threads.data?.length === 0 && !unavailable && !threads.error && <MessagesEmpty persona={persona} />}
          {view.id === "messages" && !threads.data?.length && !unavailable && (threads.error || threads.loading) && (
            <section className="surface data-panel" aria-label="Conversations">
              {threads.error ? <Notice title="Could not load conversations">{threads.error}</Notice> : <Loading>Loading conversations…</Loading>}
            </section>
          )}
          {view.id === "messages" && !!threads.data?.length && (
            <div className="inbox-layout messages-layout">
              <section className="surface data-panel" aria-label="Conversations">
                <div className="data-panel-heading"><MessageSquare size={18} /><h2>Conversations</h2></div>
                {threads.error && !unavailable && <Notice title="Could not load conversations">{threads.error}</Notice>}
                {(threads.data ?? []).map((thread) => <button className={`job-card${threadId === thread.thread_id ? " job-card--active" : ""}`} key={thread.thread_id} onClick={() => go("messages", { thread: thread.thread_id })}><strong>{thread.job_title}</strong><div className="job-card-meta"><span>{friendly(thread.stage)}</span><span>{shortDate(thread.last_message_at)}</span></div></button>)}
              </section>
              <section className="surface data-panel" aria-label="Messages in conversation">
                <div className="data-panel-heading"><MessageSquare size={18} /><h2>{activeThread?.job_title ?? "Client messages"}</h2></div>
                {messages.error && !unavailable && <Notice title="Could not load messages">{messages.error}</Notice>}
                {messages.loading && !messages.data ? <Loading>Loading messages…</Loading> : messages.data?.length === 0 && <div className="empty-state"><MessageSquare size={22} aria-hidden="true" /><p>This conversation has no messages yet.</p></div>}
                <div className="messages">{(messages.data ?? []).map((message) => <article className={message.sender === "freelancer" ? "bubble bubble--own" : "bubble"} key={message.id}><div><strong>{friendly(message.sender)}</strong><time dateTime={message.sent_at}>{shortDate(message.sent_at)}</time></div><p>{message.body}</p></article>)}</div>
                {threadId && <ConversationReview key={`${persona}:${threadId}`} persona={persona} threadId={threadId} latest={messages.data?.at(-1)} refresh={refresh} unavailable={unavailable} onRefresh={refreshData} />}
              </section>
            </div>
          )}
          <footer className="workspace-footer"><CircleCheck size={13} /> GigSim actions use simulated Connects.</footer>
        </main>
      </div>
    </div>
  );
}
