<!-- Provenance: Nick's research, two independent sweeps run 2026-08-17,
     delivered to the repo 2026-08-21. Live API/GBIF counts are as-of that
     date. S26 exists to VERIFY these claims — treat every row as unverified
     until S26 bite 1 stamps it. -->
# Labeled urchin datasets — exhaustive search results

Date: 2026-08-17. Two independent research sweeps (marine-science institutions
+ ML platforms); findings cross-checked where they overlapped.

**Headline: no existing dataset labels purple vs red urchins (S. purpuratus vs
M. franciscanus) with bounding boxes at scale.** But strong building blocks
exist for every layer of the model, and one dataset is a near-perfect
pretraining match. Live FathomNet API counts and GBIF counts below were queried
2026-08-17.

## Tier 1 — Bounding-box detection sets (directly trainable)

| Dataset | Link | Classes | Size | License | Fit |
|---|---|---|---|---|---|
| **Urchinbot** (U. Auckland / SQUIDLE+) ★ best find | [Zenodo](https://zenodo.org/records/16060266) · [GitHub + weights](https://github.com/kraw084/Urchin-Detector) | 3 urchin spp: kina, Centrostephanus, Heliocidaris | 9,872 imgs, >44k boxes, YOLO format + depth/lat-lon metadata | **CC-BY 4.0** (commercial OK) | Temperate-reef AUV/diver imagery (AUS/NZ barrens) — closest visual analog to CA kelp/barrens. Pretrained YOLOv5 included (P .90/R .86) |
| **DUO** (URPC re-annotation) | [GitHub](https://github.com/chongweiliu/DUO) · [figshare](https://figshare.com/articles/dataset/DUO_zip/25370527) · [paper](https://arxiv.org/abs/2106.05681) | echinus/holothurian/scallop/starfish | 7,782 imgs, 74.5k boxes — **50,156 urchin boxes**, largest anywhere | ⚠ unstated (contest lineage) — treat research-only | Chinese aquaculture urchins (S. intermedius), turbid nearshore; huge volume, wrong water color. Use DUO, not raw URPC (URPC years have duplication + label noise; DUO is the dedup/fix) |
| **FathomNet — S. fragilis** (MBARI) | [fathomnet.org](https://www.fathomnet.org) · `pip install fathomnet` · [download how-to](https://www.fathomnet.org/post/how-to-download-images-and-bounding-boxes) | deep-sea pink urchin | **23,061 boxes** (live API count). S. purpuratus: 7. M. franciscanus: **0** | per-image CC0/CC-BY/CC-BY-NC/NC-ND — **filter by license for commercial use**; acknowledge FathomNet | MBARI ROV, Monterey deep water. Pink/purple spiny shape on seafloor — good shape prior, wrong habitat |
| **Marine-Detect FishInv** (Orange OSS) | [GitHub](https://github.com/Orange-OpenSource/marine-detect) | 15 cls incl. urchin | ~12.7k imgs | data via repo (AGPL code) | Tropical Indo-Pacific reef |
| **RUOD** | [GitHub](https://github.com/xiaoDetection/RUOD) | 10 cls incl. echinus | ~14k imgs, ~74.9k boxes | unstated | Broader scenes than URPC, same region lineage |
| **NOAA HF urchin detectors** | [yolo11n](https://huggingface.co/akridge/yolo11n-sea-urchin-detector) · [yolo11x](https://huggingface.co/NMFS-OSI/yolo11x-sea-urchin-detector) | 1 cls "urchin" | trained on [Sakana/Roboflow ~1.7k imgs](https://universe.roboflow.com/sakana/urchins-cjlib/dataset/1) (CC-BY) + FishInv | models open | Ready-made single-class weights to benchmark against |

## Tier 1b — Small but on-target (Roboflow Universe)

| Project | Size | Why it matters |
|---|---|---|
| [urchin detector](https://universe.roboflow.com/sea-urchin/urchin-detector) | 74 imgs | **Has Purple Sea Urchin + Red Sea Urchin classes** — apparently CA urchin-removal footage. Tiny, but the only purple-vs-red boxes found anywhere |
| [sea urchin body](https://universe.roboflow.com/new-workspace-dex2x/sea-urchin-body-5bmiy) | 2.27k imgs, segmentation | Includes Purple Sea Urchin class |
| [Spectral Labs Marine](https://universe.roboflow.com/search?q=class%3Asea-urchin) | 818 det | Sea Urchin + Kelp + Starfish, likely Pacific NW |
| [RF100 underwater-objects](https://universe.roboflow.com/roboflow-100/underwater-objects-5v7p8) | 7.6k | URPC2019-derived, CC-BY 4.0 — license-clean alternative to DUO's volume |
| [FathomNet SA-Co segmentation](https://universe.roboflow.com/sa-co-silver/fathomnet-kmz5d) | 9.3k seg | FathomNet-derived masks incl. urchins |

Full search: [class:urchin](https://universe.roboflow.com/search?q=class:urchin) / [class:sea-urchin](https://universe.roboflow.com/search?q=class%3Asea-urchin).
Caveats: many projects are DUO/URPC forks (dedupe before merging); counts often
include augmented copies; verify license per project. "Sea Urchin (clamshell)"
is mislabeled — actually sea cucumbers.

## Tier 2 — The purple-vs-red species signal

| Source | Size | Type |
|---|---|---|
| **iNaturalist via GBIF** — [S. purpuratus](https://api.gbif.org/v1/occurrence/search?scientificName=Strongylocentrotus%20purpuratus&mediaType=StillImage&limit=0) / M. franciscanus | **13,387 purple / 3,666 red** research-grade photos (live counts) | Image-level species labels, no boxes. Filterable to CC0/CC-BY. Mix of intertidal/out-of-water — needs filtering + auto-boxing |

This is the only large corpus that distinguishes your two species. Strategy:
run a class-agnostic urchin detector (from Tier 1) over these photos to
auto-generate boxes, keep underwater-looking frames, and you have a
purple-vs-red classification set for ~free.

## Tier 3 — Point/weak labels and raw-imagery mines

- **CoralNet** — ["Urchins" label](https://coralnet.ucsd.edu/label/342/): 27,724 point annotations across 1,139 sources. Points, not boxes; per-source access. Weak-supervision material.
- **SQUIDLE+ / IMOS** ([squidle.org](https://squidle.org)) — >10M images, >6.5M annotations; the Tasmania/NSW Centrostephanus barrens campaigns live here (e.g. [IMAS Bicheno barrens](https://catalogue-temperatereefbase.imas.utas.edu.au/geonetwork/srv/metadata/d29fa59e-203f-42a8-b0a7-cf77fde7b88a)). Free account + API. Urchinbot is the curated bbox export of this — go upstream only if you need more.
- **BenthicNet** ([paper](https://www.nature.com/articles/s41597-025-04491-1)) — 188k labeled imgs / 3.1M point+image labels, global. Backbone pretraining.
- **FathomNet FGVC Kaggle comps** — [2023 out-of-sample detection](https://www.kaggle.com/competitions/fathomnet-out-of-sample-detection) has an Urchin supercategory in COCO format.
- **Channel Islands NPS Kelp Forest Monitoring** — 40+ years of transect video, CA species, *unannotated* ([handbook](https://irma.nps.gov/DataStore/DownloadFile/485444)); request via NPS. Best raw-CA-footage mine if you need more fine-tune material than your own camera provides.
- **NOAA PIFSC** wrote an [SOP for building urchin training sets in VIAME](https://repository.library.noaa.gov/view/noaa/72416) (Hawaii); the dataset itself wasn't found published — contact PIFSC if curious.

## Dead ends (so nobody re-searches them)

Reef Check California publishes **count data, not imagery**
([kelp program](https://www.reefcheck.org/kelp-forest-program/), [MPA viewers](https://californiampas.org/data-viewers)).
PISCO/CDFW: counts only. Scripps 100 Island Challenge: tropical orthomosaics,
by-request, low value. KelpWatch/TNC: satellite canopy, no urchins. SUIM,
Brackish, TrashCan, DeepFish, OzFish, MegaFauna: **no urchin class**. DeepSee:
echinoderms but no urchins, data by request. Urchinomics/culling programs:
nothing published.

## Recommended data strategy (maps to repo Phase 2/5)

1. **Detector backbone:** pretrain "urchin-ness" on Urchinbot (CC-BY, temperate
   reef) + FathomNet S. fragilis (license-filtered) + RF100 underwater (CC-BY).
   Hold DUO's 50k boxes aside unless research-only licensing is acceptable for
   the experiment at hand — its lineage is unclear for commercial products.
2. **Species head:** purple-vs-red from iNaturalist/GBIF (CC0/CC-BY filtered,
   auto-boxed), seeded with the 74-image Roboflow purple/red set.
3. **Domain fine-tune:** your own BM camera footage (and NPS KFM video if
   needed) labeled with the pretrained detector + human correction — the same
   auto-label-then-correct loop the bench rehearses with balls.
4. **Benchmark:** score NOAA's off-the-shelf yolo11 urchin detectors on your
   eval set before training anything — free baseline.

License watch-list for commercial use: DUO/URPC/RUOD (unstated), FathomNet
CC-BY-NC/NC-ND subsets (filter them out), per-project Roboflow terms.
