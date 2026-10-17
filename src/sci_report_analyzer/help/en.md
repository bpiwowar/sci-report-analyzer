## What SciReport Analyzer does

SciReport Analyzer gathers the publications of the people you follow from several sources, merges
them, and ranks the **venue** (journal or conference) of each paper — rankings rate venues,
not individual papers. Each person also gets folders with periods, tags, notes, stored PDFs
and reports, to prepare an evaluation.

## People and sources

1. **Add a person** (name, optional affiliation and ORCID, the sources to search; in a
   folder, their period and tags in it). The sources are searched and the possible profiles
   are listed in the dialog: tick the right ones (the likely ones are) to validate them right
   away. The others stay *candidates* in the **Profile** tab, under **Sources** (its sub-tabs:
   **Career**, **Periods** and **Sources**; the person is edited, purged or deleted above them).
   Their **career** (their CV) is built there too: positions and diplomas, dated (a year,
   `2019-09` or `2019-09-01`), a diploma with its thesis; the position without an end is the
   current one, shown under their name. Their own thesis on theses.fr (once validated) is
   offered as a PhD to add. A report shows the CV with the block `[[cv]]`.
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

A **primary source** (Settings → Publication sources; a folder can use another one, or none,
in its settings' “Citations & sources” section, and so can a person in a folder, from their
periods) decides
which papers count: one it doesn't list for the person is left out of the reports and the
summary, e.g. HAL, where CNRS researchers must deposit their papers. The panel lists them all
by default (a warning says how many are not in HAL, each one marked); its filter *all / in
HAL / not in HAL* shows only those it lists, or only the others, to add them there. The other sources still help find
the venue and track. People without a validated profile on that source are not affected.

## Merging

Records from different sources are merged when they share a DOI, or have the same title
(or nearly) and close years. **Preprints** (arXiv, HAL preprints…) fold into the published
version whatever their year. If two different works were merged, **split** a record from the
details; if one work appears twice, **merge** it into the other. Misattributed records can
be **hidden** (after a confirmation): they leave the lists, charts, counts, reports and exports,
but stay in the database; the papers' "only the hidden" filter finds them again (to unhide
them). Preprints that are not (yet) published are not shown by default ("hide preprints").

## Venues: kind, then rank

Every publication is linked to a **venue** (see the *Venues* page), except books and book
chapters: their venue text is their own title, series or publisher, so they have none (unless
linked to one by hand).
Each source gives a venue text, which is matched to a venue:

1. The text is cleaned by the **cleaning rules** (Settings → Cleaning rules: regular expressions,
   possibly for some sources only, removing years, ordinals, page ranges…).
