import { useEffect, useState } from "react";

import { ApiError } from "./api/client";

type Snapshot<T> = {
  key: string;
  revision: number;
  data?: T;
  error?: string;
  errorStatus?: number;
};

export function useResource<T>(
  key: string | null,
  revision: number,
  load: (signal: AbortSignal) => Promise<T>,
) {
  const [snapshot, setSnapshot] = useState<Snapshot<T>>();

  useEffect(() => {
    if (key === null) return;
    const controller = new AbortController();
    void load(controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setSnapshot({ key, revision, data });
      },
      (error: unknown) => {
        if (controller.signal.aborted) return;
        setSnapshot((previous) => ({
          key,
          revision,
          data: previous?.key === key ? previous.data : undefined,
          error: error instanceof Error ? error.message : "The request could not be completed.",
          errorStatus: error instanceof ApiError ? error.status : undefined,
        }));
      },
    );
    return () => controller.abort();
  }, [key, revision, load]);

  const current = snapshot?.key === key ? snapshot : undefined;
  return { ...current, loading: key !== null && current?.revision !== revision };
}

export function useDebouncedSearch(value: string) {
  const [query, setQuery] = useState(value);
  useEffect(() => {
    const timeout = window.setTimeout(() => setQuery(value.trim()), 300);
    return () => window.clearTimeout(timeout);
  }, [value]);
  return query;
}
