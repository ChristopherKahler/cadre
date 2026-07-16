"""What a shell command DOES — resolved from the head of every segment it runs.

A mention is not an invocation. `grep slack_send_message src/` names a
forbidden verb; it does not perform it. Fork 015 knew this and accepted the
false positive anyway ("a shell is unparseable and send-capable, so it fails
closed"), which is right for `curl … chat.postMessage` and wrong for the
Member who greps for the pattern to VERIFY the lock, or escalates about it
with `firm escalation raise --body "…slack_send_message…"`. Both happened
(chief-of-staff, 2026-07-16), and the second one is ESC-021's inversion
rebuilt: the gate could not stop a send, but it stopped the report about it.

So a shell IS parseable into intent, far enough: not "what does this string
say" but "what programs does this string RUN". That is the head of each
segment, and it is decidable.

The asymmetry is deliberate and load-bearing:

- **Only the enumerated heads are exempt.** Everything else — every sender,
  every interpreter, every unknown — keeps failing closed exactly as before.
  This module can only ever make the gate quieter about `grep`; it can never
  teach it to allow `curl`. A new binary nobody listed here is denied.
- **Every segment must be an inspection.** `grep x | curl …` runs curl, so
  the pipeline is not an inspection. Unquoted operators start new segments;
  a `|` inside quotes is data and starts nothing (which is what keeps prose
  arguments — an escalation body, a `--text` — from tripping this).
- **A read-only head that can hand off control is not read-only.**
  `find . -exec curl …`, `awk 'BEGIN{system("curl …")}'`, `sed 's/x/y/e'`
  all run another program from inside an allowlisted head. Those escapes are
  enumerated below and disqualify the segment.
- **Unparseable is not inspectable.** Unbalanced quotes, a backtick
  substitution, a `PATH=`-shadowed head: no answer, so the answer is no.

The gate cannot import this module — it runs under the system `python3`,
where `firm` need not exist, and an ImportError must never disable a NEVER.
So `install_hooks` splices this source INTO the generated hook. That is why
this module is stdlib-only and imports nothing from `firm`: it is the one
copy of the logic, and `services/policy.ingest_denials` imports it directly.
Two hand-written copies would be ESC-021's offline replica — a model of the
gate that agrees with itself while the real gate does something else.
"""

from __future__ import annotations

import re
import shlex

# Heads that read, print, search, or navigate. None of them can send, and
# none can start another program without one of the EXEC_ESCAPES below.
# Add to this list only with that sentence in mind — this is the whole of
# what the gate stops reading arguments for.
READ_ONLY_HEADS = frozenset("""
grep egrep fgrep rg ag cat bat head tail less more sed awk gawk mawk
cut sort uniq wc nl ls find fd stat file tree echo printf true cd pwd :
""".split())

# Heads where the first argument decides whether the call reads or acts.
# `git log` reads; `git push` is a send. `firm escalation raise` records;
# `firm pulse` spawns Members and `firm notify` DMs the Board — neither is
# an inspection. Anything not listed here fails closed, including a verb
# added to these CLIs after this line was written.
SUBCOMMAND_HEADS = {
    "git": frozenset("log show diff status blame cat-file rev-parse ls-files".split()),
    "firm": frozenset("unit doc document escalation goal run doctor templates".split()),
    "base": frozenset(
        "learn recall decision rule project task milestone ast sync context".split()
    ),
}

# Routes that run another program from inside an allowlisted head. Scanned
# against every token of a segment: `find -exec`, awk's `system()`, a
# backtick or `$(` substitution. A token carrying one of these disqualifies
# its segment — `find . -exec curl … chat.postMessage \\;` is a send with a
# read-only head, and it must go on blocking.
EXEC_ESCAPES = ("-exec", "-execdir", "-ok", "-okdir", "--exec",
                "system(", "popen(", "exec(", "|&")

