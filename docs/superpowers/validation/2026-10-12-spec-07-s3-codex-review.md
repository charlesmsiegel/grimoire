# 07-S3 native implementation review

Native /codex:review at4ed7da891, thread01a12f57-ab8f-7d43-956e-bf7dfd9aa03d,
found one P2: a Windows owned mara directory shadows the World MARA directory,
but exact-ID roster union labels the raw MARA reference with the World name.

Reproduced the supported sequence: copy group and Mara, detach Mara, rename
World mara to MARA and label it Seraphine, then change the group's raw reference
to characters:MARA. RED displayed Seraphine with the own-campaign warning.
The fix maps queried native aliases to enumerated owned directory IDs using
existing samefile proof and roster names. It adds no actor/card reads or case
folding. All8 focused field cases pass; lint1146/mypy161 and TypeScript pass.
Native recheck/final gate pending. Independent initial gate was non-blocking.

Corrected recheck3fcaefe18 thread01a12f7a-bc97-7210-b779-bb76e18e99e6 found native flat-reference names still taking the World row and quadratic actor alias probing for exact owned IDs. Both concrete cases reproduced2RED. The resolver now applies owned identity precedence to every referenced kind, skips exact/invalid IDs and uses a native device/inode identity map with one stat per owned slot/queried alias instead of pairwise scans.10 focused GREEN plus malformed-token/exact-ID probe1GREEN; lint1136/mypy161. No additional record/card content reads, case folding or raw-value rewrite. Corrected review gates remain pending.
