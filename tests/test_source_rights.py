"""Unified rights gate: simulated YouTube API responses and owned local files only, no network."""
import io
import json
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from youtube_edit_ultra import catalogue, source_rights
from youtube_edit_ultra.source_rights import ACCEPTED, REJECTED, UNVERIFIED, gate, licence_terms

VID = "jNQXAC9IVRw"
CC_BY = "https://creativecommons.org/licenses/by/4.0/"


def api_item(vid=VID, **overrides):
    item = {"id": vid, "contentDetails": {"licensedContent": False},
            "status": {"license": "creativeCommon", "privacyStatus": "public",
                       "embeddable": True, "uploadStatus": "processed"}}
    for group, values in overrides.items():
        item[group].update(values)
    return item


class Response:
    def __init__(self, payload):
        self.data = json.dumps(payload).encode()
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, _): return self.data


def media(tmp_path, name, *, sidecar=None, grant=None, state=None):
    """An owned fixture file, optionally with a yt-dlp sidecar and a recorded grant."""
    path = tmp_path / name
    path.write_bytes(b"owned fixture " + name.encode())
    if sidecar is not None:
        path.with_suffix(".info.json").write_text(json.dumps(sidecar))
    state = state if state is not None else catalogue.empty()
    if grant is not None:
        state["permissions"][catalogue.sha256(path)] = grant
    return path, state


def youtube_source(tmp_path, **sidecar):
    meta = dict(webpage_url=f"https://www.youtube.com/watch?v={VID}", id=VID,
                uploader="Independent Creator", channel_url="https://example.org/publisher",
                title="Training session", license="Creative Commons Attribution license (reuse allowed)")
    meta.update(sidecar)
    grant = catalogue.permission_grant(dict(meta, checked_at=1), basis="CC_BY_from_user_trusted_publisher")
    return media(tmp_path, f"source-{VID}.mp4", sidecar=meta, grant=grant)


def decide(path, state, kind="video", items=None, **kwargs):
    if items is None:
        return gate([(str(path), kind, None)], state=state, **kwargs)[0]
    with patch("youtube_edit_ultra.youtube_preflight.urlopen", return_value=Response({"items": items})):
        return gate([(str(path), kind, None)], state=state, api_key="test-key", **kwargs)[0]


def test_verifiable_cc_by_youtube_source_is_accepted(tmp_path):
    path, state = youtube_source(tmp_path)
    record = decide(path, state, items=[api_item()])
    assert record["decision"] == ACCEPTED, record["reasons"]
    assert record["licence"]["spdx"] == "CC-BY-3.0"
    assert record["modification_allowed"] and record["redistribution_allowed"]
    assert record["evidence_basis"] == ["youtube_data_api", "recorded_grant"]
    assert record["checked_at"] and record["youtube_id"] == VID


def test_licensed_stock_music_without_youtube_id_is_accepted(tmp_path):
    grant = catalogue.permission_grant(dict(license=CC_BY, url="https://example.org/track"),
                                       credit="Song — Artist | CC BY 4.0")
    path, state = media(tmp_path, "track.mp3", grant=grant)
    record = decide(path, state, kind="audio")
    assert record["decision"] == ACCEPTED and record["licence"]["spdx"] == "CC-BY-4.0"


def test_documented_commercial_grant_is_accepted(tmp_path):
    grant = catalogue.permission_grant(
        "Signed licence #12", mode="commercial", license="Custom sync licence",
        rights_verified_by="Rights reviewer", rights_verified_at="2026-11-17", commercial_use=True,
        adaptation=True, distribution=True, monetization=True, attribution_required=False)
    path, state = media(tmp_path, "licensed.mp4", grant=grant)
    record = decide(path, state)
    assert record["decision"] == ACCEPTED and record["reliability"] == "documented_licence"


def test_worldwide_block_is_rejected_and_recorded(tmp_path):
    path, state = youtube_source(tmp_path)
    record = decide(path, state, items=[api_item(contentDetails={"regionRestriction": {"allowed": []}})])
    assert record["decision"] == REJECTED
    assert "PARTIAL_OR_FULL_GEO_RESTRICTION" in record["reasons"]
    assert record["geo_restriction"] == {"allowed": []}


