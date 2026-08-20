"""Parser for alice-tools ALICEPACK manifest files.

Format, confirmed by reading alice-tools' own source
(src/cli/ar_extract.c:write_manifest_iter / command_ar_extract, and
src/core/ar/manifest_parser.c):

    #ALICEPACK --src-dir=<out> --cache-dir=<out>/alice-tools-cache
    <archive-file-name>.afa            (or .ald)
    path/to/file1.png,QNT
    path/to/file2.png,QNT
    "path with, comma.png",QNT
    ...

- Line 1 is the manifest header, a magic token ("#ALICEPACK" is the only
  format alice-tools' extract command writes) followed by space-separated
  ``--option=value`` flags. ``--src-dir`` and ``--cache-dir`` are written
  whenever the archive was extracted with ``-o`` and caching was enabled.
- Line 2 is the original archive path (as passed to ``ar extract``). Its
  extension (.afa / .ald) is the only reliable signal for which archive
  format this manifest describes.
- Every following non-blank line is a row, holding the in-archive path, a comma, the
  format the file should be repacked as (e.g. QNT, PNG), and optionally a
  third column (used for DCF base-CG references). Fields are comma
  separated; a field containing a comma, space, or other special character
  is wrapped in double quotes with backslash escaping (``\\``, ``\"``,
  ``\\n``, ``\\r``), mirroring escape_string_noconv() in alice-tools.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class ManifestFormat(Enum):
    AFA = "afa"
    ALD = "ald"


class ManifestError(ValueError):
    """Raised when a manifest file can't be parsed."""


@dataclass
class ManifestEntry:
    """One row of the manifest, a single file to be (re)packed."""

    path: str
    dst_format: str | None = None
    extra: str | None = None
    line_no: int = 0


@dataclass
class ManifestOptions:
    src_dir: str | None = None
    cache_dir: str | None = None
    backslash: bool = False
    afa_version: int | None = None


@dataclass
class Manifest:
    manifest_path: Path
    magic: str
    options: ManifestOptions
    archive_line: str
    archive_format: ManifestFormat
    entries: list[ManifestEntry] = field(default_factory=list)

    @property
    def archive_name(self) -> str:
        """The archive filename as written in the manifest, stripped of any
        leading './' or '.\\' the extractor may have carried over."""
        name = self.archive_line.strip()
        return re.sub(r"^\.[\\/]", "", name)

    def resolved_src_dir(self) -> Path:
        """Directory that manifest paths are relative to, for reading files
        off disk. Falls back to the manifest's own directory if the manifest
        didn't record --src-dir (e.g. it was extracted without -o)."""
        base = self.manifest_path.parent
        if self.options.src_dir:
            p = Path(self.options.src_dir)
            return p if p.is_absolute() else base / p
        return base

    def resolved_cache_dir(self) -> Path | None:
        if not self.options.cache_dir:
            return None
        base = self.manifest_path.parent
        p = Path(self.options.cache_dir)
        return p if p.is_absolute() else base / p

    def resolved_archive_path(self) -> Path:
        """Where `ar pack` will write (overwrite) the output archive.

        alice-tools writes the archive line verbatim from whatever was
        passed to `ar extract`; if it's relative, it's resolved relative to
        wherever `alice ar pack` is invoked from. We always invoke `pack`
        with cwd set to the manifest's own directory (see AliceTools.pack),
        so that's the base used here too.
        """
        p = Path(self.archive_name)
        return p if p.is_absolute() else self.manifest_path.parent / p

    def paths(self) -> list[str]:
        return [e.path for e in self.entries]


# Escape sequences the manifest lexer recognizes inside a quoted field, in
# manifest_lexer.l's <str> state. \n \t \r \b \f map to control chars, and
# any other \X, notably \" and \\, maps to the literal char X.
_UNESCAPE_MAP = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f"}

_UNQUOTED_OPTION_BOUNDARY_RE = re.compile(r" (?=--)")


