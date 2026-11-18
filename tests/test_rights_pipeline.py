"""Rights gate wired into one-shot jobs, upload receipts and the publication check (no network)."""
import json
import shutil
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest

from youtube_edit_ultra import catalogue, jobs
from youtube_edit_ultra.cli import main as ultra

from test_jobs_integration import fixture_job, job_state

CC_BY = "https://creativecommons.org/licenses/by/4.0/"
RELEASE = "https://example.org/fixture-release"  # page where the fixture licence is published
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg not installed")


def licence_fixture_grants(state_dir, paths):
    state = catalogue.read(state_dir / "catalogue.json")
    for p in paths:
        state["permissions"][catalogue.sha256(p)] = catalogue.permission_grant(
            dict(license=CC_BY, source_url=RELEASE, note="Owned synthetic fixture released by this test under CC BY 4.0"))
    catalogue.save(state_dir / "catalogue.json", state)


@needs_ffmpeg
def test_personal_job_still_delivers_but_reports_unverified_rights(tmp_path):
    args, _, video, music = fixture_job(tmp_path)
    assert jobs.main(args) == 0
    job, manifest = job_state(tmp_path)
    gate = catalogue.read(job / "rights_gate_report.json")
    assert gate["summary"]["counts"]["UNVERIFIED"] == 2 and not gate["summary"]["publishable"]
    assert {r["reasons"][0] for r in gate["records"]} == {"LICENCE_UNKNOWN"}
    report = catalogue.read(job / "production_report.json")
    assert report["rights_gate"]["publishable"] is False
    assert any("Publication non autorisée" in w for w in report["warnings"])
    # The delivered MP4 carries its own report, so a later upload can find it.
    delivered = Path(manifest["delivery"]["video"])
    assert catalogue.read(delivered.parent / "rights_gate_report.json")["summary"]["publishable"] is False


@needs_ffmpeg
def test_publishable_job_refuses_sources_that_are_not_accepted(tmp_path):
    args, _, _, _ = fixture_job(tmp_path)
    assert jobs.main([*args, "--publishable"]) == 2
    job, manifest = job_state(tmp_path)
    assert manifest["status"] != "COMPLETE_TECHNICAL"
    assert not list(job.rglob("*.mp4"))
    blockers = catalogue.read(job / "production_report.json")["blockers"]
    assert any("Aucune vidéo pertinente" in b for b in blockers)


@needs_ffmpeg
def test_publishable_job_renders_with_documented_cc_sources(tmp_path):
    args, state_dir, video, music = fixture_job(tmp_path)
    licence_fixture_grants(state_dir, (video, music))
    assert jobs.main([*args, "--publishable"]) == 0
    job, manifest = job_state(tmp_path)
    assert manifest["status"] == "COMPLETE_TECHNICAL"
    delivered = Path(manifest["delivery"]["video"])
    gate = catalogue.read(delivered.parent / "rights_gate_report.json")
    assert gate["summary"]["publishable"] and gate["summary"]["counts"]["ACCEPTED"] == 2
    assert gate["montage_sha256"] == catalogue.sha256(delivered)  # bound to the delivered file


@needs_ffmpeg
def test_direct_create_refuses_unprovenanced_local_file_when_publishable(tmp_path):
    args, _, video, music = fixture_job(tmp_path)
    out = tmp_path / "direct"
    code = ultra(["--no-banner", "create", "--athlete", "Canelo Alvarez", "--duration", "6s",
                  "--video", str(video), "--music", str(music), "--output-dir", str(out),
                  "--publishable", "--dry-run"])
    assert code == 2
    [report] = out.glob("*/rights_gate_report.json")
    reasons = {r["path"]: r["reasons"] for r in catalogue.read(report)["records"]}
    assert reasons[str(video.resolve())] == ["LOCAL_FILE_WITHOUT_PROVENANCE"]


