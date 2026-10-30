# Commit SHAs before and after the 2026-10-30 history rewrite

Before the repository was made public, its history was rewritten with `git filter-repo`:
- one merge commit's author address was replaced by the GitHub noreply address;
- a personal home directory quoted in `docs/DOGFOOD-7-harness.md` was replaced by
  `/home/<user>@<domain>/…`.

No code or other text changed. Every commit from the first rewritten one on has a new SHA:
78 of 153.

- **Commit messages** were updated by the rewrite.
- **The documents were not:** DOGFOOD reports, frozen oracles, `AUDIT.md`,
  `eval/configs.toml`'s `scored` labels and code comments still cite the old SHAs, since
  some of them must not be edited after the fact (ORACLE-RULES §1.4; AUDIT.md). Look a
  cited SHA up here.

| Old | New | Commit |
|---|---|---|
| `c0bfe334376a` | `3c75918244f2` | scripts/publish.sh: scan what a push would publish; push only on "yes" |
| `645e5ceefd03` | `2d37ce8c79b4` | publish.sh: pass the commit range to the scanner; check the scanned count |
| `33148245b349` | `b426da2b6b33` | publish.sh: a home path needs a user name, not a placeholder |
| `6bfc3b93319e` | `a8f12839409a` | zmig doctor; PYTHONPATH removed from every command zmig runs |
| `c9b1f0ab991e` | `933ecf2d7445` | zmig fix kconfig: renames with Kconfig-level evidence only (P3, approved scope) |
| `b0ab1da7c0d2` | `603f92483de2` | Proposal (not implemented): act-now / review / FYI tiers, computed on the clean reports |
| `16403a6a0e6a` | `ab130d8cad62` | fix kconfig: base defconfigs count as user config; show what was checked; P3 results |
| `6bf6982f5907` | `4ccde97894e7` | README: first-run clone is roughly 20 minutes at 1 MB/s, hours on slow wifi; later runs un |
| `6d82f0810f99` | `5e6d95a2d968` | P3 doc: the remove category was never implemented; removed-symbol dry-run diff compared li |
| `fe5fb722c35a` | `559537fd624d` | Tiers proposal: a design with run-time inputs only, invariants, and sample sizes |
| `6a0810b099b4` | `b37c48c90ff5` | Proposal (not run): blobless Zephyr mirror; which analyzers read blobs or history, lazy-fe |
| `f07a590113ef` | `bcf2d517fa37` | publish.sh: say why it didn't push when stdin isn't a terminal (it exited silently) |
| `6f110224d4e9` | `a94ff6f05842` | Scan index: Kconfig assignments from the fragments the build read, not *.conf names |
| `e6b22910a1a0` | `aaef0d56ee88` | fix kconfig: remove with no replacement evidenced (comment out with a note) |
| `69518163bd85` | `50da206a8030` | fix kconfig: list app Kconfig overrides of removed symbols as needs-human; map any built c |
| `6c79dc01d5a6` | `aae688eb0bd9` | Overrides note: the no-Kconfig-link claims, checked against the builds' symbol dumps (choi |
| `7b60ea33b18b` | `c6bbddf417a9` | Tiers: additive `tier` field and a By tier grouping in MIGRATION.md |
| `fa886934883d` | `146a444d922a` | Docs: partial clone must record not_checked for every missing blob; public-release TODO (t |
| `5f7560a45a56` | `a33f819a65e0` | docs/TRY-IT.md: running plan, scan and fix kconfig on a real project, and recording what w |
| `0965e6d027c2` | `865643e11bd7` | Docs: real fix --apply run for the remove category; tiers recomputed on regenerated report |
| `0afdfab098ce` | `da5c9f17c914` | Cache misses say why (debuggability fix): markers record the key's inputs |
| `039f5082d763` | `b95581e84bba` | buildlog: Kconfig-diagnostic findings list the target's warning as evidence |
| `ddf6b2bf141d` | `fa9561a12871` | module-required cites the user's manifest; act-now on the target's own failure |
| `93241f8cec28` | `0c019fbdcf6c` | Add independent audit of zmig at fa9561a (AUDIT.md) |
| `f82c644ee439` | `ffba84d77876` | README: watch, API diffing and MCUboot checks are implemented (AUDIT F15) |
| `d5e35615d95b` | `c0764ee06acd` | Configure diagnostics already printed at the source are pre-existing (AUDIT F1) |
| `54aa423ce702` | `42a829e7e072` | Findings only in uncompiled source files become info (AUDIT F7) |
| `d0f9e0358028` | `9b7ddf7e9728` | header-changed is info when libclang found no used declaration changed (AUDIT F8) |
| `5753b7ad4f48` | `4a92ed1410cf` | build_files follows the app copy a cached build was configured from |
| `49bc2d12e80a` | `301aa00c66ec` | A module missing at both refs is recovered at the source and is info (AUDIT §6 item 2) |
| `d4181ac0b311` | `903f00014960` | Binding checks: required-property errors, type changes, deprecated reads (AUDIT §6 item 6) |
| `22a54055c3dc` | `cbefd3536263` | Dependency-expression changes: one info finding, and value findings say why (AUDIT §6 item |
| `721d1563d539` | `c61fab642503` | AUDIT.md: append the dated response (verification, fixes, departures, deferred) |
| `61cd355c42e6` | `d1acb59eb1cb` | report.json is the same on a cold and a warm parse cache |
| `cb904655737f` | `c1ca8b90b1a5` | Build key includes the app directory name |
| `f284f92b5ec3` | `d5c6752464db` | Kconfig warnings keep their identity when their definition moves |
| `1275465bc14a` | `a000852aa4c2` | Crossed releases without a migration guide are "not checked" (AUDIT F4) |
| `9ab41027871f` | `3d96c599f588` | AUDIT.md: follow-up to the response (build key, byte stability, warning key, F4) |
| `aa95a10c8160` | `4bce521b9816` | Merge pull request #1 from beriberikix/audit-fixes |
| `e4e9f01a89f9` | `f25482ea2ccf` | Re-measure the evaluation after the audit fixes (DOGFOOD-6) |
| `b6402d9cb5cf` | `1027320b0b4a` | eval/: a reproducible harness for the scored evaluation configurations |
| `ab676fda061d` | `6ff175348a01` | Re-measure the last six evaluation rows with the harness (DOGFOOD-7) |
| `d10eaaf83ddf` | `bf6304e5273a` | Correct actionable in DOGFOOD-6/7; compare also checks actions and evidence |
| `a73e7d73b3b4` | `a5069bf1e946` | Proposal: say where the fix goes and what it is (actions, change database, mining) |
| `7f9e029eb7d5` | `de3e8f419257` | Record the change-database decisions; define fix-actionable; ZMK #3 is partly (c) |
| `5ce354706aa7` | `600807970084` | Freeze held-out oracles: mercedes-benz/ardep and RIT-Launch-Initiative/FSW |
| `d0567c6ac18b` | `d10d60a41486` | Freeze held-out oracle: Alicipy/CoffeeCaller |
| `9d09bbd55f49` | `b7076774d250` | Clean baseline on three held-out projects (DOGFOOD-8) |
| `6f93881d73ae` | `f02c34639671` | Phase 0: fixes built from the findings' own facts (schema 1.5 `fix` field) |
| `dfc5d5a61812` | `fa0f29f773d6` | Measure Phase 0 (DOGFOOD-9) |
| `534897941267` | `fcdadc7b5db7` | Fix Phase 0 defects D1 and D2 (confidently wrong fixes) |
| `3a9d79157085` | `b7291ef2a34d` | outside_build: fragments outside the app dir match their app-relative form (DOGFOOD-8 B1) |
| `e86d249757c7` | `adff8507c4db` | devicetree: compare written values with whitespace collapsed (DOGFOOD-8 B2) |
| `3e480f3cf3ca` | `b97c64cda718` | Configure failure names the Kconfig assignments behind the abort (DOGFOOD-8 B4) |
| `7db8ba01d5d7` | `8d9e30589a13` | fixes: don't prefix a location the action already names |
| `9dc159146dd5` | `15b10028ab5d` | API diff: a function declared the same at both refs isn't removed (DOGFOOD-8 B3) |
| `c057404f9c60` | `ea890236763d` | Measure the detection fixes (DOGFOOD-10) |
| `98e1da7d4f7c` | `881986554c5e` | Design: the change database (PROPOSAL-CHANGE-DB Phase 1), for review |
| `8366fd9dd346` | `05c9c5424bb3` | Change database, step 1: Kconfig facts from the tree alone (`zmig changes`) |
| `c5ca2be5d52d` | `7bc16bb89dba` | Change database, step 2: the `changes` analyzer for Kconfig |
| `87cb54ca328a` | `75dc2c03d26d` | Change database, step 3: bindings table and compatible-unbound for project DTS |
| `234150477dbd` | `fec5d17e51b1` | Change database, step 4: modules the Zephyr tree says the target needs |
| `983fe98ea5fd` | `05d610985e8c` | API diff: the compiler's builtin headers aren't Zephyr's API (DOGFOOD-8 B3, va_start) |
| `79840749323e` | `a953de3131fc` | DOGFOOD-11: the change database measured; EVALUATION rows; baselines refreshed |
| `b5ef18dc370c` | `984801acceb9` | Change database: module-defined symbols aren't removed; a missing database is reported |
| `841701afc6c6` | `18da302c9e9d` | Configure failure: name what the board schema wants (ardep A3) |
| `d5ff8f298623` | `9cdb4a0345b7` | Build log: report project inputs the target no longer applies (CoffeeCaller C1) |
| `4e97c2e18620` | `eccc5d0778c4` | Change database: newly deprecated symbols the project enables (ardep A2) |
| `c9194988f282` | `1ec4361f5c30` | Oracles for two new held-out projects: ble-copro and proves (frozen before any run) |
| `dc7185ee9574` | `0a777a99f2e8` | DOGFOOD-12: clean baseline on ble-copro and proves |
| `a34eea65218c` | `74925433e0f5` | Configure failed after Kconfig (E1); API diff follows compatibility macros (E2) |
| `887190df87aa` | `500946541e56` | Build sites in file order, distinct (DOGFOOD-12 E8) |
| `cb3bea6d2e15` | `d7c6651311fc` | Guide matches: vendor sections and struct members (DOGFOOD-12 E3) |
| `6227d2fe2ea5` | `c3ea53abf545` | Build failure: name the include a header stopped providing (DOGFOOD-12 E4) |
| `7c649740970d` | `f7848b391f2e` | Oracle for a third new held-out project: edgehog (frozen before any run) |
| `e6699b6e70c4` | `c3d137e47daa` | DOGFOOD-13: clean baseline on edgehog |
| `61e6987b71ca` | `b405cd079a6c` | Warnings at the project's own lines; new dependencies named (DOGFOOD-13 F1-F3) |
| `d91a9df3f6e0` | `b127f6028d1d` | Prepare for a public release: LICENSE, CI, README status, scrubbed path |
