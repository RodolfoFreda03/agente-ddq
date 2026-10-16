"""Tests for demo.py's pure logic. The data-dir redirect mutates module
globals in store.py/memory.py/llm.py -- every test restores them
afterwards (autouse fixture) so this file never leaks state into other
test modules that run in the same pytest process."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import demo
import llm
import memory
import store


@pytest.fixture(autouse=True)
def _restore_module_globals():
    originals = {
        "store.DDQS_DIR": store.DDQS_DIR,
        "memory.VAULT_DIR": memory.VAULT_DIR,
        "memory.MEMORANDUM_DIR": memory.MEMORANDUM_DIR,
        "memory.APPROVED_ANSWERS_DIR": memory.APPROVED_ANSWERS_DIR,
        "memory.LOG_DIR": memory.LOG_DIR,
        "llm.CACHE_DIR": llm.CACHE_DIR,
    }
    yield
    store.DDQS_DIR = originals["store.DDQS_DIR"]
    memory.VAULT_DIR = originals["memory.VAULT_DIR"]
    memory.MEMORANDUM_DIR = originals["memory.MEMORANDUM_DIR"]
    memory.APPROVED_ANSWERS_DIR = originals["memory.APPROVED_ANSWERS_DIR"]
    memory.LOG_DIR = originals["memory.LOG_DIR"]
    llm.CACHE_DIR = originals["llm.CACHE_DIR"]


# ── is_enabled ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("value,expected", [
    ("true", True), ("True", True), ("1", True), ("yes", True), ("YES", True),
    ("false", False), ("0", False), ("", False),
])
def test_is_enabled_reads_the_env_var(monkeypatch, value, expected):
    monkeypatch.setenv("DEMO_PUBLIC", value)
    assert demo.is_enabled() is expected


def test_is_enabled_defaults_to_false_when_unset(monkeypatch):
    monkeypatch.delenv("DEMO_PUBLIC", raising=False)
    assert demo.is_enabled() is False


# ── _redirect_data_dirs ──────────────────────────────────────────────────


def test_redirect_points_store_and_memory_and_llm_at_the_base(tmp_path):
    demo._redirect_data_dirs(tmp_path)
    assert store.DDQS_DIR == tmp_path / "ddqs"
    assert memory.VAULT_DIR == tmp_path / "obsidian-vault"
    assert memory.MEMORANDUM_DIR == tmp_path / "obsidian-vault" / "memorandum"
    assert memory.APPROVED_ANSWERS_DIR == tmp_path / "obsidian-vault" / "approved-answers"
    assert memory.LOG_DIR == tmp_path / "obsidian-vault" / "log"
    assert llm.CACHE_DIR == tmp_path / "llm_cache"


def test_redirect_is_actually_honoured_by_store_calls(tmp_path):
    """Not just that the variable changes -- that store.py's functions
    genuinely read the current value at call time (the same gotcha fixed
    in memory.py's load_vault_notes default argument)."""
    demo._redirect_data_dirs(tmp_path)
    assert store.ddq_dir("some-id") == tmp_path / "ddqs" / "some-id"


def test_redirect_is_actually_honoured_by_memory_calls(tmp_path):
    (tmp_path / "obsidian-vault").mkdir(parents=True)
    demo._redirect_data_dirs(tmp_path)
    # load_vault_notes() with no vault_dir argument must use the redirected
    # VAULT_DIR, not the one captured at module-import time.
    assert memory.load_vault_notes() == []  # empty dir, but must not raise/use the wrong path


# ── setup / reset ────────────────────────────────────────────────────────


def test_setup_forces_mail_mode_to_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("MAIL_MODE", "gmail")
    monkeypatch.setattr(demo, "_TEMP_ROOT", tmp_path / "working_copy")
    monkeypatch.setattr(demo, "DEMO_DATA_DIR", tmp_path / "demo_data_src")
    (tmp_path / "demo_data_src").mkdir()
    demo.setup()
    assert __import__("os").environ["MAIL_MODE"] == "folder"


def test_reset_copies_demo_data_into_a_fresh_working_copy(monkeypatch, tmp_path):
    src = tmp_path / "demo_data_src"
    (src / "ddqs" / "demo-x").mkdir(parents=True)
    (src / "ddqs" / "demo-x" / "state.json").write_text("{}", encoding="utf-8")
    working_copy = tmp_path / "working_copy"

    monkeypatch.setattr(demo, "DEMO_DATA_DIR", src)
    monkeypatch.setattr(demo, "_TEMP_ROOT", working_copy)

    demo.reset()

    assert (working_copy / "ddqs" / "demo-x" / "state.json").exists()
    assert store.DDQS_DIR == working_copy / "ddqs"


def test_reset_discards_edits_made_since_the_last_reset(monkeypatch, tmp_path):
    src = tmp_path / "demo_data_src"
    (src / "ddqs" / "demo-x").mkdir(parents=True)
    (src / "ddqs" / "demo-x" / "state.json").write_text('{"original": true}', encoding="utf-8")
    working_copy = tmp_path / "working_copy"

    monkeypatch.setattr(demo, "DEMO_DATA_DIR", src)
    monkeypatch.setattr(demo, "_TEMP_ROOT", working_copy)

    demo.reset()
    edited_file = working_copy / "ddqs" / "demo-x" / "state.json"
    edited_file.write_text('{"original": false, "edited": true}', encoding="utf-8")

    demo.reset()  # simulates clicking "Reset demo" again

    assert edited_file.read_text(encoding="utf-8") == '{"original": true}'