def fake_youtube(monkeypatch):
    package = types.ModuleType("googleapiclient")
    package.__path__ = []
    http = types.ModuleType("googleapiclient.http")
    http.MediaFileUpload = lambda *args, **kwargs: object()
    monkeypatch.setitem(sys.modules, "googleapiclient", package)
    monkeypatch.setitem(sys.modules, "googleapiclient.http", http)
    youtube = Mock()
    youtube.videos.return_value.insert.return_value.next_chunk.return_value = (None, {"id": "jNQXAC9IVRw"})
    monkeypatch.setattr("youtube_edit_ultra.youtube.client", lambda: youtube)
    return youtube


def rendered_with_report(tmp_path, *, licensed):
    folder = tmp_path / "render"
    folder.mkdir()
    video = folder / "montage.mp4"
    video.write_bytes(b"simulated montage")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"owned source")
    state_dir = tmp_path / "state"
    state = catalogue.empty()
    if licensed:
        state["permissions"][catalogue.sha256(source)] = catalogue.permission_grant(dict(license=CC_BY, source_url=RELEASE))
    catalogue.save(state_dir / "catalogue.json", state)
    from youtube_edit_ultra.source_rights import bind_montage, gate, write_report
    write_report(folder, gate([(str(source), "video", None)], state=state))
    bind_montage(folder, catalogue.sha256(video))
    return video, source, state_dir


def check(receipt, state_dir, *extra):
    return ultra(["--no-banner", "publication-check", str(receipt), "--state-dir", str(state_dir), *extra])


def test_public_request_stays_private_and_records_rights_gate(tmp_path, monkeypatch):
    youtube = fake_youtube(monkeypatch)
    video, _, _ = rendered_with_report(tmp_path, licensed=False)
    assert ultra(["--no-banner", "publish", str(video), "--privacy", "public"]) == 0
    body = youtube.videos.return_value.insert.call_args.kwargs["body"]
    assert body["status"]["privacyStatus"] == "private"
    receipt = json.loads(video.with_suffix(".youtube.json").read_text())
    assert receipt["uploaded_privacy"] == "private" and receipt["rights_gate"]["publishable"] is False
    assert not youtube.videos.return_value.update.called  # visibility is never changed by code


def test_upload_without_rights_report_is_recorded_as_missing(tmp_path, monkeypatch):
    fake_youtube(monkeypatch)
    video = tmp_path / "loose.mp4"
    video.write_bytes(b"simulated")
    assert ultra(["--no-banner", "publish", str(video)]) == 0
    receipt = json.loads(video.with_suffix(".youtube.json").read_text())
    assert receipt["rights_gate"] == "MISSING"
    assert check(video.with_suffix(".youtube.json"), tmp_path, "--studio", "no-claims", "--approve") == 2
    reasons = json.loads(video.with_suffix(".youtube.json").read_text())["publication_check"]["reasons"]
    assert reasons == ["RIGHTS_GATE_REPORT_MISSING"]


@pytest.mark.parametrize("licensed,studio,approve,expected", [
    (True, "pending", True, "STUDIO_CHECK_PENDING"),
    (True, "blocked", True, "STUDIO_CHECK_BLOCKED"),
    (True, "restricted", True, "STUDIO_CHECK_RESTRICTED"),
    (True, "no-claims", False, "EXPLICIT_PUBLICATION_APPROVAL_MISSING"),
    (False, "no-claims", True, "SOURCE_UNVERIFIED"),
])
def test_premature_publication_is_refused(tmp_path, monkeypatch, licensed, studio, approve, expected):
    fake_youtube(monkeypatch)
    video, _, state_dir = rendered_with_report(tmp_path, licensed=licensed)
    assert ultra(["--no-banner", "publish", str(video)]) == 0
    receipt = video.with_suffix(".youtube.json")
    assert check(receipt, state_dir, "--studio", studio, *(["--approve"] if approve else [])) == 2
    result = json.loads(receipt.read_text())["publication_check"]
    assert result["decision"] == "KEEP_PRIVATE"
    assert any(r.startswith(expected) for r in result["reasons"]), result["reasons"]


