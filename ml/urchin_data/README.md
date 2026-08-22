# ml/urchin_data — S26 verification artifacts

Repo-side artifacts for the urchin dataset verification (S26). The data
itself lives OUTSIDE the repo at `~/nereus_ml/datasets/<source>/`
(see `~/nereus_ml/README.md` for the layout + hygiene rules).

- `licenses/` — verbatim per-source license captures, dated, with the
  exact API field / page text / file quoted. These are the artifacts the
  dossier's license column points at.
- `manifests/` — per-source JSON: claims vs verified counts, sha256 of
  every archive on disk, access notes, verdicts.
- `NICKS_HANDS.md` — the account/key steps only Nick performs, and
  downloads awaiting his gate.

The human-readable verdict table is `docs/urchin_datasets.md`
§Verification. Verification scripts that earn keeping land here as they
stabilize; one-shot probes stay in session scratch.
