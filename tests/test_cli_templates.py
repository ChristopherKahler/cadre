"""Tests for ``cadre templates`` — family listing and workspace installs."""

from __future__ import annotations

from pathlib import Path

from firm.cli.templates import list_families, run_templates_install, run_templates_list


def _make_workspace(tmp_path: Path) -> Path:
    (tmp_path / ".firm").mkdir()
    return tmp_path


def test_discipline_family_ships():
    families = list_families()
    assert "discipline" in families
    names = families["discipline"]
    assert "15-execution-discipline.md" in names
    assert "lead-unit-authoring.json" in names
    assert "dev-discipline.json" in names
    assert "README.md" in names
    assert "SETUP.md" in names


def test_list_prints_families(capsys):
    assert run_templates_list() == 0
    out = capsys.readouterr().out
    assert "discipline" in out
    assert "15-execution-discipline.md" in out


def test_install_routes_protocols_and_stages_packs(tmp_path, capsys):
    ws = _make_workspace(tmp_path)
    assert run_templates_install("discipline", ws) == 0

    proto = ws / ".firm" / "protocols" / "15-execution-discipline.md"
    assert proto.is_file()
    assert "Evidence before claims" in proto.read_text()

    stage = ws / ".firm" / "templates" / "discipline"
    assert (stage / "lead-unit-authoring.json").is_file()
    assert (stage / "dev-discipline.json").is_file()
    assert (stage / "SETUP.md").is_file()
    # docs are staged, never installed as protocols
    assert not (ws / ".firm" / "protocols" / "README.md").exists()

    out = capsys.readouterr().out
    assert "installed" in out
    assert "SETUP.md" in out


def test_install_skips_existing_without_force(tmp_path, capsys):
    ws = _make_workspace(tmp_path)
    assert run_templates_install("discipline", ws) == 0
    proto = ws / ".firm" / "protocols" / "15-execution-discipline.md"
    proto.write_text("firm-customized law — do not clobber")

    assert run_templates_install("discipline", ws) == 0
    assert proto.read_text() == "firm-customized law — do not clobber"
    assert "skipped" in capsys.readouterr().out

    assert run_templates_install("discipline", ws, force=True) == 0
    assert "Evidence before claims" in proto.read_text()


def test_install_requires_firm_workspace(tmp_path, capsys):
    assert run_templates_install("discipline", tmp_path) == 1
    assert "not a firm workspace" in capsys.readouterr().out


def test_install_unknown_family(tmp_path, capsys):
    ws = _make_workspace(tmp_path)
    assert run_templates_install("nope", ws) == 1
    assert "Unknown template family" in capsys.readouterr().out


def test_main_wires_templates_subcommand(tmp_path, capsys):
    from firm.__main__ import main

    ws = _make_workspace(tmp_path)
    assert main(["templates", "list"]) == 0
    assert main(["templates", "install", "discipline", "--workspace", str(ws)]) == 0
    assert (ws / ".firm" / "protocols" / "15-execution-discipline.md").is_file()
