import { Link } from "react-router-dom";

import { Transcript } from "@/features/detail/Transcript";
import type { ProjectContext, ProjectContextMessage } from "@/lib/api";

type Knowledge = ProjectContext["knowledge"][number];

interface SessionGroup {
  id: number;
  title: string | null;
  generatedBy: string | null;
  messages: ProjectContextMessage[];
}

function humanise(value: string): string {
  const words = value.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function groupKnowledge(items: Knowledge[]): Array<[string, Knowledge[]]> {
  const groups = new Map<string, Knowledge[]>();
  for (const item of items) {
    const group = groups.get(item.category) ?? [];
    group.push(item);
    groups.set(item.category, group);
  }
  return [...groups.entries()];
}

function groupSessionRuns(messages: ProjectContextMessage[]): SessionGroup[] {
  const groups: SessionGroup[] = [];
  for (const message of messages) {
    const current = groups.at(-1);
    if (current?.id === message.conversation_id) {
      current.messages.push(message);
      continue;
    }
    groups.push({
      id: message.conversation_id,
      title: message.conversation_title,
      generatedBy: message.generated_by,
      messages: [message],
    });
  }
  return groups;
}

function Provenance({ item }: { item: Knowledge }) {
  if (item.source_type === "conversation" && item.source_id) {
    return <Link to={`/c/${item.source_id}`}>Open source conversation</Link>;
  }
  return (
    <span className="project-knowledge-source">
      Source: {item.source_type.replace(/_/g, " ")}
      {item.source_id ? ` #${item.source_id}` : ""}
    </span>
  );
}

export function ProjectDocument({
  summary,
  knowledge,
  messages,
  complete,
  onLoadComplete,
  loading,
}: {
  summary: string;
  knowledge: Knowledge[];
  messages: ProjectContextMessage[];
  complete: boolean;
  onLoadComplete: () => void;
  loading: boolean;
}) {
  const knowledgeGroups = groupKnowledge(knowledge);
  // A session can overlap another session in wall-clock time. Group only
  // contiguous runs so A1, B1, A2 remains A1, B1, A2 in the document.
  const sessions = groupSessionRuns(messages);

  return (
    <div className="project-document">
      <p className="project-summary">{summary}</p>

      <section aria-labelledby="project-knowledge-heading">
        <h2 id="project-knowledge-heading">Knowledge</h2>
        {knowledgeGroups.length ? (
          <div className="project-knowledge-groups">
            {knowledgeGroups.map(([category, items]) => (
              <section className="project-knowledge-group" key={category}>
                <h3>{humanise(category)}</h3>
                <ul className="project-knowledge">
                  {items.map((item) => (
                    <li key={item.id}>
                      <p>{item.content}</p>
                      <div className="project-knowledge-meta">
                        <Provenance item={item} />
                        <span>Confidence {Math.round(item.confidence * 100)}%</span>
                      </div>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        ) : (
          <p className="empty-state">No extracted knowledge yet.</p>
        )}
      </section>

      <section aria-labelledby="project-transcript-heading">
        <h2 id="project-transcript-heading">Transcript</h2>
        {sessions.length ? (
          <div className="project-session-documents">
            {sessions.map((session, index) => (
              <article className="project-session-document" key={`${session.id}-${index}`}>
                <header className="project-session-document-head">
                  <h3>
                    <Link to={`/c/${session.id}`}>{session.title || "Untitled session"}</Link>
                  </h3>
                  {session.generatedBy && <span className="generated-label">Generated</span>}
                </header>
                <Transcript messages={session.messages} />
              </article>
            ))}
          </div>
        ) : (
          <p className="empty-state">No transcript messages yet.</p>
        )}
      </section>

      {!complete && (
        <button
          type="button"
          className="button stack-top"
          onClick={onLoadComplete}
          disabled={loading}
        >
          {loading ? "Loading complete project…" : "Load complete project"}
        </button>
      )}
    </div>
  );
}