def test_publication_is_allowed_only_after_every_check(tmp_path, monkeypatch):
    youtube = fake_youtube(monkeypatch)
    video, source, state_dir = rendered_with_report(tmp_path, licensed=True)
    assert ultra(["--no-banner", "publish", str(video)]) == 0
    receipt = video.with_suffix(".youtube.json")
    assert check(receipt, state_dir, "--studio", "claims-no-impact", "--approve") == 0
    result = json.loads(receipt.read_text())["publication_check"]
    assert result["decision"] == "READY_FOR_MANUAL_PUBLICATION"
    assert result["studio_result_source"] == "user_declaration"
    assert not youtube.videos.return_value.update.called
    # A source removed after the upload makes the next check fail again.
    source.unlink()
    assert check(receipt, state_dir, "--studio", "no-claims", "--approve") == 2
    assert "SOURCE_FILE_MISSING" in json.loads(receipt.read_text())["publication_check"]["reasons"][0]


def test_rewritten_report_or_replaced_source_keeps_the_upload_private(tmp_path, monkeypatch):
    fake_youtube(monkeypatch)
    video, source, state_dir = rendered_with_report(tmp_path, licensed=True)
    assert ultra(["--no-banner", "publish", str(video)]) == 0
    receipt = video.with_suffix(".youtube.json")
    # Same path, other bytes, also granted: accepted today, but not what was edited.
    state = catalogue.read(state_dir / "catalogue.json")
    source.write_bytes(b"replacement bytes")
    state["permissions"][catalogue.sha256(source)] = catalogue.permission_grant(dict(license=CC_BY, source_url=RELEASE))
    catalogue.save(state_dir / "catalogue.json", state)
    assert check(receipt, state_dir, "--studio", "no-claims", "--approve") == 2
    assert any(r.startswith("SOURCE_CHANGED_SINCE_RENDER")
               for r in json.loads(receipt.read_text())["publication_check"]["reasons"])
    # A report regenerated after the upload is not the one that went with it.
    from youtube_edit_ultra.source_rights import bind_montage, gate, write_report
    write_report(video.parent, gate([(str(source), "video", None)], state=state))
    bind_montage(video.parent, catalogue.sha256(video))
    assert check(receipt, state_dir, "--studio", "no-claims", "--approve") == 2
    assert "RIGHTS_GATE_REPORT_NOT_THE_ONE_UPLOADED" in json.loads(receipt.read_text())["publication_check"]["reasons"]


@needs_ffmpeg
def test_publishable_flag_never_reuses_a_personal_job(tmp_path):
    args, state_dir, video, music = fixture_job(tmp_path)
    assert jobs.main(args) == 0
    licence_fixture_grants(state_dir, (video, music))
    jobs.main([*args, "--publishable"])  # music already used by the first job then blocks: expected
    assert len(list((tmp_path / "jobs").glob("*/manifest.json"))) == 2


def test_report_of_another_montage_in_the_same_folder_is_not_trusted(tmp_path, monkeypatch):
    fake_youtube(monkeypatch)
    video, _, state_dir = rendered_with_report(tmp_path, licensed=True)
    other = video.with_name("other.mp4")
    other.write_bytes(b"another montage")
    assert ultra(["--no-banner", "publish", str(other)]) == 0
    receipt = other.with_suffix(".youtube.json")
    assert json.loads(receipt.read_text())["rights_gate"] == "NOT_BOUND_TO_THIS_MONTAGE"
    assert check(receipt, state_dir, "--studio", "no-claims", "--approve") == 2
    assert "RIGHTS_GATE_NOT_PUBLISHABLE_AT_UPLOAD" in json.loads(receipt.read_text())["publication_check"]["reasons"]


@needs_ffmpeg
def test_render_subprocess_rechecks_publishable_from_the_manifest(tmp_path):
    _, _, video, music = fixture_job(tmp_path)
    spec = tmp_path / "project.json"
    catalogue.save(spec, dict(athlete="Canelo Alvarez", duration="6s", sources=[str(video)],
                              sources_only=True, music=[str(music)], publishable=True,
                              rights_state_dir=str(tmp_path / "state"), output_dir=str(tmp_path / "out")))
    assert ultra(["--no-banner", "create", "--spec", str(spec), "--dry-run"]) == 2
    [report] = (tmp_path / "out").glob("*/rights_gate_report.json")
    assert catalogue.read(report)["summary"]["counts"]["UNVERIFIED"] == 2


