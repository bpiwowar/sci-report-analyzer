## What SciReport Analyzer does

SciReport Analyzer gathers the publications of the people you follow from several sources, merges
them, and ranks the **venue** (journal or conference) of each paper — rankings rate venues,
not individual papers. Each person also gets folders with periods, tags, notes, stored PDFs
and reports, to prepare an evaluation.

## People and sources

1. **Add a person** (name, optional affiliation). Every source is searched and the possible
   profiles are listed as *candidates* in the **Sources** tab.
2. **Validate** the right profiles and **reject** the others. Only validated profiles are used.
   You can also paste a profile URL or id (DBLP, HAL, ORCID, Semantic Scholar, Google Scholar,
   theses.fr, OpenAlex). Once a source is validated, a candidate's papers can be **checked
   against it** (⋂ button): the card then says how many of them are already known, which
   also counts in its score. Its ORCID is shown in red when it differs from the person's (or
   from the one the validated profiles give).
3. Each source shows its **update status**: *never synced*, *updating…*, *up to date*,
   *out of date* (validated after its last sync) or *error*. Syncing is on demand only; a
   banner tells you when some sources are not up to date.

| Source | What it provides |
|---|---|
| DBLP (SPARQL) | Computer-science publications, exact author order |
| HAL | French open archive, PDFs, document types |
| ORCID | Works declared by the researcher (no author lists) |
| Semantic Scholar | Publications, open-access PDFs (disambiguation can be noisy) |
| Google Scholar | Profile pages; if blocked, upload a saved profile page |
| theses.fr | PhD theses supervised, reviewed (rapporteur), examined, presided |
| OpenAlex | Needs a (free) API key: set it in Settings → API keys |

Your **email** (Settings → API keys) is required before anything is fetched: it is sent to
the sources (OpenAlex, Crossref and Unpaywall ask for it) so they can reach you.

A **primary source** (Settings → Sources; a folder can use another one, or none) decides
which papers count: one it doesn't list for the person is left out of the panel, the reports
and the summary, e.g. HAL, where CNRS researchers must deposit their papers. The switch
*only those not in HAL* lists them, to add them there. The other sources still help find
the venue and track. People without a validated profile on that source are not affected.

## Merging

Records from different sources are merged when they share a DOI, or have the same title
(or nearly) and close years. **Preprints** (arXiv, HAL preprints…) fold into the published
version whatever their year. If two different works were merged, **split** a record from the
details; if one work appears twice, **merge** it into the other. Misattributed records can
be **hidden**.

## Venues: kind, then rank

Every publication is linked to a **venue** (see the *Venues* page), except books and book
chapters: their venue text is their own title, series or publisher, so they have none (unless
linked to one by hand).
Each source gives a venue text, which is matched to a venue:

1. The text is cleaned by the **normalization rules** (Settings: regular expressions,
   possibly for some sources only, removing years, ordinals, page ranges…).
