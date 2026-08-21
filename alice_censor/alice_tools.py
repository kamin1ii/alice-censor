"""Wrapper around the alice-tools `alice` (ar) CLI.

Automates the manual loop this whole app exists to speed up.

    alice ar extract --manifest=manifest.txt -o out archive.afa
    <edit the extracted PNGs>
    alice ar pack manifest.txt

The one non-obvious step is the pack cache. alice-tools keeps converted
image data in the directory named by the manifest's --cache-dir option,
e.g. `out/alice-tools-cache`, keyed by file path, and packs a cache entry
verbatim whenever it is newer than its source rather than converting
again. A stale entry therefore means an edited image never reaches the
archive, silently.

Emptying the whole cache before every pack is the blunt way to avoid
that, and `clear_cache_dir` still does it for any caller that wants it.
The export path takes a sharper approach instead. It seeds the cache with
the original bytes of every file it did not touch and deletes the entry
for every file it re-rendered, then packs with `clear_cache=False`. Same
guarantee about edits landing, and untouched images go back into the
archive as the exact bytes they came out as rather than being decoded and
re-encoded. See export.render_export and AliceTools.repack.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .manifest import Manifest, format_manifest_field, split_header_tokens

OutputCallback = Callable[[str], None]


# Flags extract() passes that alice-tools gained after the 0.13.0 release.
# A build without them predates the manifest workflow this app is built on.
REQUIRED_EXTRACT_FLAGS = ("--manifest", "--cache", "--images-only")


class AliceToolsOutdated(RuntimeError):
    """The alice.exe found is too old to do what this app needs.

    Kept apart from AliceToolsError because it is not a failed command. It
    is a build that will accept the command and quietly do nothing useful.
    """


class AliceToolsError(RuntimeError):
    def __init__(self, message: str, *, args: Sequence[str], returncode: int | None,
                 stdout: str = ""):
        super().__init__(message)
        self.args_ = list(args)
        self.returncode = returncode
        self.stdout = stdout

    def __str__(self) -> str:
        base = super().__str__()
        tail = self.stdout.strip()
        if tail:
            return f"{base}\n[alice-tools output]\n{tail}"
        return base


@dataclass
class CommandResult:
    """One finished `alice` invocation.

    There is no stderr field because there is no stderr stream. `_run`
    merges the child's stderr into stdout on purpose, so `stdout` already
    holds everything alice-tools wrote, interleaved in the order it wrote
    it.
    """

    args: list[str]
    returncode: int
    stdout: str


def ensure_archive_backup(archive_path: str | Path) -> Path | None:
    """Copy `archive_path` to `<name>.orig-backup` if no backup exists yet.

    `ar pack` overwrites the archive named on the manifest's second line
    *in place*, with no undo. Since that's almost always the user's original
    game archive, we keep one pristine copy around automatically. Returns
    the backup path if a copy was made, or None if a backup already existed
    (or there was nothing to back up).
    """
    archive_path = Path(archive_path)
    if not archive_path.exists():
        return None
    backup_path = archive_path.with_name(archive_path.name + ".orig-backup")
    if backup_path.exists():
        return None
    shutil.copy2(archive_path, backup_path)
    return backup_path


def clear_cache_dir(cache_dir: str | Path) -> int:
    """Delete the *contents* of `cache_dir`, keeping the directory itself.

    Returns the number of top-level entries removed. If the directory
    doesn't exist yet, this is a no-op (nothing to invalidate).
    """
    cache_dir = Path(cache_dir)
    if not cache_dir.exists():
        return 0
    if not cache_dir.is_dir():
        raise NotADirectoryError(f"cache path is not a directory: {cache_dir}")

    removed = 0
    for child in cache_dir.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
        removed += 1
    return removed


def _sanitize_manifest_header(manifest_path: Path) -> bool:
    """Re-quote a manifest's header options and archive line if needed.

    alice-tools writes the --src-dir=/--cache-dir= option values and the
    archive-name line completely unquoted (see write_manifest_iter and
    command_ar_extract in ar_extract.c), unlike row fields, which it does
    quote when necessary. Its own lexer splits unquoted tokens on space, so
    any project living under a path containing a space (extremely common on
    Windows, think "Program Files" or "OneDrive - ...") produces a manifest
    that `ar pack` immediately fails to parse ("syntax error"). Quoting is
    syntactically valid in both positions (confirmed against
    manifest_parser.y, where both are plain STRING tokens), so we fix it up
    here rather than requiring users to avoid spaces in their folder names.

    Returns True if the file was rewritten.
    """
    text = manifest_path.read_text(encoding="utf-8-sig")
    lines = text.splitlines(keepends=True)
    if len(lines) < 2:
        return False

    def split_eol(line: str) -> tuple[str, str]:
        body = line.rstrip("\r\n")
        return body, line[len(body):]

    header_body, header_eol = split_eol(lines[0])
    tokens = split_header_tokens(header_body)
    new_header = " ".join(
        tok if i == 0 else format_manifest_field(tok) for i, tok in enumerate(tokens)
    )

    archive_body, archive_eol = split_eol(lines[1])
    new_archive = format_manifest_field(archive_body)

    if new_header == header_body and new_archive == archive_body:
        return False

    lines[0] = new_header + header_eol
    lines[1] = new_archive + archive_eol
    manifest_path.write_text("".join(lines), encoding="utf-8")
    return True


class AliceTools:
    """Thin, testable wrapper over the `alice` CLI executable."""

    def __init__(self, exe_path: str | Path):
        self.exe_path = Path(exe_path)
        # Cached because check_supported is called before every operation
        # and the answer cannot change while the app is running.
        self._supported: bool | None = None

    def check_available(self) -> None:
        if not self.exe_path.exists():
            raise FileNotFoundError(f"alice-tools executable not found: {self.exe_path}")

    def missing_extract_flags(self) -> list[str]:
        """Which flags this build's `ar extract` does not advertise.

        Asks the binary what it can do rather than what it is called.
        `alice --version` is no help, because the nightlies still report
        0.13.0, the same string the 2023 release reports, so the version
        cannot distinguish a build from three years ago from today's.

        Reads `ar extract --help`, which is fast and touches no archive.
        """
        result = self._run(["ar", "extract", "--help"], check=False)
        return [flag for flag in REQUIRED_EXTRACT_FLAGS if flag not in result.stdout]

    def check_supported(self) -> None:
        """Raise AliceToolsOutdated unless this build can do the job.

        Worth checking up front because the failure is otherwise silent
        and misleading. Handed `--manifest`, alice-tools 0.13.0 prints its
        usage text, extracts nothing, writes no manifest, and exits 0, so
        nothing downstream sees an error until the missing manifest fails
        to parse and the user is told their manifest is broken.
        """
        if self._supported:
            return
        self.check_available()
        missing = self.missing_extract_flags()
        if missing:
            raise AliceToolsOutdated(
                f"This alice.exe is too old for Alice Censor.\n\n{self.exe_path}\n\n"
                f"Its 'ar extract' has no {', '.join(missing)}, so it cannot write the "
                f"manifest everything here is built on. Given one anyway it prints its "
                f"usage text, extracts nothing and reports success.\n\n"
                f"Get a recent nightly from github.com/nunuhara/alice-tools and point "
                f"the project at that instead. Checking the version will not tell you "
                f"which you have, since the nightlies still report 0.13.0 themselves."
            )
        self._supported = True

    # ===== low-level

    def _run(
        self,
        args: Sequence[str],
        *,
        cwd: str | Path | None = None,
        check: bool = True,
        on_output: OutputCallback | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        self.check_available()
        full_args = [str(self.exe_path), *args]

        popen_kwargs: dict = {}
        if sys.platform == "win32":
            # Don't flash a console window when launched from the GUI.
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        proc = subprocess.Popen(
            full_args,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            # Merged into stdout rather than read as a second pipe.
            # alice-tools can write a meaningful amount of output to stderr
            # (warnings, per-file messages) while we're blocked reading
            # stdout in the loop below. If stderr's OS pipe buffer fills up
            # while nobody's draining it, the child blocks on that write and
            # we're stuck waiting for stdout lines that will never come, the
            # classic two-pipe subprocess deadlock. Confirmed the hard way,
            # hanging on a 1300+ file repack with both processes idle. A
            # single merged stream has no second pipe to fill.
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **popen_kwargs,
        )

        output_lines: list[str] = []

        assert proc.stdout is not None
        try:
            for line in proc.stdout:
                output_lines.append(line)
                if on_output:
                    on_output(line.rstrip("\n"))
        finally:
            proc.stdout.close()
        returncode = proc.wait(timeout=timeout)

        result = CommandResult(
            args=full_args, returncode=returncode, stdout="".join(output_lines)
        )

        if check and returncode != 0:
            raise AliceToolsError(
                f"alice-tools exited with code {returncode}: {' '.join(full_args)}",
                args=full_args,
                returncode=returncode,
                stdout=result.stdout,
            )
        return result

    # ===== commands

    def list_archive(self, archive_path: str | Path) -> CommandResult:
        return self._run(["ar", "list", str(archive_path)])

    def cg_convert(
        self, src: str | Path, dst: str | Path, *, to: str = "qnt"
    ) -> CommandResult:
        """Convert a single image, used to turn a rendered PNG back into
        the format an archive entry needs.

        Only qnt, png, webp and dcf can be produced. alice-tools has no
        encoder for ajp, pms, pcf or rou, so an edited image originally in
        one of those has to change format, and only qnt is lossless enough
        to be worth changing to.
        """
        return self._run(["cg", "convert", "-t", to, str(src), str(dst)])

    def extract(
        self,
        archive_path: str | Path,
        output_dir: str | Path,
        *,
        manifest_path: str | Path,
        images_only: bool = True,
        raw: bool = False,
        with_cache: bool = True,
        extra_args: Sequence[str] | None = None,
        on_output: OutputCallback | None = None,
    ) -> CommandResult:
        """Extract an archive to `output_dir`, writing an ALICEPACK
        manifest to `manifest_path` that can later be handed to `pack`.

        `archive_path` and `output_dir` are resolved to absolute paths
        before being passed to alice.exe. alice-tools writes whatever
        strings it's given for -o/the archive argument verbatim into the
        manifest's --src-dir/--cache-dir/archive line, resolved relative to
        wherever alice.exe is invoked from. If we passed relative paths
        here, later resolving them relative to the *manifest's* directory
        (which is what `pack`/Manifest.resolved_*() do, matching the manual
        cd-then-run-both-commands workflow) would silently break whenever
        this process's cwd isn't already that same directory. Absolute
        paths sidestep that mismatch entirely.
        """
        archive_path = Path(archive_path).resolve()
        output_dir = Path(output_dir).resolve()
        args = ["ar", "extract", "-o", str(output_dir), f"--manifest={manifest_path}"]
        if images_only:
            args.append("--images-only")
        if raw:
            args.append("--raw")
        if with_cache:
            args.append("--cache")
        if extra_args:
            args.extend(extra_args)
        args.append(str(archive_path))
        result = self._run(args, on_output=on_output)

        manifest_file = Path(manifest_path)
        if manifest_file.exists() and _sanitize_manifest_header(manifest_file) and on_output:
            on_output(
                "[alice-censor] quoted manifest header/archive line "
                "(path contains a space or other special character)"
            )
        return result

    def pack(
        self,
        manifest_path: str | Path,
        *,
        extra_args: Sequence[str] | None = None,
        on_output: OutputCallback | None = None,
    ) -> CommandResult:
        manifest_path = Path(manifest_path)
        args = ["ar", "pack"]
        if extra_args:
            args.extend(extra_args)
        args.append(str(manifest_path.name))
        # Run with cwd set to the manifest's own directory. --src-dir and
        # --cache-dir in the header, and the output archive path, are all
        # resolved relative to wherever alice.exe is invoked from, matching
        # the manual `./alice.exe ar pack manifest.txt` workflow this mirrors.
        return self._run(args, cwd=manifest_path.parent, on_output=on_output)

    def repack(
        self,
        manifest: Manifest,
        *,
        backup_original: bool = True,
        clear_cache: bool = True,
        extra_args: Sequence[str] | None = None,
        on_output: OutputCallback | None = None,
    ) -> CommandResult:
        """Repack the archive the manifest names, clearing its cache first.

        A cache entry newer than its source is packed verbatim with no
        conversion, so a stale one means an edited image never reaches the
        archive. Wiping the cache is the blunt way to guarantee that, and
        it is the default.

        `clear_cache=False` is for a caller that has curated the cache
        itself, deliberately seeding original bytes for untouched files and
        deleting the entry for every file it re-rendered. That preserves
        untouched images exactly rather than decoding and re-encoding them.
        See export.render_export, which is the only caller that does this.
        Passing False without doing that work silently drops edits.

        Before the first repack, a one-time backup of the original archive
        is made, since `ar pack` overwrites it in place, unless
        `backup_original` is False.
        """
        if backup_original:
            archive_path = manifest.resolved_archive_path()
            backup_path = ensure_archive_backup(archive_path)
            if backup_path and on_output:
                on_output(f"[alice-censor] backed up original archive to {backup_path}")

        cache_dir = manifest.resolved_cache_dir()
        if not clear_cache:
            if on_output:
                on_output("[alice-censor] using the curated pack cache, untouched files keep their original bytes")
        elif cache_dir is not None:
            removed = clear_cache_dir(cache_dir)
            if on_output:
                on_output(f"[alice-censor] cleared {removed} cache entr{'y' if removed == 1 else 'ies'} in {cache_dir}")
        elif on_output:
            on_output("[alice-censor] manifest has no --cache-dir; skipping cache clear")
        return self.pack(manifest.manifest_path, extra_args=extra_args, on_output=on_output)