@pytest.mark.parametrize("content", ["[1, 2]", '{"records": {"a": 1}}', '{"records": [{"kind": "video"}]}', "{bad"])
def test_malformed_report_keeps_private_without_crashing(tmp_path, monkeypatch, content):
    fake_youtube(monkeypatch)
    video, _, state_dir = rendered_with_report(tmp_path, licensed=True)
    assert ultra(["--no-banner", "publish", str(video)]) == 0
    receipt = video.with_suffix(".youtube.json")
    (video.parent / "rights_gate_report.json").write_text(content)
    assert check(receipt, state_dir, "--studio", "no-claims", "--approve") == 2
    reasons = json.loads(receipt.read_text())["publication_check"]["reasons"]
    assert "RIGHTS_GATE_REPORT_NOT_THE_ONE_UPLOADED" in reasons


@needs_ffmpeg
def test_unreadable_catalogue_does_not_abort_a_personal_edit(tmp_path):
    _, _, video, music = fixture_job(tmp_path)
    state = tmp_path / "broken-state"
    state.mkdir()
    (state / "catalogue.json").write_text("{not json")
    spec = tmp_path / "project.json"
    catalogue.save(spec, dict(athlete="Canelo Alvarez", duration="6s", sources=[str(video)], sources_only=True,
                              music=[str(music)], rights_state_dir=str(state), output_dir=str(tmp_path / "out")))
    assert ultra(["--no-banner", "create", "--spec", str(spec), "--dry-run"]) == 0
    [report] = (tmp_path / "out").glob("*/rights_gate_report.json")
    assert not catalogue.read(report)["summary"]["publishable"]


# Tests added after mutation testing showed these produce/cli paths could break silently.

@needs_ffmpeg
def test_publishable_job_drops_unlicensed_music_even_when_video_is_accepted(tmp_path):
    args, state_dir, video, _ = fixture_job(tmp_path)
    licence_fixture_grants(state_dir, (video,))  # music keeps its text-only grant: UNVERIFIED
    assert jobs.main([*args, "--publishable"]) == 2
    job, _ = job_state(tmp_path)
    assert any("Aucune musique autorisée" in b for b in catalogue.read(job / "production_report.json")["blockers"])
    assert not list(job.rglob("*.mp4"))


@needs_ffmpeg
def test_publishable_job_passes_its_flags_to_the_render_subprocess(tmp_path):
    args, state_dir, video, music = fixture_job(tmp_path)
    licence_fixture_grants(state_dir, (video, music))
    assert jobs.main([*args, "--publishable"]) == 0
    job, _ = job_state(tmp_path)
    project = catalogue.read(job / "project.json")
    assert project["publishable"] is True and project["offline"] is True
    assert project["rights_state_dir"] == str(state_dir.resolve())


@needs_ffmpeg
def test_commercial_mode_refuses_a_claimed_source_despite_commercial_grants(tmp_path, monkeypatch):
    args, state_dir, source, music = fixture_job(tmp_path)
    state = catalogue.read(state_dir / "catalogue.json")
    state["permissions"] = {catalogue.sha256(p): catalogue.permission_grant(
        "Owned test fixture", mode="commercial", license="Owned fixture licence",
        rights_verified_by="Fixture author", rights_verified_at="2026-11-17T00:00:00Z", commercial_use=True,
        adaptation=True, distribution=True, monetization=True, attribution_required=False)
        for p in (source, music)}
    catalogue.save(state_dir / "catalogue.json", state)
    registry = tmp_path / "excluded_media.json"
    registry.write_text(json.dumps([dict(sha256=catalogue.sha256(source), reason="claimed in Studio")]))
    monkeypatch.setenv("YEU_CLAIMS_FILE", str(registry))
    assert jobs.main(args + ["--mode", "commercial"]) == 2
    job, _ = job_state(tmp_path)
    gate = catalogue.read(job / "rights_gate_report.json")
    assert [r["decision"] for r in gate["records"] if r["kind"] == "video"] == ["REJECTED"]


