"""Owner-only file permissions, on every platform Cadre ships to.

POSIX says "only my user may read this" with mode 0600. Windows has no mode
bits: os.chmod there sets the read-only attribute and nothing else, so a 0600
write of the board token, the board password hash, or the vault master key
produced a file that every account on the machine could read.

The suite did not catch it. tests/test_secrets.py guarded its permission
assertion with 'os.name != "posix" or ...', which passes on Windows by
asserting nothing, and the two board-auth tests asserted the POSIX mode
unguarded and simply failed there.

The Windows implementation replaces the file's DACL with a protected
(inheritance-blocking) ACL carrying exactly one entry: full control for the
SID that owns the current process. It is pure ctypes against advapi32 - no
subprocess, no pywin32, nothing outside the standard library.

Residual, stated plainly so nobody mistakes this for a sandbox: a member of
Administrators can take ownership and read the file anyway, exactly as root
can on POSIX. This raises the bar to the platform's own definition of
private, not above it.

owner_only() reads the property back off disk, so a test asserts the real
thing on both platforms instead of asserting that the call was made.
"""

from __future__ import annotations

import os
import stat
import sys
import warnings
from pathlib import Path

WINDOWS = sys.platform == "win32"

# Cadre's own wording for the shortfall, so one grep finds every mention.
UNENFORCED_WARNING = (
    "cadre could not make {path} readable only by you on this platform "
    "({reason}). The file holds a credential and may be readable by other "
    "accounts on this machine. Restrict it by hand, or move CADRE_HOME onto "
    "a volume that supports per-user permissions."
)


class PermissionUnsupported(RuntimeError):
    """The platform refused to express owner-only for this path."""


def restrict_to_owner(path: str | Path, *, is_dir: bool = False) -> None:
    """Make path readable and writable only by the current user.

    POSIX: mode 0600, or 0700 for a directory. Windows: a protected DACL whose
    single entry grants full control to the current process's user SID.

    Never raises on the caller's behalf. If the platform cannot express the
    property, it warns with the path and the reason. Silence is the failure
    mode this module exists to remove.
    """
    p = Path(path)
    try:
        if WINDOWS:
            _windows_restrict(p, is_dir=is_dir)
        else:
            os.chmod(p, 0o700 if is_dir else 0o600)
    except Exception as exc:  # noqa: BLE001 - credential file, warn on anything
        warnings.warn(
            UNENFORCED_WARNING.format(path=p, reason=exc),
            RuntimeWarning,
            stacklevel=2,
        )


def owner_only(path: str | Path) -> bool:
    """Read the permission back off disk. True only if no other account can read it.

    This is the assertion surface. It inspects the filesystem rather than a
    flag the writer set, so it is equally valid in a test and in a runtime
    self-check.
    """
    p = Path(path)
    if not WINDOWS:
        return stat.S_IMODE(p.stat().st_mode) & 0o077 == 0
    trustees, protected = _windows_dacl(p)
    if not protected:
        return False
    me = _current_user_sid()
    return bool(trustees) and all(t == me for t in trustees)


def describe(path: str | Path) -> str:
    """One line of evidence about a path's permission, for diagnostics."""
    p = Path(path)
    if not WINDOWS:
        return "{}: mode {:04o}".format(p, stat.S_IMODE(p.stat().st_mode))
    try:
        trustees, protected = _windows_dacl(p)
    except Exception as exc:  # noqa: BLE001
        return "{}: DACL unreadable ({})".format(p, exc)
    flag = "protected" if protected else "INHERITING"
    return "{}: DACL {}, trustees {}".format(
        p, flag, ", ".join(trustees) or "(none)")


# --------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------

_SE_FILE_OBJECT = 1
_DACL_SECURITY_INFORMATION = 0x00000004
_PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
_SDDL_REVISION_1 = 1
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_ERROR_INSUFFICIENT_BUFFER = 122

_BOUND = None


def _win():
    """Bind the advapi32 and kernel32 entry points once, with real prototypes."""
    global _BOUND
    if _BOUND is not None:
        return _BOUND

    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL

    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD)]
    advapi32.GetTokenInformation.restype = wintypes.BOOL

    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL

    advapi32.ConvertStringSidToSidW.argtypes = [
        wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL

    convert_sddl = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert_sddl.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.ULONG)]
    convert_sddl.restype = wintypes.BOOL

    to_sddl = advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW
    to_sddl.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.ULONG)]
    to_sddl.restype = wintypes.BOOL

    advapi32.GetSecurityDescriptorDacl.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
    advapi32.GetSecurityDescriptorDacl.restype = wintypes.BOOL

    advapi32.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD

    advapi32.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p)]
    advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD

    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    _BOUND = (ctypes, wintypes, advapi32, kernel32)
    return _BOUND


def _check(ok, ctypes, what: str) -> None:
    if not ok:
        raise PermissionUnsupported(
            "{} failed (WinError {})".format(what, ctypes.get_last_error()))


