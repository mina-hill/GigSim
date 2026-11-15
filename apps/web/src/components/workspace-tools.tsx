"use client";

import { useCallback, useState } from "react";
import type { FormEvent } from "react";

import { api, unwrap } from "@/lib/api/client";
import type { PersonaId } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { domainLabel, friendly, money, shortDate } from "@/lib/format";
import { useResource } from "@/lib/use-resource";

import { Loading, Notice } from "./ui";

type Schemas = components["schemas"];
type Preferences = Schemas["Preferences"];
type Search = Schemas["SavedSearch"];
type Evidence = Schemas["UploadedEvidence"];
type Settings = Schemas["WorkspaceSettings"];
type Memory = Schemas["MemoryEntry"];
type PipelineCard = Schemas["PipelineCard"];

const STAGES = ["drafted", "submitted", "viewed", "replied", "interview", "hired", "lost"] as const;

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "The workspace could not complete the request.";
}

export function PipelineBoard({ cards, onJob }: { cards: PipelineCard[]; onJob: (id: string) => void }) {
  return (
    <div className="kanban-board" tabIndex={0} aria-label="Pipeline stages">
      {STAGES.map((stage) => {
        const items = cards.filter((item) => item.stage === stage);
        return (
          <section className="kanban-stage" aria-label={friendly(stage)} key={stage}>
            <h3>{friendly(stage)} <span>{items.length}</span></h3>
            {items.map((item) => (
              <article className="kanban-card" key={item.proposal_id ?? item.job_id}>
                <strong>{item.title}</strong>
                <p>{item.bid_amount != null && <span>{money(item.bid_amount)}{item.bid_type === "hourly" ? "/hr" : ""}</span>}
                  {item.created_at && <span>{shortDate(item.created_at)}</span>}</p>
                {item.stage !== "drafted" && <p>{item.connects_spent} Connects spent</p>}
                <button className="button secondary" onClick={() => onJob(item.job_id)}>Open job</button>
              </article>
            ))}
          </section>
        );
      })}
    </div>
  );
}

const ratio = (part: number, total: number) => total ? `${Math.round(part * 100 / total)}%` : "—";

export function Analytics({ persona, revision }: { persona: PersonaId; revision: number }) {
  const load = useCallback((signal: AbortSignal) => api.GET("/api/v1/workspace/analytics", {
    signal, params: { query: { persona } },
  }).then(unwrap), [persona]);
  const stats = useResource(persona, revision, load);
  if (stats.error) return <Notice title="Could not load analytics">{stats.error}</Notice>;
  if (!stats.data) return <Loading>Loading proposal metrics…</Loading>;
  const categories = stats.data.categories ?? [];
  const proposals = stats.data.proposals ?? [];
  const rejections = stats.data.top_rejections ?? [];
  return (
    <div className="workspace-tool-grid">
      {!stats.data.event_history_complete && <Notice title="Partial event history">Some older replies may be absent from these totals.</Notice>}
      <section className="surface workspace-panel">
        <h2>Outcomes by category</h2>
        {categories.length ? <div className="table-scroll" tabIndex={0} aria-label="Category outcomes">
          <table className="workspace-table">
            <thead><tr><th>Category</th><th>Submitted</th><th>Reply rate</th><th>Hire rate</th><th>Connects spent</th></tr></thead>
            <tbody>{categories.map((item) => (
              <tr key={item.domain}><th scope="row">{domainLabel(item.domain)}</th><td>{item.submitted ?? 0}</td>
                <td>{ratio(item.replied ?? 0, item.submitted ?? 0)}</td><td>{ratio(item.hired ?? 0, item.submitted ?? 0)}</td><td>{item.connects_spent ?? 0}</td></tr>
            ))}</tbody>
          </table>
        </div> : <p className="panel-muted">Submitted proposals will appear here.</p>}
      </section>
      <section className="surface workspace-panel">
        <h2>Proposal preparation</h2>
        {proposals.length ? <div className="table-scroll" tabIndex={0} aria-label="Proposal timing and cost">
          <table className="workspace-table">
            <thead><tr><th>Job</th><th>Time to first draft</th><th>Provider cost</th><th>Connects spent</th></tr></thead>
            <tbody>{proposals.map((item) => (
              <tr key={item.proposal_id ?? item.job_id}><th scope="row">{item.title}</th><td>{item.preparation_ms == null ? "Unmeasured" : `${item.preparation_ms} ms`}</td>
                <td>{item.provider_cost_usd == null ? "Unmeasured" : money(item.provider_cost_usd)}</td><td>{item.connects_spent ?? 0}</td></tr>
            ))}</tbody>
          </table>
        </div> : <p className="panel-muted">Timing appears after a measured proposal has been submitted.</p>}
      </section>
      <section className="surface workspace-panel">
        <h2>Top rejection reasons</h2>
        {rejections.length ? <ul className="plain-metrics">{rejections.map((reason) => <li key={reason.reason}><span>{reason.reason}</span><strong>{reason.count}</strong></li>)}</ul>
          : <p className="panel-muted">No rejection reasons recorded.</p>}
      </section>
    </div>
  );
}

