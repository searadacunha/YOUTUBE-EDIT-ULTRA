import json
from unittest.mock import patch

from youtube_edit_ultra.youtube_preflight import classify, screen, youtube_id

VID = "jNQXAC9IVRw"


def payload(**overrides):
    item = {"id": VID,
            "contentDetails": {"licensedContent": False},
            "status": {"license": "creativeCommon", "privacyStatus": "public",
                       "embeddable": True, "uploadStatus": "processed"}}
    for group, values in overrides.items():
        item[group].update(values)
    return {"items": [item]}


def test_youtube_id_strict_hostname_and_routes():
    assert youtube_id(f"https://youtu.be/{VID}?t=2") == VID
    assert youtube_id(f"https://www.youtube.com/watch?v={VID}&t=2") == VID
    assert youtube_id(f"https://m.youtube.com/shorts/{VID}") == VID
    assert youtube_id(f"https://youtube.com/live/{VID}") == VID
    for url in [f"https://youtube.com.evil.test/watch?v={VID}",
                f"http://youtube.com/watch?v={VID}",
                "https://youtube.com/watch?v=NOT_AN_ID",
                f"https://youtube.com:bad/watch?v={VID}",
                "https://youtube.com/playlist?list=abc"]:
        assert youtube_id(url) is None


def test_rejects_partial_geo_and_partner_content():
    result = classify(VID, payload(contentDetails={"regionRestriction": {"blocked": ["PT"]},
                                                    "licensedContent": True}))
    assert result["status"] == "REJECT"
    assert "PARTIAL_OR_FULL_GEO_RESTRICTION" in result["reasons"]
    assert "CONTENT_PARTNER_CLAIM_RISK" in result["reasons"]
    assert classify(VID, payload(contentDetails={"regionRestriction": {"allowed": ["US"]}}))["status"] == "REJECT"


def test_status_and_unknown_not_approved():
    assert classify(VID, payload())["status"] == "AVAILABLE_METADATA_ONLY"
    assert classify(VID, {"items": []})["status"] == "REJECT"
    assert classify(VID, payload(status={"license": "youtube"}))["status"] == "REJECT"
    assert classify(VID, payload(status={"embeddable": False}))["status"] == "REJECT"
    incomplete = payload()
    del incomplete["items"][0]["contentDetails"]["licensedContent"]
    assert classify(VID, incomplete)["status"] == "REJECT"
    incomplete = payload()
    del incomplete["items"][0]["status"]["embeddable"]
    assert classify(VID, incomplete)["status"] == "REJECT"
    assert classify(VID, {"items": [{"id": "OTHER"}]})["status"] == "UNKNOWN"


def test_missing_key_and_api_error_fail_closed():
    candidate = {"webpage_url": f"https://www.youtube.com/watch?v={VID}"}
    assert screen(candidate, api_key="")["status"] == "UNKNOWN"
    with patch("youtube_edit_ultra.youtube_preflight.urlopen", side_effect=OSError("token=secret")):
        result = screen(candidate, api_key="secret")
    assert result["status"] == "UNKNOWN"
    assert "secret" not in json.dumps(result)


def test_api_response_success():
    class MockResponse:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, _): return json.dumps(payload()).encode()
    candidate = {"webpage_url": f"https://www.youtube.com/watch?v={VID}"}
    with patch("youtube_edit_ultra.youtube_preflight.urlopen", return_value=MockResponse()) as req:
        assert screen(candidate, api_key="secret")["status"] == "AVAILABLE_METADATA_ONLY"
    assert "googleapis.com/youtube/v3/videos" in req.call_args.args[0]

def test_empty_blocklist_is_explicitly_worldwide_available():
    result = classify(VID, payload(contentDetails={"regionRestriction": {"blocked": []}}))
    assert result["status"] == "AVAILABLE_METADATA_ONLY"
    assert result["region_restriction"] == {"blocked": []}
    assert "PARTIAL_OR_FULL_GEO_RESTRICTION" not in result["reasons"]


def test_empty_allowlist_still_means_worldwide_block_and_invalid_geo_fails_closed():
    assert classify(VID, payload(contentDetails={"regionRestriction": {"allowed": []}}))["status"] == "REJECT"
    assert classify(VID, payload(contentDetails={"regionRestriction": {}}))["status"] == "REJECT"
