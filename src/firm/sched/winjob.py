r"""Ending a Cadre task ends the work it started. Windows job objects, issue #141.

``schtasks /End`` ends the LAUNCHER. Measured at #119 (fork doc, M-W2 arm
A4b-1): the command it had started, that command's console and its python were
all still running five seconds later -- three survivors. So "stop the heartbeat"
stopped the supervisor and left the pulse running, and a restart inside the
lock's ten-minute TTL was then refused because the dead holder still held it.

A Windows JOB OBJECT is the mechanism that fixes it. A job with
``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` ends every process in it when the LAST
handle to the job closes. So the launcher creates one, puts ITSELF in it, and
holds the handle for its own life: every child it starts inherits the job, and
the launcher's exit -- however it is ended -- closes the handle and ends the
tree.

NEITHER BREAKAWAY FLAG IS SET, and that is a decision rather than an omission
(G0 addendum condition C2). ``JOB_OBJECT_LIMIT_BREAKAWAY_OK`` and
``JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK`` let a child leave the job. Without
them, a child that asks to break away FAILS TO START, loudly, which is a thing
J1 counts; with them, a child leaves silently and survives the launcher, which
is the defect this module exists to close. Loud is the better failure.

EVERY ctypes PROTOTYPE IS DECLARED, and this is not decoration. Without
``restype`` ctypes assumes ``c_int``, and ``GetCurrentProcess`` returns the
pseudo-handle -1, which then reaches a HANDLE parameter on a 64-bit process as
``0x00000000FFFFFFFF``. Measured while building this: ``IsProcessInJob`` came
back ``GetLastError=6 (The handle is invalid)``. That one failed loudly. The
same omission on a ``BOOL*`` out-parameter would not -- a value written through
a mis-sized pointer is simply wrong, and "this process is not in a job" reads
exactly like a measurement. For a module whose whole job is to answer yes/no
questions about process membership, an undeclared prototype is a false-reading
generator.

NOTHING HERE RAISES INTO A LAUNCHER. :func:`contain_this_process` returns a
:class:`Containment` either way, because a host that cannot make a job must
still get its pulse: refusing to run would turn a machine with a locked-down
job policy into a firm with no heartbeat at all, which is worse than today.
What it must never do is run while REPORTING itself contained -- see condition
C3, and :meth:`firm.sched.winsched.WindowsScheduler.status`, which carries the
answer to the operator.

Measured on this machine, Windows 10 19045, Python 3.12.6, before any of this
was written: a fresh process is in no job; a kill-on-close job is creatable and
its ``LimitFlags`` read back ``0x00002000`` with neither breakaway flag; a real
``CREATE_NO_WINDOW`` child reads "in no job" before assignment and "in this job"
after, which is the discriminating pair. And end to end: a command that starts a
background child and exits leaves that child alive five seconds after the
launcher exits WITHOUT the job, and gone WITH it.
"""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass

#: ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` -- end every process in the job when
#: the last handle to it closes. The whole mechanism, in one flag.
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
#: Deliberately NOT set; see the module docstring and condition C2.
JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
#: Deliberately NOT set; see the module docstring and condition C2.
JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK = 0x00001000

_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

_WINDOWS = sys.platform == "win32"


@dataclass(frozen=True)
class Containment:
    """What happened when a process tried to put itself in a job.

    ``contained`` False with a ``reason`` is a complete answer, not an error:
    the caller runs anyway and the reason reaches the operator. ``limit_flags``
    is read back OFF THE JOB rather than echoed from the constant that was
    passed in, because the only flags worth reporting are the ones the kernel
    agreed to.
    """

    contained: bool
    reason: str
    limit_flags: int | None = None
    #: Does this host HAVE job objects at all? Absent is not failed, and the
    #: two want different things from the reader. A Windows machine whose job
    #: could not be created has a problem an operator can act on, and the
    #: launcher says so in its log. A host with no job objects has no such
    #: problem -- this launcher is not its scheduler -- and a warning there
    #: would be noise in every log for a failure that cannot be fixed.
    #: `contained` is False either way, because the tree is not contained
    #: either way and reporting otherwise would be a claim nothing backs.
    supported: bool = True

    def as_record(self, pid: int, at: str) -> dict:
        """The shape written beside the launcher for ``status()`` to read."""
        return {"contained": self.contained,
                "reason": self.reason,
                "limit_flags": (None if self.limit_flags is None
                                else f"0x{self.limit_flags:08x}"),
                "supported": self.supported,
                "pid": pid,
                "at": at}


if _WINDOWS:                                    # pragma: no cover - by platform
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _k32.GetCurrentProcess.restype = wintypes.HANDLE
    _k32.GetCurrentProcess.argtypes = []
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _k32.SetInformationJobObject.restype = wintypes.BOOL
    _k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                             wintypes.LPVOID, wintypes.DWORD]
    _k32.QueryInformationJobObject.restype = wintypes.BOOL
    _k32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                               wintypes.LPVOID, wintypes.DWORD,
                                               ctypes.POINTER(wintypes.DWORD)]
    _k32.AssignProcessToJobObject.restype = wintypes.BOOL
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.IsProcessInJob.restype = wintypes.BOOL
    _k32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE,
                                    ctypes.POINTER(wintypes.BOOL)]
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]

    class _BASIC_LIMIT(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                    ("WriteOperationCount", ctypes.c_ulonglong),
                    ("OtherOperationCount", ctypes.c_ulonglong),
                    ("ReadTransferCount", ctypes.c_ulonglong),
                    ("WriteTransferCount", ctypes.c_ulonglong),
                    ("OtherTransferCount", ctypes.c_ulonglong)]

    class _EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BASIC_LIMIT),
                    ("IoInfo", _IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]