def test_partial_country_block_is_rejected(tmp_path):
    path, state = youtube_source(tmp_path)
    record = decide(path, state, items=[api_item(contentDetails={"regionRestriction": {"blocked": ["FR", "CA"]}})])
    assert record["decision"] == REJECTED and record["geo_restriction"] == {"blocked": ["FR", "CA"]}


def test_removed_or_private_source_is_rejected_even_with_old_grant(tmp_path):
    path, state = youtube_source(tmp_path)
    record = decide(path, state, items=[])  # videos.list silently omits deleted/private ids
    assert record["decision"] == REJECTED and "SOURCE_REMOVED_OR_PRIVATE" in record["reasons"]


def test_partner_claimed_source_is_rejected(tmp_path):
    path, state = youtube_source(tmp_path)
    record = decide(path, state, items=[api_item(contentDetails={"licensedContent": True})])
    assert "CONTENT_PARTNER_CLAIM_RISK" in record["reasons"] and record["decision"] == REJECTED


def test_missing_api_metadata_is_unverified_not_accepted(tmp_path):
    path, state = youtube_source(tmp_path)
    item = api_item()
    del item["contentDetails"]["licensedContent"]
    record = decide(path, state, items=[item])
    assert record["decision"] == UNVERIFIED and "CONTENT_PARTNER_METADATA_UNVERIFIED" in record["reasons"]


@pytest.mark.parametrize("error", [
    HTTPError("https://www.googleapis.com/?key=test-key", 403, "quotaExceeded key=test-key", {}, io.BytesIO()),
    OSError("network down key=test-key"),
])
def test_api_error_is_unverified_and_never_leaks_the_key(tmp_path, error):
    path, state = youtube_source(tmp_path)
    with patch("youtube_edit_ultra.youtube_preflight.urlopen", side_effect=error):
        record = gate([(str(path), "video", None)], state=state, api_key="test-key")[0]
    assert record["decision"] == UNVERIFIED and "YOUTUBE_API_UNAVAILABLE" in record["reasons"]
    assert ("HTTP_403" in record["reasons"]) == isinstance(error, HTTPError)
    assert "test-key" not in json.dumps(record)


def test_missing_key_and_offline_mode_are_unverified(tmp_path):
    path, state = youtube_source(tmp_path)
    assert "YOUTUBE_API_KEY_MISSING" in decide(path, state)["reasons"]
    offline = decide(path, state, offline=True)
    assert offline["decision"] == UNVERIFIED and "OFFLINE_NOT_RECHECKED" in offline["reasons"]


def test_unknown_licence_is_unverified(tmp_path):
    grant = catalogue.permission_grant("Owner said yes by message")
    path, state = media(tmp_path, "clip.mp4", grant=grant)
    record = decide(path, state)
    assert record["decision"] == UNVERIFIED and record["reasons"] == ["LICENCE_UNKNOWN"]


def test_local_file_without_provenance_is_unverified(tmp_path):
    path, state = media(tmp_path, "found-on-disk.mp4")
    record = decide(path, state)
    assert record["decision"] == UNVERIFIED and record["reasons"] == ["LOCAL_FILE_WITHOUT_PROVENANCE"]
    assert record["reliability"] == "none"


def test_user_attestation_is_never_accepted(tmp_path):
    grant = catalogue.permission_grant("Explicit user attestation", basis="user_attested_not_independently_verified")
    path, state = media(tmp_path, "mine.mp4", grant=grant)
    assert decide(path, state)["reasons"] == ["USER_ATTESTATION_ONLY"]


@pytest.mark.parametrize("sidecar", [
    dict(uploader="Daddy Yankee - Topic"),
    dict(uploader="Independent", description="Provided to YouTube by Universal Music"),
    dict(uploader="DaddyYankeeVEVO", channel="DaddyYankeeVEVO"),
])
def test_commercial_music_without_licence_is_rejected(tmp_path, sidecar):
    sidecar = dict(sidecar, webpage_url="https://example.org/song", license=CC_BY)
    grant = catalogue.permission_grant(dict(license=CC_BY))
    path, state = media(tmp_path, "song.mp3", sidecar=sidecar, grant=grant)
    record = decide(path, state, kind="audio")
    assert record["decision"] == REJECTED and "COMMERCIAL_MUSIC_WITHOUT_LICENCE" in record["reasons"]


