# To do

## Folders: a hierarchy, shared settings

(To do after the other items are merged.)

- Folders can be nested; a folder's settings are inherited from its parent (shared).
- The settings are decoupled from the folder: their own table, and a relation table
  folder → settings, so that several folders use the same settings.
- Sharing is all or nothing: a folder uses its parent's settings (all of them), or its own.
- The shared settings:
  1. the starring system (the numbered tag, the number formats, the templates, the
     starting notes: `Folder.citations`);
  2. the excerpt categories.
- Rename "People" to "Reports" in the UI.
- A tree editor of the folders (collapsible): nest, move, rename them.

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
