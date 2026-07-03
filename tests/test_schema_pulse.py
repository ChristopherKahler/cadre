"""Schema tests for migration 003_pulse.

Verifies AC-1..AC-5 from Plan 03.1-01:
- Migration 003_pulse applies cleanly (fresh and incremental)
- Member gains PULSE columns (frequency, last_activated, can_self_assign)
- Contract gains pulse_config (not heartbeat_config), validation_config, budget_config
- member_run invocation_source CHECK accepts 'pulse', rejects 'heartbeat'
- budget_period table exists, is repo-accessible, unique index enforced, mutable
"""

from __future__ import annotations

import sqlite3

import pytest

from firm.core.migrate import apply_migrations, applied_migration_names
from firm.core import repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_migrated_conn() -> sqlite3.Connection:
    """In-memory DB with all migrations applied."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_migrations(conn)
    return conn


def _seed_firm(conn: sqlite3.Connection, firm_id: str = "chrisai") -> str:
    conn.execute(
        "INSERT INTO firm (id, name) VALUES (?, ?)", (firm_id, f"Firm {firm_id}")
    )
    conn.commit()
    return firm_id


def _seed_member(
    conn: sqlite3.Connection,
    member_id: str = "MEM-001",
    firm_id: str = "chrisai",
    **overrides: object,
) -> str:
    defaults = {
        "id": member_id,
        "firm_id": firm_id,
        "name": "Quill",
        "role": "Blog Author",
        "status": "active",
    }
    defaults.update(overrides)
    cols = ", ".join(defaults)
    placeholders = ", ".join("?" for _ in defaults)
    conn.execute(
        f"INSERT INTO member ({cols}) VALUES ({placeholders})",
        tuple(defaults.values()),
    )
    conn.commit()
    return member_id


def _seed_contract(
    conn: sqlite3.Connection,
    contract_id: str = "CON-001",
    firm_id: str = "chrisai",
    **overrides: object,
) -> str:
    defaults = {
        "id": contract_id,
        "firm_id": firm_id,
        "name": "Quill Runtime",
        "runtime_type": "claude_code",
    }
    defaults.update(overrides)
    cols = ", ".join(defaults)
    placeholders = ", ".join("?" for _ in defaults)
    conn.execute(
        f"INSERT INTO contract ({cols}) VALUES ({placeholders})",
        tuple(defaults.values()),
    )
    conn.commit()
    return contract_id


def _seed_member_run(
    conn: sqlite3.Connection,
    run_id: str = "RUN-001",
    firm_id: str = "chrisai",
    member_id: str = "MEM-001",
    **overrides: object,
) -> str:
    defaults = {
        "id": run_id,
        "firm_id": firm_id,
        "member_id": member_id,
        "status": "running",
        "started_at": "2026-04-16T09:00:00",
    }
    defaults.update(overrides)
    cols = ", ".join(defaults)
    placeholders = ", ".join("?" for _ in defaults)
    conn.execute(
        f"INSERT INTO member_run ({cols}) VALUES ({placeholders})",
        tuple(defaults.values()),
    )
    conn.commit()
    return run_id


# ---------------------------------------------------------------------------
# AC-1: Migration 003_pulse applies cleanly
# ---------------------------------------------------------------------------

class TestMigrationApplies:
    def test_migration_003_in_applied_list(self) -> None:
        """Fresh DB → 003_pulse is among the applied migrations."""
        conn = _fresh_migrated_conn()
        try:
            names = applied_migration_names(conn)
            assert "003_pulse" in names
        finally:
            conn.close()

    def test_all_three_migrations_apply(self) -> None:
        """All bundled migrations apply in order on a fresh DB."""
        conn = _fresh_migrated_conn()
        try:
            names = applied_migration_names(conn)
            assert "001_init" in names
            assert "002_entities" in names
            assert "003_pulse" in names
        finally:
            conn.close()

    def test_no_heartbeat_migration(self) -> None:
        """003_heartbeat should NOT appear — it was renamed to 003_pulse."""
        conn = _fresh_migrated_conn()
        try:
            names = applied_migration_names(conn)
            assert "003_heartbeat" not in names
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# AC-2: Member gains PULSE columns with correct defaults
# ---------------------------------------------------------------------------

class TestMemberPulseColumns:
    def test_columns_exist(self) -> None:
        """member table has frequency, last_activated, can_self_assign columns."""
        conn = _fresh_migrated_conn()
        try:
            cols = {
                row[1]: {"type": row[2], "notnull": row[3], "default": row[4]}
                for row in conn.execute("PRAGMA table_info(member)").fetchall()
            }
            assert "frequency" in cols
            assert cols["frequency"]["type"] == "INTEGER"
            assert cols["frequency"]["notnull"] == 0  # nullable

            assert "last_activated" in cols
            assert cols["last_activated"]["type"] == "TEXT"
            assert cols["last_activated"]["notnull"] == 0  # nullable

            assert "can_self_assign" in cols
            assert cols["can_self_assign"]["type"] == "INTEGER"
            assert cols["can_self_assign"]["notnull"] == 1  # NOT NULL
        finally:
            conn.close()

    def test_defaults_on_new_row(self) -> None:
        """Insert Member without specifying new columns → correct defaults."""
        conn = _fresh_migrated_conn()
        try:
            firm_id = _seed_firm(conn)
            _seed_member(conn, firm_id=firm_id)
            row = conn.execute(
                "SELECT frequency, last_activated, can_self_assign FROM member WHERE id = 'MEM-001'"
            ).fetchone()
            assert row[0] is None  # frequency default NULL
            assert row[1] is None  # last_activated default NULL
            assert row[2] == 0     # can_self_assign default 0
        finally:
            conn.close()

    def test_existing_member_data_survives(self) -> None:
        """Members seeded before 003 keep their data intact after migration."""
        # Simulate incremental: apply 001+002, seed data, then apply 003
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

        from pathlib import Path
        from firm.core.migrate import (
            _default_migrations_dir,
            discover_migrations,
            ensure_migrations_table,
            _split_sql,
        )

        migrations_dir = _default_migrations_dir()
        ensure_migrations_table(conn)

        # Apply only 001 + 002
        all_migs = discover_migrations(migrations_dir)
        saved_isolation = conn.isolation_level
        conn.isolation_level = None
        for num, name, path in all_migs:
            if num > 2:
                break
            stmts = _split_sql(path.read_text(encoding="utf-8"))
            conn.execute("BEGIN")
            try:
                for stmt in stmts:
                    conn.execute(stmt)
                conn.execute(
                    "INSERT INTO _migrations (name) VALUES (?)", (name,)
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        conn.isolation_level = saved_isolation

        # Seed a Member under the 002 schema
        _seed_firm(conn)
        conn.execute(
            "INSERT INTO member (id, firm_id, name, role, status) VALUES (?, ?, ?, ?, ?)",
            ("MEM-OLD", "chrisai", "OldBot", "Legacy", "active"),
        )
        conn.commit()

        # Now apply 003
        applied = apply_migrations(conn)
        assert "003_pulse" in applied

        # Verify old data is intact
        row = conn.execute(
            "SELECT name, role, status, frequency, last_activated, can_self_assign "
            "FROM member WHERE id = 'MEM-OLD'"
        ).fetchone()
        assert row[0] == "OldBot"
        assert row[1] == "Legacy"
        assert row[2] == "active"
        assert row[3] is None   # frequency default
        assert row[4] is None   # last_activated default
        assert row[5] == 0      # can_self_assign default
        conn.close()


# ---------------------------------------------------------------------------
# AC-3: Contract gains PULSE columns, naming is pulse not heartbeat
# ---------------------------------------------------------------------------

class TestContractPulseColumns:
    def test_pulse_config_exists(self) -> None:
        """contract table has pulse_config column, NOT heartbeat_config."""
        conn = _fresh_migrated_conn()
        try:
            col_names = {
                row[1]
                for row in conn.execute("PRAGMA table_info(contract)").fetchall()
            }
            assert "pulse_config" in col_names
            assert "heartbeat_config" not in col_names
            assert "validation_config" in col_names
            assert "budget_config" in col_names
        finally:
            conn.close()

    def test_contract_json_columns_round_trip(self) -> None:
        """JSON columns on contract serialize/deserialize via repo."""
        conn = _fresh_migrated_conn()
        try:
            _seed_firm(conn)
            data = {
                "id": "CON-T1",
                "firm_id": "chrisai",
                "name": "Test Runtime",
                "runtime_type": "claude_code",
                "pulse_config": {"timeout_sec": 300, "model": "claude-sonnet-4-6"},
                "validation_config": {"enabled": True, "max_retries": 1},
                "budget_config": {"enforcement": "hard", "period": "monthly"},
            }
            created = repo.create(conn, "contract", data)
            assert created["pulse_config"] == {"timeout_sec": 300, "model": "claude-sonnet-4-6"}
            assert created["validation_config"] == {"enabled": True, "max_retries": 1}
            assert created["budget_config"] == {"enforcement": "hard", "period": "monthly"}

            found = repo.find(conn, "contract", id="CON-T1")
            assert len(found) == 1
            assert found[0]["pulse_config"] == {"timeout_sec": 300, "model": "claude-sonnet-4-6"}
        finally:
            conn.close()

    def test_member_run_invocation_source_pulse(self) -> None:
        """invocation_source='pulse' is valid, 'heartbeat' is rejected."""
        conn = _fresh_migrated_conn()
        try:
            _seed_firm(conn)
            _seed_member(conn)

            # 'pulse' should succeed
            _seed_member_run(conn, run_id="RUN-P1", invocation_source="pulse")
            row = conn.execute(
                "SELECT invocation_source FROM member_run WHERE id = 'RUN-P1'"
            ).fetchone()
            assert row[0] == "pulse"

            # 'heartbeat' should fail CHECK constraint
            with pytest.raises(sqlite3.IntegrityError):
                _seed_member_run(conn, run_id="RUN-HB", invocation_source="heartbeat")
        finally:
            conn.close()

    def test_member_run_new_columns(self) -> None:
        """retry_of_run_id, prompt_snapshot, validation_result exist and accept data."""
        conn = _fresh_migrated_conn()
        try:
            _seed_firm(conn)
            _seed_member(conn)

            # Create initial run
            _seed_member_run(conn, run_id="RUN-ORIG")

            # Create retry run referencing original
            conn.execute(
                "INSERT INTO member_run (id, firm_id, member_id, status, started_at, "
                "retry_of_run_id, prompt_snapshot, validation_result, invocation_source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "RUN-RETRY",
                    "chrisai",
                    "MEM-001",
                    "running",
                    "2026-04-16T09:05:00",
                    "RUN-ORIG",
                    "You are Quill, Blog Author...",
                    '{"passed": false, "errors": ["file_missing"]}',
                    "retry",
                ),
            )
            conn.commit()

            row = conn.execute(
                "SELECT retry_of_run_id, prompt_snapshot, validation_result "
                "FROM member_run WHERE id = 'RUN-RETRY'"
            ).fetchone()
            assert row[0] == "RUN-ORIG"
            assert row[1] == "You are Quill, Blog Author..."
            assert row[2] == '{"passed": false, "errors": ["file_missing"]}'
        finally:
            conn.close()

    def test_firm_schedule_column(self) -> None:
        """firm table has schedule column, accepts JSON via repo."""
        conn = _fresh_migrated_conn()
        try:
            schedule = {
                "timezone": "America/Chicago",
                "business_hours": {"start": "07:00", "end": "17:00"},
            }
            data = {"id": "test-firm", "name": "Test Firm", "schedule": schedule}
            created = repo.create(conn, "firm", data)
            assert created["schedule"] == schedule

            found = repo.find(conn, "firm", id="test-firm")
            assert found[0]["schedule"] == schedule
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# AC-4: budget_period table exists and is repo-accessible
# ---------------------------------------------------------------------------

class TestBudgetPeriod:
    def test_table_exists(self) -> None:
        """budget_period table exists with correct columns after migration."""
        conn = _fresh_migrated_conn()
        try:
            col_names = {
                row[1]
                for row in conn.execute("PRAGMA table_info(budget_period)").fetchall()
            }
            expected = {
                "id", "firm_id", "member_id", "period_start", "period_end",
                "run_count", "total_input_tokens", "total_output_tokens",
                "total_cost_usd", "status", "created_at", "updated_at",
            }
            assert expected == col_names
        finally:
            conn.close()

    def test_crud_via_repo(self) -> None:
        """repo.create and repo.find work for budget_period."""
        conn = _fresh_migrated_conn()
        try:
            _seed_firm(conn)
            _seed_member(conn)

            data = {
                "id": "BP-001",
                "firm_id": "chrisai",
                "member_id": "MEM-001",
                "period_start": "2026-04-01",
                "period_end": "2026-04-30",
                "run_count": 5,
                "total_input_tokens": 100000,
                "total_output_tokens": 8000,
                "total_cost_usd": 4.50,
                "status": "active",
            }
            created = repo.create(conn, "budget_period", data)
            assert created["id"] == "BP-001"
            assert created["run_count"] == 5
            assert created["total_cost_usd"] == 4.50

            found = repo.find(conn, "budget_period", id="BP-001")
            assert len(found) == 1
            assert found[0]["member_id"] == "MEM-001"
            assert found[0]["total_input_tokens"] == 100000
        finally:
            conn.close()

    def test_unique_index_prevents_duplicates(self) -> None:
        """Duplicate (member_id, period_start) raises IntegrityError."""
        conn = _fresh_migrated_conn()
        try:
            _seed_firm(conn)
            _seed_member(conn)

            base = {
                "firm_id": "chrisai",
                "member_id": "MEM-001",
                "period_start": "2026-04-01",
                "period_end": "2026-04-30",
            }
            repo.create(conn, "budget_period", {"id": "BP-DUP1", **base})

            with pytest.raises(sqlite3.IntegrityError):
                repo.create(conn, "budget_period", {"id": "BP-DUP2", **base})
        finally:
            conn.close()

    def test_budget_period_is_mutable(self) -> None:
        """budget_period can be updated (not in IMMUTABLE_TABLES)."""
        conn = _fresh_migrated_conn()
        try:
            _seed_firm(conn)
            _seed_member(conn)

            repo.create(conn, "budget_period", {
                "id": "BP-MUT",
                "firm_id": "chrisai",
                "member_id": "MEM-001",
                "period_start": "2026-04-01",
                "period_end": "2026-04-30",
                "run_count": 0,
            })

            updated = repo.update(conn, "budget_period", "BP-MUT", {"run_count": 10})
            assert updated["run_count"] == 10
        finally:
            conn.close()

    def test_budget_period_status_check(self) -> None:
        """budget_period status must be 'active', 'closed', or 'limit_reached'."""
        conn = _fresh_migrated_conn()
        try:
            _seed_firm(conn)
            _seed_member(conn)

            with pytest.raises(sqlite3.IntegrityError):
                repo.create(conn, "budget_period", {
                    "id": "BP-BAD",
                    "firm_id": "chrisai",
                    "member_id": "MEM-001",
                    "period_start": "2026-04-01",
                    "period_end": "2026-04-30",
                    "status": "invalid_status",
                })
        finally:
            conn.close()
