# Screenshots

Screenshots of the GUI are stored here.

## Required screenshots

- [ ] `dashboard.png` — Main dashboard overview (conversation count, memory stats, recent activity feed)
- [ ] `calendar.png` — Calendar month view showing session activity per day
- [ ] `memory.png` — Memory chunks card grid with category filter and confidence indicators
- [ ] `knowledge-graph.png` — Graph visualization of entities and relationships
- [ ] `semantic-search.png` — Semantic search results with similarity scores
- [ ] `conversation-detail.png` — Chat view of a full conversation with tool calls expanded

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
