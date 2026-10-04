# Ranking datasets

Third-party data shipped with SciReport Analyzer. The MIT licence of the code does not apply
to these files: each keeps its source's terms.

| File | Source | Terms |
|------|--------|-------|
| `journals.json` (in the repository's `datasets/`, downloaded by the app) | [SCImago Journal & Country Rank](https://www.scimagojr.com) (SJR, quartiles, h-index), built by `scripts/build_datasets.py --scimago=…` from the CSV exports of several years (a journal no longer listed keeps its last year) | Free to use as long as the source is cited (SCImago Lab FAQ); the journal metadata (titles, ISSNs) comes from Scopus (Elsevier) |
| `conferences.json`, `conferences.past.json` | [ICORE conference rankings](https://portal.core.edu.au/conf-ranks/) (CORE), every edition since 2008, built by `scripts/build_core.py` from the portal's exports | Published openly by the Computing Research and Education Association of Australasia (CORE) and ICORE; cite them when reporting ranks |

Not shipped:

- The **predatory list** (Beall's list of potentially predatory journals and publishers, as
  maintained by [stop-predatory-journals](https://github.com/stop-predatory-journals/stop-predatory-journals.github.io),
  MIT licence) is downloaded by the app into the data directory and refreshed every week.
  Inclusion in it is an indication, not a verdict.
- **JCR** impact factors belong to Clarivate: users import their own export.

Their format: [`docs/json-formats.md`](../../../docs/json-formats.md#ranking-records).

## Citations

> SCImago, (n.d.). SJR — SCImago Journal & Country Rank [Portal]. Retrieved from
> https://www.scimagojr.com

> ICORE Conference Rankings. Retrieved from https://portal.core.edu.au/conf-ranks/