def _scan_quoted(text: str, start: int) -> tuple[str, int]:
    """Read one double-quoted run out of `text`.

    `start` is the index of the opening quote. Returns the unescaped
    contents and the index just past the closing quote, or just past the
    end if the run is unterminated, which alice-tools itself tolerates.

    Every quoted thing in a manifest obeys these same escape rules, so
    every quoted thing is read through here. Header option, data row and
    bare field each used to carry a private copy of this loop, which meant
    a fix to the escaping was silently a fix to one third of the parser.
    """
    i, n = start + 1, len(text)
    buf: list[str] = []
    while i < n and text[i] != '"':
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            buf.append(_UNESCAPE_MAP.get(text[i + 1], text[i + 1]))
            i += 2
            continue
        buf.append(ch)
        i += 1
    return "".join(buf), i + 1


def split_header_tokens(line: str) -> list[str]:
    """Split a manifest header line into its magic token followed by
    option tokens.

    Handles two cases.
    - Our own sanitized output (see format_manifest_field and
      alice_tools._sanitize_manifest_header), whose option values are
      double-quoted with backslash escaping and parse exactly like a
      quoted row field.
    - alice-tools' own raw, never-quoted --src-dir=/--cache-dir= values,
      which can themselves contain spaces if the project lives under a
      path with spaces in it (extremely common on Windows). Since there's
      no quoting to delimit them, we fall back to the heuristic that a new
      option always starts with a bare "--", so an unquoted value keeps any
      internal spaces as long as none of them are immediately followed by
      "--".
    """
    body = line.rstrip("\r\n")
    if not body:
        return []
    split_at = body.find(" ")
    if split_at == -1:
        return [body]

    tokens = [body[:split_at]]
    i, n = split_at, len(body)
    while i < n:
        while i < n and body[i] == " ":
            i += 1
        if i >= n:
            break
        if body[i] == '"':
            value, i = _scan_quoted(body, i)
            tokens.append(value)
        else:
            m = _UNQUOTED_OPTION_BOUNDARY_RE.search(body, i)
            end = m.start() if m else n
            tokens.append(body[i:end])
            i = end
    return tokens


# Characters that force a manifest field to be double-quoted, matching
# escape_string_noconv() in alice-tools (src/core/util.c) plus the
# additional characters write_manifest_iter() checks for (comma, space,
# tab, \n, \r, \b, \f).
_QUOTE_TRIGGER_CHARS = set(" \t\r\n\b\f,\"\\")
_ESCAPE_MAP = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r"}


def format_manifest_field(text: str) -> str:
    """Quote+escape a manifest field if needed so alice-tools' own lexer
    (which splits unquoted tokens on space/tab/CR/LF/comma/quote) can parse
    it back. Fields without any special characters are left bare."""
    if not any(c in _QUOTE_TRIGGER_CHARS for c in text):
        return text
    escaped = "".join(_ESCAPE_MAP.get(c, c) for c in text)
    return f'"{escaped}"'


def _parse_header(line: str) -> tuple[str, ManifestOptions]:
    parts = split_header_tokens(line)
    magic = parts[0].strip()
    options = ManifestOptions()
    for tok in parts[1:]:
        tok = tok.strip()
        if not tok:
            continue
        if tok == "--backslash":
            options.backslash = True
        elif tok.startswith("--afa-version="):
            try:
                options.afa_version = int(tok.split("=", 1)[1])
            except ValueError:
                pass
        elif tok.startswith("--src-dir="):
            options.src_dir = tok.split("=", 1)[1]
        elif tok.startswith("--cache-dir="):
            options.cache_dir = tok.split("=", 1)[1]
        # Unrecognized options are ignored, mirroring alice-tools' own
        # behavior (it warns and continues rather than failing).
    return magic, options


