# Screenshots

Screenshots of the GUI are stored here.

## Available screenshots

- [x] `Dashboard.png` — Main dashboard overview
- [x] `calendar.png` — Calendar month view
- [x] `search.png` — Global search across all artifacts
- [x] `conversations.png` — Recorded Claude Code sessions
- [x] `memory.png` — Memory chunks grid with category filter
- [x] `skills.png` — Registered skills catalog
- [x] `knowledge-graph.png` — Entity / relationship graph
- [x] `projects.png` — Tracked projects
- [x] `prompts.png` — Reusable prompt library
- [x] `ingestion.png` — Pipeline runner
- [x] `sql-console.png` — Direct SQL access

## How to take screenshots

Take screenshots at **1440 x 900** on a dark macOS terminal with the Streamlit
GUI running locally:

```bash
streamlit run gui/app.py
# Open http://localhost:8501 in Chrome or Safari
# Use macOS Screenshot (Cmd+Shift+4) or a tool like Shottr
```

Preferred settings:
- Dark mode (System Preference → Appearance → Dark)
- Streamlit dark theme (already set in `gui/.streamlit/config.toml`)
- Browser zoom at 100%
- No browser chrome visible (use full-screen or crop tightly)

## Naming convention

`<screen-name>.png` — lowercase, hyphenated, no version suffixes.

If you add a new major screen, add a checklist entry above before adding the file.
