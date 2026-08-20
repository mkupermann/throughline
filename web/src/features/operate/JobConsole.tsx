import { useEffect, useRef, useState } from "react";

/**
 * Live job output.
 *
 * EventSource rather than polling: a job emits output in bursts and long
 * quiet stretches, and polling either lags the bursts or hammers the server
 * through the quiet. The stream replays the retained buffer on connect, so
 * opening this panel mid-run shows the whole run so far.
 */
export function JobConsole({ jobId, onFinished }: { jobId: string; onFinished: () => void }) {
  const [lines, setLines] = useState<string[]>([]);
  const [done, setDone] = useState<string | null>(null);
  const boxRef = useRef<HTMLPreElement>(null);
  const pinned = useRef(true);

  useEffect(() => {
    setLines([]);
    setDone(null);
    const es = new EventSource(`/api/operate/job/${jobId}/stream`);
    es.addEventListener("line", (e) => setLines((l) => [...l, (e as MessageEvent).data]));
    es.addEventListener("done", (e) => {
      setDone((e as MessageEvent).data);
      es.close();
      onFinished();
    });
    es.onerror = () => es.close();
    return () => es.close();
  }, [jobId, onFinished]);

  // Follow the tail, but stop fighting the user the moment they scroll up.
  useEffect(() => {
    const el = boxRef.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [lines]);

  return (
    <div className="console">
      <pre
        ref={boxRef}
        className="console-out"
        tabIndex={0}
        aria-label="Job output"
        onScroll={(e) => {
          const el = e.currentTarget;
          pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}
      >
        {lines.join("\n")}
        {done && `\n\n— ${done}`}
      </pre>
    </div>
  );
}
