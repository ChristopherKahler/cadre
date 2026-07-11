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


# ---------------------------------------------------------------------------
# init installs the discipline family automatically
# ---------------------------------------------------------------------------


def test_init_installs_discipline_family(tmp_path):
    from firm.cli.init import run_init

    assert run_init(tmp_path) == 0
    proto = tmp_path / ".firm" / "protocols" / "15-execution-discipline.md"
    assert proto.is_file()
    assert "Evidence before claims" in proto.read_text()
    assert (tmp_path / ".firm" / "templates" / "discipline" / "SETUP.md").is_file()


def test_init_rerun_preserves_customized_protocol(tmp_path):
    from firm.cli.init import run_init

    assert run_init(tmp_path) == 0
    proto = tmp_path / ".firm" / "protocols" / "15-execution-discipline.md"
    proto.write_text("customized")
    assert run_init(tmp_path, force=True) == 0
    assert proto.read_text() == "customized"


# ---------------------------------------------------------------------------
# templates apply — merge packs into contracts
# ---------------------------------------------------------------------------


def _make_firm_with_contracts(tmp_path):
    import json

    from firm.core.db import connect, get_db_path
    from firm.core.migrate import apply_migrations
    from firm.core.repo import create

    (tmp_path / ".firm").mkdir(exist_ok=True)
    conn = connect(get_db_path(tmp_path))
    apply_migrations(conn)
    create(conn, "firm", {"id": "acme", "name": "Acme"})
    for cid in ("CON-ENG", "CON-LEAD"):
        create(conn, "contract", {
            "id": cid, "firm_id": "acme", "name": cid,
            "runtime_type": "claude_code",
            "skill_loadout": json.dumps({"duties": ["existing duty"]}),
        })
    conn.commit()
    conn.close()
    return tmp_path


def _loadout(ws, cid):
    import json

    from firm.core.db import connect, get_db_path
    from firm.core.repo import get

    conn = connect(get_db_path(ws))
    row = get(conn, "contract", cid)
    conn.close()
    raw = row["skill_loadout"]
    return raw if isinstance(raw, dict) else json.loads(raw)


def test_apply_merges_packs_into_contracts(tmp_path, capsys):
    from firm.cli.templates import run_templates_apply

    ws = _make_firm_with_contracts(tmp_path)
    assert run_templates_apply("discipline", ws, ["dev=CON-ENG", "lead=CON-LEAD"]) == 0

    eng = _loadout(ws, "CON-ENG")
    assert "existing duty" in eng["duties"]
    assert any("TDD heuristic" in d for d in eng["duties"])
    assert any("SHIP GATE" in p for p in eng["policies"])

    lead = _loadout(ws, "CON-LEAD")
    assert any("Unit-authoring law" in d for d in lead["duties"])
    assert not any("TDD heuristic" in d for d in lead.get("duties", []) if "Unit-authoring" in d)


def test_apply_is_idempotent(tmp_path, capsys):
    from firm.cli.templates import run_templates_apply

    ws = _make_firm_with_contracts(tmp_path)
    assert run_templates_apply("discipline", ws, ["dev=CON-ENG"]) == 0
    before = _loadout(ws, "CON-ENG")
    assert run_templates_apply("discipline", ws, ["dev=CON-ENG"]) == 0
    assert _loadout(ws, "CON-ENG") == before
    assert "already applied" in capsys.readouterr().out


def test_apply_multiple_contracts_per_pack(tmp_path):
    from firm.cli.templates import run_templates_apply

    ws = _make_firm_with_contracts(tmp_path)
    assert run_templates_apply("discipline", ws, ["dev=CON-ENG,CON-LEAD"]) == 0
    assert any("TDD heuristic" in d for d in _loadout(ws, "CON-LEAD")["duties"])


def test_apply_unknown_contract_writes_nothing(tmp_path, capsys):
    from firm.cli.templates import run_templates_apply

    ws = _make_firm_with_contracts(tmp_path)
    assert run_templates_apply("discipline", ws, ["dev=CON-ENG", "lead=CON-NOPE"]) == 1
    assert not any("TDD heuristic" in d for d in _loadout(ws, "CON-ENG")["duties"])


def test_apply_ambiguous_or_unknown_pack(tmp_path, capsys):
    from firm.cli.templates import run_templates_apply

    ws = _make_firm_with_contracts(tmp_path)
    assert run_templates_apply("discipline", ws, ["zzz=CON-ENG"]) == 1
    assert "not a unique prefix" in capsys.readouterr().out


def test_main_wires_templates_apply(tmp_path):
    from firm.__main__ import main

    ws = _make_firm_with_contracts(tmp_path)
    assert main([
        "templates", "apply", "discipline",
        "--map", "dev=CON-ENG", "--workspace", str(ws),
    ]) == 0
    assert any("TDD heuristic" in d for d in _loadout(ws, "CON-ENG")["duties"])
