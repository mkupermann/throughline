//! Handoff brief: resume context for a new agent session (R1).
//!
//! Offline — no LLM. The brief is what an agent should see at session start.
//! Ranking: decisions and clear first-user text are surfaced first.

use anyhow::Result;
use postgres::GenericClient;
use serde::Serialize;

#[derive(Debug, Serialize)]
pub struct HandoffBrief {
    pub project: Option<String>,
    pub generated_at: chrono::DateTime<chrono::Utc>,
    pub headline: String,
    pub recent_sessions: Vec<SessionNote>,
    pub decisions: Vec<DecisionNote>,
    pub open_questions: Vec<String>,
    pub stale_memory: Vec<String>,
    pub stats: HandoffStats,
}

#[derive(Debug, Serialize)]
pub struct SessionNote {
    pub id: i64,
    pub session_id: String,
    pub source_tool: Option<String>,
    pub summary: Option<String>,
    pub started_at: chrono::DateTime<chrono::Utc>,
    pub message_count: i32,
    pub first_user: String,
}

#[derive(Debug, Serialize)]
pub struct DecisionNote {
    pub id: i64,
    pub content: String,
    pub category: String,
    pub project_name: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct HandoffStats {
    pub conversations: i64,
    pub messages: i64,
    pub memory_chunks: i64,
    pub tools: Vec<String>,
}

/// Score a session note's usefulness for handoff: higher is better.
/// Prefers sessions with clear, non-trivial first-user text.
pub fn session_relevance_score(s: &SessionNote) -> i32 {
    let mut score = 0;
    let fu = s.first_user.trim();
    // Non-empty first_user text is the primary signal.
    if fu.is_empty() {
        return 0;
    }
    score += 10;
    // Longer (but not spammy) first_user is more informative.
    let len = fu.chars().count();
    if len >= 20 && len <= 200 {
        score += 5;
    } else if len > 200 {
        score += 2;
    }
    // Sessions with a summary are curated and more useful.
    if s.summary
        .as_deref()
        .map(|x| !x.trim().is_empty())
        .unwrap_or(false)
    {
        score += 8;
    }
    // More messages = more context to resume.
    if s.message_count > 10 {
        score += 3;
    } else if s.message_count > 2 {
        score += 1;
    }
    score
}

/// Score a decision note: higher = more important to surface first.
pub fn decision_relevance_score(d: &DecisionNote) -> i32 {
    let mut score = 0;
    match d.category.as_str() {
        "decision" => score += 20,
        "preference" => score += 15,
        "project_context" => score += 12,
        "workflow" => score += 10,
        "pattern" => score += 8,
        _ => score += 5,
    }
    // Content quality: longer content is usually more specific.
    let len = d.content.chars().count();
    if len >= 30 && len <= 300 {
        score += 3;
    }
    // Decisions with an explicit project are more actionable.
    if d.project_name
        .as_deref()
        .map(|p| !p.is_empty())
        .unwrap_or(false)
    {
        score += 2;
    }
    score
}

/// Build a compact resume brief for `project` (or the whole corpus).
pub fn build_handoff<C: GenericClient>(
    client: &mut C,
    project: Option<&str>,
    limit: usize,
) -> Result<HandoffBrief> {
    let sessions = client.query(
        r#"
        SELECT id, session_id::text, source_tool, summary, started_at, message_count,
               left(COALESCE(
                   (SELECT content FROM messages m
                    WHERE m.conversation_id = c.id AND m.role = 'user'
                    ORDER BY m.created_at LIMIT 1),
                   ''), 120)
        FROM conversations c
        WHERE ($1::text IS NULL OR project_name = $1)
          AND generated_by IS NULL
        ORDER BY started_at DESC
        LIMIT $2::bigint
        "#,
        &[&project, &(limit as i64)],
    )?;

    let decisions = client.query(
        r#"
        SELECT id, content, category::text, project_name
        FROM memory_chunks
        WHERE status = 'active'
          AND category IN ('decision','preference','project_context')
          AND ($1::text IS NULL OR project_name = $1)
        ORDER BY created_at DESC
        LIMIT 12
        "#,
        &[&project],
    )?;

    let stale = client.query(
        r#"
        SELECT content FROM memory_chunks
        WHERE status = 'active'
          AND (expires_at IS NOT NULL AND expires_at < now())
        ORDER BY created_at DESC
        LIMIT 5
        "#,
        &[],
    )?;

    let questions = client.query(
        r#"
        SELECT content FROM memory_chunks
        WHERE status = 'active'
          AND (content ILIKE '%?%' OR content ILIKE '%todo%' OR content ILIKE '%open%')
          AND ($1::text IS NULL OR project_name = $1)
        ORDER BY created_at DESC
        LIMIT 5
        "#,
        &[&project],
    )?;

    let conversations: i64 = client
        .query_one(
            "SELECT COUNT(*) FROM conversations WHERE ($1::text IS NULL OR project_name = $1) AND generated_by IS NULL",
            &[&project],
        )?
        .get(0);
    let messages: i64 = client
        .query_one(
            "SELECT COUNT(*) FROM messages m JOIN conversations c ON c.id = m.conversation_id WHERE ($1::text IS NULL OR c.project_name = $1)",
            &[&project],
        )?
        .get(0);
    let memory_chunks: i64 = client
        .query_one(
            "SELECT COUNT(*) FROM memory_chunks WHERE status = 'active' AND ($1::text IS NULL OR project_name = $1)",
            &[&project],
        )?
        .get(0);
    let tools: Vec<String> = client
        .query(
            "SELECT DISTINCT COALESCE(source_tool,'?') FROM conversations WHERE generated_by IS NULL ORDER BY 1",
            &[],
        )?
        .into_iter()
        .map(|r| r.get(0))
        .collect();

    let mut recent_sessions: Vec<SessionNote> = sessions
        .into_iter()
        .map(|r| SessionNote {
            id: r.get(0),
            session_id: r.get(1),
            source_tool: r.get(2),
            summary: r.get(3),
            started_at: r.get(4),
            message_count: r.get(5),
            first_user: r.get(6),
        })
        .collect();
    // Rank: clear first-user text and curated summaries first.
    recent_sessions.sort_by(|a, b| {
        session_relevance_score(b)
            .cmp(&session_relevance_score(a))
            .then_with(|| b.started_at.cmp(&a.started_at))
    });

    let mut decisions: Vec<DecisionNote> = decisions
        .into_iter()
        .map(|r| DecisionNote {
            id: r.get(0),
            content: r.get(1),
            category: r.get(2),
            project_name: r.get(3),
        })
        .collect();
    // Rank: decision category first, then preference, then context.
    decisions.sort_by(|a, b| {
        decision_relevance_score(b)
            .cmp(&decision_relevance_score(a))
            .then_with(|| b.id.cmp(&a.id))
    });

    let open_questions: Vec<String> = questions.into_iter().map(|r| r.get(0)).collect();
    let stale_memory: Vec<String> = stale.into_iter().map(|r| r.get(0)).collect();

    let headline = format!(
        "Continue work{} — {} sessions, {} messages, {} active memories across tools [{}]",
        project.map(|p| format!(" on {p}")).unwrap_or_default(),
        recent_sessions.len(),
        messages,
        memory_chunks,
        tools.join(", ")
    );

    Ok(HandoffBrief {
        project: project.map(|s| s.to_string()),
        generated_at: chrono::Utc::now(),
        headline,
        recent_sessions,
        decisions,
        open_questions,
        stale_memory,
        stats: HandoffStats {
            conversations,
            messages,
            memory_chunks,
            tools,
        },
    })
}

/// Clipboard tool for one OS family. `args` are passed to `program`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ClipboardCmd {
    pub program: &'static str,
    pub args: &'static [&'static str],
}