def test_spotify_identified_track_is_commercial(tmp_path):
    path, state = media(tmp_path, "hit.mp3", grant=catalogue.permission_grant("bought it"))
    state["media"][str(path.resolve())] = dict(path=str(path.resolve()), sha256=catalogue.sha256(path),
                                               spotify_uri="spotify:track:abc")
    assert "COMMERCIAL_MUSIC_WITHOUT_LICENCE" in decide(path, state, kind="audio")["reasons"]


@pytest.mark.parametrize("uploader,title", [
    ("UEFA", "Ronaldo header"),
    ("Fan Account", "Champions League final 2016 all goals"),
    ("beIN SPORTS France", "Résumé"),
    ("Some fan", "UCL_Final_World_Feed"),
])
def test_sports_rightsholder_footage_is_rejected_despite_cc_label(tmp_path, uploader, title):
    path, state = youtube_source(tmp_path, uploader=uploader, title=title)
    record = decide(path, state, items=[api_item()])
    assert record["decision"] == REJECTED and "SPORTS_RIGHTSHOLDER_FOOTAGE" in record["reasons"]
    assert record["claim_hints"]["sports_rightsholder"]


def test_short_broadcaster_acronyms_do_not_match_titles(tmp_path):
    path, state = youtube_source(tmp_path, uploader="Independent Creator", title="Classic sic parry drills")
    assert decide(path, state, items=[api_item()])["decision"] == ACCEPTED


def test_previous_content_id_claim_rejects_the_whole_source(tmp_path):
    path, state = youtube_source(tmp_path)
    claims = [dict(id=VID, reason="UEFA visual claim", window=[10, 20])]
    record = decide(path, state, items=[api_item()], claims=claims)
    assert record["decision"] == REJECTED and record["claim_hints"]["previous_claims"] == ["UEFA visual claim"]
    by_hash = [dict(sha256=catalogue.sha256(path), reason="claimed by hash")]
    assert decide(path, state, items=[api_item()], claims=by_hash)["decision"] == REJECTED


def test_claim_registry_shared_with_render_is_read_and_validated(tmp_path):
    registry = tmp_path / "excluded_media.json"
    registry.write_text(json.dumps([dict(id=VID, reason="claimed")]))
    assert source_rights.load_claims(registry)[0]["id"] == VID
    registry.write_text(json.dumps([dict(reason="no id")]))
    with pytest.raises(source_rights.UltraError):
        source_rights.load_claims(registry)


def test_cc_label_contradicted_by_youtube_is_rejected(tmp_path):
    path, state = youtube_source(tmp_path)
    record = decide(path, state, items=[api_item(status={"license": "youtube"})])
    assert record["decision"] == REJECTED and "LICENCE_CONTRADICTED_BY_YOUTUBE" in record["reasons"]


@pytest.mark.parametrize("licence,reason", [
    ("https://creativecommons.org/licenses/by-nd/4.0/", "LICENCE_FORBIDS_MODIFICATION"),
    ("CC BY-NC-SA 4.0", "NON_COMMERCIAL_LICENCE"),
])
def test_nd_and_nc_licences_are_rejected(tmp_path, licence, reason):
    path, state = media(tmp_path, "art.mp4", grant=catalogue.permission_grant(dict(license=licence)))
    assert reason in decide(path, state)["reasons"]


def test_missing_file_is_rejected(tmp_path):
    path, state = media(tmp_path, "gone.mp4", grant=catalogue.permission_grant(dict(license=CC_BY)))
    path.unlink()
    assert "SOURCE_FILE_MISSING" in decide(path, state)["reasons"]


def test_licence_parsing_uses_an_explicit_list():
    assert licence_terms("CC-BY-SA-4.0")["spdx"] == "CC-BY-SA-4.0"
    assert licence_terms("https://creativecommons.org/publicdomain/zero/1.0/")["spdx"] == "CC0-1.0"
    assert licence_terms("Standard YouTube License")["modification"] is False
    for unknown in ("", None, "free to use", "https://creativecommons.org/licenses/sampling+/1.0/",
                    "https://evil.example/creativecommons"):
        assert licence_terms(unknown) is None


