# RUOD & Marine-Detect — license captures (S26 bite 1)

## RUOD (xiaoDetection/RUOD)

- Captured 2026-08-21: the GitHub repo contains **only `README.md`**
  (verified via `api.github.com/repos/xiaoDetection/RUOD/contents/`) —
  no LICENSE file, and the README carries no license statement.
  **License: UNSTATED, confirmed** (matches the research file).
- Access verified: data hosted as GitHub release tar-parts
  (`RUOD.tar.partaa/ab`, release tag `untagged-4f7c7ab75187d68b6449`),
  Google Drive (3.4 GB), and a DLUT mirror — no account needed.
  Not downloaded (research-only fence + over-gate size; metadata row
  only this bite).

## Marine-Detect / FishInv (Orange-OpenSource/marine-detect)

- Code license: AGPL-3.0 (repo LICENSE).
- **Data license: none stated for the dataset zips.** README says the
  models (and by construction the dataset) use "a combination of
  publicly available datasets (~90%) and Tēnaka-based datasets (~10%)"
  — the references list is a mix of Roboflow projects, OzFish, ImageNet
  and GBIF pulls, i.e. **mixed per-source licenses with no per-image
  license map in the zip**.
- Access verified 2026-08-21: `FishInv-dataset.zip` is a public Azure
  blob SAS link in the README (valid to 2099), HTTP 200,
  **Content-Length 6,721,790,595 B (6.72 GB)** — download deferred
  (over the ~100 MB gate; Nick's call whether the mixed-provenance set
  is worth the pull).