# GNU sed runs a shell for the `e` command and the `s///e` flag. Everything
# else sed does is text.
_SED_EXEC = re.compile(
    r"(?:^|[;{])\s*\d*\s*e(?:\s|$)"
    r"|[sy](?P<d>[^\w\s])(?:\\.|(?!(?P=d)).)*(?P=d)"
    r"(?:\\.|(?!(?P=d)).)*(?P=d)[a-zA-Z0-9]*e"
)

_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# Assignments that decide WHICH binary a head resolves to. `FIRM_ID=x firm …`
# is ordinary and fine; `PATH=/tmp/evil grep …` makes `grep` a lie.
_ENV_HIJACK = frozenset(
    "PATH LD_PRELOAD LD_LIBRARY_PATH LD_AUDIT BASH_ENV ENV SHELL IFS".split()
)

# Unquoted tokens that end one command and begin another.
_SEPARATORS = frozenset(["|", "||", "&&", ";", ";;", "&", "(", ")", "\n", "{", "}"])


def _lex(command: str) -> list[str] | None:
    """Shell tokens, or None when the string cannot be read as a command.

    `punctuation_chars` is what makes this a shell lexer rather than a
    splitter: it emits `|`, `&&`, `(` as their own tokens when they are
    unquoted, and leaves them inside the token when they are quoted. That
    single distinction is why `firm escalation raise --body "do x; then y"`
    is one command and `cat x; curl y` is two.
    """
    if "`" in command:
        return None          # a backtick substitution — no answer, so: no
    lex = shlex.shlex(command, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        return list(lex)
    except ValueError:
        return None          # unbalanced quotes — unparseable, fail closed


def _is_redirect(token: str) -> bool:
    stripped = token.lstrip("0123456789")
    return bool(stripped) and set(stripped) <= {"<", ">", "&"} and (
        ">" in stripped or "<" in stripped
    )


def _segments(tokens: list[str]) -> list[list[str]]:
    """Tokens split into the commands they run; redirect targets dropped."""
    segments: list[list[str]] = []
    current: list[str] = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if _is_redirect(token):
            skip_next = True          # the next token names a file, not a program
            continue
        if token in _SEPARATORS:
            if current:
                segments.append(current)
            current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments


def _segment_head(segment: list[str]) -> tuple[str, list[str]] | None:
    """(head, arguments) with env assignments stripped. None = not resolvable."""
    for index, token in enumerate(segment):
        if _ENV_ASSIGN.match(token):
            if token.split("=", 1)[0] in _ENV_HIJACK:
                return None
            continue
        return token.rpartition("/")[2], segment[index + 1:]
    return None


def _segment_is_inspection(segment: list[str]) -> bool:
    resolved = _segment_head(segment)
    if resolved is None:
        return False
    head, arguments = resolved
    if any(escape in token for token in segment for escape in EXEC_ESCAPES):
        return False
    if head == "sed" and any(_SED_EXEC.search(token) for token in arguments):
        return False
    if head in SUBCOMMAND_HEADS:
        subcommand = next((t for t in arguments if not t.startswith("-")), "")
        return subcommand in SUBCOMMAND_HEADS[head]
    return head in READ_ONLY_HEADS


def command_heads(command: str) -> list[str]:
    """The program each segment runs. Empty when the string is unparseable.

    Stamped onto every denial receipt so the Board (and `ingest_denials`)
    can read what the gate actually resolved, rather than re-deriving it.
    """
    tokens = _lex(command)
    if tokens is None:
        return []
    heads = []
    for segment in _segments(tokens):
        resolved = _segment_head(segment)
        if resolved is not None:
            heads.append(resolved[0])
    return heads


def is_inspection(command: str) -> bool:
    """True when every segment only reads — so the arguments are data.

    False for everything else, including everything unknown. A caller may
    use this to stop matching send-patterns against a command's arguments;
    it must never use it to allow a command it would otherwise deny.
    """
    tokens = _lex(command)
    if tokens is None:
        return False
    segments = _segments(tokens)
    if not segments:
        return False              # nothing to run is not an inspection
    return all(_segment_is_inspection(segment) for segment in segments)