@needs_ffmpeg
def test_prepared_soundtrack_is_gated_like_any_music(tmp_path):
    _, _, video, music = fixture_job(tmp_path)
    spec = tmp_path / "project.json"
    catalogue.save(spec, dict(athlete="Canelo Alvarez", duration="6s", sources=[str(video)], sources_only=True,
                              prepared_soundtrack=str(music), rights_state_dir=str(tmp_path / "state"),
                              output_dir=str(tmp_path / "out")))
    assert ultra(["--no-banner", "create", "--spec", str(spec), "--dry-run"]) == 0
    [report] = (tmp_path / "out").glob("*/rights_gate_report.json")
    prepared = [r for r in catalogue.read(report)["records"] if r["provenance"] == "prepared_soundtrack"]
    assert len(prepared) == 1 and prepared[0]["path"] == str(music.resolve())


def test_receipt_without_upload_id_is_never_ready(tmp_path):
    receipt = tmp_path / "montage.youtube.json"
    receipt.write_text(json.dumps({"uploaded_privacy": "private"}))
    assert check(receipt, tmp_path, "--studio", "no-claims", "--approve") == 2
    assert "NO_UPLOAD_RECEIPT" in json.loads(receipt.read_text())["publication_check"]["reasons"]


def test_empty_report_is_named_as_such(tmp_path, monkeypatch):
    fake_youtube(monkeypatch)
    folder = tmp_path / "render"
    folder.mkdir()
    video = folder / "montage.mp4"
    video.write_bytes(b"montage without recorded sources")
    from youtube_edit_ultra.source_rights import bind_montage, write_report
    write_report(folder, [])
    bind_montage(folder, catalogue.sha256(video))
    assert ultra(["--no-banner", "publish", str(video)]) == 0
    receipt = video.with_suffix(".youtube.json")
    assert check(receipt, tmp_path, "--studio", "no-claims", "--approve") == 2
    assert "RIGHTS_GATE_REPORT_EMPTY" in json.loads(receipt.read_text())["publication_check"]["reasons"]


@needs_ffmpeg
def test_offline_job_records_youtube_sources_as_not_rechecked(tmp_path):
    args, _, video, _ = fixture_job(tmp_path)
    video.with_suffix(".info.json").write_text(json.dumps(
        dict(webpage_url="https://www.youtube.com/watch?v=jNQXAC9IVRw", id="jNQXAC9IVRw", extractor_key="Youtube")))
    assert jobs.main(args) == 0
    job, _ = job_state(tmp_path)
    [record] = [r for r in catalogue.read(job / "rights_gate_report.json")["records"] if r["kind"] == "video"]
    assert record["youtube_id"] == "jNQXAC9IVRw" and "OFFLINE_NOT_RECHECKED" in record["reasons"]


@needs_ffmpeg
def test_produce_saves_new_grants_before_the_render_subprocess_rechecks_them(tmp_path, monkeypatch):
    from youtube_edit_ultra import music_sources, produce
    _, state_dir, video, music = fixture_job(tmp_path, authorized=False)
    licence_fixture_grants(state_dir, (music,))
    state = catalogue.read(state_dir / "catalogue.json")
    url = "https://example.org/canelo-clip.mp4"
    state["authorized_urls"] = [dict(url=url, kind="video", athlete="Canelo Alvarez", reuse=True,
                                     evidence=dict(license=CC_BY, source_url=RELEASE))]
    catalogue.save(state_dir / "catalogue.json", state)
    monkeypatch.setattr(music_sources, "acquire", lambda *a, **k: dict(candidates=[], attempts=[], errors=[]))

    def download(urls, dest):  # simulated acquisition: the grant exists only in memory afterwards
        dest.mkdir(parents=True, exist_ok=True)
        copy = dest / "canelo-clip.mp4"
        shutil.copy(video, copy)
        shutil.copy(video.with_suffix(".actions.json"), copy.with_suffix(".actions.json"))
        return [copy]
    monkeypatch.setattr(produce, "download_explicit", download)
    code = ultra(["--no-banner", "produce", "Canelo Álvarez — Reggaeton — 6s", "--publishable",
                  "--state-dir", str(state_dir), "--root", str(video.parent),
                  "--output-dir", str(tmp_path / "out"), "--width", "320", "--height", "180"])
    [report] = (tmp_path / "out").glob("*/production_report.json")
    assert code == 0, catalogue.read(report)["blockers"]
