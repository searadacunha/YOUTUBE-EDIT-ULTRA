# YouTube rights and availability preflight

This is a **risk-reduction workflow, not a Content ID prediction system**. A source
that plays on YouTube can still be claimed, monetized by the rightsholder, muted,
blocked in some countries, or blocked everywhere after you re-upload any part of it.

## 1. Select footage before editing

The one-shot discovery pipeline first requires the existing documented reuse
permission and a trusted Creative Commons publisher. It now also calls the
official YouTube Data API v3 for **automatically discovered YouTube candidates**.
Any of these conditions exclude a candidate from automatic acquisition:

- API shows regional restrictions (either an allowlist or a blocklist), even if
  the current viewer can play the source.
- `licensedContent=true` indicates content claimed by a YouTube partner.
- Source lacks `creativeCommon` licence metadata, is not public or fully
  processed, or disallows embedded playback.
- API request fails, response is incomplete or the API key is unavailable.
  Such candidates are `UNKNOWN`, never silently classified as safe.

This policy is deliberately conservative and may exclude reusable footage.
Passing metadata means only that these specific warning signs were absent at
the time of checking. Creative Commons labels, successful extraction, official
accounts, fair-use arguments and video view counts do **not** establish ownership
or permission to redistribute footage.

Get a YouTube Data API v3 key with permission to call `videos.list` and set
it in your shell without adding it to this repository:

```bash
export YEU_YOUTUBE_API_KEY='YOUR_API_KEY'
ultra inspect-youtube 'https://www.youtube.com/watch?v=jNQXAC9IVRw'
ueu create 'Terence Crawford — R&B 2000s — 10 minutes'
```

The inspect result is `AVAILABLE_METADATA_ONLY`, `REJECT`, or `UNKNOWN`.
The first **does not mean** Content ID clean, rights-cleared, or publishable.
The decision and its reasons are stored on candidates in
`discovery_report.json`. Missing keys or quota failures block newly discovered
YouTube candidates instead of approving them by default.

Existing locally authorised media and explicit authorised URLs retain their
existing permission flow; this metadata check is **not** a substitute for a
complete audit of locally imported footage. Use `ueu create --mode commercial`
only with explicit rights documentation for each audio and video asset when
production is intended for distribution, and review the
`rights_report.json`. User `--attest-local-media` declarations are personal
attestations, not validated upload rights.

## 1b. Unified rights gate for every source

`src/youtube_edit_ultra/source_rights.py` decides for **every** video and music
file that reaches a montage, not only discovered ones: catalogue media, cached
downloads, `authorize-url` acquisitions, `--attest-local-media` files, `ultra
create --video/--music/--source/--music-source`, spec paths, the library
fallback and a `prepared_soundtrack`. `ueu create`/`ultra produce` run it after
selection; `ultra create` runs it before analysis. Each record in
`rights_gate_report.json` holds the identifier and provenance, licence (SPDX-like
id) and evidence, modification and redistribution permission, geo restriction,
claim hints, check date, reliability and the decision:

| Decision | When |
|---|---|
| `ACCEPTED` | recorded grant with evidence, licence allowing modification and redistribution (CC BY, BY-SA, CC0, PDM, Mixkit music) or an explicit commercial grant (`grant_allows(..., "commercial")`), fresh API check for YouTube-origin files, no negative signal |
| `REJECTED` | file missing; source removed/private (API) or private, members-only, paid or login-gated when yt-dlp fetched it; any `regionRestriction` (worldwide or partial); `licensedContent=true`; embedding disabled; claim already read in Studio (`excluded_media.json`, by YouTube id or sha256, whole source even for a windowed claim); sports rightsholder; label-distributed music; ND or NC licence; CC label contradicted by the API |
| `UNVERIFIED` | no grant, local file without provenance, user attestation only, licence unknown, licence declared only by the source (uploader label or API) without a trusted-publisher grant, licence recorded without the https page where it was read, several YouTube videos named for the same bytes, API key missing, quota/HTTP error, offline, incomplete API metadata, unreadable claim registry |

Identity follows content: a copy or hardlink of a catalogued file inherits its
YouTube id, uploader and title, not only its grant. When the grant and the
source's own metadata declare different licences, the most restrictive wins.
Registry rows accept a bare id, a YouTube URL or a sha256 (any case); a row
that can never match is refused.

The decision is conservative and may exclude reusable footage. `ACCEPTED`
still is **not** a Content ID clearance. Personal edits keep their sources and
receive a "publication non autorisée" warning; `--publishable` (and commercial
mode) keeps only `ACCEPTED` sources, so a montage built for publication cannot
be assembled from material whose redistribution is not established.

YouTube-origin files are re-checked with batched `videos.list` calls (50 ids,
1 quota unit each). An id missing from `items` is treated as removed or
private. The API key is never written to reports or receipts.

### Football and other sports