const initialSearch: Search = { name: "", query: "", domain: "", payment_type: "any", interval_minutes: 60, enabled: false };

function SettingsForm({ settings, persona, onSaved }: {
  settings: Settings; persona: PersonaId; onSaved: () => void;
}) {
  const [preferences, setPreferences] = useState<Preferences>(settings.preferences);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [upload, setUpload] = useState({ title: "", skills: "", summary: "" });
  const searches = preferences.saved_searches ?? [];
  const portfolio = preferences.portfolio ?? [];

  function editSearch(index: number, fields: Partial<Search>) {
    setPreferences((current) => ({
      ...current, saved_searches: (current.saved_searches ?? []).map((item, position) => position === index ? { ...item, ...fields } : item),
    }));
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setStatus("");
    setError("");
    try {
      const saved = unwrap(await api.PUT("/api/v1/workspace/settings", { params: { query: { persona } }, body: preferences }));
      setPreferences(saved.preferences);
      setStatus("Settings saved");
      onSaved();
    } catch (problem) {
      setError(errorMessage(problem));
    } finally {
      setSaving(false);
    }
  }

  async function readPortfolio(file?: File) {
    if (!file) return;
    setError("");
    if (!/\.(md|txt)$/i.test(file.name) || file.size > 4800) {
      setError("Choose a Markdown or text file of up to 4,800 bytes.");
      return;
    }
    const summary = (await file.text()).trim();
    if (summary.length < 20 || summary.length > 1200) {
      setError("Use a project summary between 20 and 1,200 characters.");
      return;
    }
    setUpload((value) => ({ ...value, title: value.title || file.name.replace(/\.[^.]+$/, "").slice(0, 100), summary }));
  }

  function addPortfolio() {
    const item: Evidence = {
      title: upload.title.trim(), summary: upload.summary.trim(),
      skills: upload.skills.split(",").map((skill) => skill.trim()).filter(Boolean),
    };
    if (!item.title || item.summary.length < 20 || !item.skills.length || item.skills.length > 12 || item.skills.some((skill) => skill.length > 40)) {
      setError("Add a title, a project summary, and 1–12 skills of up to 40 characters each.");
      return;
    }
    setError("");
    setPreferences({ ...preferences, portfolio: [...portfolio, item] });
    setUpload({ title: "", skills: "", summary: "" });
  }

  return (
    <form onSubmit={save} className="surface workspace-panel workspace-settings-form">
      <h2>Bid preferences</h2>
      <div className="settings-grid">
        <label>Weekly Connects budget
          <input type="number" min={0} max={1000} placeholder={String(settings.default_weekly_budget)}
            value={preferences.weekly_connects_budget ?? ""} onChange={(event) => setPreferences({ ...preferences, weekly_connects_budget: event.target.value === "" ? null : Number(event.target.value) })} />
        </label>
        <label>Preferred hourly rate (USD)
          <input type="number" min={1} max={10000} step="0.01" placeholder={String(settings.default_hourly_rate)}
            value={preferences.preferred_hourly_rate ?? ""} onChange={(event) => setPreferences({ ...preferences, preferred_hourly_rate: event.target.value === "" ? null : Number(event.target.value) })} />
        </label>
        <label>Borderline fit
          <select value={(preferences.auto_skip_low_fit ?? settings.default_auto_skip) ? "skip" : "watch"}
            onChange={(event) => setPreferences({ ...preferences, auto_skip_low_fit: event.target.value === "skip" })}>
            <option value="watch">Keep for review</option><option value="skip">Skip automatically</option>
          </select>
        </label>
      </div>
      <p className="panel-muted">Every proposal and reply requires approval before sending.</p>
      <div className="settings-searches">
        <h3>Saved searches</h3>
        <p className="panel-muted">Use a saved search from the inbox. Turn on scouting for a search when the local worker is running.</p>
        {searches.map((search, index) => (
          <fieldset className="settings-search" key={index}>
            <legend>Search {index + 1}</legend>
            <div className="settings-grid">
              <label>Name<input required maxLength={60} value={search.name} onChange={(event) => editSearch(index, { name: event.target.value })} /></label>
              <label>Keywords<input maxLength={200} value={search.query ?? ""} onChange={(event) => editSearch(index, { query: event.target.value })} /></label>
              <label>Category<select value={search.domain ?? ""} onChange={(event) => editSearch(index, { domain: event.target.value })}>
                <option value="">All categories</option><option value="ai_ml">AI / ML</option><option value="fullstack_web">Full-stack web</option><option value="data_analysis">Data analysis</option>
              </select></label>
              <label>Payment<select value={search.payment_type ?? "any"} onChange={(event) => editSearch(index, { payment_type: event.target.value as Search["payment_type"] })}>
                <option value="any">Any</option><option value="fixed">Fixed</option><option value="hourly">Hourly</option>
              </select></label>
              <label>Scout every<select value={search.interval_minutes ?? 60} onChange={(event) => editSearch(index, { interval_minutes: Number(event.target.value) as Search["interval_minutes"] })}>
                <option value={30}>30 minutes</option><option value={60}>60 minutes</option><option value={180}>3 hours</option>
              </select></label>
            </div>
            <div className="settings-actions">
              <label className="settings-checkbox"><input type="checkbox" checked={search.enabled ?? false} onChange={(event) => editSearch(index, { enabled: event.target.checked })} /> Schedule scouting</label>
              <button type="button" className="button ghost" aria-label={`Remove search ${index + 1}`} onClick={() => setPreferences({ ...preferences, saved_searches: searches.filter((_, position) => position !== index) })}>Remove</button>
            </div>
          </fieldset>
        ))}
        {searches.length < 5 && <button type="button" className="button secondary" onClick={() => setPreferences({ ...preferences, saved_searches: [...searches, { ...initialSearch }] })}>Add saved search</button>}
      </div>
      <div className="settings-searches">
        <h3>Portfolio evidence</h3>
        <p className="panel-muted">Add a short project summary and its skills. New bid drafts can cite the evidence you save here.</p>
        <ul className="plain-metrics">
          {portfolio.map((item, index) => <li key={item.title}>
            <div><strong>{item.title}</strong><p className="panel-muted">{item.skills.join(", ")}</p></div>
            <button type="button" className="button ghost" aria-label={`Remove ${item.title}`}
              onClick={() => setPreferences({ ...preferences, portfolio: portfolio.filter((_, position) => position !== index) })}>Remove</button>
          </li>)}
        </ul>
        {portfolio.length < 5 && <>
          <div className="settings-grid">
            <label>Import Markdown or text<input type="file" accept=".md,.txt,text/plain,text/markdown" onChange={(event) => {
              const file = event.currentTarget.files?.[0];
              event.currentTarget.value = "";
              void readPortfolio(file);
            }} /></label>
            <label>Project title<input maxLength={100} value={upload.title} onChange={(event) => setUpload({ ...upload, title: event.target.value })} /></label>
            <label>Skills, separated by commas<input maxLength={500} value={upload.skills} onChange={(event) => setUpload({ ...upload, skills: event.target.value })} /></label>
          </div>
          <label className="settings-long-field">Project summary<textarea rows={3} maxLength={1200} value={upload.summary} onChange={(event) => setUpload({ ...upload, summary: event.target.value })} /></label>
          <button type="button" className="button secondary" onClick={addPortfolio}>Add portfolio evidence</button>
        </>}
      </div>
      {error && <Notice title="Could not save settings">{error}</Notice>}
      <div className="settings-actions"><button className="button primary" disabled={saving}>Save settings</button>{status && <span role="status">{status}</span>}</div>
    </form>
  );
}

