# DUO — license capture (S26 bite 1)

Captured 2026-08-21 from the figshare REST API,
`https://api.figshare.com/v2/articles/25370527` (article "DUO"):

```json
{"license": {"value": 1, "name": "CC BY 4.0",
 "url": "https://creativecommons.org/licenses/by/4.0/"}}
```

**Delta vs the 2026-08-17 research file**, which recorded DUO as
"license unstated (contest lineage) — treat research-only": the figshare
record DOES declare CC BY 4.0. The GitHub repo `chongweiliu/DUO` carries
no license file of its own.

**Caveat (interpretation, not artifact):** the declaration is the
uploader's dropdown choice; DUO is a re-annotation of URPC contest
imagery whose own terms were never published. A figshare field does not
launder that lineage. Recommendation stands: research-only fence until
Nick decides whether the figshare declaration is sufficient for
commercial use. Nick's call, bite 3.

File covered: `DUO.zip`, 3,390,158,070 B,
md5 `d2b1901ea741ec7223168e0f154f5f15` (figshare `computed_md5`).
