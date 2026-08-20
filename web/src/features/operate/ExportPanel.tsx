import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Download, Info } from "lucide-react";

import { exportApi, type ExportRequest } from "@/lib/api";
import { useToast } from "@/components/Toaster";
import { JobConsole } from "./JobConsole";

/**
 * Write the corpus out as a Markdown vault.
 *
 * This is the one control on the page that creates files somewhere the
 * server did not choose, so the destination is a field rather than a
 * one-click Run, and the boundary it must stay inside is stated next to
 * it — a refusal the user can only discover by being refused is a worse
 * design than one they can read beforehand.
 */
export function ExportPanel() {
  const toast = useToast();
  const [out, setOut] = useState("");
  const [redact, setRedact] = useState(false);
  const [includeGenerated, setIncludeGenerated] = useState(false);
  const [toolOutput, setToolOutput] = useState(0);
  const [problem, setProblem] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);

  const { data: options } = useQuery({
    queryKey: ["export", "options"],
    queryFn: exportApi.options,
    staleTime: 5 * 60_000,
  });

  // Fill the field once, from the server's suggestion. Re-running this on
  // every render of a refetched query would overwrite what the user typed.
  useEffect(() => {
    if (options?.suggested) setOut((current) => current || options.suggested);
  }, [options?.suggested]);

  const start = useMutation({
    mutationFn: (body: ExportRequest) => exportApi.start(body),
    onSuccess: (res) => {
      setProblem(null);
      setJobId(res.job.id);
      toast.push({ message: `Exporting to ${res.out}` });
    },
    onError: (e) => setProblem((e as Error).message),
  });

  function submit() {
    const destination = out.trim();
    if (!destination) {
      setProblem("Enter a destination first.");
      return;
    }
    setProblem(null);
    start.mutate({ out: destination, redact, includeGenerated, toolOutput });
  }

  return (
    <div className="job" id="export">
      <div className="job-head">
        <div className="job-text">
          <h3 className="job-title">Export as Markdown</h3>
          <p className="job-desc">
            Plain Markdown for Obsidian or any editor: one folder per project, sessions oldest
            first. Each turn is labelled prompt, answer or execution, and names the model that
            produced it. Re-running updates the same folder and leaves your own notes alone.
          </p>
        </div>
        <button type="button" className="button" onClick={submit} disabled={start.isPending}>
          <Download size={13} aria-hidden />
          Export
        </button>
      </div>

      <div className="export-form">
        <label className="export-field">
          <span>Destination</span>
          <input
            type="text"
            value={out}
            spellCheck={false}
            onChange={(e) => setOut(e.target.value)}
            placeholder={options?.suggested ?? "/absolute/path/to/a/folder"}
          />
        </label>

        {options && (
          <p className="job-unavailable">
            <Info size={13} aria-hidden />
            <span>
              Must be an absolute path inside {options.root}
              {/* In a container that root is a container path, and a Windows
                  or macOS path typed here is refused — which reads as the
                  export being broken, while a successful one lands somewhere
                  the person cannot find. */}
              {options.hostPath && options.hostPath !== options.root && (
                <>
                  {" — appears on this machine under "}
                  <code>{options.hostPath}</code>
                </>
              )}
            </span>
          </p>
        )}

        <div className="export-toggles">
          <label>
            <input type="checkbox" checked={redact} onChange={(e) => setRedact(e.target.checked)} />
            <span>Redact keys, tokens, emails and home paths</span>
          </label>
          <label>
            <input
              type="checkbox"
              checked={includeGenerated}
              onChange={(e) => setIncludeGenerated(e.target.checked)}
            />
            <span>Include the tool&rsquo;s own model calls</span>
          </label>
          <label>
            <input
              type="checkbox"
              checked={toolOutput > 0}
              onChange={(e) => setToolOutput(e.target.checked ? 400 : 0)}
            />
            <span>Keep the first 400 characters of tool output</span>
          </label>
        </div>
      </div>

      {problem && (
        <p className="job-unavailable">
          <Info size={13} aria-hidden />
          <span>{problem}</span>
        </p>
      )}

      {jobId && <JobConsole jobId={jobId} onFinished={() => undefined} />}
    </div>
  );
}
