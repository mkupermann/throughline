<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/throughline-wordmark-dark.svg">
    <img src="docs/brand/throughline-wordmark.svg" alt="Throughline" width="420">
  </picture>
</p>

<p align="center"><strong>Deine Werkzeuge wechseln. Dein Projektkontext darf nicht verschwinden.</strong></p>
<p align="center">Eine selbst gehostete Gedächtnisschicht für KI-Coding-Sessions. Rust-Kern, PostgreSQL, Deine Maschine.</p>

<p align="center">
  <a href="https://github.com/mkupermann/throughline/actions/workflows/ci.yml"><img src="https://github.com/mkupermann/throughline/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-1f2328.svg" alt="Apache-2.0-Lizenz"></a>
  <a href="rust/Cargo.toml"><img src="https://img.shields.io/badge/core-Rust%201.90-b7410e.svg" alt="Rust-Kern"></a>
  <a href="sql/schema.sql"><img src="https://img.shields.io/badge/store-PostgreSQL%2016%20%2B%20pgvector-336791.svg" alt="PostgreSQL mit pgvector"></a>
  <a href="CHANGELOG.md"><img src="https://img.shields.io/badge/status-beta-0550ae.svg" alt="Beta"></a>
</p>

<p align="center"><a href="README.md">English</a> · <strong>Deutsch</strong></p>

Jedes KI-Coding-Tool speichert seine eigenen Session-Dateien und vergisst den Rest. Entscheidungen verschwinden in Transkripten. Korrekturen werden neu erfunden. Kontext wird wieder und wieder erklärt. Throughline importiert diese lokalen Sessions in eine durchsuchbare PostgreSQL-Historie. Du findest eine Entscheidung wieder, prüfst die Original-Nachrichten samt Tool-Aufrufen und nimmst eine quellenverknüpfte Übergabe in die nächste Session mit. Auch beim Werkzeugwechsel.

**Importieren. Prüfen. Merken. Weitermachen.** Stöbern, Stichwortsuche und Übergabe-Briefings funktionieren ohne Modell. Optionale lokale Modelle ergänzen Embeddings, Memory-Extraktion und Antworten mit Quellenangabe.

