# SciReport Analyzer

A local web app (NiceGUI + SQLite) to follow and evaluate researchers: it tracks their
publications across sources, ranks their venues, and keeps per-person folders with tags,
notes and stored PDFs. Venues are ranked with Scimago, CORE (every edition since
2008) and a predatory list.

## Features

- **People** with profiles on DBLP (SPARQL endpoint), HAL, ORCID, Semantic Scholar, Google
  Scholar, theses.fr and OpenAlex (needs a free API key). Profiles are found automatically
  and must be **validated** by hand; each source shows its update status (never synced,
  updating, up to date, out of date, error). Syncing is on demand only.
- **Merging** across sources (DOI, titles, years), preprints folded into the published
  version, PDF links, manual split / merge / hide.
- **Publications panel**: distribution, publications by year,
  co-authors, author position, "with PhD student", cross-filtering, named **periods** with
  **stars**, a paper's **track** (short, demo…) set by hand, **tags** (global or within a
  period) and Markdown **notes**, with a Markdown listing of the noted / tagged papers;
  **tag from a pasted list** (e.g. a report's numbered list: matched by HAL id / DOI / title,
  missing papers searched on HAL and added, the list's numbers kept for reporting).
  Notes accept LaTeX (`$…$`, `$$…$$`), edited with a Markdown editor (toolbar, preview).
- **Notes of a folder** (one Markdown text for all its documents and papers, next to any
  of their PDFs) citing the papers (Pandoc's `[@key]`, `@key`, `[@key]{.notes}`,
  `[@key]{.tags}`, templates such as `[@key]{.short-venue (.year)}`, named and composable,
  general or per folder), substituted (numbers like **#6**, titles, notes, tags); the papers
  with the folder's numbered tag are numbered first, and an icon tells whether each paper to
  discuss is cited.
- **PDFs**: downloaded from the papers' open-access links (sources, else Unpaywall), one by
  one or for all the papers shown, or uploaded; stored in the data directory and read in
  the browser with PDF.js (highlight, text, drawing, images), the annotations saved back,
  the paper's tags and notes alongside, bookmarks, the paper of a selected reference found
  (or added from HAL); clean-up of the files of deleted papers.
- **Documents** of a person within a folder / period (an application, a CV…): PDFs read and
  annotated the same way, with bookmarks and a note; the person's papers they mention are
  found (titles, DOIs / HAL ids, and the citations of numbered references like `[11]`),
  marked, and show the paper's details next to the document; references can be linked by
  hand from a selection; author-year citations (“(Lyu et al., 2023b)”) are linked too.
- **Categories** per folder (an ordered tree, drag and drop, optional years): passages
  selected in the PDFs are filed in them, listed per person, and inserted in notes as
  Markdown.
- **Venues** (Conferences / Journals pages): variants after cleaning, two-level
  classification (kind → rank), default levels per kind, manual decisions per paper or per
  venue that are never overridden.
- Author highlighting: the person (with aliases) and their PhD students (from theses.fr),
  with potential matches to confirm.
- **Settings**: ranking sources, confidence threshold, rewrite and cleaning rules, venue
  kinds, **tracks** (names, colours, detection rules), datasets, JCR import, API keys, and
  **import / export** (replace, or merge
  with per-conflict choices) to share them.

## Usage

```sh
uv tool run sci-report-analyzer   # http://127.0.0.1:8081
```

or install it with `uv tool install sci-report-analyzer` and run `sci-report-analyzer`. Options:
`--port`, `--host`, `--data-dir`, `--no-browser`. Press Ctrl-C, or the power button in the
header, to quit.

Data lives in `~/.local/share/sci-report-analyzer` (override with `SCI_REPORT_ANALYZER_DATA`). Your email is
required before anything is fetched (it identifies you to the sources); it and the API keys can
be given with `SCI_REPORT_ANALYZER_EMAIL`, `OPENALEX_API_KEY` / `S2_API_KEY` or in Settings → API keys.

The database (not the PDFs) is backed up in `backups/` next to it: daily at startup (the last 7
kept) and before each schema migration (`pre-migration-…`; a `.pending` marker next to one means
that migration failed, and it is never removed). To restore a backup, stop the app and copy it
over `sci-report-analyzer.sqlite` (removing the `-wal` / `-shm` files, if any).

## Data sources

Venue ranks come from third-party data, credited here and in the app (Settings → Data):

- **[SCImago Journal & Country Rank](https://www.scimagojr.com)** (journals), downloaded
  by the app from this repository (`datasets/journals.json`, checked every week). *SCImago, (n.d.). SJR — SCImago Journal & Country Rank [Portal].* SCImago allows
  the use of its data as long as the source is cited; the journal metadata comes from
  Scopus (Elsevier).
- **[ICORE conference rankings](https://portal.core.edu.au/conf-ranks/)** (conferences,
  every CORE edition since 2008), shipped with the app.
- **Predatory list**: Beall's list as maintained by
  [stop-predatory-journals](https://github.com/stop-predatory-journals/stop-predatory-journals.github.io)
  (MIT licence), downloaded by the app and refreshed weekly. Being listed is an indication,
  not a verdict.
- **JCR** impact factors are Clarivate's and are not distributed: import your own export.

See [`src/sci_report_analyzer/data/README.md`](src/sci_report_analyzer/data/README.md) for the
files and their terms, and [`docs/json-formats.md`](docs/json-formats.md) for the JSON formats
(settings file, ranking datasets).

## Licence

The code is released under the [MIT licence](LICENSE). The ranking datasets keep their
sources' terms (see above).

## Development

```sh
uv sync
uv run pre-commit install
uv run pytest              # unit + simulated-UI tests
uv run pytest -m live -o addopts=""   # smoke tests against the real APIs
uv run sci-report-analyzer --live-reload  # offers a restart when the code changes
```

- `src/sci_report_analyzer/ranking/` — venue normalization, fuzzy matching and resolution
  (checked against reference fixtures by `tests/test_matcher_parity.py`).
- `sources/` — one adapter per source; `merge.py` — clustering; `sync.py` — syncing and
  auto-matching; `venues.py` — venues; `pubview.py` — panel view model; `ui/` — pages.
- Schema changes go through Alembic migrations (`db/migrations/versions`), applied at
  startup; never edit a released migration.
