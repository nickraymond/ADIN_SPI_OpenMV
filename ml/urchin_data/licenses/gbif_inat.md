# iNaturalist via GBIF — license capture (S26 bite 1)

Captured 2026-08-21 from the GBIF occurrence API
(`api.gbif.org/v1/occurrence/search`, `mediaType=StillImage`,
`facet=license`). GBIF records carry a per-record machine-readable
license enum; these are the live facet counts:

**Strongylocentrotus purpuratus** (13,430 records with images):

```json
{"CC_BY_NC_4_0": 11411, "CC_BY_4_0": 1309, "CC0_1_0": 710}
```

**Mesocentrotus franciscanus** (3,720 records with images):

```json
{"CC_BY_NC_4_0": 3146, "CC_BY_4_0": 402, "CC0_1_0": 172}
```

Snapshot check vs the 2026-08-17 research file: its 13,387 / 3,666
figures match today's HUMAN_OBSERVATION facet exactly (13,387 / 3,666;
the small remainder is specimen/sample records).

**Consequence:** ~85% of the species signal is CC-BY-NC. Commercially
clean (CC0 + CC-BY) volume is **2,019 purple / 574 red** records —
roughly 7× / 6× smaller than the headline counts the strategy was
sized against.

Note: license is per-record (each photographer chooses); a record's
media items normally share the record's license. Bite-2 spot-check
should confirm media-vs-record license agreement on the sample.
