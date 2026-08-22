# FathomNet (S. fragilis) — license capture (S26 bite 1)

FathomNet licensing is **per upload set** (the image record itself
carries no license field; it lives in the upload set's Darwin Core
metadata, reached via `imagesetuploads.find_by_image_uuid`).

Captured 2026-08-21 via the `fathomnet` python client. Every upload set
behind the "Strongylocentrotus fragilis" images audited so far returns
this exact license string, verbatim:

```
Images licensed as CC BY-NC-ND
(https://creativecommons.org/licenses/by-nc-nd/4.0/legalcode),
annotations licensed as CC BY-NC
(https://creativecommons.org/licenses/by-nc/4.0/legalcode)
```

Audit result (FINAL, 2026-08-21): full-corpus sweep over **all 4,600**
fragilis images (23,061 boxes, all contributed by brian@mbari.org) —
**4,600/4,600 return exactly the string above; zero exceptions, zero
errors.**

**Consequence:** NC-ND on images means NO commercial use and NO
derivatives. The "FathomNet license-filtered" leg of strategy step 1
contributes **zero** commercially usable boxes for this concept — the
research file's "filter by license" assumption finds nothing to keep.
(Research-only pretraining experiments remain possible under NC terms.)

S. purpuratus: 7 boxes, M. franciscanus: 0 (API counts, 2026-08-21) —
negligible regardless of license.
