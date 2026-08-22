# Urchinbot — license capture (S26 bite 1)

Captured 2026-08-21 from the Zenodo REST API,
`https://zenodo.org/api/records/16060266` (DOI 10.5281/zenodo.16060266):

```json
{"license": {"id": "cc-by-4.0"}, "access_right": "open"}
```

Record title: "Urchinbot Dataset - Sea Urchin Object Detection and
Classification". CC-BY-4.0 → commercial use OK with attribution.

**Separate artifact, separate license:** the companion GitHub repo
`kraw084/Urchin-Detector` (code + the pretrained YOLOv5 weights) has **NO
license** — verified 2026-08-21 via
`https://api.github.com/repos/kraw084/Urchin-Detector/license`
(spdx_id: None). Unlicensed GitHub content is all-rights-reserved by
default: the DATASET is CC-BY, the WEIGHTS/CODE are not licensed for
reuse. Do not ship or fine-tune from those weights without clearing it.

Scope note: the images themselves are served from public S3 buckets
(`gbe-uauckland.s3.ap-southeast-2.amazonaws.com`, some rows
`imos-data` IMOS/AUV) referenced by the Zenodo CSVs; the Zenodo record's
CC-BY-4.0 declaration is the license artifact covering the dataset.
