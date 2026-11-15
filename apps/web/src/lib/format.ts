import type { JobCard, RunView } from "./api/client";

const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
  minimumFractionDigits: 0,
});

export const money = (amount: number) => usd.format(amount);

export function budget(job: JobCard) {
  const hourly = job.payment_type === "hourly";
  const min = hourly ? job.hourly_min : job.budget_min;
  const max = hourly ? job.hourly_max : job.budget_max;
  if (min == null && max == null) return "Budget unspecified";
  const range = min != null && max != null && min !== max
    ? `${money(min)}–${money(max)}`
    : money(min ?? max ?? 0);
  return hourly ? `${range}/hr` : range;
}

export function friendly(value: string) {
  const words = value.replace(/[_-]+/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function domainLabel(domain: string) {
  if (domain === "ai_ml") return "AI / ML";
  if (domain === "fullstack_web") return "Full-stack web";
  return friendly(domain);
}

const date = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });

export function shortDate(value: string | null | undefined) {
  if (!value || Number.isNaN(Date.parse(value))) return "";
  return date.format(new Date(value));
}

export function runStatus(status: RunView["status"]) {
  return {
    idle: { label: "Unreviewed", tone: "neutral" },
    running: { label: "Analyzing", tone: "blue" },
    awaiting_approval: { label: "Needs review", tone: "amber" },
    needs_attention: { label: "Needs attention", tone: "amber" },
    skipped: { label: "Skipped", tone: "neutral" },
    done: { label: "Complete", tone: "green" },
  }[status];
}
