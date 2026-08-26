import { Link } from "react-router-dom";

import { formatCount, formatDateTime, pluralise } from "@/lib/format";
import {
  ChipList,
  Crumbs,
  MetaList,
  RawData,
  RelatedSection,
  When,
  humanize,
  num,
  obj,
  percent,
  str,
  strList,
  whenEntry,
} from "./parts";

/**
 * The record-shaped detail kinds: memory, skill, prompt, entity. Each page
 * is hand-authored around what its record IS — a memory leads with what was
 * remembered, a skill with what it does, a prompt with its text, an entity
 * with what it's connected to. The shared chrome (breadcrumb, title block,
 * metadata list, raw JSON) comes from ./parts.
 */

type Rec = Record<string, unknown>;

// ── Memory ───────────────────────────────────────────────────────────────

/** Status is written out in text; the pill's tone is never the only signal. */
function memoryStatus(status: string | null): { label: string; cls: string } | null {
  if (!status) return null;
  const cls =
    status === "active" ? "is-good" : status === "forgotten" ? "is-muted" : "is-warning";
  return { label: humanize(status), cls };
}

export function MemoryDetail({ id, record }: { id: string; record: Rec }) {
  const category = str(record.category);
  const content = str(record.content) ?? "";
  const project = str(record.project_name);
  const sourceType = str(record.source_type);
  const sourceId = num(record.source_id);
  const supersededBy = num(record.superseded_by);
  const mergedFrom = Array.isArray(record.merged_from)
    ? (record.merged_from as unknown[]).map((v) => num(v)).filter((v): v is number => v !== null)
    : [];
  const status = memoryStatus(str(record.status));
  const createdAt = str(record.created_at);

  return (
    <>
      <Crumbs kind="memory" current={category ? humanize(category) : `#${id}`} />
      <header className="page-header detail-head">
        <p className="detail-kicker">
          <span className="kind kind-memory">Memory</span>
          <span className="detail-id tabular">#{id}</span>
          {status && <span className={`detail-status ${status.cls}`}>{status.label}</span>}
        </p>
        <h1 className="page-title detail-title">
          {category ? humanize(category) : "Memory"}
        </h1>
        {createdAt && (
          <p className="page-subtitle">
            Remembered {formatDateTime(createdAt)}
            {sourceType === "conversation" && sourceId !== null && (
              <>
                {" from "}
                <Link to={`/c/${sourceId}`}>conversation #{sourceId}</Link>
              </>
            )}
          </p>
        )}
      </header>

      {/* The content IS the memory — it comes first, before any metadata. */}
      <section className="detail-content is-memory" aria-label="Memory content">
        <p>{content || "This memory has no content."}</p>
      </section>

      <MetaList
        label="Memory details"
        items={[
          { label: "Confidence", value: percent(record.confidence), num: true },
          project
            ? {
                label: "Project",
                value: <Link to={`/project/${encodeURIComponent(project)}`}>{project}</Link>,
              }
            : null,
          sourceType === "conversation" && sourceId !== null
            ? { label: "Source", value: <Link to={`/c/${sourceId}`}>Conversation #{sourceId}</Link> }
            : sourceType
              ? { label: "Source", value: humanize(sourceType) }
              : null,
          { label: "Expires", value: str(record.expires_at) ? <When iso={str(record.expires_at)} /> : "Never" },
          num(record.access_count) !== null
            ? { label: "Recalled", value: pluralise(num(record.access_count)!, "time"), num: true }
            : null,
          whenEntry("Last recalled", record.last_accessed),
          supersededBy !== null
            ? {
                label: "Superseded by",
                value: (
                  <>
                    <Link to={`/m/${supersededBy}`}>memory #{supersededBy}</Link>
                    {str(record.superseded_at) && (
                      <>
                        {" on "}
                        <When iso={str(record.superseded_at)} />
                      </>
                    )}
                  </>
                ),
              }
            : null,
        ]}
      />

      <ChipList label="Tags" items={strList(record.tags)} />

      {mergedFrom.length > 0 && (
        <RelatedSection title="Merged from" count={mergedFrom.length}>
          {mergedFrom.map((m) => (
            <li key={m} className="result">
              <Link to={`/m/${m}`} className="result-link">
                <div className="result-head">
                  <span className="kind kind-memory">Memory</span>
                  <span className="result-title">#{m}</span>
                </div>
              </Link>
            </li>
          ))}
        </RelatedSection>
      )}

      <RawData record={record} />
    </>
  );
}

// ── Skill ────────────────────────────────────────────────────────────────

export function SkillDetail({ id, record }: { id: string; record: Rec }) {
  const name = str(record.name) ?? `Skill #${id}`;
  const version = str(record.version);
  const config = obj(record.config) ?? {};
  const skillType = str(config.skill_type);
  const useCount = num(record.use_count);

  return (
    <>
      <Crumbs kind="skill" current={name} />
      <header className="page-header detail-head">
        <p className="detail-kicker">
          <span className="kind kind-skill">Skill</span>
          {version && <span className="detail-id tabular">v{version}</span>}
          {skillType && <span className="detail-status is-muted">{humanize(skillType)}</span>}
        </p>
        <h1 className="page-title detail-title">{name}</h1>
        {useCount !== null && (
          <p className="page-subtitle">
            {useCount === 0
              ? "Never used"
              : `Used ${pluralise(useCount, "time")}`}
            {str(record.last_used) && <> · last on {formatDateTime(str(record.last_used)!)}</>}
          </p>
        )}
      </header>

      {str(record.description) && (
        <section className="detail-content is-skill" aria-label="Skill description">
          <p>{str(record.description)}</p>
        </section>
      )}

      <MetaList
        label="Skill details"
        items={[
          { label: "Path", value: str(record.path), mono: true },
          whenEntry("Last used", record.last_used),
          whenEntry("Registered", record.created_at),
          whenEntry("Updated", record.updated_at),
          whenEntry("File created", record.file_created),
          whenEntry("File modified", record.file_modified),
        ]}
      />

      <ChipList label="Triggers" items={strList(record.triggers)} />

      <RawData record={record} />
    </>
  );
}

// ── Prompt ───────────────────────────────────────────────────────────────

export function PromptDetail({ id, record }: { id: string; record: Rec }) {
  const name = str(record.name) ?? `Prompt #${id}`;
  const category = str(record.category);
  const usageCount = num(record.usage_count);

  return (
    <>
      <Crumbs kind="prompt" current={name} />
      <header className="page-header detail-head">
        <p className="detail-kicker">
          <span className="kind kind-prompt">Prompt</span>
          {category && <span className="detail-status is-muted">{humanize(category)}</span>}
        </p>
        <h1 className="page-title detail-title">{name}</h1>
        {usageCount !== null && (
          <p className="page-subtitle">
            {usageCount === 0 ? "Never used" : `Used ${pluralise(usageCount, "time")}`}
          </p>
        )}
      </header>

      {/* The prompt's text is the record. Monospace, whitespace preserved,
          never truncated — a prompt with a silently cut tail is a different
          prompt. */}
      {str(record.content) && (
        <section className="detail-promptbody" aria-label="Prompt text">
          <pre>{str(record.content)}</pre>
        </section>
      )}

      <MetaList
        label="Prompt details"
        items={[
          { label: "Source", value: str(record.source_path), mono: true },
          whenEntry("Added", record.created_at),
          whenEntry("Updated", record.updated_at),
        ]}
      />

      <ChipList label="Variables" items={strList(record.variables)} />
      <ChipList label="Tags" items={strList(record.tags)} />

      <RawData record={record} />
    </>
  );
}

// ── Entity ───────────────────────────────────────────────────────────────

/** "technology" -> "technologies", "decision" -> "decisions" — entity types
 *  are open-ended, so the heading needs a plural that at least never
 *  produces "technologys". */
function pluralType(word: string, n: number): string {
  if (n === 1) return word;
  if (/[^aeiou]y$/.test(word)) return `${word.slice(0, -1)}ies`;
  if (/(s|x|z|ch|sh)$/.test(word)) return `${word}es`;
  return `${word}s`;
}

interface Relation {
  direction?: string;
  relation_type?: string;
  confidence?: unknown;
  other_id?: number;
  other_name?: string;
  other_type?: string;
}

export function EntityDetail({ id, record, related }: { id: string; record: Rec; related: Rec }) {
  const name = str(record.name) ?? `Entity #${id}`;
  const entityType = str(record.entity_type);
  const canonical = str(record.canonical_name);
  const project = str(record.project_name);
  const attributes = obj(record.attributes) ?? {};
  const relations = Array.isArray(related.relations) ? (related.relations as Relation[]) : [];

  // Related entities grouped by what they are, with counts — a person, a
  // technology, and a decision are different answers to "what is this
  // connected to".
  const groups = new Map<string, Relation[]>();
  for (const r of relations) {
    const key = r.other_type ?? "other";
    const list = groups.get(key) ?? [];
    list.push(r);
    groups.set(key, list);
  }

  return (
    <>
      <Crumbs kind="entity" current={name} />
      <header className="page-header detail-head">
        <p className="detail-kicker">
          <span className="kind kind-entity">Entity</span>
          {entityType && <span className="detail-status is-muted">{humanize(entityType)}</span>}
        </p>
        <h1 className="page-title detail-title">{name}</h1>
        {canonical && canonical.toLowerCase() !== name.toLowerCase() && (
          <p className="page-subtitle">Canonical name: {canonical}</p>
        )}
      </header>

      <MetaList
        label="Entity details"
        items={[
          project
            ? {
                label: "Project",
                value: <Link to={`/project/${encodeURIComponent(project)}`}>{project}</Link>,
              }
            : null,
          num(record.mention_count) !== null
            ? { label: "Mentions", value: formatCount(num(record.mention_count)!), num: true }
            : null,
          { label: "Confidence", value: percent(record.confidence), num: true },
          whenEntry("First seen", record.first_seen),
          whenEntry("Last seen", record.last_seen),
        ]}
      />

      {Object.keys(attributes).length > 0 && (
        <section className="stack-top">
          <h2 className="section-label">Attributes</h2>
          <MetaList
            label="Entity attributes"
            items={Object.entries(attributes).map(([k, v]) => ({
              label: humanize(k),
              value: typeof v === "string" ? v : JSON.stringify(v),
            }))}
          />
        </section>
      )}

      {[...groups.entries()].map(([type, rels]) => (
        <RelatedSection
          key={type}
          title={`Related ${pluralType(humanize(type).toLowerCase(), rels.length)}`}
          count={rels.length}
        >
          {rels.map((r, i) => (
            <li key={`${r.other_id}-${i}`} className="result">
              <Link to={`/e/${r.other_id}`} className="result-link">
                <div className="result-head">
                  <span className="kind kind-entity">{humanize(r.other_type ?? "entity")}</span>
                  <span className="result-title">{r.other_name}</span>
                </div>
                <p className="result-snippet">
                  {r.direction === "in"
                    ? `${humanize(r.relation_type ?? "relates to")} this entity`
                    : `This entity ${(r.relation_type ?? "relates to").replace(/_/g, " ")} ${r.other_name}`}
                  {percent(r.confidence) ? ` · ${percent(r.confidence)} confidence` : ""}
                </p>
              </Link>
            </li>
          ))}
        </RelatedSection>
      ))}

      <RawData record={record} />
    </>
  );
}
