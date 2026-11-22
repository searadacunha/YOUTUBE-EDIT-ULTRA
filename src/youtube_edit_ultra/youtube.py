"""Explicit OAuth YouTube upload; no unapproved automatic publication."""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from .media import UltraError

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _write_private_token(path: Path, content: str) -> None:
    """Atomically replace OAuth credentials, never creating a world-readable file.

    mkstemp creates mode 0600 from the outset. On failure the previous token
    remains intact and the temporary file is removed.
    """
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".token-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def client():
    try:
        from googleapiclient.discovery import build
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise UltraError("Option YouTube manquante : pip install '.[youtube]'") from exc
    auth_dir = Path.home() / ".config" / "youtube-edit-ultra"
    auth_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    token = auth_dir / "token.json"
    secrets = Path(os.environ.get("YEU_CLIENT_SECRETS", str(auth_dir / "client_secret.json")))
    creds = None
    if token.exists():
        creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not secrets.exists():
                raise UltraError(f"OAuth absent : placer le fichier client_secret.json ici : {secrets}")
            flow = InstalledAppFlow.from_client_secrets_file(str(secrets), scopes=SCOPES)
            creds = flow.run_local_server(port=0)
        _write_private_token(token, creds.to_json())
    return build("youtube", "v3", credentials=creds)


def _digest(path: Path | None):
    """Freeze the report seen at upload, so a later rewrite cannot unlock publication."""
    try:
        import hashlib
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except (OSError, TypeError):
        return None


def _gate_summary(report: Path | None, video: Path):
    """Record the pre-montage rights decision with the upload; a missing report is never a pass."""
    try:
        data = json.loads(Path(report).read_text(encoding="utf-8"))
        summary = data["summary"]
    except (OSError, TypeError, ValueError, KeyError):
        return "MISSING"
    if data.get("montage_sha256") != _digest(video):
        return "NOT_BOUND_TO_THIS_MONTAGE"
    return summary


def upload(video: Path, *, title: str, description: str, tags: list[str],
           privacy: str, thumbnail: Path | None = None, state_path: Path | None = None,
           synthetic_media: bool = False, rights_report: Path | None = None) -> dict:
    if privacy not in {"private", "unlisted", "public"}:
        raise UltraError("Publication explicite requise : private, unlisted ou public")
    # A public API upload cannot report Content ID claims for an ordinary
    # creator account. Never make a newly rendered video public or unlisted
    # before the uploader reviews YouTube Studio's actual Copyright checks.
    actual_privacy = "private"
    if not video.is_file():
        raise UltraError(f"Vidéo manquante: {video}")
    state_path = state_path or video.with_suffix(".youtube.json")
    if state_path.is_file():
        state = json.loads(state_path.read_text())
        if state.get("video_id"):
            # Do not post duplicates after a successful upload.
            return state
    from googleapiclient.http import MediaFileUpload
    youtube = client()
    request = youtube.videos().insert(
        part="snippet,status",
        body={"snippet": {"title": title[:100], "description": description[:5000],
                          "tags": tags[:35], "categoryId": "17"},
              "status": {"privacyStatus": actual_privacy, "selfDeclaredMadeForKids": False,
                         # YouTube disclosure for realistic altered/synthetic content (e.g. a cloned voice)
                         "containsSyntheticMedia": bool(synthetic_media)}},
        media_body=MediaFileUpload(str(video), mimetype="video/mp4",
                                   chunksize=8 * 1024 * 1024, resumable=True),
    )
    response = None
    retries = 0
    while response is None:
        try:
            _, response = request.next_chunk()
            retries = 0
        except Exception as exc:
            retries += 1
            if retries > 5:
                raise UltraError(f"YouTube upload échoué : {exc}") from exc
            time.sleep(min(2 ** retries, 32))
    vid = response.get("id")
    if not vid:
        raise UltraError("YouTube n'a pas renvoyé d'identifiant de vidéo")
    state = {"video_id": vid, "url": f"https://www.youtube.com/watch?v={vid}",
             "studio_url": f"https://studio.youtube.com/video/{vid}/edit",
             "requested_privacy": privacy, "uploaded_privacy": actual_privacy,
             "copyright_check": "PENDING_STUDIO_REVIEW",
             "publication_requires_manual_approval": True,
             "rights_report": str(rights_report) if rights_report and Path(rights_report).is_file() else None,
             "rights_gate": _gate_summary(rights_report, video),
             "rights_report_sha256": _digest(rights_report),
             "note": "Upload privé. Vérifier les restrictions Content ID et pays dans YouTube Studio, lancer 'ultra publication-check', puis publier manuellement."}
    state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    if thumbnail and thumbnail.exists():
        try:
            from googleapiclient.http import MediaFileUpload
            youtube.thumbnails().set(videoId=vid,
                                     media_body=MediaFileUpload(str(thumbnail), mimetype="image/jpeg")).execute()
            state["thumbnail_uploaded"] = True
            state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False))
        except Exception as exc:
            state["thumbnail_error"] = str(exc)
            state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    return state