export function WorkspaceSettingsPanel({ persona, revision }: { persona: PersonaId; revision: number }) {
  const [saved, setSaved] = useState(0);
  const [memoryError, setMemoryError] = useState("");
  const [removing, setRemoving] = useState(false);
  const loadSettings = useCallback((signal: AbortSignal) => api.GET("/api/v1/workspace/settings", {
    signal, params: { query: { persona } },
  }).then(unwrap), [persona]);
  const loadMemory = useCallback((signal: AbortSignal) => api.GET("/api/v1/workspace/memory", {
    signal, params: { query: { persona } },
  }).then(unwrap), [persona]);
  const settings = useResource(persona, revision + saved, loadSettings);
  const memories = useResource(persona, revision + saved, loadMemory);

  async function remove(item: Memory) {
    setRemoving(true);
    setMemoryError("");
    try {
      unwrap(await api.POST("/api/v1/workspace/memory/remove", {
        params: { query: { persona } }, body: { kind: item.kind, category: item.category, subject: item.subject },
      }));
      setSaved((value) => value + 1);
    } catch (problem) {
      setMemoryError(errorMessage(problem));
    } finally {
      setRemoving(false);
    }
  }

  if (settings.error) return <Notice title="Could not load settings">{settings.error}</Notice>;
  if (!settings.data) return <Loading>Loading settings…</Loading>;
  const account = settings.data;
  return (
    <div className="workspace-tool-grid">
      <SettingsForm key={persona} settings={account} persona={persona} onSaved={() => setSaved((value) => value + 1)} />
      <section className="surface workspace-panel">
        <h2>Connected account</h2>
        <p>GigSim · {account.account_id}</p>
        <p className="panel-muted">{account.connects_balance} available · {account.weekly_connects_spent} spent this simulator week</p>
      </section>
      <section className="surface workspace-panel">
        <h2>Learned preferences</h2>
        {(memoryError || memories.error) && <Notice title="Memory update unavailable">{memoryError || memories.error}</Notice>}
        {memories.loading && !memories.data && <Loading>Loading memory…</Loading>}
        {memories.data?.length === 0 && <p className="panel-muted">Preferences appear after repeated approved edits or enough supporting outcomes.</p>}
        <ul className="plain-metrics">
          {memories.data?.map((item) => (
            <li key={`${item.kind}:${item.category ?? ""}:${item.subject}`}>
              <div><strong>{friendly(item.subject.replace(":", " · "))}</strong><p className="panel-muted">{friendly(item.kind)}{item.category ? ` · ${domainLabel(item.category)}` : ""} · {item.observations} observations</p></div>
              <button className="button secondary" disabled={removing} onClick={() => void remove(item)}>Forget</button>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
