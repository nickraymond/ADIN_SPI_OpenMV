# S26 — steps that need Nick's hands (accounts/keys; same rule as sudo)

Only ONE blocking item exists for bites 1–2:

## Roboflow account + API key (blocks the 4 Tier-1b exports)

1. Sign up (free tier is enough): https://app.roboflow.com/login
2. Get the key: Settings → Workspaces → your workspace → **API Keys** →
   copy the **Private API Key**.
3. Hand it to the agent via env var (don't paste into chat/files):
   `export ROBOFLOW_API_KEY=...` in the shell you launch the session
   from, or tell the agent where you put it.
4. While logged in, also check whether
   https://universe.roboflow.com/sakana/urchins-cjlib still exists
   behind login (it 404s logged-out as of 2026-08-21 — it is NOAA
   yolo11n's training set).

With the key the agent can export, license-stamp and verify:
`sea-urchin/urchin-detector` (74-img purple/red seed),
`new-workspace-dex2x/sea-urchin-body-5bmiy` (2,273 seg),
`roboflow-100/underwater-objects-5v7p8` (7,600 det, license-clean DUO
alternative), + pin down the Spectral Labs project.

## Not needed (verified 2026-08-21)

- **GBIF/iNat**: no account — API paging covers ~17k records, media is
  public S3.
- **Urchinbot**: no SQUIDLE+ account — images are public S3 URLs in the
  CSVs.
- **FathomNet**: API is open; and the license audit made bulk download
  moot for commercial use (all NC-ND).

## Downloads awaiting Nick's gate (size/value calls, not credentials)

- Urchinbot full image set: **~32 GB** (only its 300-img sample is on
  disk). Needed in full before backbone training, not before.
- Marine-Detect FishInv: 6.72 GB, mixed unstated provenance — worth it?
- RUOD: 3.4 GB, no license — research-only; skip unless wanted.
