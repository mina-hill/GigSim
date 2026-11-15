"use client";

import { useEffect, useState } from "react";

import type { PersonaId } from "@/lib/api/client";

type TaskStatus = "started" | "completed" | "waiting_for_approval" | "error";
const taskStatuses: readonly TaskStatus[] = ["started", "completed", "waiting_for_approval", "error"];

type ProgressEntry = {
  id: string;
  node: string;
  status: TaskStatus;
};

type StreamState = {
  key: string;
  entries: ProgressEntry[];
};

export function useRunProgress(persona: PersonaId, jobId: string) {
  const key = `/api/v1/jobs/${encodeURIComponent(jobId)}/events?persona=${encodeURIComponent(persona)}`;
  const [state, setState] = useState<StreamState>();

  useEffect(() => {
    const stream = new EventSource(key);
    function handleProgress(message: Event) {
      if (!(message instanceof MessageEvent) || typeof message.data !== "string") return;
      let data: unknown;
      try {
        data = JSON.parse(message.data);
      } catch {
        return;
      }
      if (typeof data !== "object" || data === null || !("kind" in data) || !("status" in data)) return;
      if (data.kind === "run" && data.status === "started") {
        setState({ key, entries: [] });
        return;
      }
      if (data.kind !== "node" || !("node" in data) || !("task_id" in data)) return;
      const { node, task_id: id, status } = data;
      const taskStatus = taskStatuses.find((value) => value === status);
      if (typeof node !== "string" || typeof id !== "string" || taskStatus === undefined) return;
      setState((previous) => {
        const entries = previous?.key === key ? previous.entries : [];
        const updated = { id, node, status: taskStatus };
        return {
          key,
          entries: entries.some((entry) => entry.id === id)
            ? entries.map((entry) => entry.id === id ? updated : entry)
            : [...entries, updated],
        };
      });
    }

    stream.addEventListener("progress", handleProgress);
    return () => stream.close();
  }, [key]);

  return state?.key === key ? state.entries : [];
}