def _parse_row(line: str) -> list[str]:
    """Split one manifest data row into its comma-separated fields,
    respecting double-quoted fields with backslash escaping."""
    fields: list[str] = []
    i, n = 0, len(line)
    while i < n:
        if line[i] == '"':
            value, i = _scan_quoted(line, i)
            fields.append(value)
            # Anything between the closing quote and the next comma is not
            # part of any field, so skip past it rather than treating it
            # as the start of one.
            while i < n and line[i] != ",":
                i += 1
            if i < n and line[i] == ",":
                i += 1
        else:
            j = line.find(",", i)
            if j == -1:
                fields.append(line[i:])
                i = n
            else:
                fields.append(line[i:j])
                i = j + 1
    return fields


def detect_format(archive_line: str) -> ManifestFormat:
    ext = Path(archive_line.strip()).suffix.lower()
    if ext == ".afa":
        return ManifestFormat.AFA
    if ext == ".ald":
        return ManifestFormat.ALD
    raise ManifestError(
        f"Can't detect archive format (expected .afa or .ald) from line: {archive_line!r}"
    )


def _unquote_field(text: str) -> str:
    """Inverse of format_manifest_field. Strips surrounding quotes and
    unescape, if the field is quoted. Unquoted fields are returned as-is."""
    text = text.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return _scan_quoted(text, 0)[0]
    return text


def parse_manifest(path: str | Path) -> Manifest:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as e:
        raise ManifestError(f"{path}: not valid UTF-8: {e}") from e

    lines = text.splitlines()
    if len(lines) < 2:
        raise ManifestError(f"{path}: manifest must have at least a header and archive line")

    magic, options = _parse_header(lines[0])
    if magic.lower() != "#alicepack":
        raise ManifestError(
            f"{path}: unsupported manifest type {magic!r} (only #ALICEPACK is supported)"
        )

    archive_line = _unquote_field(lines[1])
    if not archive_line:
        raise ManifestError(f"{path}: line 2 (archive name) is empty")
    archive_format = detect_format(archive_line)

    entries: list[ManifestEntry] = []
    for line_no, raw_line in enumerate(lines[2:], start=3):
        line = raw_line.rstrip("\r\n")
        if not line.strip():
            continue
        fields = _parse_row(line)
        if not fields or not fields[0]:
            continue
        if len(fields) > 3:
            raise ManifestError(f"{path}:{line_no}: too many columns ({len(fields)})")
        entries.append(
            ManifestEntry(
                path=fields[0],
                dst_format=fields[1] if len(fields) >= 2 and fields[1] else None,
                extra=fields[2] if len(fields) >= 3 and fields[2] else None,
                line_no=line_no,
            )
        )

    return Manifest(
        manifest_path=path,
        magic=magic,
        options=options,
        archive_line=archive_line,
        archive_format=archive_format,
        entries=entries,
    )


def write_manifest(
    manifest: Manifest,
    path: str | Path,
    *,
    src_dir: str | Path,
    cache_dir: str | Path | None = None,
) -> None:
    """Write a fresh ALICEPACK manifest re-using `manifest`'s archive line
    and entries but pointing --src-dir/--cache-dir somewhere else.

    This is how the export step hands `ar pack` a folder of *rendered*
    (censored) images instead of the raw extracted ones. Same file list
    and output archive, different source directory. The archive line is always
    written as manifest.resolved_archive_path() (absolute), since this
    manifest lives in a different directory than the original and a
    relative archive line would otherwise resolve against the wrong base.
    """
    path = Path(path)
    # Quote the WHOLE "--name=value" token, not just the value. The parser
    # (split_header_tokens) only recognizes a token as quoted if the quote
    # is its very first character, matching how alice_tools._sanitize_manifest_header
    # already does this for the same reason.
    header_tokens = ["#ALICEPACK", format_manifest_field(f"--src-dir={src_dir}")]
    if cache_dir:
        header_tokens.append(format_manifest_field(f"--cache-dir={cache_dir}"))
    lines = [" ".join(header_tokens)]
    lines.append(format_manifest_field(str(manifest.resolved_archive_path())))
    for entry in manifest.entries:
        row = format_manifest_field(entry.path)
        if entry.dst_format:
            row += "," + format_manifest_field(entry.dst_format)
            if entry.extra:
                row += "," + format_manifest_field(entry.extra)
        lines.append(row)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