/// Clipboard tools to try for `os` (`std::env::consts::OS` values).
/// Windows: `clip`. macOS: `pbcopy`. Linux: `wl-copy`, then `xclip -selection clipboard`.
pub fn clipboard_candidates(os: &str) -> Vec<ClipboardCmd> {
    match os {
        "windows" => vec![ClipboardCmd {
            program: "clip",
            args: &[],
        }],
        "macos" => vec![ClipboardCmd {
            program: "pbcopy",
            args: &[],
        }],
        "linux" => vec![
            ClipboardCmd {
                program: "wl-copy",
                args: &[],
            },
            ClipboardCmd {
                program: "xclip",
                args: &["-selection", "clipboard"],
            },
        ],
        _ => vec![],
    }
}

/// Copy `text` to the system clipboard via the first working OS tool.
/// No extra crate: the tool is a child process with `text` on stdin.
pub fn copy_to_clipboard(text: &str) -> Result<()> {
    copy_to_clipboard_with(text, &clipboard_candidates(std::env::consts::OS))
}

/// Same as [`copy_to_clipboard`], with an explicit candidate list (tests).
pub fn copy_to_clipboard_with(text: &str, candidates: &[ClipboardCmd]) -> Result<()> {
    use std::io::Write;
    use std::process::{Command, Stdio};

    if candidates.is_empty() {
        anyhow::bail!(
            "no clipboard tool for this platform; stdout was still printed, \
             use --out <path> to save the handoff"
        );
    }
    let mut last_err: Option<anyhow::Error> = None;
    for cmd in candidates {
        let child = Command::new(cmd.program)
            .args(cmd.args)
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn();
        let mut child = match child {
            Ok(c) => c,
            Err(e) => {
                last_err = Some(anyhow::anyhow!("{}: {e}", cmd.program));
                continue;
            }
        };
        if let Some(mut stdin) = child.stdin.take() {
            if let Err(e) = stdin.write_all(text.as_bytes()) {
                last_err = Some(anyhow::anyhow!("{}: write stdin: {e}", cmd.program));
                let _ = child.wait();
                continue;
            }
        }
        match child.wait() {
            Ok(status) if status.success() => return Ok(()),
            Ok(status) => {
                last_err = Some(anyhow::anyhow!("{}: exit {status}", cmd.program));
            }
            Err(e) => {
                last_err = Some(anyhow::anyhow!("{}: wait: {e}", cmd.program));
            }
        }
    }
    Err(last_err
        .unwrap_or_else(|| anyhow::anyhow!("clipboard copy failed"))
        .context(format!(
            "clipboard tool unavailable (tried {}); stdout was still printed, \
             use --out <path> to save the handoff",
            candidates
                .iter()
                .map(|c| c.program)
                .collect::<Vec<_>>()
                .join(", ")
        )))
}

