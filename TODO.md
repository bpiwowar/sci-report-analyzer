# To do

- **Better venue merge suggestions** (`venues_page.py`, "Possibly the same venue:" strip).
  Today each suggestion only offers a plain merge, which is wrong for e.g. EMNLP:
  - *Findings of the ACL: EMNLP* is EMNLP's Findings track;
  - *EMNLP and IJCNLP* / *Proceedings of EMNLP and IJCNLP* are a joint conference that
    includes EMNLP (and are the same venue as each other).

  Proposal: one row per suggestion, with the relation guessed and pre-selected, editable:

  ```
  ⇄ Related venues
    Findings of the ACL: EMNLP (3)           [Its Findings track ▾]   [Apply] [✕]
    EMNLP and IJCNLP (1)                     [Joint, includes it ▾]   [Apply] [✕]
    Proceedings of EMNLP and IJCNLP (1)      [Joint, includes it ▾]   [Apply] [✕]
  ```
  Relations in the menu (all use what the model already has):
  - **Same venue**: the merge we already have.
  - **Its {track} track** (Findings, demo, short…): merge, and give the merged variants that
    track (`set_variant_track`). Guess it from a track word in the name (`_FINDINGS`, `TRACK_ORDER`).
  - **Joint conference that includes it**: add this venue to the other's joint parts
    (`set_joint_parts`). Guess it from "and the International Joint Conference", "joint", " and ".
  - **Workshop of it**: add this venue as the other's host (hosts editor).
  - **✕ Not related**: hide the suggestion for good (store the dismissed pair).

  Also group suggestions that are the same venue as each other (both EMNLP-IJCNLP titles)
  so one action covers them: "merge these two, then mark as joint including EMNLP".
  Apply shows the usual confirmation, with a sentence saying what will happen.
