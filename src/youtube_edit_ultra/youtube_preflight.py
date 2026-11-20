"""Conservative YouTube source availability screen, not a Content ID clearance."""
from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import urlopen

VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
API = "https://www.googleapis.com/youtube/v3/videos"


def youtube_id(url: str) -> str | None:
    """Accept only real YouTube hosts; do not fetch a user-controlled URL."""
    if not isinstance(url, str):
        return None
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return None
    try:
        if parsed.port is not None:
            return None
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host in {"youtu.be", "www.youtu.be"}:
        candidate = parsed.path.strip("/")
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        parts = parsed.path.strip("/").split("/")
        if parts[0] == "watch":
            candidate = parse_qs(parsed.query).get("v", [""])[0]
        elif len(parts) == 2 and parts[0] in {"shorts", "live", "embed"}:
            candidate = parts[1]
        else:
            return None
    else:
        return None
    return candidate if VIDEO_ID.fullmatch(candidate) else None


def classify(video_id: str, payload: dict) -> dict:
    """Fail closed; a positive result still does NOT authorize a re-upload."""
    items = payload.get("items", []) if isinstance(payload, dict) else []
    if not isinstance(items, list) or not items:
        return dict(status="REJECT", reasons=["SOURCE_UNAVAILABLE_OR_NOT_PUBLIC"], video_id=video_id)
    item = next((x for x in items if isinstance(x, dict) and x.get("id") == video_id), None)
    if item is None:
        return dict(status="UNKNOWN", reasons=["MISMATCHED_API_RESULT"], video_id=video_id)
    details = item.get("contentDetails") or {}
    status = item.get("status") or {}
    if not isinstance(details, dict) or not isinstance(status, dict):
        return dict(status="UNKNOWN", reasons=["INCOMPLETE_API_RESULT"], video_id=video_id)
    reasons = []
    geo = None
    region = details.get("regionRestriction")
    if region is not None:
        if isinstance(region, dict) and region.get("blocked") == [] and "allowed" not in region:
            # The official API explicitly defines blocked=[] as viewable in every country.
            # Keep the evidence but do not reject an unrestricted source.
            geo = {"blocked": []}
        else:
            reasons.append("PARTIAL_OR_FULL_GEO_RESTRICTION" if isinstance(region, dict)
                           else "GEO_RESTRICTION_METADATA_UNVERIFIED")
            if isinstance(region, dict):
                # allowed=[] means blocked everywhere. Unknown/malformed geo fields fail closed.
                geo = {k: list(region[k]) for k in ("allowed", "blocked") if isinstance(region.get(k), list)}
    if details.get("licensedContent") is True:
        reasons.append("CONTENT_PARTNER_CLAIM_RISK")
    elif details.get("licensedContent") is not False:
        reasons.append("CONTENT_PARTNER_METADATA_UNVERIFIED")
    if status.get("license") != "creativeCommon":
        reasons.append("NO_CREATIVE_COMMONS_LICENSE")
    if status.get("privacyStatus") != "public":
        reasons.append("SOURCE_NOT_PUBLIC")
    if status.get("embeddable") is False:
        reasons.append("SOURCE_EMBED_RESTRICTED")
    elif status.get("embeddable") is not True:
        reasons.append("SOURCE_EMBED_METADATA_UNVERIFIED")
    if status.get("uploadStatus") not in {"processed"}:
        reasons.append("SOURCE_PROCESSING_UNVERIFIED")
    result = dict(status="REJECT" if reasons else "AVAILABLE_METADATA_ONLY",
                  reasons=reasons, video_id=video_id, license=status.get("license"))
    if geo is not None:
        result["region_restriction"] = geo
    return result


def lookup(video_ids, *, api_key: str | None = None) -> dict:
    """videos.list in batches of 50 ids (1 quota unit each); every id gets a fail-closed result."""
    ids = list(dict.fromkeys(i for i in video_ids if isinstance(i, str) and VIDEO_ID.fullmatch(i)))
    key = api_key if api_key is not None else os.environ.get("YEU_YOUTUBE_API_KEY", "")
    if not key:
        return {i: dict(status="UNKNOWN", reasons=["YOUTUBE_API_KEY_MISSING"], video_id=i) for i in ids}
    out = {}
    for start in range(0, len(ids), 50):
        chunk = ids[start:start + 50]
        query = urlencode({"part": "contentDetails,status", "id": ",".join(chunk), "key": key})
        try:
            with urlopen(API + "?" + query, timeout=10) as response:
                data = response.read(1_000_001)
            if len(data) > 1_000_000:
                raise ValueError("Excessive response size")
            payload = json.loads(data)
            if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                raise ValueError("Incomplete API result")
        except HTTPError as exc:
            # Quota, invalid key or disabled API: never persist the message, it may echo the key.
            out.update({i: dict(status="UNKNOWN", reasons=["YOUTUBE_API_UNAVAILABLE", f"HTTP_{exc.code}"],
                                video_id=i) for i in chunk})
            continue
        except (OSError, ValueError, UnicodeError):
            out.update({i: dict(status="UNKNOWN", reasons=["YOUTUBE_API_UNAVAILABLE"], video_id=i)
                        for i in chunk})
            continue
        for i in chunk:
            # A deleted, private or terminated video is silently omitted from items.
            out[i] = classify(i, {"items": [x for x in payload["items"] if isinstance(x, dict) and x.get("id") == i]})
    return out


def screen(candidate: dict, *, api_key: str | None = None) -> dict:
    """Only called on independently trusted CC candidates before acquisition.

    The API is unable to predict future Content ID claims on the edited video.
    """
    video_id = youtube_id(candidate.get("webpage_url", ""))
    if video_id is None:
        return dict(status="UNKNOWN", reasons=["NOT_A_CANONICAL_YOUTUBE_URL"])
    return lookup([video_id], api_key=api_key)[video_id]
