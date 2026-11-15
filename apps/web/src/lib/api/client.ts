import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

export const api = createClient<paths>({ cache: "no-store" });

export type Persona = components["schemas"]["PersonaView"];
export type PersonaId = Persona["id"];
export type JobCard = components["schemas"]["JobCard"];
export type JobRecord = components["schemas"]["JobRecord"];
export type RunView = components["schemas"]["RunView"];
export type PendingApproval = components["schemas"]["PendingApproval"];
export type ApprovalDecision = components["schemas"]["ApprovalDecision"];
export type ConversationRunView = components["schemas"]["ConversationRunView"];
export type ConversationPreview = components["schemas"]["ConversationPreview"];
export type Proposal = components["schemas"]["Proposal"];
export type Thread = components["schemas"]["Thread"];
export type Message = components["schemas"]["Message"];
export type DependencyView = components["schemas"]["DependencyView"];

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

export function unwrap<T>({ data, error, response }: {
  data?: T;
  error?: unknown;
  response: Response;
}): T {
  if (data !== undefined) return data;
  const detail = typeof error === "object" && error && "detail" in error ? error.detail : undefined;
  const validation = Array.isArray(detail) ? (detail as { msg?: unknown }[])[0]?.msg : undefined;
  const message = typeof validation === "string"
    ? validation.replace(/^Value error, /, "")
    : typeof detail === "string" ? detail : `Request failed (${response.status}).`;
  throw new ApiError(message, response.status);
}