2. It belongs to the venue having it as a **variant** (same cleaned text); a variant added
   by hand wins over everything else. Otherwise a **venue rule** (a regex on the source's
   raw text, or on its cleaned text, on the venue's Matching tab) can claim it; otherwise a
   new venue is created.
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

**Sources used.** Settings → Publication sources chooses the sources the app uses; a disabled
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
paper wins, as always. A joint venue can also be added by hand (**Add a venue**, "A joint
conference"): left without a name, it is named after its conferences in their order ("A / B"),
and its acronym joins theirs with dashes (CORIA-TALN).

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

**Not ranked, checked.** A venue with no rank found may be in no ranking, or just not
matched yet: its rank shows "?" (dashed) until someone decides, and the venue lists and the
person's papers can show only those **to check** (as do the papers of a fuzzy match). The
level **Not ranked (checked)** says the venue is in no ranking (for some periods, or every
year: the button of that name, which also keeps its kind as it is); its papers stay in the
category of their kind (and take the kind's default level, if any). A workshop with a main
conference still takes its rank.

**Periods.** A venue's level set by hand can change over time: give one level per period
(e.g. B until 2017, A from 2018) and each paper takes the level of its year; the years no
period covers keep the venue's other ranking (its record, else the automatic one). A venue
also keeps its **former names** (NIPS until 2017, then NeurIPS): a paper shows the name and
acronym of its year. A **venue rule** can be limited to the papers of some years: in those
years it wins over the text's variants (e.g. a workshop that became a conference under the
same name).

**Shared settings.** A group can share its settings and venue decisions through a git
repository (Settings → Shared settings): one file per venue, reviewed like code. **Pull**
takes the group's changes and keeps yours (a decision changed on both sides is a conflict
you resolve); **Push** proposes yours, on a branch of your own for a pull request or on the
group's branch. Each decision records who made it and when (your name, set there), and can
say why (the note of a level). People, papers and JCR rows are never shared. The format:
`docs/json-formats.md`.

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
to the selection (click it again to drop it). **Shift**-click adds an entry to the selection
(or removes it), **Alt**-click selects it alone: e.g. Q1, Q2, CORE A* and CORE A together. A
paper matches one of the entries picked on a chart, and those of all the charts. The chips
next to the filters remove one entry; **Reset** clears all the filters (search, tags,
tracks, selection…), the period and its years aside. An unranked paper shows its kind (e.g. "Natl. conf.", "Book") instead.

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

**Tags & notes** (a paper's details, "Tags & notes" tab): type a name to create a tag; it
sticks to the paper. The **star lists** (★, e.g. "starred") are the folder's ordered lists of
papers, set within each person's period in it: the ★ button puts a paper in the first one,
the details' ★ chips in any. They are the folder's settings (their “Citations & sources”
section: the folder dialog's “Settings” tab, or Settings → Folder settings; their names, their
numbers' format, whether their papers are to be cited, their order). Rename and recolour tags with the 🏷 button or in Settings → Tags &
categories. Each paper has a **note** (Markdown),
plus a note within the selected period. The "tags" filter shows the papers having one of
the chosen tags. The person's **Starred papers** tab shows each star list of the period's
folder, in its order, with its number of papers (following the panel's period). Its ZIP
button downloads the stored PDFs of its papers as one file (“01 - Author - Year - Title.pdf”,
in the list's order); the papers without one are listed in a text file in it (its tooltip
gives “n of m PDFs”; with no stored PDF, it is disabled). To write
about them, use the report editor (below).

**Tag from a list** (the panel's list button): paste a list of publications, e.g. a report's
numbered list copied from a PDF. Each item is matched with a paper by its HAL id or DOI, else
by its title (the authors and venue around it do not matter); check the matches, add the
papers the sources miss (by the item's HAL id / DOI, or found on HAL), then put a tag on
them, or put them in a star list (★, within the period) with their **numbers** in the list.
The Starred papers tab shows that list's papers in its order (#3 on their chip), and the 🗒
listing numbers them and ends with the items not found.
A messy list (lines broken, words cut, items run together, page numbers mixed in): **Read
with the LLM** (optional, see Local AI below) has the local LLM read it into references
(title, authors, venue, year, DOI or link), shown each with its title first and matched by
it; a long list is read in parts, in its order, the part being read shown (Stop: the parts
not read split by their lines, as without it). A part without a valid answer is split by
its lines too; a DOI or link the LLM gives that is not in the list is left out. An item read
is edited by its parts (title, authors, venue, year, DOI). Off (the LLM off, or no model):
the button is greyed, the dialog says so, with a link to its settings.

**Numbers in a star list** (a paper's place in it): Alt-click a number (`#3` next to the
list on a paper's row; in its details, where `#` is shown when it has none, a plain click
too) to type it (Enter or leaving it saves, Escape cancels, empty removes it). To order all the papers of a list, click the
numbered-list button next to its name in the Starred papers tab: drag the papers or use their ↑ / ↓
arrows (they are then all numbered 1, 2… in the new order), type a number, “Number in this
order” or “Clear the numbers”. Papers without a number come after the numbered ones (latest
first), in the list as in the citations.

Notes are Markdown, with **LaTeX**: `$x^2$` inline, `$$\sum_i x_i$$` displayed.
A line break is kept (as in Obsidian). They are edited with a Markdown editor (toolbar,
⌘B / ⌘I, a preview beside or instead). A link is followed with a click in the preview, or a
⌘/Ctrl-click in the editor: `[Dossier CRCH, p. 15](/doc/2?page=15)` opens the document at
that page (in the PDF window showing it: there), others in a new tab.

**Citations in the folder notes** (the folder tab next to a PDF): the person's notes within
the folder cite their papers with Pandoc's syntax: `[@key]` with its citing format (that of
its star list, else that of the papers cited only; none: its reference), `[@a; @b]`
or `[@a, @b]` several (each with the template, as in `[@a, @b]{.notes}`, also written
without brackets: `@a, @b{.notes}`), `@key` its title, venue, year and category, `[@key]{.notes}` with its notes,
`[@key]{.tags}` with its tags (`{.notes .tags}` both), and templates:
`[@key]{.short-venue (.year)}` gives `EMNLP (2026)` (fields: `.number` the number as its
star list writes it, `.index` the bare number as in `{**#.index**}`, `.title`, `.venue`,
`.short-venue` the acronym, `.year`, `.tags`, `.notes`; a bracket without a value is dropped).
Named templates (Settings → Citation templates, or the folder's own) are used as `.name`.
The folder's settings (their “Citations & sources” section) set its star lists: a paper
is numbered in the first one it is in (in its order, else by year). Each list, and the
papers cited only (in no list: numbered apart, as first cited), has two formats: its
**reference** (the number, e.g. `**#{index}**`, in the references and as `.number`) and its
**citation** (how `[@key]` cites, e.g. `{(.number .short-venue, .year)}`; empty: the
reference), with an example of each; inserted citations are plain `[@key]`. The papers to discuss are those of the lists to cite
(else those of the period's years); the editor's
"Papers" mode (its icon red until each is cited, orange if one cited is not of the
period's years, green once all are; how many on hover) shows, instead of the preview, the papers to discuss with their numbers (red
until cited, orange: not of the period's years; a click cites it, the ⋮ menu with a
template), the one at the cursor highlighted, a search (Enter cites the first match; the
person's other papers too), and "Cite them" (those not cited yet); the papers of each
list are reordered there too (dragged, their ↑ / ↓ arrows, or their number
Alt-clicked and typed), the citations renumbered. ⌘/Ctrl-click on a citation (or the
toolbar's ↗ button, the cursor on it) shows its paper's details in a new window. Copy
substitutes the citations, followed by the references, numbered as cited (or where
`[[references]]` is, alone on its line: the toolbar's “Insert a block” menu; `[[references notes tags]]`:
with their notes, their tags). Blocks are written `[[name]]` (formerly `[]{.name}`, still
read).

**Report templates** (the “Report” section of each folder settings: Settings → Folder
settings, or the folder dialog's “Settings” tab): the texts a report starts from, their
headings, blocks (`[[publications]]`, `[[excerpts]]`, `[[cv]]`, `[[references]]`) and guidance as
comments (`%% … %%`, left out when shown or copied); one is shipped. A person's notes in a
folder start empty: the toolbar's 📄 button of the folder notes starts them from a template
(asked first when they are written), or saves their structure as one (“Save as a
template…”: their headings and blocks, what is written left out). The same section sets the
language of the reports (references, excerpts, summary…; by default, that of the interface).

**Copy filters**: Shift+click on a copy button (the notes, the excerpts) copies through a
filter, as rich text pasted formatted into a form: those enabled in Settings → Copy filters
(several: the one to use is asked). **Marmotte** (CNRS) keeps the basic formatting its
forms allow: `#` headings as numbered banners (“--- 1. Research ---”), `##` as bold lines,
lists as paragraphs (“• …”); the comments (`%% … %%`, e.g. the places of quotes), callouts,
tasks, tags and the lines tagged #question or #to-check are left out, tables too. A tag can
instead be turned into a text (Settings → Copy filters → Tags turned into a text): one before
the line's text (e.g. an icon, 🌍) and one after, in a colour (or highlighted), in bold, the
line's text too if checked, e.g. the tags of the excerpts' markers (by default: 🌍 for
#influence or #rayonnement, ⭐ for #salient or #saillant, 🔹 for #minor or #mineur, ⚠️ for
#caveat or #bemol; the
levels' #local, #national, #international as “(national)”… after the line's text). Marmotte keeps bold, italics, underlining, text colours,
highlights, emojis (🌍 ⭐ 📣) and symbols (★ ✦ ◆ ➤); it drops superscripts, font sizes and
strikethroughs.

**PDFs**: the PDF file icon next to a title (with a pencil once annotated; the plain red PDF
icons after a source's badge are that source's PDF, online) opens the paper's stored PDF
beside the person's page (⌘/Ctrl-click: in a new window). A grey icon means an open-access
link (or a DOI) is known: click it to
download the PDF (after confirmation, unless turned off) and open it. The viewer (PDF.js,
installed once) has highlight, text, drawing and image tools; annotations are saved into the
stored PDF (every few seconds, with Save or ⌘S/Ctrl+S); the viewer's menu (») saves,
downloads a copy, prints and adds images. ← / → go back and forth after following a link
inside the PDF. Above it, the menu bar and the person's header (their tabs) stay; the
full-screen button hides them (for every PDF, until shown again). The side panel button shows the paper's tags and notes
(also those of a period), its details (as in the publications panel) and its bookmarks next
to it; 🔖+ bookmarks the selection (else the place shown), and 🔍 finds the paper of the
selected text (among the person's papers, else on HAL, to add); the actions needing a
selection are greyed out without one. An area of a page selects too (a figure, a table, a
passage over columns): turn the area tool on (the dashed box in the header, or **A** or **R**) and
draw a rectangle, or Alt+drag; its text is the selection, and the rectangle its place
(Escape, or a click elsewhere, drops it). Keys (single, but when typing): **H** highlights
the selection (else turns the highlighting tool on or off), **T** text, **D** drawing, **I**
image (on or off), **A** or **R** area, **B** bookmark, **F** find the paper, **E** add to a
category, **Q** quote into the note last used (a Markdown quote, with its page), **Shift+Q**
quote there at the cursor, within the sentence (`"…" %% [Application, p. 4](link) %%`: the
place in an Obsidian comment, linking back to the PDF's page, not shown in the preview; the
note toolbars' quote button, shift-clicked; clicked: as Q); PDF.js's own: ⌘F / Ctrl+F search, + / − zoom, N / P (or J / K) next and previous
page, R rotate, Home / End, Delete removes the annotation selected. The panel's ⬇ button
downloads the PDFs of all the papers shown; "Store a PDF" (details) uploads one. Settings →
Data & cache shows the space used and cleans up the files of papers no longer in the database. A
paper that leaves the sources takes its downloaded PDF with it; an uploaded or annotated one
keeps the paper.

**PDFs beside a person's page**: whatever the tab (the publications, the report, the
excerpts…), PDFs open on the right of it: the person's documents (Report tab, “Documents”
menu) and papers (their PDF icon, in the list or beside the notes; a link to a PDF, e.g. a
quote's in the notes, or an excerpt of another PDF), e.g. the excerpts with a document, or the
report with a paper. A strip at the bottom lists them: click one to show it, click the one
shown (or ») to minimize them (the page alone; « shows the last one again), ✕ to close one
(from the view only: nothing is deleted). Drag their left edge to resize them. They stay open
for the person (and in the address) until closed. Each has the viewer's header (its ↗ opens it
in a window of its own) and its side panel, hidden: meanwhile, the page's notes and excerpts
are its own, Q quoting into the notes (the Report tab), E filing the selection (the Excerpts
tab), F finding the selection's paper and L tagging the papers of a list (from the Report tab),
B bookmarking the place read, an excerpt clicked on its pages selected in the Excerpts tab,
and an excerpt clicked there shown on its page. A document renamed from its title (double-click)
is renamed in the strip too.
Within a folder, the strip is always there, whatever the tab: its + adds a document (PDF) of
the person there, as do PDF files dropped onto it.

**Documents** (person's Report tab, “Documents” menu): PDFs of the person within a folder or
a period (an application, a CV…), uploaded there and read in the same viewer (annotations,
bookmarks, a note), beside the page (or in a new tab, from their ⋮). The person's papers
mentioned in a document are found (by title, a few typos
allowed, or DOI / HAL id) and become links (a light dotted underline; red when the paper's
PDF is stored). Numbered
references (“[11]”, “11.”, “[C3]”) make their citations (“[11]”, “[3, 11]”, “[3-5]”) point
to the paper too, as do author-year citations (“(Lyu et al., 2023b)”, “Marti Vidal and
Montiverdi, 2021, 2020”) matched with the references found (first author, year and letter).
A click shows the paper's details next to the document,
as in the publications panel (tags, notes, its PDF to view online, download or store); “Not this
paper” forgets a wrong match. A reference that was not found: select it, 🔍, then “Link
here”. The papers tab lists them (a search box filters it: every word, in the title,
authors, venue or year). The document's note cites the person's papers as reports do
(`[@key]`, the ❝ button to pick one, those of the document first); its preview and copy
number them (“[1]”, as first cited), the copy ending with the references.

**Categories** (folder page: the category button; the person's Excerpts tab; the viewer's
categories tab; or the “Categories” section of the folder settings, changed at once): a
folder's ordered tree of categories (e.g. an evaluation grid), dragged to order and nest
them, each with an optional id (`#id`, for the reports: see below); one with excerpts (there or below, of anyone in the folder)
is deleted once they are moved to another one, chosen then. In a document's PDF (a report,
an application… not a paper's), select a passage (or click a highlight), then ‘add to a
category’ (header, or the E key; on an excerpt filed already, clicked or its text selected:
edits it): click a category, or type to find it (the one chosen last proposed, marked and scrolled to: Enter picks it; typed, Enter:
the first match; a new name creates it, the dialog staying open), with the years it is about (those found
in the passage, e.g. “2026-32”: 2026–2032, and taken out of its text when leading it with a
colon or ending it after one; its crossed-out calendar clears them, the text as selected
again) and its markers if need be: salient (“saillant”, a strong point), influence
(“rayonnement”), minor (“mineur”, of lesser weight) or caveat (“bémol”, a reservation); when it might be an excerpt
already (similar words, shown with a warning; or found by typing its words), merge it with
that one instead, keeping its text or only its place (a reference). The categories tab lists
the person's excerpts (click: go to the passage; a category collapsed with its chevron, or all of
them; searched by their text, tags or a section's text), to edit (one dialog, in tabs: “Excerpt”, its text, years, markers, level and tags, the
passages as selected shown, to go back to or add to the text; “Category”, to move it, with the
same picker, what was edited saved first; “Notes”, its commentary and a link), move (its tab
of that dialog), merge (drag one onto another, or its merge icon then a click on
the other: grouped, each its own quote or only its place, its link / quote icon switching,
on one item with the other's category, years…; the group's pencil edits its overall text,
e.g. one sentence summarising them, its excerpts then quoted in the comment after it, with
their places, all still tinted on the PDF; taken out again
with its split icon), reorder (dragged onto the top or bottom edge of another one), discard
(its ✕: left out of the generated text, the notes and reports, still kept and listed, struck
through; back with the same button), annotate (its speech bubble, the dialog's “Notes”: a commentary in
Markdown on it, or on a group, shown under it and put after its item in the notes as a comment
`%% … %%`, not shown; and a link, URL, written after it in the notes, its 🔗 on its row),
remove,
or copy as Markdown (unquoted, their places in Obsidian comments: `%% Application, p. 4;
p. 12 %%`, then their years; those showing influence also listed, by category, in a last
“Rayonnement” section, or where the block `[[influence]]` (or `[[rayonnement]]`) is; the badge icon names the documents, e.g. a long file name as
“Application”);
the report's “Insert a block” menu inserts them, by category (the block `[[excerpts]]`, kept up to
date), those of a category with an id, or the Rayonnement section; `[[excerpts#id]]` inserts those of the category of that id (and below it; its
subcategories as headings below the block's heading), then left out of `[[excerpts]]`, e.g.
`# Research` then `[[excerpts#research]]`, and `# Other activities` then `[[excerpts]]`.
Where the influence excerpts go, the folder's settings say (their “Excerpts” section:
“Rayonnement excerpts”): also
listed in the Rayonnement section (where `[[influence]]` is, else at the end of
`[[excerpts]]`), as by default; only there, under subsections named after their categories (nested as these, one
level below), taken out of their categories, one left empty being left out; or only in their
categories, tagged #influence, e.g. turned into an icon by the copy filters. The salient,
minor and caveat excerpts are always tagged. The folder's settings (“Excerpts” section) set each marker's tag (by
default in the language of its reports: #salient, #influence, #minor, #caveat; in French
#saillant, #rayonnement, #mineur, #bemol) and whether an excerpt moves when marked (“First”, by
default): marked, it moves to the top of its category, among those marked so (salient ones
first, then influence, then minor ones, then caveats); unmarked, after them; it can then be moved as one
likes, the report following that order. An excerpt can also have a level (its edit dialog:
“Level”), e.g. local, national or international, tagged after its markers (#national); the
folder's settings set its levels (their tags, by default in the language of its reports; one
added with +, removed with its ×) and the default level of the excerpts without one (shown in
italics on their rows, in the categories tab, where the search finds them by their level too).
Each category can
have a text of its own for the person (Markdown, e.g. a summary of its items; its pencil, on
hover, in the categories tab, or a click on the text): put after its heading, before its
items (nested, that of a category left empty goes under its subsection of the Rayonnement
section). With “Only this text, its excerpts not listed”, the section is summarised by its
text: in the notes and the copy, its excerpts are not listed but follow the text between
`%% … %%` (a comment, never shown) for reference; the categories tab still lists them.

**Folders** (Reports page) group people, e.g. for a hiring committee: a folder has a name,
a date and can be hidden. Each person has their **own period** in each of their folders
(click it on their line in the folder, or set it in their Profile tab, under Periods), with its own stars, tags and notes; opening a person from a
folder selects it. Removing a person from a folder (✕ on their line, or in their Profile
tab) either keeps that period, with its data, as one of their own periods, or deletes it
(confirmed again, listing them, if it has notes, documents, excerpts or report texts).
**Moving** them to another folder (the folder-with-an-arrow button, same places) takes
that period with them, with all its data; their excerpts are filed in the other folder's
categories (added if missing).
The 📝 button of a person's line (also next to the folder's name on their page) opens their
**notes in the folder** (their Report tab; their excerpts by category: the Excerpts tab), as
next to their PDFs: to write them without opening any document (e.g. when they have none).
An excerpt's dialog names its category under its title. Their **Timeline** tab draws
what is dated about them along a vertical axis of years, the most recent at the top (every
year a tick, so that the gaps show; their period in the folder shaded), an arrow from the
axis to each item. On the left, their **career** (Profile tab): a line of their CV as a bar
along its years (a current position: up to now), and beyond it the **theses they
supervised**, each a bar from its start to its defence (in progress: up to now), overlapping
ones side by side; on the right, their **excerpts** in the folder (in their category's
colour) and their **papers** (a small mark each, their titles hovered). Each kind is shown or hidden with its
switch; one they have none of is said so. An excerpt is at its first year (a paper's
excerpt without years: at the paper's year); clicked, it is shown in the Excerpts tab (where
its years are edited), its PDF icon opening its PDF beside the page; a line of the career
clicked shows the Profile tab, a thesis the Theses tab. The excerpts without years, and the
lines of the career without dates, are listed apart, below.

Folders can be **within other folders**: the Reports page shows them as a **tree**, each
folder expanded showing the folders within it then its people, one line each (their period,
stars, tags, sources, what needs a look); collapsed, its number of people. The folders
expanded or collapsed (also all at once, with the buttons above the tree) and the one
selected are remembered. A click on a folder selects it (expanded): its actions and its tag
filter are in the bar above, staying on top; "All people (cleanup)" lists every person. Drag a folder onto another one to
put it within it (onto its top or bottom: next to it), or onto "Drop here" for the top level.
Each folder's ⋮ menu renames it, adds a folder within it, moves it ("Move to…") and chooses
its settings ("Its settings…"). The **folder settings** are named, in Settings → Folder
settings. There they are created (empty, or a copy of others), renamed, edited, made the
default, and deleted (once no folder uses them and no excerpt is filed in their categories).
They are edited in sections, in tabs atop (also the folder dialog's “Settings” tab, its
“Folder” tab giving its name, date, notes and the settings it uses):
**Categories** (their tree, changed at once), **Report** (the report templates, the language
of the reports), **Excerpts** (the markers' tags and whether a marked excerpt comes first,
the levels, where the rayonnement excerpts go), **Citations & sources** (the primary source,
the star lists, how the notes number and cite the papers, the folder's own citation
templates) and **Models (local AI)** (an LLM and an embedding model used instead of the
default ones; blank: the default). Each folder uses the
settings chosen in its dialog or its menu (an icon in the tree when not the default ones),
else the default ones; a folder made within another one starts with its choice, and a folder
moved keeps its settings. Choosing other settings files its people's excerpts in the
categories of the same names there (added if missing), their starred papers in the star lists
of the same names. Settings used by several folders are changed for all of them: the
categories and citations dialogs say which.

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

**PhD students** can be edited by hand (Theses tab): the dates of one from theses.fr (set
over its own, kept when theses.fr is fetched again; marked ✎), and a student added by hand
(name, title, supervisors, dates). Removing one added by hand deletes it; one from theses.fr
is hidden instead: still listed (struck, with a comment on why), it can be restored, and is
counted nowhere (author lists, the "with PhD student" filter, contribution roles), even once
theses.fr is fetched again.

**Settings** are grouped (Sources, Venues, Annotations, Reports, AI, Data) in a list on the
left; the search box above it finds a setting by its words. A page with unsaved changes is
listed below them, with its Save / Cancel.

**Local AI** (optional: Settings → AI). Personal information is confidential: only local
models are used, served on this machine or the local network (other servers are refused,
Ollama's cloud models never listed nor used). **Local LLM** turns it on, with its server
(Ollama, or an OpenAI-compatible one, `/v1/chat/completions`, with an optional API key, also
read from `SCI_REPORT_ANALYZER_LLM_API_KEY`), the general model, whether it thinks before answering (by default), and the
**embeddings** (from Ollama or an OpenAI-compatible server) suggesting an excerpt's category
(the closest of each category's clusters of excerpts: how many clusters at most, how many
excerpts in one at least; the category picker's “Train” learns again from the excerpts
filed). Each task has its own page, with its model (the general one unless another is
chosen; a small one, 4–8B, is often enough, and much faster), whether it thinks (“Default”:
it doesn't; “Thinks”; “Doesn't think”) and its instructions (edited there; “Default instructions”
brings them back):

- **Reading a career**: on a person's Career tab, “Read in their documents”, the LLM reads
  the pages of their documents about a career (found by the words set there, in each
  language) and suggests lines of their CV.
- **Rewriting the excerpts**: a new excerpt is reworded (short, impersonal, without its
  dates) and its years read in it and around it, proposed in its dialog, where its text can
  also be edited at once (the selection kept as its original). “None (no rewriting)”: only
  its years, found around it.
  Its “Change LLM” button runs the same rewriting through other models, one after the
  other, their wordings side by side with their time (the first run of a model includes its
  loading); “Keep it” takes one, and can make its model the one rewriting the excerpts.
- **Reading a list of references**: in “Tag from a list”, “Read with the LLM” reads a pasted
  list into references (title, authors, venue, year, DOI), matched by their title; a long
  list in parts.

When the LLM is off (or a task has no model, or gets no valid answer), the task's page and
the excerpt dialog and the Tag from a list dialog say so, with a link to turn it on. A folder's settings can use other
models (their “Models (local AI)” section).

**Backups** of the database (not of the PDFs) are made in `backups/` next to it (Settings →
Data & cache shows where): one a day at startup (the last 7 kept), and one before each upgrade of
its schema (`pre-migration-…`; one marked `.pending` is from an upgrade that failed, and is
always kept). To restore one, quit the app and copy it over the database file (removing the
`-wal` and `-shm` files next to it).