[Demo starten](#die-demo-mit-fiktiven-daten) · [Installation](#eigene-sessions-nutzen) · [Produkt-Tour](#die-produkt-tour) · [CLI und MCP](#jenseits-des-browsers) · [Architektur](#wie-es-zusammenspielt) · [Grenzen](#aktuelle-grenzen)

[![Animierte Tour durch die Throughline-Oberfläche](docs/media/rust/tour.gif)](docs/media/rust/tour.mp4)

*Echte Anwendung, fiktive Daten, keine gefälschten API-Antworten. [Ganze Tour als Video (MP4)](docs/media/rust/tour.mp4) · [Capture-Manifest](docs/media/rust/manifest.json).*

> **Welche Version ist das?** Das Standard-Docker-Compose baut die Rust-Anwendung mit eingebetteter Browser-Oberfläche. Das Repository enthält zusätzlich eine ältere Python/FastAPI + React-Anwendung. Deren Team-Operations, Template-Bibliothek und Konten-Verwaltung sind keine Funktionen der Rust-Oberfläche. Dieses README beschreibt den Rust-Standardpfad.

## Warum Throughline?

| Wenn Du das brauchst… | …liefert Throughline |
| --- | --- |
| Ein Projekt nach einer Pause wieder aufnehmen | Letzte Sessions, dauerhafte Notizen und ein Offline-Übergabe-Briefing. |
| Verstehen, warum eine Entscheidung fiel | Suchtreffer mit Verbindung zur Original-Konversation. |
| Zwischen Coding-Tools wechseln | Einen gemeinsamen Speicher, Markdown-Export und MCP-Zugriff. |
| Prüfen, was ein Agent aufgezeichnet hat | Nachrichteninhalt, Tool-Call-JSON, Zeitstempel und Quell-Tool-Label. |
| Angesammelten Kontext pflegen | Memory-Kategorien, Deduplizierung von Notizen mit gleichem Anfang, Stale-Memory-Berichte und explizites Writeback. |

### Unterstützte Session-Quellen

Die Rust-Registry enthält **11 Adapter**. Sie lesen lokale Dateien. Es sind keine Cloud-Synchronisierungen. Die Abdeckung unterscheidet sich stark. Ein externes Audit hat sie am 30.10.2026 gegen echte Session-Dateien geprüft.

| Stand | Adapter | Was das heißt |
| --- | --- | --- |
| Mit echten Dateien geprüft | Claude Code, Codex, Vibe | Rollen, Tool-Aufrufe, Modell und Zeitstempel kommen an. Die Token-Summen von Claude Code zählen jeden gestreamten Eintrag und liegen etwa doppelt zu hoch. |
| Teilweise | Cline, Hermes, Windsurf | Cline gibt jeder Nachricht die Startzeit der Session und kein Modell. Hermes liest aus `state.db` nur die erste Session. Windsurf importiert Plandateien, keine Cascade-Gespräche. |
| Nicht geprüft, Pfad oder Format vermutlich falsch | Cursor, Zed, Continue, OpenCode, Aider | Cursor und Zed suchen in Ordnern, die diese Tools nicht nutzen. OpenCode fand 254 echte Dateien und importierte keine. Aider und Continue beruhen auf einem angenommenen Format. |

Das Standard-Compose mountet die ersten neun Tool-Verzeichnisse schreibgeschützt. OpenCode und Aider brauchen ihre Quellverzeichnisse separat. Quelle der Wahrheit ist die [Adapter-Registry](rust/crates/throughline/src/adapters/registry.rs).

## Die Demo mit fiktiven Daten

Drei Befehle. Kein Modell-Download, kein API-Schlüssel. Du brauchst Git und Docker mit Compose.

```bash
git clone https://github.com/mkupermann/throughline.git
cd throughline
docker compose -f docs/demo/rust/compose.yml up -d --build
```

Öffne **[http://127.0.0.1:8796](http://127.0.0.1:8796)**.

Die Demo hat eine eigene Datenbank und ein eigenes Volume. Sie veröffentlicht nur den Web-Port auf Loopback und mountet keine persönlichen Session-Ordner. Ihr öffentliches Passwort gilt nur für diesen Wegwerf-Stack. Die Modell-Endpunkte zeigen absichtlich auf eine nicht erreichbare Loopback-Adresse.

| Demo-Projekt | Geschichte zum Erkunden |
| --- | --- |
| **Atlas-demo** | Lexikalische Suche als Baseline behalten. Ein Gegenbeispiel bei langen Dokumenten untersuchen. |
| **Harbor-demo** | Tastatur-Navigation und sichtbaren Fokus erhalten. Eine Release-Übergabe vorbereiten. |
| **Orchard-demo** | Datenverträge validieren. Die Erholung nach einem abgebrochenen Ladevorgang proben. |

Die Fixture enthält **18 Konversationen, 72 Nachrichten, 12 Memory-Notizen und drei Projekte** über sechs Quell-Tool-Label. Entscheidungen, Tool-Ergebnisse und Modellnamen sind erfunden. Die Daten werden beim Seed zeitlich neu verankert. Das ist kein Benchmark-Beleg und keine abgeschlossene Agenten-Arbeit.

1. Öffne **Projekte → Atlas-demo**, dann eine Sitzung, und klappe die Nachrichten auf.
2. Prüfe das fiktive `Read`-Werkzeugergebnis über **Nachrichten**.
3. Wähle in **Suche** den Modus **Lexikalisch** und suche nach `decision`.
4. Filtere in **Memory** die Kategorie `decision`.
5. Schalte die **Zeitachse** auf **Kalender** um und geh zurück auf **Übersicht** zur Übergabe.
6. Öffne **Import und Status → Status** und prüfe die Summen des Datenbestands.

Stoppen mit `docker compose -f docs/demo/rust/compose.yml stop`. Der [Demo-Leitfaden](docs/demo/rust/README.md) erklärt Neu-Seeden, Screenshots und Aufräumen.

## Eigene Sessions nutzen

Du brauchst Git, Python 3 für den Umgebungs-Initialisierer und Docker mit Compose. Die Anwendung selbst läuft als Rust im Container.

```bash
git clone https://github.com/mkupermann/throughline.git
cd throughline
python3 scripts/init_compose_env.py --check-docker
docker compose up -d --build
docker compose exec web throughline ingest --source all
docker compose exec web throughline doctor
```

Öffne **[http://127.0.0.1:8788](http://127.0.0.1:8788)**. Unter Windows nimm `py -3`, falls `python3` fehlt.

Der Initialisierer erzeugt eine ignorierte `.env` mit zufälligem Datenbank-Passwort und löst den Cline-Mount auf. Die Quellpfade stehen in [docker-compose.yml](docker-compose.yml). Der Import ist explizit. Unveränderte Datei-Hashes werden beim nächsten Lauf übersprungen. Eine neue `.env` rotiert keine Zugangsdaten in einem bestehenden Datenbank-Volume.

### Sichern, aktualisieren und exportieren

Sichere immer mit explizitem Ziel in das gemountete Backup-Volume:

```bash
docker compose exec web throughline backup --dir /var/lib/throughline/backups
git pull
docker compose build web migrate
docker compose up -d migrate web
docker compose exec web throughline doctor
```

Rust liest `THROUGHLINE_BACKUP_DIR`, nicht die ältere Variable `CLAUDE_MEMORY_BACKUP_DIR` im Compose. Das explizite `--dir` hält das Backup aus der Wegwerf-Schicht des Containers heraus. Halte Kopien außerhalb des Hosts vor und prüfe die Wiederherstellung.

```bash
docker compose exec web throughline export-markdown --out /home/throughline/exports
```

Exporte landen im gemounteten `./exports`, solange Du `THROUGHLINE_EXPORT_DIR` nicht änderst. Optionen sind unter anderem `--project`, `--include-generated` und `--redact`. Prüfe einen Export, bevor Du ihn teilst. Die Schwärzung senkt das Risiko, sie garantiert keine Anonymisierung.

**`docker compose down -v` löscht die Daten-Volumes. Auf einer echten Installation nur ausführen, wenn genau das beabsichtigt ist.**

## Die Produkt-Tour

Alle zehn Menüs und ihre Nebenansichten sind unten abgebildet, aufgenommen aus der laufenden Demo. Der [visuelle Funktionsleitfaden](docs/FEATURE_GUIDE.md) zeigt zusätzlich Alternativ-Ansichten, Detailseiten, jede Wartungsaktion und die CLI.

| Menü | Zweck | Kürzel |
| --- | --- | --- |
| Übersicht | Summen, Aktivität, Hinweise, Übergabe und letzte Sitzungen. | `g h` |
| Sitzungen | Sitzungen filtern, sortieren, blättern, exportieren. | `g s` |
| Nachrichten | Nachrichten und Werkzeugaufrufe durchsuchen. | `g b` |
| Memory | Dauerhafte Notizen nach Kategorie prüfen. | `g m` |
| Suche | Lexikalische, semantische und hybride Suche. | `g f` |
| Projekte | Die Sitzungen eines Projekts öffnen. | `g j` |
| KI-Konfiguration | Anweisungen, Skills, Agents und Konfiguration je Assistent. | `g k` |
| Zeitachse | Sitzungen nach Datum und Assistent. | `g t` |
| Recherche | Antworten mit Quellenangaben. | `g a` |
| Import und Status | Import, Wartung und Status. | `g p` |

`Ctrl+K` / `Cmd+K` öffnet die Befehls-Palette. Ein Kürzel heißt: erst `g`, dann die zweite Taste.

**Design.** Die Oberfläche nutzt das Theme Ink: helles Papier, ein Schwarz ohne Farbton, die Schrift Epilogue und feine Linien. Teal steht nur für einen Zustand. **Design** wechselt zwischen Hell, Dunkel und der Systemeinstellung. Beide Modi sind eigenständig gestaltet. Die Schriften liefert der Server selbst unter `/fonts/` aus, die Seite ruft keinen fremden Schriftdienst auf.

**Favoriten.** Jede Sitzung, Nachricht, Memory-Notiz, jedes Projekt und jeder Eintrag der KI-Konfiguration hat einen Stern, ebenso Sitzungs- und Nachrichtendetail. Ein Klick setzt oder entfernt ihn sofort. Jede dieser Listen hat den Umschalter **Nur Favoriten**, der seinen Zustand je Ansicht behält. Die Übersicht zählt alle Favoriten. Die API ist `GET /api/favorites`, `POST /api/favorites` und `DELETE /api/favorites/{kind}/{ref}`, jede Liste nimmt `favorites=1`.

### Übersicht. Den Faden wieder aufnehmen

Klick auf eine Kennzahl und steig in den Datenbestand ein. Die Kennzahl **Favoriten** öffnet die Sitzungen gefiltert auf Ihre Sterne. Prüfe Aktivität, letzte Sitzungen und die Offline-Übergabe. **Leere ausblenden** räumt die Sitzungsliste auf, **Alle durchsuchen** öffnet die Nachrichten. Die modellfreie Demo meldet das fehlende Ollama sichtbar in den Hinweisen. Nichts wird versteckt.

![Übersicht mit Übergabe](docs/media/rust/de/home.png)

### Sitzungen. Konversationen im Zusammenhang

Filtere nach Text, Assistent, Projekt und Datum. Sortiere, wechsle zwischen Tabelle und Karten, speichere Filter, blättere oder exportiere CSV. Öffne eine Sitzung und lies ihre Nachrichten mit Rollen.

![Sitzungstabelle mit Filtern](docs/media/rust/de/history.png)

![Sitzungen als Karten](docs/media/rust/de/history-cards.png)

![Aufgeklappte Sitzung](docs/media/rust/de/conversation.png)

### Nachrichten. Die Nachrichten darunter prüfen

Filtere nach Text, Rolle, Werkzeugname oder Sitzungs-ID, oder wähle **Nur Werkzeugaufrufe**. Eine Nachricht öffnet den vollen Inhalt, das Tool-Call-JSON und die Content-Blöcke, mit **Copy JSON** und **Open session**.

![Nachrichten-Browser](docs/media/rust/de/browse.png)

![Nachrichten-Detail](docs/media/rust/de/message.png)

### Memory. Dauerhaften Kontext griffbereit halten

Durchsuche Notizen, filtere Kategorien und klappe Karten auf. **Ingest agent memory** liest lokale Markdown-Memory-Dateien. `THROUGHLINE_AGENT_MEMORY_DIR` bestimmt deren Ort. Die Kategorien decken Entscheidungen, Präferenzen, Muster, Erkenntnisse, Fehlerlösungen, Projektkontext und Workflows ab.

Jede Notiz zeigt, ab wann sie gilt und, sobald sie ersetzt ist, bis wann. Das ist getrennt vom Zeitpunkt der Erfassung. **Stand am** nimmt ein Datum und beantwortet mit „galt“ oder „war bekannt“, was an diesem Tag galt oder was Throughline bis dahin erfasst hatte. Beim Ersetzen fragt der Dialog, ob es eine Aktualisierung oder eine Korrektur ist. **Nach Bedeutung** ordnet die aktuellen Notizen über `recall` nach Bedeutung. Ohne Ollama sagt die Ansicht das und fällt auf Wörter zurück.

![Fiktive Memory-Notizen mit Kategorie-Filter](docs/media/rust/de/memory.png)

### Suche. Über alle Assistenten hinweg

Wähle **Lexical**, **Semantic** oder **Hybrid**. Die lexikalische Suche braucht kein Modell. Die semantische Suche braucht gespeicherte Embeddings und einen passenden Embedding-Dienst. Treffer verlinken auf Sitzungen und nennen das Such-Backend. Scores sind Retrieval-Werte, keine Wahrheits-Wahrscheinlichkeiten.

![Lexikalische Suche nach decision](docs/media/rust/de/find.png)

### Projekte. Über das Projekt einsteigen

Karten zeigen die aufgezeichnete Beschreibung, den Status und die Zahl der Sitzungen. Eine Auswahl öffnet die Sitzungen des Projekts. Aufgezeichneter Stand ist Historie, keine Prüfung des heutigen Repositories.

![Projektkarten Atlas, Harbor und Orchard](docs/media/rust/de/projects.png)

### KI-Konfiguration. Die Regeln jedes Assistenten sehen

Jeder Assistent hält seine Regeln woanders: Claude Code in CLAUDE.md, `.claude/skills` und `.claude/agents`, Codex in AGENTS.md, `config.toml`, skills und rules, Cursor in `.cursorrules` und `.cursor/rules`, und so weiter für Vibe, aider, opencode, Cline, Windsurf, Continue, Zed und Hermes. Diese Ansicht listet sie je Assistent, global und je Projekt, mit Reitern für Anweisungen, Skills, Agents und Konfiguration. Ein Klick zeigt die Datei als Text. Werte von Schlüsseln wie `api_key`, `token` oder `password` werden maskiert, bevor sie den Server verlassen, und der Server öffnet nur Dateien aus seiner eigenen Liste, nie einen Pfad aus dem Browser.

Die Ansicht zeigt standardmäßig den importierten Stand mit der Zeit des letzten Imports als „Stand". Jeder Import-Lauf und der Job **KI-Konfiguration importieren** unter Import und Status erneuern diesen Stand, mit maskiertem Inhalt, Hash, der Änderungszeit der Datei und Dateien, die inzwischen verschwunden sind. **Live** liest direkt von der Platte. Die Suche findet Anweisungen, Skills und Agents und öffnet den Eintrag.

**Bearbeiten ist abschaltbar und standardmäßig aus.** Mit `THROUGHLINE_CONFIG_EDIT=1` (in Docker zusätzlich `docker-compose.edit.yml`, die die Assistenten-Ordner und `~/Documents/GitHub` beschreibbar einhängt) bietet die Live-Ansicht **Bearbeiten** für gelistete Dateien. Eine Datei mit Schlüsseln oder Tokens bleibt schreibgeschützt. Vor jedem Speichern wird die bisherige Fassung nach `config-edits/` im Backup-Ordner kopiert, und eine Datei, die seit dem Öffnen auf der Platte geändert wurde, wird nicht überschrieben.

![KI-Konfiguration mit dem Reiter Anweisungen eines fiktiven Assistenten](docs/media/rust/de/assistants.png)

In Docker sieht der Container die Assistenten-Ordner, die `docker-compose.yml` read-only einbindet (`~/.claude`, `~/.codex`, `~/.cursor`, `~/.vibe`, `~/Documents/GitHub` und die anderen). `THROUGHLINE_ASSISTANT_HOME` lenkt die Suche auf ein anderes Home, `THROUGHLINE_SCAN_ROOTS` auf die Projektordner, und `THROUGHLINE_HOST_HOME` bildet Host-Pfade in Symlink-Zielen auf das Container-Home ab.

### Zeitachse. Aktivität über die Zeit verfolgen

Prüfe die Sitzungen pro Tag, filtere nach Assistent, wechsle in den Kalender, navigiere durch Monate oder spring zurück auf heute. Chronologie ist keine Kausalität, und die Ansicht hält diesen Unterschied sichtbar.

![Sitzungen nach Tag und Assistent](docs/media/rust/de/timeline.png)

![Kalenderansicht](docs/media/rust/de/calendar.png)

### Recherche. Die aufgezeichnete Evidenz befragen

Stelle eine Frage und folge den gelieferten Quellen. Ein konfiguriertes Generierungs-Backend ist Voraussetzung. Nach der Backend-Auflösung kann ein Generierungsfehler noch Auszüge liefern. Ein nicht auflösbares Backend liefert stattdessen einen Fehler. Der Screenshot zeigt den Eingabe-Zustand, keine erfundene Antwort.

![Recherche vor dem Absenden der Beispielfrage](docs/media/rust/de/ask.png)

### Import und Status. Den Datenbestand pflegen

Starte Import, KI-Konfiguration importieren, Status, Embedding, Deduplizierung, Backup, Audit, Stale-Memory-Bericht, Instruktionsdatei-Writeback oder Antwort-Evaluierung. Einige Aktionen verändern Daten oder Dateien, und modellabhängige Aktionen brauchen vorher Konfiguration. Siehe die [Aktions-Referenz](docs/FEATURE_GUIDE.md#maintenance-actions).

![Alle zehn Wartungsaktionen mit Importstatus](docs/media/rust/de/operate.png)

![Abgeschlossene Status-Aktion der Demo](docs/media/rust/de/operate-status.png)

### Befehls-Palette

Spring zwischen Ansichten, suche Sessions oder wähle eine Aktion, ohne die Tastatur zu verlassen.

![Befehls-Palette](docs/media/rust/de/palette.png)

## Jenseits des Browsers

### CLI-Funktionen

Nutze `throughline --help` und `throughline <befehl> --help` für die Optionen. Im Compose stellst Du `docker compose exec web` voran.

```bash
throughline ingest --source codex
throughline search "decision" --limit 10
throughline handoff --project Atlas-demo --copy
throughline decisions --project Atlas-demo
throughline export-markdown --out ./exports --project Atlas-demo --evidence
```

| Bereich | Befehle |
| --- | --- |
| Einrichtung und Gesundheit | `migrate`, `serve`, `status`, `doctor` |
| Import und Abruf | `ingest`, `import-config`, `search`, `recall`, `context`, `ask`, `handoff` |
| Anreicherung | `extract-memory`, `embed`, `generate-titles`, `extract-entities` |
| Prüfen und pflegen | `decisions`, `costs`, `audit`, `conflicts`, `reflect`, `stale-digest`, `answer-eval`, `recall-eval`, `repair-timestamps` |
| Kontext weitertragen | `scan-instructions`, `writeback`, `export-markdown`, `export-otel` |
| Schützen und integrieren | `backup`, `retention`, `mcp` |

Das sind alle 31 Befehle. Einen Restore-Befehl gibt es nicht. Ein Dump aus `backup` geht mit `psql` zurück. `retention --apply` archiviert Konversationen samt Notizen und löscht sie danach. Ohne `--apply` ist es ein Probelauf.

Die [Funktions-Referenz](docs/FEATURE_GUIDE.md#cli-functions) nennt Abhängigkeiten und Nebenwirkungen. Verfügbarkeit heißt nicht, dass jeder Befehl gegen jede Schema-Variante getestet wurde.

### MCP

Starte `throughline mcp` aus einem kompatiblen Client über stdio, mit den Datenbank-Variablen in dessen Prozess-Umgebung. Das Container-Pendant ist `docker compose run -i --rm mcp`.

Der Rust-Server bietet `search`, `recall`, `project_context`, `list_projects`, `stats`, `write`, `forget` und `supersede` an. `recall`, `supersede` und `write` brauchen das aktuelle Schema (Migration 012 oder neuer). Der Server prüft die Schema-Version nicht. Auf einem älteren Schema scheitern diese Tools mit einem rohen SQL-Fehler, also vorher `throughline migrate` laufen lassen. Der ältere Python-MCP-Server hat einen anderen Tool-Vertrag.

### Optionale Modelle

Setze diese Variablen in der Umgebung des Anwendungs-Containers. Compose braucht einen `environment`-Eintrag oder eine Override-Datei für Variablen, die es nicht ohnehin weiterreicht.

Die Demo bringt ein fertiges Overlay mit. Ein Befehl startet ein lokales Ollama, zieht `nomic-embed-text` plus ein kleines Instruct-Modell und schaltet die Demo auf echte semantische Suche und Ask:

```bash
docker compose -f docs/demo/rust/compose.yml -f docs/demo/rust/compose.models.yml up -d --build
```

Ohne die zweite Datei bleibt die Demo modellfrei. In beiden Fällen verlässt nichts Deinen Rechner.

| Zweck | Rust-Einstellungen |
| --- | --- |
| Datenbank | `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` |
| Ollama-Endpunkt | `OLLAMA_HOST`, z. B. `http://host.docker.internal:11434` auf Docker Desktop |
| Embeddings | `THROUGHLINE_EMBED_BACKEND=ollama`, `OLLAMA_EMBED_MODEL=nomic-embed-text` (der aktuelle Pfad erwartet 768 Dimensionen) |
| Ollama-Generierung | `THROUGHLINE_ANSWER_BACKEND=ollama`, `THROUGHLINE_ANSWER_MODEL=<installiertes-Modell>` |
| Grenzen der Ollama-Generierung | `THROUGHLINE_OLLAMA_THINK=false`, `THROUGHLINE_OLLAMA_NUM_CTX=16384`, `THROUGHLINE_LLM_TIMEOUT_SECS` (Standard 180) |
| Kompatible Generierung | `THROUGHLINE_ANSWER_BACKEND=compatible`, `THROUGHLINE_ANSWER_BASE_URL`, `THROUGHLINE_ANSWER_MODEL`, optional `THROUGHLINE_ANSWER_API_KEY` |
| Backup-Verzeichnis | `THROUGHLINE_BACKUP_DIR` oder CLI `backup --dir` |

Die Rust-Implementierung unterstützt Ollama und einen explizit konfigurierten OpenAI-kompatiblen Generierungs-Endpunkt. Gehostetes OpenAI als benannter Modus ist deaktiviert, und Embeddings laufen über einen Ollama-kompatiblen Dienst. Ein entfernter Endpunkt erhält die Inhalte, die Du ihm schickst. Details stehen in den Quellen zu [Generierung](rust/crates/throughline/src/llm.rs) und [Embedding](rust/crates/throughline/src/embedding.rs).

## Wie es zusammenspielt

```mermaid
flowchart LR
    Files[Lokale Session-Dateien] --> Import[11 Quell-Adapter]
    Import --> DB[(PostgreSQL + pgvector)]
    DB <--> Runtime[Rust-Anwendung]
    Runtime <--> Browser[Eingebettete Browser-UI]
    Runtime <--> Clients[CLI- und MCP-Clients]
    Runtime -. optional .-> Models[Ollama / kompatible Generierungs-API]
    Runtime --> Export[Markdown-Übergaben und Exporte]
    Runtime --> Instructions[Explizites Instruktionsdatei-Writeback]
```

Der Container bettet `rust/crates/throughline/web/index.html` ein. Node ist für den Betrieb nicht nötig. Compose wendet Migrationen an, bevor der Web-Dienst startet. Quelldateien, der PostgreSQL-Speicher und exportiertes Markdown bleiben getrennte Artefakte.

## Nachvollziehbarkeit als Bauprinzip

Regulierer, Prüfer und Forschungseinrichtungen verlangen zunehmend dasselbe: KI-gestützte Arbeit muss im Nachhinein rekonstruierbar sein. Throughline zeichnet KI-Coding-Arbeit genau in der Form auf, die diese Rahmenwerke beschreiben. Das ist eine Design-Aussage, keine Compliance-Zertifizierung.

| Anforderung | Quelle | Was Throughline speichert |
| --- | --- | --- |
| Automatische Aufzeichnung von Ereignissen über die Lebensdauer des Systems, für Nachvollziehbarkeit | [EU AI Act, Art. 12(1) und 12(2)](https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-12), Verordnung (EU) 2024/1689 | Jede importierte Session wird automatisch erfasst, mit Zeitstempeln, Quell-Tool und Modell-Label. Keine manuelle Buchführung. |
| Log-Aufbewahrung von mindestens sechs Monaten durch den Betreiber | [EU AI Act, Art. 19 und Art. 26(6)](https://artificialintelligenceact.eu/article/19/) | Der Korpus liegt im eigenen PostgreSQL-Volume, so lange Du ihn behältst, mit `backup` als explizitem Befehl und Restore über `psql`. Löschen ist eine bewusste Handlung über `retention --apply`. |
| Ein Prüfer muss sehen, welches Modell lief, wie es angewiesen wurde und wie das Ergebnis validiert wurde | [Weltbank-Leitlinien zur KI-Reproduzierbarkeit](https://worldbank.github.io/wb-reproducible-research-repository/guidance/AI_reproducibility_guidelines.html) | Je Nachricht: Modell-Label, vollständiger Prompt- und Antworttext, Rolle, Zeitstempel. Je Antwort in Ask: die zitierten Quelldatensätze. |
| Protokoll je Interaktion mit Zeitstempel, Modellversion, Prompts, Tool-Aufrufen und Ergebnissen für agentengestützte Forschung | [Logging-Schema aus arXiv:2604.00945](https://arxiv.org/pdf/2604.00945) | Der Nachrichtendatensatz enthält genau diese Felder, samt Tool-Call-JSON und Content-Blöcken, prüfbar in Browse. |
| Ein Prüfpaket, das sich unabhängig vom erzeugenden System verifizieren lässt | [Weltbank-Leitlinien zur KI-Reproduzierbarkeit](https://worldbank.github.io/wb-reproducible-research-repository/guidance/AI_reproducibility_guidelines.html), [arXiv:2604.00945](https://arxiv.org/pdf/2604.00945) | `export-markdown --evidence` legt dem Export ein Provenance-Manifest bei: je Konversation Modell, Quell-Tool und Zeitraum, je Datei SHA-256 und Größe, dazu die Korpus-Summen. Deterministisch bis auf den Erzeugungszeitstempel. |
| Standardisierte Trace-Attribute für GenAI-Operationen | [OpenTelemetry-GenAI-Konventionen](https://opentelemetry.io/docs/specs/semconv/gen-ai/) | `export-otel` schreibt den Korpus als OTLP/JSON-Traces mit `gen_ai.*`-Attributen, eine Trace je Konversation, ein Span je Nachricht. Standardmäßig nur Metadaten, Nachrichtentexte nur mit ausdrücklichem Flag. |

Zwei ehrliche Grenzen. Erstens: Throughline zeichnet auf, was die Quell-Tools auf die Platte schreiben. Die Vollständigkeit hängt an deren Session-Formaten. Zweitens: Ob ein konkretes System unter den AI Act fällt, ist eine Rechtsfrage zu diesem System. Diese Software beantwortet sie nicht und behauptet für nichts eine Hochrisiko-Konformität. Was sie liefert, ist das Rohmaterial, das alle diese Rahmenwerke verlangen: die Originalevidenz, lokal gehalten, durchsuchbar, mit intaktem Kontext.

Verwaltungen auf dem Weg zur digitalen Souveränität, etwa die [deutsch-französische Initiative für einen quelloffenen Verwaltungsarbeitsplatz](https://www.dbb.de/artikel-dbb-europathemen/silberbach-begruesst-deutsch-franzoesische-initiative.html), nennen genau die Eigenschaften, auf denen dieses Projekt aufbaut: quelloffen, lokaler Betrieb, keine Anbieterbindung.

## Aktuelle Grenzen

Ehrliche Grenzen sind besser als stille Überraschungen. Das sind die aktuellen.

- **Lokaler, vertrauenswürdiger Betrieb.** Der Rust-Router hat keinen Anwendungs-Login und keine Rollenprüfung. Lass ihn auf Loopback. Ältere Python-Team-Einstellungen schützen diese Laufzeit nicht. Er weist Anfragen ab, deren Host weder Loopback noch `THROUGHLINE_PUBLIC_URL` oder `THROUGHLINE_ALLOWED_HOSTS` ist, Anfragen von fremder Herkunft und API-Aufrufe, die ein Browser als seitenübergreifend kennzeichnet. Eine besuchte Webseite kann ihn also nicht steuern. Routen, die Daten verändern, nehmen nur POST an, und eine API-Anfrage ohne Host-Header wird abgewiesen. Im Browser fragt jede Wartungsaktion, die Daten verändert, vorher nach.
- **Sicherungen brauchen einen gemounteten Ordner.** Der Sicherungs-Knopf und ein nacktes `backup` schreiben in den Container, solange `THROUGHLINE_BACKUP_DIR` nicht auf einen gemounteten Pfad zeigt. Ein solcher Dump ist mit dem Container weg.
- **Wartung nach Faustregeln.** Die Deduplizierung führt Notizen zusammen, deren erste 80 Zeichen gleich sind. Konflikt- und Stale-Bericht melden jede Notiz über ihrer Altersgrenze als nie abgerufen, weil Abrufe noch nicht gezählt werden. Der API-Job `/api/operate?job=extract-memory` zählt Konversationen und extrahiert nichts. Dafür `throughline extract-memory` nutzen.
- **Historische Evidenz.** Konversationen, Notizen und Antworten können unvollständig, veraltet oder falsch sein. Ein Tool-Call-Eintrag beweist nicht, dass ein Befehl gelang oder eine Datei noch existiert.
- **Modell-Validierung ist getrennt.** Die Demo zeigt Navigation und ausgewählte deterministische Aktionen, nicht Modellqualität, Vektor-Retrieval, Agenten-Ausführung oder Backup-Wiederherstellung.
- **Unvollständige Parität.** React-Team-Operations, die Template-Bibliothek, Review, SQL-Konsole und Konten-Verwaltung registriert der Rust-Server nicht. Ältere Dokumentation kann andere Befehle und Variablen verwenden.
- **Begrenzte Evaluierung.** Die Antwort-Evaluierung prüft Zitate und konfigurierte Phrasen. Ihre Quote ist kein allgemeines Maß für faktische Richtigkeit. Die Entitäten-Extraktion nutzt Token-Heuristiken.

Der [Verifikationsnachweis](docs/demo/rust/VERIFICATION.md) listet Belege und offene Lücken. Sicherheitsmeldungen laufen über [Security](SECURITY.md).

## Entwickeln und beitragen

Die Standard-Laufzeit liegt in [`rust/`](rust/). Nutze eine zum Lockfile passende Toolchain. Der Container pinnt Rust 1.90.

```bash
cargo build --release --manifest-path rust/Cargo.toml -p throughline
cargo test --manifest-path rust/Cargo.toml -p throughline --lib
```

Integrationstests brauchen eine Wegwerf-PostgreSQL-Datenbank mit pgvector. Screenshot- und Aufnahme-Werkzeuge liegen unter `web/`. Das macht React nicht zur Standard-Oberfläche.

| Weiterlesen | Inhalt |
| --- | --- |
| [Visueller Funktionsleitfaden](docs/FEATURE_GUIDE.md) | Alle Menüs, Detailansichten, Wartungsaktionen und CLI-Funktionen. |
| [Rust-Demo-Leitfaden](docs/demo/rust/README.md) | Isolierte Fixture, Screenshot-Erneuerung und Aufräumen. |
| [Verifikationsnachweis](docs/demo/rust/VERIFICATION.md) | Was geprüft wurde und was offen bleibt. |
| [Contributing](CONTRIBUTING.md) | Konventionen, und wie sich Rust-Arbeit von Python/React-Prüfungen unterscheidet. |
| [Changelog](CHANGELOG.md) | Dokumentierte Projektänderungen. |
| [Frühere Python/React-Demo](docs/DEMO.md) | Fixture und Aufnahmen der Vorgänger-Anwendung. |

Reproduzierbare Fehler melde über [GitHub Issues](https://github.com/mkupermann/throughline/issues). Beiträge folgen dem [Code of Conduct](CODE_OF_CONDUCT.md).

## Lizenz

[Apache 2.0](LICENSE). Selbst hosten, selbst prüfen, selbst anpassen. Die Lizenz enthält eine ausdrückliche Patentlizenz für alles, was hier beigetragen wird.

Deine KI-Arbeit soll sich verzinsen. Nicht verdunsten.
