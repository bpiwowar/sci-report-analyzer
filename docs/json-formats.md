# JSON formats

The JSON files SciReport Analyzer reads or writes outside its database. The database
(`sci-report-analyzer.sqlite`) and its backups (`backups/*.sqlite`) are SQLite, not JSON.

| File | Written by | Read by |
|------|-----------|---------|
| [Settings file](#settings-file) (`sci-report-analyzer-settings.json`) | Settings → Import / export → *Export settings* | Settings → Import / export → *Import* |
| [Ranking records](#ranking-records): `datasets/journals.json` | `scripts/build_datasets.py` | the app (downloaded from GitHub) |
| [Ranking records](#ranking-records): `data/conferences.json`, `data/conferences.past.json` | `scripts/build_core.py` | the app (shipped) |
| [Data directory files](#data-directory-files): `datasets/journals.json`, `datasets/scimago.json`, `datasets/predatory.json` | the app | the app |
| [`location.json`](#locationjson) | Settings → Data | the app, at startup |

Code: `settings_io.py` (settings file), `ranking/datasets.py` (datasets), `config.py`
(`location.json`).

## Settings file

Shareable matching settings: the matching settings (tracks included), the venues with
manual decisions and, optionally, the imported JCR rows. People, publications and their
annotations (a paper's track set by hand included) are never in it. Pydantic models: `settings_io.SettingsFile` and the classes it
uses.

### Versioning

`format` must be `"sci-report-analyzer-settings"`, or the file is rejected. `version` (now
`6`) must be 6 or later: older files (with `flags`, or tracks without `name_rules`) are
refused. Every other field is optional: a missing one takes its default, an unknown one is
ignored.

### Top level

| Field | Type | Default | |
|-------|------|---------|-|
| `format` | `"sci-report-analyzer-settings"` | that | required in effect (the only accepted value) |
| `version` | int | `6` | 6 or later |
| `exported_at` | string \| null | null | ISO 8601, UTC, seconds (`2026-10-04T09:00:00+00:00`) |
| `matching` | [Matching](#matching) | defaults | |
| `venues` | list of [Venue](#venue) | `[]` | only venues with a manual decision or a manual variant are exported |
| `jcr` | list of [ranking records](#ranking-records) \| null | null | JCR rows, when *Include imported JCR rows* is checked |

### Matching

`ranking.service.MatchSettings`, as stored in the database (`app_setting` row `matching`).

| Field | Type | Default | |
|-------|------|---------|-|
| `sources` | object: source → bool | `scimago`, `core`, `jcr`, `predatory`: true; `openalex`: false | ranking sources on / off |
| `min_score` | float | `0.8` | fuzzy-match threshold |
| `conference_alt_score` | float | `0.75` | score of a CORE conference over a journal for a conference-like text |
| `predatory_min_score` | float | `0.9` | score at which a predatory list's entry flags the venue |
| `openalex_score` | float | `0.8` | score given to an OpenAlex answer |
| `merge_title_jaccard` | float | `0.9` | title similarity for two records to be the same paper |
| `merge_year_slack` | int | `1` | years apart two records of the same paper may be |
| `unreliable_venue_sources` | list of string | `["orcid"]` | sources whose venue is used only as a last resort |
| `reflist_match` | float | `0.85` | title overlap linking a list's entry to a paper |
| `reflist_suggest` | float | `0.5` | title overlap proposing a paper (or a HAL document) for an entry |
| `venue_merge_score` | float | `0.5` | similarity proposing to merge two venues |
| `venue_similar_score` | float | `0.3` | similarity listing a venue as similar |
| `former_student_after` | int | `2` | years after a thesis when its author counts as a former student |
| `norm_rules` | list of [NormRule](#normrule) | the built-in rules | applied in order |
| `national_keywords` | list of string | built-in list | words making a venue national |
| `international_keywords` | list of string | built-in list | words making it international |
| `unknown_scope` | `"international"` \| `"national"` | `"international"` | scope of a venue without a clue |
| `kind_levels` | object: kind → level | `{}` | default level per [kind](#venue-kinds), e.g. `{"natl_conference": "C"}`; levels `A*`, `A`, `B`, `C`, `Q1`…`Q4` or any typed text |
| `detection_rules` | list of [DetectionRule](#detectionrule) | the built-in rules | regexes classifying venues, by `id`; a missing one takes its default |
| `tracks` | list of [Track](#track) | the built-in tracks | the satellite tracks, in their order; a built-in one missing is appended with its default |
| `core_edition` | `"publication"` \| `"latest"` | `"publication"` | CORE edition giving a paper its rank |

#### NormRule

A regex substitution cleaning the venue texts (`ranking.normalize.NormRule`). Python syntax,
compiled with `re.ASCII`; `\1` back-references; `{ordinals:en}` / `{ordinals:fr}` stand for
the spelled ordinals.

| Field | Type | Default | |
|-------|------|---------|-|
| `id` | string | required | identity: a merge import compares rules by id |
| `name` | string | required | |
| `description` | string | `""` | |
| `pattern` | string | required | |
| `replacement` | string | `" "` | |
| `ignore_case` | bool | false | |
| `enabled` | bool | true | |
| `sources` | list of string | `[]` | sources it applies to (empty: all) |
| `language` | string \| null | null | language whose words it removes (`en`, `fr`); null: a general rule |
| `example` | string \| null | null | a venue text it changes |

#### DetectionRule

A regex classifying venues (`ranking.detection.DetectionRule`; Settings →
Detection rules). Python syntax (not `re.ASCII`); `(?-i:…)` makes a part case-sensitive;
`{rule:<id>}` stands for another rule's pattern, in a group. An invalid pattern takes the
rule's default.

| Field | Type | Default | |
|-------|------|---------|-|
| `id` | string | required | which rule (below); unknown ids are dropped |
| `pattern` | string | required | |
| `ignore_case` | bool | true | |

The rules (defaults in `ranking.detection.DEFAULT_DETECTION_RULES`), all searched in the
venue text. A rule is general or of a language (Settings → Detection rules, by language);
one *part of* another is searched with it: a text matching either matches the latter (its
leftmost match counts).

| `id` | Language | Part of | Decides |
|------|----------|---------|---------|
| `workshop` | en | | a workshop (ranked as its main conference) |
| `workshop_at` | — | `workshop` | likewise: “X @ SIGIR” |
| `workshop_fr` | fr | `workshop` | likewise: “atelier” |
| `workshop_host` | en | | the workshop's main conference (“co-located with …”): the first group that matches |
| `workshop_host_at` | — | `workshop_host` | likewise, after an “@” |
| `conference` | en | | a conference, when neither the rankings nor the sources tell |
| `conference_fr` | fr | `conference` | likewise |
| `journal` | en | | a journal, likewise (after `conference`) |
| `journal_fr` | fr | `journal` | likewise |
| `joint` | en | | a joint conference (its parts looked for) |

A shared task (`shared_task`) is never detected: it is set by hand. The tracks' rules are
the tracks' own ([Track](#track)); `track_*` rules saved here before version 5 are dropped
(the database migration moved them into the tracks).

#### Track

A satellite track of the conferences (`ranking.tracks.Track`; Settings → Tracks): a paper of
a track is counted apart ("Short CORE A*"), its category striped in the track's colour. The
tracks are tried in their order (Findings last, `findings` being also ranked as its main
conference); the first whose rules match a venue text gives its track.

| Field | Type | Default | |
|-------|------|---------|-|
| `id` | string | required | identity (a merge import compares tracks by id); `[a-z0-9_]`, never `main` |
| `names` | object: language → string | `{}` | its name by language (`{"en": "Short", "fr": "Court"}`); a missing one: the English one, else the id |
| `colour` | string | `"#2f6fb0"` | CSS colour (its chips, its categories' stripes) |
| `rules` | list of [TrackRule](#trackrule) | `[]` | |
| `name_rules` | list of [NameRule](#namerule) | a built-in track's: its defaults; else `[]` | the name of the conference of a venue marked as the track |

The built-in tracks (`ranking.tracks.DEFAULT_TRACKS`): `findings` (`#2f6fb0`), `tutorial`
(`#1a7f37`), `demo` (`#8a6fd0`), `short` (`#d4a72c`); they cannot be deleted. A variant's,
a venue rule's or a paper's track (set by hand: `publication.track_override` in the
database, `"main"` for the main track) is a track's `id`.

#### TrackRule

| Field | Type | Default | |
|-------|------|---------|-|
| `id` | string | required | a built-in rule's id (`track_short`, `track_short_fr`…: its default known), else an added one's (`<track>_<n>`) |
| `pattern` | string | required | Python regex (`re.search`); `""`: matches nothing; invalid: a built-in rule's default, an added one ignored |
| `ignore_case` | bool | true | |
| `language` | string \| null | null | the language of its words (`en`, `fr`); null: general |
| `examples` | list of string | `[]` | venue texts it should match (shown in Settings) |

#### NameRule

A replacement regex (`ranking.tracks.NameRule`): a venue marked as a track of a conference
with no venue of its own (Venues → *Mark as a track*) is proposed the name its track's name
rules give from its name, applied in turn (spaces then collapsed); none when they change
nothing.

| Field | Type | Default | |
|-------|------|---------|-|
| `id` | string | required | a built-in rule's id (`name_demo_part`…: its default known), else an added one's (`<track>_name_<n>`) |
| `pattern` | string | required | Python regex (`re.sub`); `""`: changes nothing |
| `replacement` | string | `""` | `\1`, `\g<name>` for the groups |
| `ignore_case` | bool | true | |
| `examples` | list of string | `[]` | venue names it should change (shown in Settings) |

### Venue

`settings_io.VenueIO`. On import a venue is found by its variants' keys (computed with the
local rules), else by the key of its name; none found: it is created. Null fields are not
imported.

| Field | Type | Default | |
|-------|------|---------|-|
| `name` | string | required | |
| `variants` | list of [Variant](#variant) | `[]` | |
| `short_name` | string \| null | null | Set by hand, e.g. `"ICLR"`; `""`: the venue has no acronym; null: inferred (not exported) |
| `url` | string \| null | null | website |
| `kind` | string \| null | null | a manual [kind](#venue-kinds) only (an automatic one is not exported) |
| `level_type` | `"conference"` \| `"journal"` \| null | null | null with a `level_rank`: conference |
| `level_rank` | string \| null | null | manual level: `A*`…`C` (CORE) or `Q1`…`Q4` |
| `record_key` | string \| null | null | ranking record picked by hand: `source:type:id`, `id` being the record's `sourceId` or its normalized name (`scimago:journal:21100497291`, `core:conference:acm international conference on research and development in information retrieval`) |
| `match_text` | string \| null | null | "Search rankings as" text |
| `patterns` | list of [VenuePattern](#venuepattern) \| null | null | venue rules |
| `identifiers` | object \| null | null | `{"issn": ["1234-5678", …]}` |
| `hosts` | list of [Host](#host) \| null | null | a workshop's main conferences |
| `joint` | [Joint](#joint) \| null | null | a joint venue's parts |

#### Variant

A raw venue text of the venue (`VariantIO`).

| Field | Type | Default | |
|-------|------|---------|-|
| `raw` | string | required | the raw text; its key is computed on import |
| `source` | string \| null | null | its source (`dblp`, `hal`, `orcid`, `openalex`, `semanticscholar`, `scholar`, `doi`, `thesesfr`): that source's rules apply |
| `manual` | bool | true | assigned by hand (never re-assigned automatically) |
| `track` | string \| null | null | a [track](#track)'s `id` (`findings`, `tutorial`, `demo`, `short`…) |

A variant already in another local venue moves only with *Replace*, or when its conflict is
taken in a merge.

#### VenuePattern

A regex: the source venue texts it matches (`re.search`) belong to the venue
(`ranking.service.VenuePattern`).

| Field | Type | Default | |
|-------|------|---------|-|
| `pattern` | string | required | |
| `ignore_case` | bool | true | |
| `sources` | list of string | `[]` | sources it applies to (empty: all) |
| `track` | string \| null | null | [track](#track) of the matching papers (its `id`) |
| `note` | string \| null | null | |

#### Host

| Field | Type | Default | |
|-------|------|---------|-|
| `venue` | string | required | the main conference, by name (matched by name, else by its key) |
| `start` | int \| null | null | first year (included; null: open) |
| `end` | int \| null | null | last year (included; null: open) |

#### Joint

| Field | Type | Default | |
|-------|------|---------|-|
| `parts` | list of string \| null | null | its venues, by name; null: found automatically; `[]`: not joint |
| `use` | string \| null | null | the part whose level it takes |

`hosts` and `joint` name other venues: they are resolved once every venue of the file is
imported; an unknown name is dropped.

#### Venue kinds

Keys of `ranking.kinds.KINDS`: `intl_conference`, `intl_workshop`, `intl_journal`,
`natl_conference`, `natl_workshop`, `natl_journal`, `shared_task`, `preprint`, `book`,
`chapter`, `proceedings`, `software`, `dataset`, `thesis`, `other`. `proceedings` and
`thesis` are publication kinds only: valid in `kind_levels`, not as a venue's `kind`.

### Import modes

- **Replace**: erases every venue decision (kind, level, record, search text, short name,
  URL, rules, identifiers, hosts, manual joint; variants and paper links are kept), the JCR
  rows when the file has `jcr`, then applies the file; the matching settings are replaced
  as a whole, except the local tracks added by hand that the file lacks (papers, variants
  or venue rules may be of them): they are kept, after the file's, unless removed.
- **Merge**: keeps local values and adds the imported ones. A value set on both sides and
  different is a conflict (matching field, cleaning or detection rule or track by `id`,
  venue field, variant); the local value stays unless the imported one is taken. A track
  only in the file is added (after the local ones). JCR rows whose
  `name` is already there are skipped.

Before importing, the file's tracks are shown against the local ones (by `id`): added (in
the file only), in both (identical, or which one is kept), and the local tracks added by
hand that the file lacks, each kept or removed as chosen (a removed one is no paper's,
variant's or venue rule's track any more).

### Example

```json
{
  "format": "sci-report-analyzer-settings",
  "version": 6,
  "exported_at": "2026-10-04T09:00:00+00:00",
  "matching": {
    "sources": {"scimago": true, "core": true, "jcr": true, "openalex": false, "predatory": true},
    "min_score": 0.8,
    "norm_rules": [
      {
        "id": "ordinalsEn",
        "name": "Ordinals",
        "description": "Remove ordinals: 1st, 35th…",
        "pattern": "\\b(?:\\d+(?:st|nd|rd|th)|{ordinals:en})\\b",
        "replacement": " ",
        "ignore_case": true,
        "enabled": true,
        "sources": [],
        "language": "en",
        "example": "Fourteenth ACM Conference on Recommender Systems"
      }
    ],
    "national_keywords": ["conférence", "colloque", "CORIA"],
    "international_keywords": ["international", "ACM", "IEEE"],
    "unknown_scope": "international",
    "kind_levels": {"natl_conference": "C"},
    "detection_rules": [
      {"id": "workshop", "pattern": "\\bworkshops?\\b|\\bseminars?\\b", "ignore_case": true}
    ],
    "tracks": [
      {
        "id": "short",
        "names": {"en": "Short", "fr": "Court"},
        "colour": "#d4a72c",
        "rules": [
          {"id": "track_short", "pattern": "\\bshort papers?\\b", "ignore_case": true, "language": "en", "examples": []},
          {"id": "track_short_fr", "pattern": "\\barticles? courts?\\b", "ignore_case": true, "language": "fr", "examples": []}
        ]
      },
      {
        "id": "industry",
        "names": {"en": "Industry", "fr": "Industriel"},
        "colour": "#bf3989",
        "rules": [{"id": "industry_1", "pattern": "\\bindustry track\\b", "ignore_case": true, "language": "en", "examples": []}]
      }
    ],
    "core_edition": "publication"
  },
  "venues": [
    {
      "name": "Small Workshop",
      "variants": [{"raw": "Small Workshop @ Big Conference", "source": "dblp", "manual": true, "track": null}],
      "kind": "intl_workshop",
      "patterns": [{"pattern": "^Small Workshop", "ignore_case": true, "sources": [], "track": null, "note": null}],
      "hosts": [{"venue": "Big Conference", "start": 2018, "end": null}]
    },
    {
      "name": "Journal of Imaginary Results",
      "variants": [{"raw": "J. Imag. Res.", "source": "hal", "manual": true, "track": null}],
      "level_type": "journal",
      "level_rank": "Q1",
      "identifiers": {"issn": ["1234-5678"]}
    }
  ],
  "jcr": null
}
```

## Ranking records

The datasets are JSON arrays of records (`ranking.matcher.Record`, a plain object), one per
line in the built files so that a rebuild gives small diffs. No version field: the app reads
the fields below and ignores others.

Common fields:

| Field | Type | |
|-------|------|-|
| `name` | string | required |
| `source` | `"scimago"` \| `"core"` \| `"jcr"` \| `"predatory"` | required |
| `type` | `"journal"` \| `"conference"` | default `"journal"` |
| `aliases` | list of string | other names matched (acronyms) |
| `issn` | list of string (or one string) | an ISSN match is exact; Scimago writes `["-"]` when it has none |

A record's identity (`record_key`) is `source:type:` + its `sourceId`, or its normalized name.

### Scimago journals (`datasets/journals.json`)

Built by `scripts/build_datasets.py --scimago=…` from Scimago's CSV exports, merged with the
file already there.

| Field | Type | |
|-------|------|-|
| `sjr` | float \| null | SJR of `sjrYear` |
| `quartile` | string \| null | best quartile of `sjrYear` (`Q1`…`Q4`, `-`: none) |
| `hindex` | int \| null | |
| `sourceId` | string | Scimago id: identity when merging years |
| `sjrYear` | int | the latest year listed; the other fields are of that year |
| `sjrHistory` | object: year (string) → quartile | quartile each year; a paper takes the one of its year |

```json
{"name":"Journal of Imaginary Results","source":"scimago","type":"journal","sjr":18.5,"quartile":"Q1","hindex":120,"issn":["12345678"],"sourceId":"1","sjrYear":2024,"sjrHistory":{"2021":"Q2","2024":"Q1"}}
```

### CORE conferences (`data/conferences.json`, `data/conferences.past.json`)

Built by `scripts/build_core.py`: `conferences.json` holds the conferences of the latest
edition, `conferences.past.json` those only listed in earlier ones.

| Field | Type | |
|-------|------|-|
| `coreRank` | string | rank in `coreEdition` (`A*`, `A`, `B`, `C`, …) |
| `coreEdition` | string | the latest edition listing it (`CORE2008` … `ICORE2026`) |
| `coreId` | string | CORE id, linking editions |
| `coreHistory` | object: edition → rank | rank in each edition |

```json
{"name":"AAAI Conference on Human Computation and Crowdsourcing","source":"core","type":"conference","coreRank":"B","coreEdition":"ICORE2026","aliases":["HCOMP"],"coreId":"2264","coreHistory":{"CORE2021":"B","CORE2023":"B","ICORE2026":"B"}}
```

### JCR rows

Parsed from the user's JCR CSV export (`jcr_rows_from_csv`), stored in the database and in
the settings file's `jcr`: `name`, `source: "jcr"`, `type: "journal"`, `impactFactor` (float
\| null), `quartile` (`Q1`…`Q4` \| null), `issn` (list).

### Predatory list

From the stop-predatory-journals CSVs: `name`, `source: "predatory"`, `type: "journal"`,
`predatory: true`, optional `url` and `aliases` (its abbreviation).

## Data directory files

In `<data dir>/datasets/`, written by the app (atomically, through a `.tmp` file):

- `journals.json`: the Scimago journals downloaded from the repository, merged into the
  previous copy (a journal dropped since keeps its years); `journals.etag` next to it. Not
  used when the app runs from the source tree (the repository's file is read instead).
- `scimago.json`: the Scimago years imported in the app,
  `{"years": [2021, …], "journals": [records]}`, each journal once; merged into the
  journals on load.
- `predatory.json`: an array of predatory records, refreshed weekly.

## `location.json`

`$XDG_CONFIG_HOME/sci-report-analyzer/location.json` (default `~/.config/…`): the data
directory chosen in Settings → Data, `{"data_dir": "/path/to/dir"}`. Ignored when
`--data-dir` or `SCI_REPORT_ANALYZER_DATA` is given; removed when back to the default.
