"""Permanent error references agree across Python, UI messages and boundary logs."""

import ast
import json
import logging
import re
from pathlib import Path

import pytest

from bmpt.error_codes import code_of, level_of
from bmpt.errors import ServiceError
from bmpt.webui.events import EventBus
from bmpt.webui.jobs import JobRunner
from bmpt.webui.rpc import RpcRegistry

PACKAGE = Path(__file__).parents[1] / "src" / "bmpt"
REGISTRY = json.loads((PACKAGE / "web" / "error-codes.json").read_text(encoding="utf-8"))
LANGUAGES = ("en-US", "zh-CN", "zh-TW", "ja-JP", "ko-KR", "th-TH")
INTERNAL_CODES = {
    "capture_not_ready",  # photography/shoot.js translates exhausted retries to photo_capture_unavailable.
}
# These assignments are permanent. Append a new name within its block; never renumber an old one.
BLOCKS = (
    (
        'bad_params busy classic_failed classic_open data_dir_readonly dev_path_outside failed folder_changed '
        'internal job_not_found lines_unavailable native_only native_unavailable not_enough_space path_not_found '
        'profiles_unreadable reveal_failed safe_restart_unavailable scan_off state_changed unknown_method '
    ).split(),
    (
        'catalog_data_invalid catalog_family_not_found catalog_variant_not_found game_directory_missing '
        'game_file_locked game_file_not_found game_file_outside_resources game_not_found game_running '
        'game_status_unavailable original_invalid original_unavailable steam_busy variant_incompatible '
        'variant_not_found untested_build '
    ).split(),
    (
        'bundle_invalid conflict conversion_failed conversion_names game_file_conflict mod_parts_missing '
        'mod_platform_mismatch mod_platform_unknown mod_release_mismatch mod_target_ambiguous mod_target_mismatch '
        'mod_target_unknown mod_unity_version_mismatch mod_unsupported no_matching_assets no_target '
        'platform_unsupported required_asset_apply_failed required_asset_extract_failed '
        'required_asset_stream_missing required_assets_unmatched source_conflict source_unknown '
        'source_unsupported spine_version target_unknown unreadable unsupported '
    ).split(),
    (
        'asset_delete_confirm asset_official crc_fix_failed entry_changed export_empty export_target_unavailable '
        'library_io_error library_operation_failed library_recovery_pending library_state_unreadable '
        'library_target_not_empty mod_apply_failed mod_in_use mod_in_use_elsewhere mod_last_copy mod_not_found '
        'mod_update_failed '
    ).split(),
    (
        'asset_extraction_failed asset_load_failed atlas_remap_asset_ambiguous atlas_remap_atlas_missing '
        'atlas_remap_bounds atlas_remap_invalid atlas_remap_page_ambiguous atlas_remap_page_missing '
        'atlas_remap_property atlas_remap_region_ambiguous atlas_remap_regions_missing atlas_remap_source_missing '
        'atlas_remap_structure atlas_remap_target_mismatch atlas_remap_redrawn '
    ).split(),
    (
        'alias_too_long alias_too_many hall_conflict hall_duplicate_memorial hall_duplicate_portrait '
        'hall_last_memorial hall_last_portrait photo_apply_failed photo_capture_unavailable '
        'photo_delete_other_variant photo_in_use photo_invalid photo_library_invalid photo_missing '
        'photo_target_unavailable lobby_camera_failed lobby_camera_unsupported '
    ).split(),
    (
        'update_check_failed update_checksum_mismatch update_download_failed update_restart_failed '
        'update_unavailable update_unsupported '
    ).split(),
)


def test_registry_has_permanent_unique_references_and_domain_blocks():
    assert list(REGISTRY) == sorted(REGISTRY)
    expected = {name: f"E{block}{number:03}" for block, names in enumerate(BLOCKS, 1)
                for number, name in enumerate(names, 1)}
    assert {name: item["code"] for name, item in REGISTRY.items()} == expected
    assert len({item["code"] for item in REGISTRY.values()}) == len(REGISTRY)
    for name, item in REGISTRY.items():
        assert set(item) == {"code", "level"}
        assert re.fullmatch(r"E[1-7]\d{3}", item["code"]), name
        assert item["level"] in {"failed", "notice"}, name
        assert code_of(name) == item["code"]
        assert level_of(name) == item["level"]


@pytest.mark.parametrize("language", LANGUAGES)
def test_registry_names_have_a_message_in_every_locale(language):
    ui = json.loads((PACKAGE / "locales" / f"{language}.json").read_text(encoding="utf-8"))["webui"]
    names = set(ui["error"]) | set(ui["import"]["reason"])
    assert set(REGISTRY) <= names
    if language == "en-US":
        assert names == set(REGISTRY)


def test_every_literal_service_error_and_import_reason_is_registered():
    missing = []
    for root in (PACKAGE, PACKAGE.with_name("ba_modding_toolkit")):
        for path in root.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(node, ast.Call):
                    continue
                name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
                values = []
                if name == "ServiceError" and node.args:
                    values.append(node.args[0])
                values.extend(keyword.value for keyword in node.keywords if keyword.arg == "reason")
                for value in values:
                    if isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value not in REGISTRY:
                        missing.append(f"{path.relative_to(root)}:{node.lineno} {value.value}")
    assert missing == []