def _why(call: str) -> str:
    """A failure named with the call AND the code, because one without the other
    sends an operator nowhere."""
    code = ctypes.get_last_error()
    return f"{call} failed, GetLastError={code} ({ctypes.FormatError(code).strip()})"


def create_kill_on_close_job():
    """A job whose members all end when the last handle to it closes.

    Raises ``OSError`` with the failing call and its code. The caller that
    matters -- :func:`contain_this_process` -- turns that into a reason rather
    than letting it reach a launcher.
    """
    if not _WINDOWS:
        raise OSError("job objects are a Windows kernel mechanism")
    handle = _k32.CreateJobObjectW(None, None)
    if not handle:
        raise OSError(_why("CreateJobObjectW"))
    info = _EXTENDED_LIMIT()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not _k32.SetInformationJobObject(
            handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info), ctypes.sizeof(info)):
        why = _why("SetInformationJobObject")
        _k32.CloseHandle(handle)
        raise OSError(why)
    return handle


def job_limit_flags(handle) -> int:
    """The job's basic ``LimitFlags``, read back off the job itself."""
    if not _WINDOWS:
        raise OSError("job objects are a Windows kernel mechanism")
    info = _EXTENDED_LIMIT()
    returned = wintypes.DWORD(0)
    if not _k32.QueryInformationJobObject(
            handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info),
            ctypes.sizeof(info), ctypes.byref(returned)):
        raise OSError(_why("QueryInformationJobObject"))
    return int(info.BasicLimitInformation.LimitFlags)


def _flags_or_none(handle) -> tuple[int | None, str]:
    """The job's flags, or ``None`` with the reason. NEVER RAISES.

    :func:`job_limit_flags` raises the moment ``QueryInformationJobObject``
    fails, and :func:`contain_this_process` calls it on both of its branches
    while promising never to raise. avocet measured what that costs (#146
    FINDING 1): with only that kernel call broken, the generated launcher stub
    exits 3 WITH THE COMMAND NEVER STARTED, and the log an operator would read
    is empty. The mechanism that exists to stop a pulse outliving its task
    would have stopped the pulse from ever running.

    THE FLAGS ARE A FIELD THE LAUNCHER PRINTS, NOT THE CONTAINMENT ITSELF.
    Membership is settled by ``AssignProcessToJobObject`` and read back with
    ``IsProcessInJob`` before this is ever called; a failure here loses the
    number and nothing else. So the number goes missing and says why, which is
    what ``limit_flags: null`` in the record means.

    Both call sites go through this ONE function rather than each growing a
    try/except, so there is one rule and a test can hold it without a job, a
    handle or a platform -- and so a third caller cannot quietly reintroduce
    the bare call.
    """
    try:
        return job_limit_flags(handle), ""
    except OSError as exc:
        return None, (f"the job was made and this process is in it, but its "
                      f"limit flags could not be read: {exc}")


def close(handle) -> None:
    """Close a handle this module handed out.

    NOT for the containment handle: closing that one IS the kill, and it is
    meant to happen when the launcher's process ends.
    """
    if handle:
        _k32.CloseHandle(handle)


#: The containment handle, held for the life of the process ON PURPOSE.
#:
#: This is the entire mechanism and it looks like a leak. Kill-on-close fires
#: when the LAST handle closes, so the handle has to outlive everything the
#: launcher starts; a local would be garbage collected, the job would close
#: while the command was still running, and the launcher would kill its own
#: pulse the moment it started it. It is released by the process ending, which
#: is the event the whole design is built on.
_held = None


def contain_this_process() -> Containment:
    """Put this process in a kill-on-close job. Never raises.

    Returns ``Containment(True, "")`` with the flags read back off the job, or
    ``Containment(False, reason)`` with the call and code that failed. The
    caller runs its command either way; what it must not do is stay quiet, and
    the record :meth:`Containment.as_record` produces is where the answer goes.

    Idempotent within a process: a second call returns the first answer rather
    than making a second job, because two jobs would mean two handles and the
    kill would wait for whichever closed last.
    """
    global _held
    if not _WINDOWS:
        return Containment(
            False,
            f"job objects are a Windows kernel mechanism and this host is "
            f"{sys.platform}; the pulse tree is not contained here",
            supported=False)
    if _held is not None:
        flags, why = _flags_or_none(_held)
        return Containment(True, why, flags)
    try:
        handle = create_kill_on_close_job()
    except OSError as exc:
        return Containment(False, str(exc))
    if not _k32.AssignProcessToJobObject(handle, _k32.GetCurrentProcess()):
        why = _why("AssignProcessToJobObject")
        _k32.CloseHandle(handle)
        return Containment(False, why)

    # READ THE MEMBERSHIP BACK. An assignment that returned success while
    # leaving the process outside the job would make every child escape, and
    # the launcher would report itself contained while nothing was. Law 25:
    # never take the call's word for the thing the call was for.
    inside = wintypes.BOOL(0)
    if not _k32.IsProcessInJob(_k32.GetCurrentProcess(), handle,
                               ctypes.byref(inside)):
        why = _why("IsProcessInJob")
        _k32.CloseHandle(handle)
        return Containment(False, why)
    if not inside.value:
        _k32.CloseHandle(handle)
        return Containment(
            False, "AssignProcessToJobObject reported success but this process "
                   "is not in the job, so nothing it starts would be contained")

    _held = handle
    flags, why = _flags_or_none(handle)
    return Containment(True, why, flags)