Broadcast footage of UEFA competitions, FIFA tournaments, national leagues and
TV rights holders carries no reusable licence; Commons does not host it either.
On this channel's uploads of 2026-11, UEFA, FIFA and the LFP blocked videos
worldwide and LaLiga by territory, with references per player and competition.
The gate rejects footage whose uploader matches a competition, league or
broadcaster (UEFA, Champions League/UCL, FIFA, World Cup, Ligue 1/LFP, Premier
League, LaLiga, Serie A, Bundesliga, Liga Portugal, beIN, Canal+, DAZN, Sky,
ESPN, RTP, SIC, TVI, Top Rank, Matchroom, …) or whose title names a competition
or a "world feed", unless an explicit commercial grant documents the licence.
Prefer your own footage, footage licensed in writing, or CC BY/CC0 material
from its actual author. Never crop, mirror, change speed or pitch, overlay or
fragment matched material to defeat Content ID.

## 2. Check the final edited file, not just the source URLs

All new `ultra publish` and legacy `ultra create --publish` uploads are
**forced to private** even when `--privacy public`, `--privacy unlisted`,
or `--publish public` was requested. The requested and actual privacy states,
and the video URL, are saved in the local `*.youtube.json` upload receipt.

1. Open `https://studio.youtube.com/` for the uploaded file.
2. Wait until YouTube finishes copyright checks/processing, then inspect
   **Restrictions** and any specific countries affected. Check both the music
   and sports video claims. Do not interpret an unfinished check as a pass.
3. If there is a claim or block, do not publish the file. Rebuild from
   footage and music with documented rights, or pursue an authorized dispute
   where appropriate. Do not try to disguise Content ID matches by pitch,
   speed, cropping, mirroring, layering or fragmenting copyrighted material.
4. Record the result: `ultra publication-check montage.youtube.json --studio
   no-claims|claims-no-impact|pending|restricted|blocked [--approve]`. It
   re-checks every source of `rights_gate_report.json` (a source may have been
   removed, geo-blocked or claimed since the edit) and answers
   `READY_FOR_MANUAL_PUBLICATION` only when the report is the one frozen with
   the upload (its sha256 is in the receipt), it was already publishable at
   upload, every source still has the bytes it had at render time and is
   `ACCEPTED`, the Studio check is finished with no blocking claim, and
   `--approve` is given.
   Anything else is `KEEP_PRIVATE` with reasons, stored in the receipt. The
   Studio result is the uploader's declaration: no official API exposes Content
   ID decisions to ordinary creators, and this tool does not scrape Studio.
5. Publish manually from Studio **only after** that. The tool never calls
   `videos.update` and never changes visibility. Future claims or territorial
   policy changes remain possible; add each new claim to
   `~/.local/share/youtube-edit-ultra/excluded_media.json` so the gate rejects
   that source next time.

Pipeline: source → rights gate → selection → montage → final checks → private
upload → Studio check → `publication-check` → manual publication.

YouTube's public creator Data API does **not** provide a reliable general-purpose
pre-upload query for Content ID decisions. Studio's copyright check applies to
the **final MP4** and is not a guarantee against later claims. There is no
general GitHub solution that makes unlicensed Champions League broadcast
footage safe to re-upload.

## 2b. Open-source practice studied (2026-11-17)

Methods kept, code not copied:

- WordPress/openverse (MIT): accept a licence only from an explicit list after
  normalising its URL; unknown means not accepted.
- yt-dlp: `license` is a scraped label ("Creative Commons Attribution license
  (reuse allowed)" / "Standard YouTube License"); kept as a hint behind the Data
  API `status.license`, never as proof.
- sns-sdks/python-youtube (MIT): field set of `regionRestriction`,
  `licensedContent`, `uploadStatus`; we keep plain `urllib` instead of a new
  dependency.
- IsaacAndreu/edit-vid3 and hamravichauhan/cliper: check aggressive sports
  claimants first; their channel allowlists that grant CC by name were rejected.
- fsfe/reuse-tool, lemomo-ai/lemo-opuscar, DyeAllPies/dyeallpies-productions:
  one provenance record per asset with licence, evidence and check date, and a
  re-check before publishing.
- 7x11x13/youtube-up, adasq/youtube-studio, TAEJK88/youtube-upload-presets:
  Studio's internal claim endpoints need cookies and break often; not used.
  The YouTube Content ID API is for content partners only.

## 3. Regression scope

The pipeline leaves action rankings, whole-event boundaries, original files,
audio mastering, existing job checkpoints, and editorial style unchanged.
Unit tests mock API responses (geo allowlist/blocklist, claim warning flags,
missing/invalid metadata, no key and network errors) and the upload API
(privacy status and duplicate-upload receipt). `tests/test_source_rights.py`
and `tests/test_rights_pipeline.py` cover worldwide and partial blocks, removed
sources, unknown licences, commercial music, missing metadata, API errors,
unprovenanced local files, verifiable licences, sports footage, claim registry,
`--publishable` jobs with real FFmpeg fixtures and premature publication. They do not upload any real
YouTube video or test live Content ID on a user's account.

References:
- [YouTube Data API video resource](https://developers.google.com/youtube/v3/docs/videos)
- [YouTube Studio copyright checks](https://support.google.com/youtube/answer/17011221)
- [How Content ID works](https://support.google.com/youtube/answer/2797370)
