// web/src/features/pm/TaskDetailPage.tsx
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "react-router-dom";

import { pmApi, type PmTaskStatus } from "@/lib/api";
import "@/styles/pm.css";

const TERMINAL: PmTaskStatus[] = ["pass", "fail", "budget_exceeded", "crashed", "stopped"];

export function TaskDetailPage() {
  const { id } = useParams<{ id: string }>();
  const taskId = Number(id);
  const queryClient = useQueryClient();

  const { data: task } = useQuery({
    queryKey: ["pm-task", taskId],
    queryFn: () => pmApi.getTask(taskId),
    // Live-updating while running; stop polling once the task reaches a
    // terminal state so an old finished task's tab doesn't poll forever.
    refetchInterval: (query) =>
      query.state.data && TERMINAL.includes(query.state.data.status) ? false : 4000,
  });

  const { data: eventsData } = useQuery({
    queryKey: ["pm-task-events", taskId],
    queryFn: () => pmApi.taskEvents(taskId),
    refetchInterval: task && TERMINAL.includes(task.status) ? false : 4000,
  });

  const stop = useMutation({
    mutationFn: () => pmApi.stop(taskId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["pm-task", taskId] }),
  });

  if (!task) return null;

  const canStop = task.status === "running" && task.pid !== null;

  return (
    <section className="pm-task-detail">
      <header className="pm-task-header">
        <h1>{task.title}</h1>
        <span className={`pm-status pm-status-${task.status}`}>{task.status}</span>
        <span className="pm-tokens">{task.tokens_used.toLocaleString()} Tokens</span>
        {canStop && (
          <button onClick={() => stop.mutate()} disabled={stop.isPending}>Stop</button>
        )}
      </header>

      <ol className="pm-event-list">
        {(eventsData?.events ?? []).map((e) => (
          <li key={e.id} className={`pm-event pm-event-${e.step}`}>
            <span className="pm-event-step">{e.step}</span>
            {e.iteration != null && <span className="pm-event-iter">#{e.iteration}</span>}
            <span className="pm-event-type">{e.event_type}</span>
            {e.tokens_used != null && <span className="pm-event-tokens">{e.tokens_used} tok</span>}
            {e.message && <pre className="pm-event-message">{e.message}</pre>}
          </li>
        ))}
      </ol>
    </section>
  );
}
