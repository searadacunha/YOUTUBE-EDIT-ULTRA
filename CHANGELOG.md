# Changelog

## Unreleased — 2026-11-17

### YouTube publishing safeguards

- Automatically discovered YouTube sources now require metadata preflight with no
  regional restriction flags, partner content indicators or API uncertainty;
  existing independent rights evidence is still required.
- Added `ultra inspect-youtube URL` and explicit reason codes for exclusions.
- New YouTube uploads are held private until the creator reviews final-video
  Content ID checks and territorial availability in YouTube Studio.
- Added mocked regression tests and documentation; no claim of Content ID
  clearance or live upload testing.
- Unified rights gate (`source_rights.py`): every video and music source of
  `ueu create` and `ultra create` (discovery, catalogue, cache, authorized URL,
  local file, prepared mix) gets `ACCEPTED`, `REJECTED` or `UNVERIFIED` with
  provenance, licence and evidence, modification/redistribution permission,
  geo restriction, claim hints, check date and reliability, in
  `rights_gate_report.json`. Sources are re-checked with batched `videos.list`
  calls (removed, private, geo-blocked, partner-claimed).
- Sports rightsholder footage (UEFA, FIFA, leagues, broadcasters, world feeds),
  label-distributed music (VEVO, Topic, ISRC, Spotify), NC/ND licences and
  sources already claimed in `excluded_media.json` are rejected unless an
  explicit commercial grant exists. A creator's own CC label is never enough.
- `--publishable` keeps only accepted sources; commercial mode applies it too.
  Personal edits keep their sources and get a "publication not authorized"
  warning.
- A licence is established only by the grant itself (with the https page where
  it was read) or by a trusted-publisher grant; an uploader's own CC label or the
  API status can restrict, never accept. Contradictory YouTube provenance, sources
  restricted when fetched and YouTube Music credits on extracted audio are no
  longer accepted.
- Upload receipts record the rights gate. `ultra publication-check` re-checks
  the sources and requires a finished Studio check plus `--approve` before
  reporting `READY_FOR_MANUAL_PUBLICATION`; it never changes visibility.

Notable repository changes, not a promise of release packaging or production acceptance.

## Unreleased — 2026-10-31

### Added

- `ueu documentary --spec`: spec-driven voice-over, script-aligned subtitles, footage/card/split compositions, evidence ledger, shot/narration alignment and technical reports. The MLX voice runtime is an external Apple-Silicon setup.
- `--stage plan`, short `--preview` exports and optional proxy layout renders for documentary projects.
- `ultra insert-image`: short silent H.264 still-image inserts with a provenance sidecar, not automatically approved annotations.
- `documentary` optional dependency for `pysubs2` in the main package.
- Optional YouTube `containsSyntheticMedia` disclosure via `synthetic_media` in upload JSON metadata.

### Changed

- Updated disk-guard configuration/script lookup to user configuration paths.
- CI now installs the documentary extra while continuing Python 3.11–3.13 testing.
- Documentation distinguishes checkpointed sports highlights, explicit-input edits and spec-driven documentaries; adds a card-only YAML starter.

### Scope and limitations

- Documentary source preparation, review proxies, matched HD windows, rights evidence and consented/synthetic voice references remain external prerequisites.
- Local QC is technical, not proof of accurate boxer identification, complete action recognition, artistic approval, copyright permission or monetization.