/// Write the handoff Markdown to `path` (UTF-8). Refuses to overwrite.
pub fn write_handoff_file(path: &std::path::Path, content: &str) -> Result<()> {
    use std::io::Write;
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|e| {
            if e.kind() == std::io::ErrorKind::AlreadyExists {
                anyhow::anyhow!(
                    "refusing to overwrite existing file: {} (handoff not written)",
                    path.display()
                )
            } else {
                anyhow::anyhow!("cannot create {}: {e}", path.display())
            }
        })?;
    file.write_all(content.as_bytes())?;
    Ok(())
}

/// Plain-text rendering for CLI and MCP.
pub fn render_text(b: &HandoffBrief) -> String {
    let mut out = String::new();
    out.push_str(&format!("# Handoff\n{}\n\n", b.headline));
    out.push_str("## Recent sessions\n");
    for s in &b.recent_sessions {
        out.push_str(&format!(
            "- [{}] {} — {} ({} msgs)\n  {}\n",
            s.source_tool.as_deref().unwrap_or("?"),
            s.started_at.format("%Y-%m-%d %H:%M"),
            s.summary.as_deref().unwrap_or("(untitled)"),
            s.message_count,
            s.first_user.replace('\n', " ")
        ));
    }
    if !b.decisions.is_empty() {
        out.push_str("\n## Decisions / preferences\n");
        for d in &b.decisions {
            out.push_str(&format!(
                "- [{}] {}\n",
                d.category,
                d.content.replace('\n', " ")
            ));
        }
    }
    if !b.open_questions.is_empty() {
        out.push_str("\n## Open questions / TODOs\n");
        for q in &b.open_questions {
            out.push_str(&format!("- {}\n", q.replace('\n', " ")));
        }
    }
    if !b.stale_memory.is_empty() {
        out.push_str("\n## Stale memory (expired)\n");
        for s in &b.stale_memory {
            out.push_str(&format!("- {}\n", s.replace('\n', " ")));
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_session(first_user: &str, summary: Option<&str>, msg_count: i32) -> SessionNote {
        SessionNote {
            id: 1,
            session_id: "s1".into(),
            source_tool: Some("claude_code".into()),
            summary: summary.map(|s| s.to_string()),
            started_at: chrono::Utc::now(),
            message_count: msg_count,
            first_user: first_user.to_string(),
        }
    }

    fn make_decision(category: &str, content: &str, project: Option<&str>) -> DecisionNote {
        DecisionNote {
            id: 1,
            content: content.to_string(),
            category: category.to_string(),
            project_name: project.map(|p| p.to_string()),
        }
    }

    #[test]
    fn session_score_prefers_clear_first_user() {
        let good = make_session(
            "Implement OAuth login flow with refresh tokens",
            Some("Auth work"),
            15,
        );
        let empty = make_session("", None, 50);
        assert!(session_relevance_score(&good) > session_relevance_score(&empty));
    }

    #[test]
    fn session_score_prefers_summary() {
        let with_sum = make_session("Fix the login bug", Some("Bugfix"), 5);
        let without = make_session("Fix the login bug", None, 5);
        assert!(session_relevance_score(&with_sum) > session_relevance_score(&without));
    }

    #[test]
    fn decision_score_ranks_decision_above_pattern() {
        let dec = make_decision("decision", "Use PostgreSQL for storage", Some("proj"));
        let pat = make_decision("pattern", "Always write tests first", None);
        assert!(decision_relevance_score(&dec) > decision_relevance_score(&pat));
    }

    #[test]
    fn decision_score_ranks_preference_above_unknown() {
        let pref = make_decision("preference", "Prefer functional style", None);
        let other = make_decision("insight", "Something generic", None);
        assert!(decision_relevance_score(&pref) > decision_relevance_score(&other));
    }

    #[test]
    fn render_text_includes_sections() {
        let b = HandoffBrief {
            project: Some("test".into()),
            generated_at: chrono::Utc::now(),
            headline: "Continue work on test".into(),
            recent_sessions: vec![make_session("Do the thing", None, 3)],
            decisions: vec![make_decision("decision", "Use X", None)],
            open_questions: vec!["What about Y?".into()],
            stale_memory: vec![],
            stats: HandoffStats {
                conversations: 1,
                messages: 10,
                memory_chunks: 2,
                tools: vec!["claude_code".into()],
            },
        };
        let text = render_text(&b);
        assert!(text.contains("# Handoff"));
        assert!(text.contains("## Recent sessions"));
        assert!(text.contains("## Decisions / preferences"));
        assert!(text.contains("## Open questions / TODOs"));
    }

    #[test]
    fn out_writes_file_starting_with_handoff_heading() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("handoff.md");
        let content = "# Handoff\nContinue work on Atlas-demo\n\n## Recent sessions\n";
        write_handoff_file(&path, content).unwrap();
        let written = std::fs::read_to_string(&path).unwrap();
        assert!(written.starts_with("# Handoff"), "got: {written:?}");
        assert_eq!(written, content);
    }

    #[test]
    fn out_refuses_existing_file() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("handoff.md");
        std::fs::write(&path, "previous contents\n").unwrap();
        let err = write_handoff_file(&path, "# Handoff\nnew\n").unwrap_err();
        let msg = format!("{err:#}");
        assert!(
            msg.contains("refusing to overwrite") || msg.contains("existing"),
            "unhelpful error: {msg}"
        );
        assert_eq!(
            std::fs::read_to_string(&path).unwrap(),
            "previous contents\n",
            "file must not be overwritten"
        );
    }

    #[test]
    fn clipboard_command_selection_per_platform() {
        assert_eq!(
            clipboard_candidates("windows"),
            vec![ClipboardCmd {
                program: "clip",
                args: &[]
            }]
        );
        assert_eq!(
            clipboard_candidates("macos"),
            vec![ClipboardCmd {
                program: "pbcopy",
                args: &[]
            }]
        );
        assert_eq!(
            clipboard_candidates("linux"),
            vec![
                ClipboardCmd {
                    program: "wl-copy",
                    args: &[]
                },
                ClipboardCmd {
                    program: "xclip",
                    args: &["-selection", "clipboard"]
                },
            ]
        );
        assert!(clipboard_candidates("plan9").is_empty());
    }
}
