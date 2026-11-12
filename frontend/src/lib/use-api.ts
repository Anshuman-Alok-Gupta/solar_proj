"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "./api";

export interface ApiState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  refetch: () => Promise<void>;
}

/** Small data hook: loading / error / data with manual refetch. `deps` re-run the fetcher. */
export function useApi<T>(fetcher: (() => Promise<T>) | null, deps: unknown[] = []): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(!!fetcher);
  const seq = useRef(0);

  const run = useCallback(async () => {
    if (!fetcher) {
      setLoading(false);
      return;
    }
    const id = ++seq.current;
    setLoading(true);
    setError(null);
    try {
      const res = await fetcher();
      if (id === seq.current) setData(res);
    } catch (e) {
      if (id === seq.current) setError(errorMessage(e));
    } finally {
      if (id === seq.current) setLoading(false);
    }
    // The caller supplies the dependency list explicitly (generic data hook).
    // eslint-disable-next-line react-hooks/exhaustive-deps, react-hooks/use-memo
  }, deps);

  useEffect(() => {
    void run();
  }, [run]);

  return { data, error, loading, refetch: run };
}

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    if (typeof e.detail === "string") return e.detail;
    if (Array.isArray(e.detail)) return e.detail.map((d: { msg?: string }) => d.msg ?? JSON.stringify(d)).join("; ");
    return e.message;
  }
  if (e instanceof Error) return e.message;
  return String(e);
}
