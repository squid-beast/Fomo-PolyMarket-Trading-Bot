import { useEffect, useRef, useState, useCallback } from "react";
import type { State, ConnStatus } from "./types";

/**
 * Subscribes to the server's SSE feed.
 *
 * The server only emits when the daemon actually wrote something (it watches
 * SQLite's data_version), so this is push, not polling — roughly 90ms from
 * write to render instead of the 5s a timer gave us.
 *
 * EventSource reconnects on its own with backoff. We surface the connection
 * state rather than hiding it: a dashboard that silently shows stale numbers
 * while disconnected is worse than one that admits it.
 */
export function useLiveState() {
  const [state, setState] = useState<State | null>(null);
  const [status, setStatus] = useState<ConnStatus>("connecting");
  const [lastEventAt, setLastEventAt] = useState<number>(0);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    let cancelled = false;

    const connect = () => {
      if (cancelled) return;
      const es = new EventSource("/api/stream");
      esRef.current = es;

      es.addEventListener("state", (ev) => {
        if (cancelled) return;
        try {
          setState(JSON.parse((ev as MessageEvent).data) as State);
          setLastEventAt(Date.now());
          setStatus("live");
        } catch {
          /* a malformed frame should not tear down the stream */
        }
      });

      es.onerror = () => {
        if (cancelled) return;
        // EventSource retries by itself; reflect that rather than reconnecting
        // manually, which would fight its backoff.
        setStatus("retrying");
      };
    };

    connect();
    return () => {
      cancelled = true;
      esRef.current?.close();
    };
  }, []);

  /** Optimistically drop a decided proposal so the card disappears instantly. */
  const decide = useCallback(async (pid: string, action: "approve" | "reject") => {
    setState((s) => s ? { ...s, proposals: s.proposals.filter((p) => p.pid !== pid) } : s);
    const res = await fetch(`/api/proposals/${pid}/${action}`, { method: "POST" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail ?? `HTTP ${res.status}`);
    }
    // the daemon's write triggers a push that reconciles the real state
  }, []);

  return { state, status, lastEventAt, decide };
}