def test_batch_lookup_uses_one_call_per_fifty_ids(tmp_path):
    from youtube_edit_ultra.youtube_preflight import lookup
    ids = [f"{i:011d}" for i in range(51)]
    calls = []

    def fake(url, timeout):
        calls.append(url)
        return Response({"items": [api_item(i) for i in ids]})
    with patch("youtube_edit_ultra.youtube_preflight.urlopen", side_effect=fake):
        result = lookup(ids, api_key="k")
    assert len(calls) == 2 and all(r["status"] == "AVAILABLE_METADATA_ONLY" for r in result.values())
    # A deleted id inside a batch is omitted by the API: only that id is rejected.
    with patch("youtube_edit_ultra.youtube_preflight.urlopen",
               return_value=Response({"items": [api_item(i) for i in ids[1:]]})):
        result = lookup(ids, api_key="k")
    assert result[ids[0]]["reasons"] == ["SOURCE_UNAVAILABLE_OR_NOT_PUBLIC"]
    assert all(result[i]["status"] == "AVAILABLE_METADATA_ONLY" for i in ids[1:])
    # A malformed answer (no items list) is unknown, never "removed".
    with patch("youtube_edit_ultra.youtube_preflight.urlopen", return_value=Response({"error": {}})):
        assert lookup(ids[:1], api_key="k")[ids[0]]["status"] == "UNKNOWN"


def test_report_marks_mixed_sources_not_publishable(tmp_path):
    good, state = media(tmp_path, "good.mp3", grant=catalogue.permission_grant(
        dict(license=CC_BY, source_url="https://example.org/track")))
    bad, state = media(tmp_path, "bad.mp4", state=state)
    records = gate([(str(good), "audio", None), (str(bad), "video", None)], state=state)
    report = source_rights.write_report(tmp_path, records)
    assert report["summary"]["counts"] == {ACCEPTED: 1, REJECTED: 0, UNVERIFIED: 1}
    assert not report["summary"]["publishable"]
    assert json.loads((tmp_path / "rights_gate_report.json").read_text())["summary"]["publishable"] is False
    with pytest.raises(source_rights.UltraError, match="bad.mp4"):
        source_rights.assert_publishable(records)


# Regressions found by the adversarial review of this gate.

def test_copied_file_inherits_catalogue_identity_not_only_the_grant(tmp_path):
    original, state = youtube_source(tmp_path, uploader="UEFA")
    state["media"][str(original.resolve())] = dict(
        path=str(original.resolve()), sha256=catalogue.sha256(original),
        url=f"https://www.youtube.com/watch?v={VID}", uploader="UEFA", title="Final")
    copy = tmp_path / "desktop" / "renamed.mp4"
    copy.parent.mkdir()
    copy.write_bytes(original.read_bytes())  # no sidecar, not a catalogue key
    record = decide(copy, state, items=[api_item()])
    assert record["youtube_id"] == VID and record["decision"] == REJECTED
    assert "SPORTS_RIGHTSHOLDER_FOOTAGE" in record["reasons"]


def test_grant_evidence_supplies_identity_when_catalogue_row_is_missing(tmp_path):
    path, state = youtube_source(tmp_path)
    path.with_suffix(".info.json").unlink()
    record = decide(path, state, items=[])
    assert record["youtube_id"] == VID and "SOURCE_REMOVED_OR_PRIVATE" in record["reasons"]


@pytest.mark.parametrize("declared", ["public domain", "https://mixkit.co/license/#musicFree", "CC0"])
def test_any_declared_licence_contradicted_by_youtube_standard_is_rejected(tmp_path, declared):
    path, state = youtube_source(tmp_path, license=declared)
    state["permissions"][catalogue.sha256(path)] = catalogue.permission_grant(dict(license=declared))
    record = decide(path, state, items=[api_item(status={"license": "youtube"})])
    assert record["decision"] == REJECTED and "LICENCE_CONTRADICTED_BY_YOUTUBE" in record["reasons"]


def test_grant_licence_cannot_erase_source_nc_nd(tmp_path):
    sidecar = dict(webpage_url="https://vimeo.example/1", license="https://creativecommons.org/licenses/by-nc-nd/4.0/")
    path, state = media(tmp_path, "vimeo.mp4", sidecar=sidecar,
                        grant=catalogue.permission_grant(dict(license=CC_BY)))
    record = decide(path, state)
    assert record["decision"] == REJECTED
    assert {"NON_COMMERCIAL_LICENCE", "LICENCE_FORBIDS_MODIFICATION"} <= set(record["reasons"])


