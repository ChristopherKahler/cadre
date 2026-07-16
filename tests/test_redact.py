"""Credential redactor — the values that must never reach immutable Records.

Covers audit A9 (Authorization/Bearer shapes the original key-word set missed)
and locks the collision guard: provenance fields (author_id / author_type)
must survive, or the redactor eats the audit trail it exists to protect.
"""

from __future__ import annotations

from firm.hooks._redact import redact


class TestA9BearerShapes:

    def test_authorization_dict_key_redacted(self):
        assert redact({"Authorization": "Bearer sk-abc123"}) == {
            "Authorization": "[REDACTED]"}

    def test_authorization_bearer_header_string(self):
        assert redact("Authorization: Bearer sk-abc123def") == (
            "Authorization: Bearer [REDACTED]")

    def test_standalone_bearer_token_in_prose(self):
        assert redact("request failed with Bearer xoxb-99-88 attached") == (
            "request failed with Bearer [REDACTED] attached")

    def test_bearer_is_case_insensitive(self):
        assert redact("bearer TOKEN123") == "bearer [REDACTED]"


class TestCollisionGuard:

    def test_provenance_fields_survive(self):
        """author_id / author_type must NOT be redacted — a bare 'auth' key
        word would eat them, and they carry the Records audit trail."""
        row = {"author_id": "MEM-001", "author_type": "board", "id": "REC-1"}
        assert redact(row) == row

    def test_author_substring_in_string_survives(self):
        assert redact("authored by MEM-001") == "authored by MEM-001"


class TestExistingBehaviorIntact:

    def test_credential_dict_keys_still_redacted(self):
        assert redact({"api_token": "sk-x", "nested": {"password": "p"}}) == {
            "api_token": "[REDACTED]", "nested": {"password": "[REDACTED]"}}

    def test_key_value_string_still_redacted(self):
        assert redact("CADRE_SLACK_TOKEN=xoxb-1234") == (
            "CADRE_SLACK_TOKEN=[REDACTED]")

    def test_non_credential_data_untouched(self):
        assert redact({"name": "Nova", "count": 3, "role": "Engineer"}) == {
            "name": "Nova", "count": 3, "role": "Engineer"}

    def test_input_not_mutated(self):
        original = {"token": "secret", "keep": "me"}
        redact(original)
        assert original == {"token": "secret", "keep": "me"}
