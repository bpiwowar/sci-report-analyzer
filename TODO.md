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