def test_claim_registry_normalises_urls_spaces_and_case(tmp_path):
    path, state = youtube_source(tmp_path)
    registry = tmp_path / "excluded_media.json"
    for row in (dict(id=f"https://youtu.be/{VID}", reason="url"), dict(id=f" {VID} ", reason="space"),
                dict(sha256=catalogue.sha256(path).upper(), reason="upper")):
        registry.write_text(json.dumps([row]))
        claims = source_rights.load_claims(registry)
        assert decide(path, state, items=[api_item()], claims=claims)["decision"] == REJECTED, row
    registry.write_text(json.dumps([dict(id="not a video", reason="typo")]))
    with pytest.raises(source_rights.UltraError):
        source_rights.load_claims(registry)


def test_unreadable_registry_never_aborts_a_personal_edit_nor_looks_publishable(tmp_path, monkeypatch):
    registry = tmp_path / "broken.json"
    registry.write_text("[{\"truncated")
    monkeypatch.setenv("YEU_CLAIMS_FILE", str(registry))
    path, state = media(tmp_path, "good.mp3", grant=catalogue.permission_grant(dict(license=CC_BY)))
    record = gate([(str(path), "audio", None)], state=state)[0]
    assert record["decision"] == UNVERIFIED and "CLAIM_REGISTRY_UNREADABLE" in record["reasons"]


def test_default_claim_registry_is_loaded_by_the_gate(tmp_path, monkeypatch):
    path, state = youtube_source(tmp_path)
    registry = tmp_path / "excluded_media.json"
    registry.write_text(json.dumps([dict(id=VID, reason="claimed in Studio")]))
    monkeypatch.setenv("YEU_CLAIMS_FILE", str(registry))
    record = decide(path, state, items=[api_item()])  # claims=None: the gate reads the registry itself
    assert record["decision"] == REJECTED and "PREVIOUS_CONTENT_ID_CLAIM" in record["reasons"]


def test_youtube_cc_status_supplies_the_licence_when_none_is_declared(tmp_path):
    path, state = youtube_source(tmp_path, license=None)
    state["permissions"][catalogue.sha256(path)] = catalogue.permission_grant(
        dict(webpage_url=f"https://www.youtube.com/watch?v={VID}"), basis="CC_BY_from_user_trusted_publisher")
    record = decide(path, state, items=[api_item()])
    assert record["decision"] == ACCEPTED and record["licence"]["spdx"] == "CC-BY-3.0"


def test_copy_with_its_own_bare_catalogue_row_inherits_the_youtube_identity(tmp_path):
    original, state = youtube_source(tmp_path)
    digest = catalogue.sha256(original)
    state["media"][str(original.resolve())] = dict(path=str(original.resolve()), sha256=digest,
                                                   url=f"https://www.youtube.com/watch?v={VID}")
    copy = tmp_path / "roots" / "copy.mp4"
    copy.parent.mkdir()
    copy.write_bytes(original.read_bytes())
    state["media"][str(copy.resolve())] = dict(path=str(copy.resolve()), sha256=digest)  # scanned, no url
    state["permissions"][digest] = catalogue.permission_grant("plain text evidence")
    record = decide(copy, state, items=[])
    assert record["youtube_id"] == VID and "SOURCE_REMOVED_OR_PRIVATE" in record["reasons"]


def test_offline_never_calls_the_api_and_unchecked_youtube_is_unverified(tmp_path):
    path, state = youtube_source(tmp_path)
    with patch("youtube_edit_ultra.youtube_preflight.urlopen", side_effect=AssertionError("network used")):
        record = gate([(str(path), "video", None)], state=state, api_key="test-key", offline=True)[0]
    assert record["decision"] == UNVERIFIED and record["reasons"] == ["OFFLINE_NOT_RECHECKED"]
    source = source_rights.describe(path, "video", state=state)
    direct = source_rights.assess(source, grant=state["permissions"][catalogue.sha256(path)])
    assert direct["decision"] == UNVERIFIED and "YOUTUBE_STATUS_NOT_RECHECKED" in direct["reasons"]


