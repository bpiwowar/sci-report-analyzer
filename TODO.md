# To do

## Settings: saving

Saving the preferences is easy to forget (each screen has its own save button).

1. Show the changed (unsaved) settings in the left panel.
2. A global "Save" button at the bottom of the left panel, and "Cancel" (asks for
   confirmation before discarding).
3. A "Cancel changes" button on each screen (discards that screen's changes, no
   confirmation).

## Venues: promote a variant into a rule

In a venue's "Matching (rules and variants)", turn a variant into a venue rule (a regex,
prefilled from its cleaned text, to edit) so that its near variants match too.

The variants the rule matches are then redundant: remove them automatically when the
rule is saved (and when any venue rule is edited), with a short message ("3 variants now
matched by the rule were removed").

A variant matched by the rule but with another track (a demo variant, a rule without
track) conflicts with it: never save a rule in conflict with variants. Ask the user:
remove those variants (they take the rule's track), or edit the regex so that it no
longer matches them.

## Tracks: follow-ups

- Publications panel: a track filter (the flag filter is gone).
- A new track's id (from its English name, fixed afterwards): ensure it is unique, and
  warn the user that it cannot be changed later.
- Settings import: validate the mapping of the file's tracks to the local ones (which
  are added, removed, kept), shown to the user before applying, instead of silently
  keeping the local tracks the file lacks.
- "Mark as a track" (a venue that is a track of a conference with no venue of its
  own): no suggested conference name from hard-coded track words
  (`venues.track_free_name`, `_TRACK_PARTS`, `_TRACK_WORDS`: remove them). If a name is
  proposed at all, it comes from a setting of each track (Settings → Tracks): how to
  extract the conference name from a venue text (a regex with a capture group, or a
  replacement regex), editable, with its default / edited / added marker like the
  other rules.

## PDF viewer: the restart notice

With `--live-reload`, the "restart" offer (a new version of the code) must also show on
the PDF viewer page (it does not there now), e.g. in its header bar.

## PDF: excerpts and PDF.js highlights

When adding an excerpt from text already highlighted with PDF.js, remove that PDF.js
highlight so that the two don't conflict (overlapping marks).

Remove the "tune" button of the PDF toolbar (hide / show the editing tools' options
panel): it does not work (no effect, no tooltip).

If the bundled PDF.js supports it, enable its "note" (comment) annotation tool along with
highlighting (check the PDF.js version and its editor modes / `annotationEditorMode`).

## PDF viewer: the editor pane

- The editor pane in a separate window (e.g. on another screen), kept in sync with the PDF
  window (quotes, positions).

## Folders (after the folder-wide notes above)

- Mark some papers of a folder with tags (e.g. "starred"); several tags, each with its
  name.
- Remove the "report on cited papers" view: merged into the folder notes. Next to the
  "quote" icon, another icon colour-coded by citation status, with the details on hover:
  all cited / cited but not in the range / not cited.
- Migration to the folder-wide notes: append to each folder's notes a final "Starred
  papers" section holding everything the old per-paper notes / report had (each starred
  paper with its citation and notes), so nothing is lost when that view goes.
- `.index` (and `.number`) in the citation templates: set by the folder's settings, with
  the tag whose papers are numbered (`.index` = the paper's number among the "starred"
  ones, in my case) and the number's format (e.g. `**#{index}**`), instead of a global
  setting.
- Citation templates defined in general and per folder (a folder's definition overrides
  the general one), and usable inside other templates: e.g. `.starred` =
  `.index (.short-venue .year)`, where `.index` is itself expanded (the paper's index
  among the folder's starred papers, as set above).
