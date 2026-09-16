import { useEffect, useRef, useState, useCallback } from "react";
import type { State, ConnStatus } from "./types";

export function useLiveState() {
  const [state, setState] = useState<State | null>(null);
  const [status, setStatus] = useState<ConnStatus>("connecting");
  const [lastEventAt, setLastEventAt] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const lastRef = useRef(0);
  useEffect(() => {
    let cancelled = false;
    setStatus("connecting");
    lastRef.current = Date.now();
    const es = new EventSource("/api/stream");
    es.addEventListener("state", (event) => {
      if (cancelled) return;
      try {
        const next = JSON.parse((event as MessageEvent).data) as State;
        if (!next.configuration || !next.integrations) throw new Error("Unsupported state response");
        lastRef.current = Date.now();
        setState(next);
        setLastEventAt(lastRef.current);
        setStatus("live");
      } catch { setStatus("retrying"); }
    });
    es.onerror = () => { if (!cancelled) setStatus("retrying"); };
    // A silent socket must not keep showing a connected state indefinitely.
    const timer = window.setInterval(() => {
      if (Date.now() - lastRef.current > 45000) setStatus("retrying");
    }, 5000);
    return () => { cancelled = true; es.close(); window.clearInterval(timer); };
  }, [attempt]);
  const retry = useCallback(() => setAttempt(n => n + 1), []);
  const decide = useCallback(async (pid: string, action: "approve" | "reject") => {
    const res = await fetch(`/api/proposals/${encodeURIComponent(pid)}/${action}`, { method: "POST" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(typeof body.detail === "string" ? body.detail : `Decision failed (HTTP ${res.status}).`);
    }
    // Retain the proposal and its error when a request fails or conflicts.
    setState(s => s ? { ...s, proposals: s.proposals.filter(p => p.pid !== pid) } : s);
  }, []);
  return { state, status, lastEventAt, decide, retry };
}