def test_unlisted_source_and_empty_reports_are_not_publishable(tmp_path):
    path, state = youtube_source(tmp_path)
    record = decide(path, state, items=[api_item(status={"privacyStatus": "unlisted"})])
    assert record["decision"] == REJECTED and "SOURCE_NOT_PUBLIC" in record["reasons"]
    assert source_rights.summary([])["publishable"] is False


# False acceptances found by the 2026-11-18 audit.

def test_uploader_cc_label_with_a_plain_text_grant_is_not_enough(tmp_path):
    meta = dict(webpage_url=f"https://www.youtube.com/watch?v={VID}", id=VID, uploader="Someone",
                license="Creative Commons Attribution license (reuse allowed)")
    path, state = media(tmp_path, f"source-{VID}.mp4", sidecar=meta,
                        grant=catalogue.permission_grant("authorize-url: found it online"))
    record = decide(path, state, items=[api_item()])
    assert record["decision"] == UNVERIFIED and record["reasons"] == ["LICENCE_ONLY_DECLARED_BY_SOURCE"]


def test_declared_licence_needs_the_page_where_it_was_read(tmp_path):
    path, state = media(tmp_path, "clip.mp4", grant=catalogue.permission_grant(dict(license=CC_BY)))
    assert decide(path, state)["reasons"] == ["LICENCE_WITHOUT_SOURCE_EVIDENCE"]
    state["permissions"][catalogue.sha256(path)] = catalogue.permission_grant(
        dict(license=CC_BY, source_url="http://example.org/insecure"))
    assert decide(path, state)["reasons"] == ["LICENCE_WITHOUT_SOURCE_EVIDENCE"]


def test_contradictory_youtube_provenance_is_unverified_and_every_id_is_checked(tmp_path):
    other = "dQw4w9WgXcQ"
    path, state = youtube_source(tmp_path, webpage_url=f"https://www.youtube.com/watch?v={other}", id=other)
    state["media"][str(path.resolve())] = dict(path=str(path.resolve()), sha256=catalogue.sha256(path),
                                               url=f"https://www.youtube.com/watch?v={VID}")
    record = decide(path, state, items=[api_item()])
    assert record["decision"] == UNVERIFIED and "PROVENANCE_CONFLICT" in record["reasons"]
    claimed = decide(path, state, items=[api_item()], claims=[dict(id=other, reason="claimed")])
    assert claimed["decision"] == REJECTED and "PREVIOUS_CONTENT_ID_CLAIM" in claimed["reasons"]


@pytest.mark.parametrize("availability", ["subscriber_only", "premium_only", "needs_auth", "private"])
def test_source_restricted_when_downloaded_is_rejected(tmp_path, availability):
    path, state = youtube_source(tmp_path, availability=availability)
    record = decide(path, state, items=[api_item()])
    assert record["decision"] == REJECTED and f"SOURCE_AVAILABILITY_{availability.upper()}" in record["reasons"]


def test_youtube_music_credits_mark_extracted_audio_as_commercial(tmp_path):
    path, state = youtube_source(tmp_path, extractor_key="Youtube", track="Gasolina", artist="Daddy Yankee")
    path = path.rename(path.with_suffix(".mp3"))
    path.with_name(f"source-{VID}.info.json").rename(path.with_suffix(".info.json"))
    record = decide(path, state, kind="audio", items=[api_item()])
    assert "COMMERCIAL_MUSIC_WITHOUT_LICENCE" in record["reasons"]
    assert "youtube music metadata" in record["claim_hints"]["commercial_music"]


def test_oversized_api_answer_is_unknown(tmp_path):
    from youtube_edit_ultra.youtube_preflight import lookup

    base = json.dumps({"items": [api_item()], "pad": ""})
    # Valid JSON one byte over the cap: only the size limit can refuse it.
    huge = Response({})
    huge.data = json.dumps({"items": [api_item()], "pad": " " * (1_000_001 - len(base))}).encode()
    assert len(huge.data) == 1_000_001
    with patch("youtube_edit_ultra.youtube_preflight.urlopen", return_value=huge):
        assert lookup([VID], api_key="k")[VID]["status"] == "UNKNOWN"
