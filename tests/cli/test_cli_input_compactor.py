from pathlib import Path

from cli import (
    input_compaction_notice,
    input_compaction_result_is_stale,
    input_compaction_source,
    refresh_input_compaction_offset,
    resolve_input_compactor_path,
    splice_input_compaction_result,
)


def test_input_compaction_source_uses_uncompacted_tail_by_default():
    source = input_compaction_source("already compacted. new ramble", compacted_up_to=19)

    assert source is not None
    assert source.start == 19
    assert source.text == "new ramble"


def test_input_compaction_source_can_use_whole_buffer():
    source = input_compaction_source("already compacted. new ramble", compacted_up_to=19, whole_buffer=True)

    assert source is not None
    assert source.start == 0
    assert source.text == "already compacted. new ramble"


def test_input_compaction_source_skips_empty_slash_and_done_tail():
    assert input_compaction_source("   ") is None
    assert input_compaction_source("/model gpt-5") is None
    assert input_compaction_notice("  /model gpt-5") == "Input compactor skipped slash command."
    assert input_compaction_source("done", compacted_up_to=4) is None


def test_splice_input_compaction_result_marks_new_prefix():
    result = splice_input_compaction_result("first part raw second part", start=11, compacted="tight second")

    assert result.text == "first part tight second"
    assert result.cursor_position == len(result.text)
    assert result.compacted_up_to == len(result.text)


def test_refresh_input_compaction_offset_preserves_append_and_resets_edit():
    prefix = "first compacted"

    assert refresh_input_compaction_offset(
        "first compacted and more",
        compacted_prefix=prefix,
        compacted_up_to=len(prefix),
    ) == (len(prefix), prefix)

    assert refresh_input_compaction_offset(
        "edited compacted and more",
        compacted_prefix=prefix,
        compacted_up_to=len(prefix),
    ) == (0, "")


def test_input_compaction_stale_detects_edit_and_revert():
    original = "dictated draft"

    assert not input_compaction_result_is_stale(original, original, start_version=3, current_version=3)
    assert input_compaction_result_is_stale(original, original, start_version=3, current_version=5)
    assert input_compaction_result_is_stale(
        original, "dictated draft plus edit", start_version=3, current_version=4
    )


def test_resolve_input_compactor_path_prefers_env(monkeypatch):
    monkeypatch.setenv("HERMES_INPUT_COMPACTOR", "~/bin/env-compactor")

    assert resolve_input_compactor_path({"display": {"input_compactor_path": "~/bin/config-compactor"}}) == Path(
        "~/bin/env-compactor"
    ).expanduser()


def test_resolve_input_compactor_path_uses_display_then_root(monkeypatch):
    monkeypatch.delenv("HERMES_INPUT_COMPACTOR", raising=False)

    assert resolve_input_compactor_path({"display": {"input_compactor_path": "~/bin/display-compactor"}}) == Path(
        "~/bin/display-compactor"
    ).expanduser()
    assert resolve_input_compactor_path(
        {
            "input_compactor_path": "~/bin/root-compactor",
            "display": {"input_compactor_path": "~/bin/display-compactor"},
        }
    ) == Path("~/bin/display-compactor").expanduser()
    assert resolve_input_compactor_path({"input_compactor_path": "~/bin/root-compactor"}) == Path(
        "~/bin/root-compactor"
    ).expanduser()


def test_resolve_input_compactor_path_returns_none_without_config(monkeypatch):
    monkeypatch.delenv("HERMES_INPUT_COMPACTOR", raising=False)

    assert resolve_input_compactor_path({}) is None
    assert resolve_input_compactor_path({"display": {}}) is None