def _current_user_sid() -> str:
    """The SID string of the account this process runs as."""
    ctypes, wintypes, advapi32, kernel32 = _win()
    token = wintypes.HANDLE()
    _check(advapi32.OpenProcessToken(kernel32.GetCurrentProcess(),
                                     _TOKEN_QUERY, ctypes.byref(token)),
           ctypes, "OpenProcessToken")
    try:
        size = wintypes.DWORD(0)
        advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0,
                                     ctypes.byref(size))
        if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER:
            _check(False, ctypes, "GetTokenInformation (size probe)")
        buf = ctypes.create_string_buffer(size.value)
        _check(advapi32.GetTokenInformation(token, _TOKEN_USER, buf,
                                            size.value, ctypes.byref(size)),
               ctypes, "GetTokenInformation")
        # TOKEN_USER is a SID_AND_ATTRIBUTES: {PSID Sid; DWORD Attributes}.
        psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        return _sid_to_string(psid)
    finally:
        kernel32.CloseHandle(token)


def _sid_to_string(psid) -> str:
    ctypes, wintypes, advapi32, kernel32 = _win()
    out = wintypes.LPWSTR()
    _check(advapi32.ConvertSidToStringSidW(psid, ctypes.byref(out)),
           ctypes, "ConvertSidToStringSid")
    try:
        return out.value or ""
    finally:
        kernel32.LocalFree(out)


def _canonical_sid(text: str) -> str:
    """Normalize an SDDL trustee to canonical S-1-... form.

    SDDL abbreviates well-known trustees: BA for Administrators, SY for
    SYSTEM. Round-tripping through the SID makes a string comparison against
    the current user's SID honest for every spelling.

    Off Windows there is nothing to resolve against, so the trustee comes
    back unchanged and _parse_dacl_sddl stays testable everywhere.
    """
    if not WINDOWS:
        return text
    ctypes, wintypes, advapi32, kernel32 = _win()
    psid = ctypes.c_void_p()
    if not advapi32.ConvertStringSidToSidW(text, ctypes.byref(psid)):
        return text
    try:
        return _sid_to_string(psid)
    finally:
        kernel32.LocalFree(psid)


def _windows_restrict(path: Path, *, is_dir: bool) -> None:
    ctypes, wintypes, advapi32, kernel32 = _win()
    sid = _current_user_sid()
    # OICI on a directory so files created inside inherit the same single
    # entry. A file needs no inheritance flags.
    flags = "OICI" if is_dir else ""
    sddl = "D:P(A;{};FA;;;{})".format(flags, sid)

    psd = ctypes.c_void_p()
    size = wintypes.ULONG(0)
    convert_sddl = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    _check(convert_sddl(sddl, _SDDL_REVISION_1, ctypes.byref(psd),
                        ctypes.byref(size)),
           ctypes, "ConvertStringSecurityDescriptorToSecurityDescriptor")
    try:
        present = wintypes.BOOL()
        pacl = ctypes.c_void_p()
        defaulted = wintypes.BOOL()
        _check(advapi32.GetSecurityDescriptorDacl(
            psd, ctypes.byref(present), ctypes.byref(pacl),
            ctypes.byref(defaulted)), ctypes, "GetSecurityDescriptorDacl")
        rc = advapi32.SetNamedSecurityInfoW(
            str(path), _SE_FILE_OBJECT,
            _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION,
            None, None, pacl, None)
        if rc != 0:
            raise PermissionUnsupported(
                "SetNamedSecurityInfo failed (WinError {})".format(rc))
    finally:
        kernel32.LocalFree(psd)


def _windows_dacl(path: Path) -> tuple[list[str], bool]:
    """Return the DACL's canonical trustee SIDs and whether it is protected."""
    ctypes, wintypes, advapi32, kernel32 = _win()
    psd = ctypes.c_void_p()
    pacl = ctypes.c_void_p()
    rc = advapi32.GetNamedSecurityInfoW(
        str(path), _SE_FILE_OBJECT, _DACL_SECURITY_INFORMATION,
        None, None, ctypes.byref(pacl), None, ctypes.byref(psd))
    if rc != 0:
        raise PermissionUnsupported(
            "GetNamedSecurityInfo failed (WinError {})".format(rc))
    try:
        out = wintypes.LPWSTR()
        length = wintypes.ULONG(0)
        to_sddl = advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW
        _check(to_sddl(psd, _SDDL_REVISION_1, _DACL_SECURITY_INFORMATION,
                       ctypes.byref(out), ctypes.byref(length)),
               ctypes, "ConvertSecurityDescriptorToStringSecurityDescriptor")
        try:
            sddl = out.value or ""
        finally:
            kernel32.LocalFree(out)
    finally:
        kernel32.LocalFree(psd)
    return _parse_dacl_sddl(sddl)


def _parse_dacl_sddl(sddl: str) -> tuple[list[str], bool]:
    """Split a D:<flags>(entry)(entry) SDDL fragment into trustees, protected.

    Pure string work, so it is testable on any platform.
    """
    body = sddl.split("D:", 1)[1] if "D:" in sddl else ""
    head, _, rest = body.partition("(")
    protected = "P" in head
    entries = ("(" + rest) if rest else ""
    trustees: list[str] = []
    depth = 0
    current = ""
    for ch in entries:
        if ch == "(":
            depth += 1
            if depth == 1:
                current = ""
                continue
        elif ch == ")":
            depth -= 1
            if depth == 0:
                fields = current.split(";")
                if len(fields) >= 6:
                    trustees.append(_canonical_sid(fields[5]))
                continue
        if depth >= 1:
            current += ch
    return trustees, protected
