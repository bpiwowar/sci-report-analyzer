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
