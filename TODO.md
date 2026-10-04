# To do

## Notes in a folder: no more lost text

A person's notes within a folder are one text, open in several places at once (each of their
PDFs in the folder, the side panel in another window). The last save wins even when the
window was out of date, which erased notes twice: an editor holding older text saved over
the "Starred papers" section the migration had just appended, then a save of an empty text
erased the rest.

- Refuse stale saves: a save is refused if the notes changed since that editor loaded
  them; say so, and offer to reload (or to copy the unsaved text).
- Never save an emptied text without asking (confirmation before clearing all the notes).
- Finish `old_reports.migrate()` before pages are served (it now runs in the background
  after start-up, while editors may already show the notes as they were).
- The folder dialog's "Notes" field (the folder's own short text): save it only when
  edited (the dialog may show a stale copy).

## Folder notes: rubrics (live blocks)

Blocks on their own line in a person's folder notes, in the citations' Pandoc style,
expanded live in the preview and in the copy (the notes keep only the line), so that a
copy gives the full text:

- `[]{.publications}`: the publications summary (`pubview.summary_lines`, with the
  panel's saved "Summary" settings), restricted to the period's papers.
- `[]{.excerpts}`: the excerpts by category (`categories.markdown`), headings one level
  below the heading the block sits under.
- Two toolbar buttons to insert them (the current excerpts button pastes a snapshot that
  goes stale).
- Optional: a folder skeleton (in the folder's settings), the starting text of each
  person whose notes are empty, e.g. `## Publications` / `[]{.publications}` /
  `## Activities` / `[]{.excerpts}` / `## Starred papers`.

## Folder notes: two numberings

The papers with the folder's tag (starred) and the other papers cited are numbered
apart, each from 1: the starred ones as listed (`**#1**` … `**#10**`, "Number of a
listed paper"), the others as first cited (`[1]`, `[2]`…, "Number format"). Today the
others continue the starred ones' count (a thesis cited beside 10 starred papers is
`[11]`, or `**#11**` when both formats are the same).

- In the text (`[@key]`, `.number`, `.index`) and in the copied references (the starred
  papers, then the others, each list in its own format).
- `.index` is then ambiguous between the two lists: the number within its own list.
- Folders 2 and 4 have "Number format" `**#{index}**` (set before the listed format
  existed): reset it to `[{index}]`, or warn in the dialog when both formats are equal.

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

## PDF: area selection

- Several areas at once (e.g. Shift+drag adds a rectangle to the selection): their texts,
  in order, are the selection.
- "Tag from a list" on the selection (as in the publications panel, `reflist.py`): find
  the papers of the selected list (e.g. an area over a numbered list of references) and
  tag them.
- Copy the selected area's text to the clipboard (⌘C / Ctrl+C, as a text selection,
  and in the tooltip).

## PDF: excerpts and PDF.js highlights

When adding an excerpt from text already highlighted with PDF.js, remove that PDF.js
highlight so that the two don't conflict (overlapping marks).

Remove the "tune" button of the PDF toolbar (hide / show the editing tools' options
panel): it does not work (no effect, no tooltip).

If the bundled PDF.js supports it, enable its "note" (comment) annotation tool along with
highlighting (check the PDF.js version and its editor modes / `annotationEditorMode`).
