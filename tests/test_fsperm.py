"""firm.secrets.fsperm - owner-only files, proved by reading the disk back.

Each permission test carries its own red arm: the file is first put into a
state the assertion must reject, using the platform's own mechanism, and the
test asserts the rejection before asserting the fix. A permission check that
cannot tell restricted from unrestricted would pass either way, and that is
exactly how the Windows shortfall survived a year of green suites.
"""

from __future__ import annotations

import os
import stat

import pytest

from firm.secrets import fsperm


def _make_world_readable(path) -> None:
    """Put path into the state owner_only() must reject, per platform."""
    if not fsperm.WINDOWS:
        os.chmod(path, 0o644)
        return
    # Windows: an unprotected DACL granting Everyone full control. Unprotected
    # matters as much as the trustee - an inheriting DACL is never owner-only.
    ctypes, wintypes, advapi32, kernel32 = fsperm._win()
    psd = ctypes.c_void_p()
    size = wintypes.ULONG(0)
    convert = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    assert convert("D:(A;;FA;;;WD)", fsperm._SDDL_REVISION_1,
                   ctypes.byref(psd), ctypes.byref(size))
    try:
        present = wintypes.BOOL()
        pacl = ctypes.c_void_p()
        defaulted = wintypes.BOOL()
        assert advapi32.GetSecurityDescriptorDacl(
            psd, ctypes.byref(present), ctypes.byref(pacl),
            ctypes.byref(defaulted))
        rc = advapi32.SetNamedSecurityInfoW(
            str(path), fsperm._SE_FILE_OBJECT,
            fsperm._DACL_SECURITY_INFORMATION,
            None, None, pacl, None)
        assert rc == 0, "SetNamedSecurityInfo rc {}".format(rc)
    finally:
        kernel32.LocalFree(psd)


class TestRestrictToOwner:
    def test_rejects_a_world_readable_file_then_accepts_the_restricted_one(
            self, tmp_path):
        path = tmp_path / "credential"
        path.write_text("secret", encoding="utf-8")

        _make_world_readable(path)
        assert not fsperm.owner_only(path), (
            "red arm: a world-readable file must not read as owner-only - "
            + fsperm.describe(path))

        fsperm.restrict_to_owner(path)
        assert fsperm.owner_only(path), fsperm.describe(path)
        assert path.read_text(encoding="utf-8") == "secret"

    def test_directory_variant(self, tmp_path):
        d = tmp_path / "home"
        d.mkdir()
        fsperm.restrict_to_owner(d, is_dir=True)
        assert fsperm.owner_only(d), fsperm.describe(d)

    @pytest.mark.skipif(fsperm.WINDOWS, reason="POSIX mode bits only")
    def test_posix_mode_is_still_0600(self, tmp_path):
        """The POSIX property the old assertions checked has not been softened."""
        path = tmp_path / "credential"
        path.write_text("secret", encoding="utf-8")
        fsperm.restrict_to_owner(path)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_failure_warns_instead_of_going_quiet(self, tmp_path, monkeypatch):
        """The shortfall has to be loud. Silence is the defect being fixed."""
        path = tmp_path / "credential"
        path.write_text("secret", encoding="utf-8")

        def boom(*a, **kw):
            raise RuntimeError("no ACL support on this volume")

        monkeypatch.setattr(fsperm, "_windows_restrict", boom)
        monkeypatch.setattr(fsperm.os, "chmod", boom)

        with pytest.warns(RuntimeWarning, match="could not make"):
            fsperm.restrict_to_owner(path)


class TestParseDaclSddl:
    """Pure string work, so it runs on every platform including the CI Linux leg."""

    def test_protected_single_trustee(self):
        trustees, protected = fsperm._parse_dacl_sddl(
            "D:P(A;;FA;;;S-1-5-21-1-2-3-1001)")
        assert protected is True
        assert trustees == ["S-1-5-21-1-2-3-1001"]

    def test_inheriting_dacl_is_not_protected(self):
        trustees, protected = fsperm._parse_dacl_sddl(
            "D:AI(A;ID;FA;;;S-1-5-18)(A;ID;FA;;;S-1-5-21-1-2-3-1001)")
        assert protected is False
        assert len(trustees) == 2

    def test_flags_before_the_first_entry_are_not_mistaken_for_a_trustee(self):
        trustees, protected = fsperm._parse_dacl_sddl(
            "D:PAI(A;OICI;FA;;;S-1-5-21-1-2-3-1001)")
        assert protected is True
        assert trustees == ["S-1-5-21-1-2-3-1001"]

    def test_empty_dacl(self):
        assert fsperm._parse_dacl_sddl("D:P") == ([], True)


class TestCredentialFilesUseIt:
    """The three files that hold a credential all route through fsperm."""

    def test_board_token_and_password(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CADRE_HOME", str(tmp_path / "cadre-home"))
        from firm.dashboard import auth as board_auth

        board_auth.board_token()
        token_path = board_auth.board_token_path()
        assert fsperm.owner_only(token_path), fsperm.describe(token_path)

        board_auth.set_board_password("correct horse battery staple")
        pass_path = board_auth.board_password_path()
        assert fsperm.owner_only(pass_path), fsperm.describe(pass_path)

    def test_vault_master_key_and_payload(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CADRE_HOME", str(tmp_path / "cadre-home"))
        import firm.secrets.vault as vault_mod

        pytest.importorskip("cryptography")
        vault_mod.ensure_master_key()
        key_path = vault_mod.master_key_path()
        assert fsperm.owner_only(key_path), fsperm.describe(key_path)

        vault_path = vault_mod.cadre_home() / "vault.enc"
        vault_mod.write_vault(vault_path, {"K": "v"})
        assert fsperm.owner_only(vault_path), fsperm.describe(vault_path)