def test_every_frontend_error_literal_is_registered_or_an_allowlisted_internal_signal():
    missing, internal = [], set()
    for path in (PACKAGE / "web" / "js").rglob("*.js"):
        for match in re.finditer(r"\bcode\s*:\s*[\"']([a-z][a-z0-9_]*)[\"']", path.read_text(encoding="utf-8")):
            name = match[1]
            if name in INTERNAL_CODES:
                internal.add(name)
            elif name not in REGISTRY:
                missing.append(f"{path.name}: {name}")
    assert missing == []
    assert internal == INTERNAL_CODES, "remove stale internal-signal allowlist entries"
    assert INTERNAL_CODES.isdisjoint(REGISTRY)


def test_service_error_json_adds_ref_and_keeps_name_params_and_existing_ids():
    assert ServiceError("mod_parts_missing", id="row-1", files=[Path("part.bundle")]).to_json() == {
        "code": "mod_parts_missing", "ref": "E3006", "params": {"id": "row-1", "files": ["part.bundle"]}}
    assert ServiceError("not_registered", count=2).to_json() == {"code": "not_registered", "params": {"count": 2}}
    assert code_of("not_registered") is None and level_of("not_registered") is None
    assert code_of(None) is None and level_of({}) is None


@pytest.mark.parametrize("name,ref,level", [
    ("mod_parts_missing", "E3006", logging.WARNING), ("busy", "E1002", logging.INFO),
    ("not_registered", None, logging.WARNING),
])
@pytest.mark.parametrize("translated", [False, True])
def test_rpc_logs_service_verdict_once_with_reference_and_compact_params(caplog, name, ref, level, translated):
    error = ServiceError(name, files=[Path("part.bundle")], count=2)
    rpc = RpcRegistry(translate=(lambda exc: error) if translated else None)

    @rpc.method("fail")
    def fail():
        raise ValueError("translated") if translated else error

    with caplog.at_level(logging.INFO, logger="bmpt.webui.rpc"):
        assert rpc.call("fail", {}) == {"error": error.to_json()}
    records = [record for record in caplog.records if record.name == "bmpt.webui.rpc"]
    expected = f'{ref} {name} {{"files":["part.bundle"],"count":2}}' if ref else name
    assert [(record.levelno, record.getMessage()) for record in records] == [(level, expected)]


def test_rpc_generated_errors_also_carry_and_log_their_references(caplog):
    rpc = RpcRegistry()

    @rpc.method("needs_value")
    def needs_value(value):
        raise ValueError("broken")

    with caplog.at_level(logging.INFO):
        cases = [("missing", {}, "unknown_method"), ("needs_value", [1], "bad_params"),
                 ("needs_value", {}, "bad_params"), ("needs_value", {"value": 1}, "internal")]
        for method, params, name in cases:
            error = rpc.call(method, params)["error"]
            assert error["code"] == name and error["ref"] == code_of(name)
    coded = [record.getMessage().split(" ", 2)[:2] for record in caplog.records
             if re.match(r"E[1-7]\d{3} ", record.getMessage())]
    assert coded == [[code_of(name), name] for _, _, name in cases]


@pytest.mark.parametrize("name,ref,level", [
    ("mod_parts_missing", "E3006", logging.WARNING), ("busy", "E1002", logging.INFO),
    ("not_registered", None, logging.WARNING),
])
@pytest.mark.parametrize("translated", [False, True])
def test_job_logs_service_verdict_once_without_changing_job_id(caplog, name, ref, level, translated):
    error = ServiceError(name, count=2)
    events = EventBus()
    seen = events.subscribe()
    jobs = JobRunner(events)
    # Configure translation before submitting: the worker must never race this setup.
    jobs.translate = (lambda exc: error) if translated else None

    def fail(log, progress):
        raise ValueError("translated") if translated else error

    try:
        with caplog.at_level(logging.INFO, logger="bmpt.webui.jobs"):
            jid = jobs.submit("import", fail)
            assert jobs.wait(jid, 5)
        assert jobs.get(jid).error == error.to_json()
        assert jobs.get(jid).to_json()["id"] == jid
        records = [record for record in caplog.records if record.name == "bmpt.webui.jobs"]
        expected = f'{ref} {name} {{"count":2}}' if ref else name
        assert [(record.levelno, record.getMessage()) for record in records] == [(level, expected)]
        failures = []
        while not seen.empty():
            event, payload = seen.get_nowait()
            if event == "job.failed":
                failures.append(payload)
        assert failures == [{"id": jid, "kind": "import", **error.to_json()}]
    finally:
        jobs.shutdown()


def test_unexpected_job_failure_carries_and_logs_the_internal_reference(caplog):
    jobs = JobRunner(EventBus())

    def fail(log, progress):
        raise RuntimeError("broken")

    try:
        with caplog.at_level(logging.INFO, logger="bmpt.webui.jobs"):
            jid = jobs.submit("import", fail)
            assert jobs.wait(jid, 5)
        assert jobs.get(jid).error == {"code": "internal", "ref": "E1009",
                                       "params": {"error": "RuntimeError: broken"}}
        coded = [(record.levelno, record.getMessage()) for record in caplog.records
                 if record.getMessage().startswith("E1009 internal ")]
        assert coded == [(logging.WARNING, 'E1009 internal {"error":"RuntimeError: broken"}')]
    finally:
        jobs.shutdown()
