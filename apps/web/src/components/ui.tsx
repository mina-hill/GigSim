import { AlertTriangle, LoaderCircle } from "lucide-react";
import type { ReactNode } from "react";

import type { RunView } from "@/lib/api/client";
import { runStatus } from "@/lib/format";

export function Status({ value }: { value: RunView["status"] }) {
  const { label, tone } = runStatus(value);
  return <span className={`status status--${tone}`}><span />{label}</span>;
}

export function Notice({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <div className="notice" role="alert">
      <AlertTriangle size={17} aria-hidden="true" />
      <div>{title && <strong>{title}</strong>}{children}</div>
    </div>
  );
}

export function Loading({ children }: { children: ReactNode }) {
  return (
    <div className="empty-state" role="status">
      <LoaderCircle className="spin" size={20} aria-hidden="true" />
      <p>{children}</p>
    </div>
  );
}