2. It belongs to the venue having it as a **variant** (same cleaned text); a variant added
   by hand wins over everything else. Otherwise a **venue rule** (a regex on the source's
   text, on the venue's Matching tab) can claim it; otherwise a new venue is created.
   A record with an ISSN listed on a venue belongs to it whatever its text.
3. Variants and venue rules can mark papers with a **track** (demo, findings…).

When the sources of a paper give different venues, one is picked automatically; you can
validate another source for the paper. Settings → Data & cache can clear out everything
computed automatically (manual decisions are kept) and match again.

**DOI records.** When a paper has a DOI (from a source, or given by hand in its details), the
record registered with it (Crossref, else DataCite and the other registries) is fetched once
and kept forever. It is the paper's main source: its title, year, authors and venue are used,
the other sources only fill in what it lacks. A book chapter without an event (e.g. a volume
of a series such as LNCS) names no real venue: the other sources give it.

**Sources used.** Settings → Matching chooses the publication sources the app uses; a disabled
one is neither searched nor synced, and its records are left out (kept for later).

Venues are classified in two levels:

1. **Kind**: preprint, international or national conference, international or national
   workshop, international or national journal, book (books and chapters), or other
   (theses, reports…). Venues ranked by CORE / Scimago / JCR are international; others are
   classified with keywords (Settings → Venue kinds).
2. **Rank**: CORE level for conferences, quartile for journals, matched in the ranking
   datasets. A kind can have a **default level** (e.g. every national conference counts as
   "C"), used unless a manual decision says otherwise.

**CORE editions.** CORE ranks change over the years (editions 2008 to 2026). A paper gets the
rank of the edition in force in its year: a 2019 paper, the CORE2018 rank (Settings → Venue
kinds can use the latest edition instead). These rankings ship with the app
(`scripts/build_core.py` rebuilds them).

**Scimago years.** Likewise, a journal's paper gets its Scimago quartile of the paper's year
(else of the closest year before, else the first one). The Scimago data is downloaded from
the project's repository and checked every week; its years are listed in
Settings → Data & cache (“Scimago, past years”); scimagojr.com blocks the app, so a missing
year is downloaded in the browser and its CSV dropped there (or from a paper's details,
“Import”). It is kept (past years don't change) and also brings back the journals no longer
listed (e.g. discontinued ones).

**Data sources.** Journals: [SCImago Journal & Country Rank](https://www.scimagojr.com)
(*SCImago, (n.d.). SJR — SCImago Journal & Country Rank [Portal]*). Conferences:
[ICORE conference rankings](https://portal.core.edu.au/conf-ranks/). Predatory venues:
Beall's list, as maintained by
[stop-predatory-journals](https://github.com/stop-predatory-journals/stop-predatory-journals.github.io)
(refreshed weekly; being listed is an indication, not a verdict). JCR: your own import
(Clarivate data is never shipped). Cite the sources when you report ranks.

**Workshops** are venues of their own, with one or more **main conferences** (with years, as
a workshop can move). A workshop paper takes the rank of the main conference of its year and
is counted in its own category, e.g. "Workshop A*". Texts like "X @ SIGIR", "co-located with
…" or "Workshop on …" make a venue a workshop; its venue page suggests the main conference.
A conference venue whose variants look like workshops offers to split them into workshop
venues.

**Joint venues** are conferences held together (e.g. CORIA-TALN): a venue whose texts name
the acronyms of two conferences or more is made of them (its venue page shows them, and
they can be changed by hand). Its papers take the level its conferences share; when they
differ, the venue is listed in the **Multiple conferences** tab, where you choose which
conference's level to use (until then, the lowest). A level set by hand on the venue or the
paper wins, as always.

Everything can be decided by hand, **for the venue** (the default) or **for one paper** (tick
"Only for this paper"): link a paper to another venue, set its kind, pick a
ranking record or set a level (for one paper, say why). A paper's year and author
position can also be corrected. Manual decisions are flagged and
never overridden by automatic processing or re-syncs. On the venue pages you can also
**merge** variants that are the same venue (giving the result a new name if you like), or
split one out; **Propose merges…** goes through the venues that look alike, one pair at a
time: choose the primary venue, then say what the other one is for it (a pair said *not the same* is not proposed again), and a venue page lists the venues
that look related. Each comes with the relation guessed: the same venue, one of its tracks (e.g. *Findings of
the ACL: EMNLP* is EMNLP's Findings track), a joint conference including it (EMNLP-IJCNLP)
or one of its workshops; change it if needed, then apply. Venue decisions are part of the
exported settings.

## Categories and levels

<!-- levels -->

Satellite tracks (Findings, demos, short papers…) and workshops are shown as separate
striped categories, e.g. "Findings CORE A" or "Workshop A*" (a track's stripes in its
colour). The tracks, their names, colours and detection rules are set in Settings → Tracks
(add one, e.g. an industry track, and its papers are counted apart too); a paper's track
can be set by hand in its details (automatic, the main track, or a track).
When the sources disagree only because some give a track (demo, short…) and others none,
the track wins (demo, Findings, workshop…), whatever the venue each one gives. Different
tracks (e.g. demo and short) are a problem to settle: validate a source in the details, or
set the paper's track. Likewise a workshop
wins over its main conference (e.g. a DOI record giving EMNLP for a BlackboxNLP paper).
Edited proceedings (chairing) keep their venue's rank in categories of their own, e.g.
"Proc. (ed.) CORE A*"; an edited volume without a venue is matched by its title.

## Panel

Click a bar or legend entry to cross-filter: the other charts and the list are restricted
to the selection. An unranked paper shows its kind (e.g. "Natl. conf.", "Book") instead.

Click a publication to open its details:

- **Publication**: links, authors (confirm name matches), sources (split / merge),
  corrections (type, track, year…).
- **Venue matching**: the matching process, step by step — venue texts from each source and
  the venue each belongs to (and how), the venue, its kind, and the rank. Each step says
  whether it is automatic or manual; ✎ **override** opens its editor, ↶ goes back to automatic. When
  several sources give different venues, choose which one to use.

A ⚠ icon marks publications that need a look (name not found among the authors, possible
match to confirm, missing venue or year, sources giving different venues…); the
"with problems" switch lists only those. Hover a source badge for that source's record;
click it to open the page.

**Periods** are named year ranges (e.g. an evaluation period). Within a period you can
**star** papers. The chosen period is remembered.

**Tags & notes** (a paper's details, "Tags & notes" tab): type a name to create a tag. A
**global** tag sticks to the paper; a tag **within a period** (⏱) is set separately in each
period / folder. "starred" (the ★ button) is such a tag. Rename and recolour tags with the
🏷 button or in Settings → Tags & categories. Each paper has a **note** (Markdown),
plus a note within the selected period. The "tags" filter shows the papers having one of
the chosen tags; to write about them, use the report editor (below).

**Tag from a list** (the panel's list button): paste a list of publications, e.g. a report's
numbered list copied from a PDF. Each item is matched with a paper by its HAL id or DOI, else
by its title (the authors and venue around it do not matter); check the matches, add the
papers the sources miss (by the item's HAL id / DOI, or found on HAL), then put a tag (e.g.
starred, within the period) on them with their **numbers** in the list. Filtering on that tag
shows the papers in the list's order (#3 on their tag), and the 🗒 listing numbers them and
ends with the items not found.

Notes are Markdown, with **LaTeX**: `$x^2$` inline, `$$\sum_i x_i$$` displayed.
A line break is kept (as in Obsidian). They are edited with a Markdown editor (toolbar,
⌘B / ⌘I, a preview beside or instead).

**Citations in the folder notes** (the folder tab next to a PDF): the person's notes within
the folder cite their papers with Pandoc's syntax: `[@key]` the paper's number, `[@a; @b]`
several, `@key` its title, venue, year and category, `[@key]{.notes}` with its notes,
`[@key]{.tags}` with its tags (`{.notes .tags}` both), and templates:
`[@key]{.short-venue (.year)}` gives `EMNLP (2026)` (fields: `.number` the number as the
folder writes it, `.index` the bare number as in `{**#.index**}`, `.title`, `.venue`,
`.short-venue` the acronym, `.year`, `.tags`, `.notes`; a bracket without a value is dropped).
Named templates (Settings → Citation templates, or the folder's own) are used as `.name`.
The folder's settings (the ✓ icon of its notes, or its dialog) set the tag whose papers are
numbered (as listed, else by year; the others as first cited) and how numbers are written:
one format for the listed papers (`**#{index}**`), one for the others (`[{index}]`). The
papers to discuss are the listed ones (else those of the period's years); the ✓ icon is red
until each is cited, orange if one cited is not of the period's years. The editor's
"Papers" mode shows, instead of the preview, the papers to discuss with their numbers (red
until cited, orange: not of the period's years; a click cites it, the ⋮ menu with a
template), the one at the cursor highlighted, a search (Enter cites the first match; the
person's other papers too), and "Cite them" (those not cited yet). Copy substitutes the
citations, followed by the references, numbered as cited.

**PDFs**: the PDF icon next to a title opens the paper's stored PDF in a new window (red;
✎ once annotated). A grey icon means an open-access link (or a DOI) is known: click it to
download the PDF (after confirmation, unless turned off) and open it. The viewer (PDF.js,
installed once) has highlight, text, drawing and image tools; annotations are saved into the
stored PDF (every few seconds, with Save or ⌘S/Ctrl+S). ← / → go back and forth after
following a link inside the PDF. The side panel button shows the paper's tags and notes
(also those of a period), its details (as in the publications panel) and its bookmarks next
to it; 🔖+ bookmarks the selection (else the place shown), and 🔍 finds the paper of the
selected text (among the person's papers, else on HAL, to add); the actions needing a
selection are greyed out without one. An area of a page selects too (a figure, a table, a
passage over columns): turn the area tool on (the dashed box in the header, or **A**) and
draw a rectangle, or Alt+drag; its text is the selection, and the rectangle its place
(Escape, or a click elsewhere, drops it). Keys (single, but when typing): **H** highlights
the selection (else turns the highlighting tool on or off), **T** text, **D** drawing, **I**
image (on or off), **A** area, **B** bookmark, **F** find the paper, **E** add to a
category; PDF.js's own: ⌘F / Ctrl+F search, + / − zoom, N / P (or J / K) next and previous
page, R rotate, Home / End, Delete removes the annotation selected. The panel's ⬇ button
downloads the PDFs of all the papers shown; "Store a PDF" (details) uploads one. Settings →
Data shows the space used and cleans up the files of papers no longer in the database. A
paper that leaves the sources takes its downloaded PDF with it; an uploaded or annotated one
keeps the paper.

**Documents** (person's Documents tab): PDFs of the person within a folder or a period (an
application, a CV…), uploaded there and read in the same viewer (annotations, bookmarks, a
note). The person's papers mentioned in a document are found (by title, a few typos
allowed, or DOI / HAL id) and become links (a light dotted underline; red when the paper's
PDF is stored). Numbered
references (“[11]”, “11.”, “[C3]”) make their citations (“[11]”, “[3, 11]”, “[3-5]”) point
to the paper too, as do author-year citations (“(Lyu et al., 2023b)”, “Gari Soler and
Apidianaki, 2021, 2020”) matched with the references found (first author, year and letter).
A click shows the paper's details next to the document,
as in the publications panel (tags, notes, its PDF to view online, download or store); “Not this
paper” forgets a wrong match. A reference that was not found: select it, 🔍, then “Link
here”. The papers tab lists them (a search box filters it: every word, in the title,
authors, venue or year). The document's note cites the person's papers as reports do
(`[@key]`, the ❝ button to pick one, those of the document first); its preview and copy
number them (“[1]”, as first cited), the copy ending with the references.

**Categories** (folder page: the category button; or the viewer's categories tab): a
folder's ordered tree of categories (e.g. an evaluation grid), dragged to order and nest
them, each with optional years; one with excerpts (there or below, of anyone in the folder)
is deleted once they are moved to another one, chosen then. In a document's PDF (a report,
an application… not a paper's), select a passage (or click a highlight), then ‘add to a
category’ (header, or the E key): click a category, or type to find it (Enter: the first
one; a new name creates it, the dialog staying open), with the years it is about and whether
it shows the person's influence (“rayonnement”) if need be; when it might be an excerpt
already (similar words, shown with a warning; or found by typing its words), merge it with
that one instead, keeping its text or only its place (a reference). The categories tab lists
the person's excerpts (click: go to the passage), to edit (text, years, influence, colour:
its tint on the PDF; the passages as selected are shown, to go back to or add to the text),
move (with the same picker), merge (drag one onto another, or its merge icon then a click on
the other: grouped, each its own quote or only its place, its link / quote icon switching,
on one item with the other's category, years…; the group's pencil edits its overall text,
its excerpts then cited by their places only, all still tinted on the PDF; taken out again
with its split icon), reorder (dragged onto the top or bottom edge of another one), remove,
or copy as Markdown (unquoted, their places in Obsidian comments: `%% Application, p. 4;
p. 12 %%`, then their years; those showing influence also listed, by category, in a last
“Rayonnement” section; the badge icon names the documents, e.g. a long file name as
“Application”);
the report's ❝ button inserts them, by category.

**Folders** (Reports page) group people, e.g. for a hiring committee: a folder has a name,
a date and can be hidden. Each person has their **own period** in each of their folders
(set in their Periods tab), with its own stars, tags and notes; opening a person from a
folder selects it. Removing a person from a folder (✕ on their card, or in their Periods
tab) either keeps that period, with its data, as one of their own periods, or deletes it.

Folders can be **within other folders**: the **Folders** button of the Reports page shows
them as a tree (collapsible), to rename them, add one within another, and move them (drag
a folder onto another one, or choose where it is in its "In" menu). A folder uses the
**settings** of the folder it is in, or its own ones: all of them, its categories and the
citations in its notes (the numbered tag, the number formats, its templates, the starting
notes). Choosing its own settings starts from a copy of its parent's; going back to its
parent's drops its own (unless other folders use them), its people's excerpts going to the
categories of the same names there (added if missing), as when a folder using its parent's
settings is moved. A folder moved to the top level keeps the settings it used; the
categories and citations dialogs say when other folders use the same ones.

**Co-author categories** (e.g. "Intl. collaborators"): click a co-author's name in a
publication's details to put them in a category (or to say they are the person or one of
their PhD students); its "Look up on…" links search for them on Google Scholar, Semantic
Scholar, DBLP, ORCID, HAL and the web (with the paper's title), in a new window. Categories
are highlighted in author lists and give a "with …" filter
next to "with PhD student"; manage them in Settings → Tags & categories.

Author lists highlight the person (**bold**, using their aliases) and their PhD students
(from theses.fr, with aliases you can add in the Theses tab).

**Contribution role**: the person's role in each paper (sole, first author, contributor,
involved, supervisor, last author…), shown overall and by years; click a bar to filter.
Roles and their rules are set in Settings → Contribution roles: the first rule that matches
wins (else the catch-all role). A rule's condition uses `n` (number of authors), `p` (the
person's position) and `phd` (with one of their PhD students), e.g.
`n>=12 and p>=25% and p<=75%`; a negative position counts from the end (`p>=-3`: one of the
last three authors).

**Theses** (from theses.fr) show their start and end (defence): theses.fr gives the start of
theses in progress only, so for a defended one it is the start seen while in progress, else
estimated (3 years before the defence, marked ≈). They follow the panel's period:
supervision overlapping it, juries with the defence within it.

**Backups** of the database (not of the PDFs) are made in `backups/` next to it (Settings →
Data shows where): one a day at startup (the last 7 kept), and one before each upgrade of
its schema (`pre-migration-…`; one marked `.pending` is from an upgrade that failed, and is
always kept). To restore one, quit the app and copy it over the database file (removing the
`-wal` and `-shm` files next to it).
