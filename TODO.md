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

## Tracks replace flags (in progress)

- Tracks as editable definitions (Settings): a name, a colour used across the interface
  (variant and paper chips, categories, distribution, reports), an order and their
  matching rules (the track detection rules, by language, default / edited / added).
- No more flags: a manual track override per paper (automatic / main track / a track).
- Migration: a paper flagged with a track gets that track as its override, the flags'
  colours become the tracks' colours, flags without a track become tags; then the flag
  tables are dropped.

## PDF: excerpts and PDF.js highlights

When adding an excerpt from text already highlighted with PDF.js, remove that PDF.js
highlight so that the two don't conflict (overlapping marks).

If the bundled PDF.js supports it, enable its "note" (comment) annotation tool along with
highlighting (check the PDF.js version and its editor modes / `annotationEditorMode`).

## PDF viewer: the editor pane

- Resizable layout: drag the separator between the editor pane (notes, excerpts…) and the
  PDF display (remember the split).
- One editor per folder, not per document: several documents are annotated, but the notes
  are a single text. A quote then names its source document explicitly (e.g. its title
  or a short reference with the page, linking back to the place in that PDF).
