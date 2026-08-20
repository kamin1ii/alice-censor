"""Post-repack integrity verification.

Exists specifically because of a real, confirmed alice-tools bug
(https://github.com/nunuhara/alice-tools/issues/92): `ar pack` could exit
0 (success) while silently corrupting a filename containing a particular
character to "?", leaving it inaccessible under its expected name
and the game hardlocks trying to load it. The fix (nunuhara/alice-tools#93)
only applies going forward to *freshly generated* manifests; a manifest
produced by an older, unpatched alice.exe can still carry the bad
character into a repack done with an up-to-date one. A clean exit code
from `ar pack` is therefore not actually proof the archive is correct.
This reads the freshly-packed archive back with `ar list` and confirms
every expected file is really there under its expected name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .alice_tools import AliceTools
from .manifest import Manifest


@dataclass
class VerifyResult:
    ok: bool
    expected_count: int
    actual_count: int
    missing: list[str] = field(default_factory=list)  # expected, not found in the packed archive
    unexpected: list[str] = field(default_factory=list)  # found in archive, not expected
    suspicious: list[str] = field(default_factory=list)  # actual entries containing "?"


def _expected_archive_name(entry_path: str, dst_format: str | None) -> str:
    """What a manifest row's file is named once packed. Same path, but
    with its extension replaced by dst_format if one was given (mirrors
    alicepack_to_file_list's replace_extension in alice-tools' pack.c)."""
    if not dst_format:
        return entry_path
    stem = entry_path.rsplit(".", 1)[0] if "." in entry_path else entry_path
    return f"{stem}.{dst_format.lower()}"


def _parse_list_output(text: str) -> list[str]:
    """Parse `ar list`'s "<index>: <name>" lines into just the names."""
    names = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        _, sep, name = line.partition(": ")
        if sep and name:
            names.append(name)
    return names


def verify_archive_contents(
    tools: AliceTools, archive_path: str | Path, manifest: Manifest
) -> VerifyResult:
    """Confirm every file `manifest` says should be in `archive_path` is
    actually there under its expected packed name. Raises AliceToolsError
    if `ar list` itself fails, say because the archive is unreadable. That
    is at least as serious as a mismatch and shouldn't be swallowed."""
    expected = {
        _expected_archive_name(entry.path, entry.dst_format) for entry in manifest.entries
    }
    result = tools.list_archive(archive_path)
    actual = set(_parse_list_output(result.stdout))

    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    suspicious = sorted(name for name in actual if "?" in name)

    return VerifyResult(
        ok=not missing and not suspicious,
        expected_count=len(expected),
        actual_count=len(actual),
        missing=missing,
        unexpected=unexpected,
        suspicious=suspicious,
    )
