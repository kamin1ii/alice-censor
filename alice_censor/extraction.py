"""Extracts an archive to PNG files and a manifest, without alice-tools.

This is the other half of doing without alice.exe. The repack side rebuilds
an archive; this side takes one apart, which is what a new project does
first and the only reason a project needed alice.exe at all.

The manifest it writes is the same ALICEPACK file alice-tools writes, so a
project extracted here can still be packed by `ar pack` later if that is
what somebody wants. That includes copying two of its habits. An AJP is
recorded as qnt, because there is no AJP encoder anywhere and packing one
would have to convert it. A DCF is recorded with the name of the image it
is a difference from, which is the third field on those rows.

Every image is decoded on a worker thread, since Pillow and zlib both let go
of the interpreter lock. Reading from the archive stays on the calling
thread, because an AFA is one file handle being seeked. A DCF needs a second
entry, the image it differs from, so that one is read up front and handed
over with it.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from .ald import read_ald
from .formats import ajp, dcf, qnt
from .formats.afa import AfaError, AfaReader
from .manifest import Manifest, ManifestEntry, ManifestFormat, ManifestOptions, write_manifest
from .paths import resolve_fs_path

DEFAULT_WORKERS = max(2, min(8, os.cpu_count() or 4))

MAGIC_AFA = b"AFAH"

# What the manifest should say to pack an entry back. Anything this package
# cannot write becomes QNT, which is what alice-tools does too.
PACKABLE = "qnt"
DIFF_FORMAT = "dcf"


class ExtractionError(RuntimeError):
    """The archive cannot be opened or read at all."""


@dataclass
class ExtractResult:
    written: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    manifest_path: Path | None = None
    archive_format: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class _Item:
    """One archive entry on its way to becoming a PNG."""
    name: str
    path: str  # what the manifest calls it, the name with a .png extension
    data: bytes
    base: bytes | None = None  # the image a DCF differs from
    dst_format: str = PACKABLE
    extra: str | None = None


def extract_archive(
    archive_path: str | Path,
    output_dir: str | Path,
    manifest_path: str | Path,
    *,
    cache_dir: str | Path | None = None,
    archive_line: str | Path | None = None,
    on_progress=None,
    workers: int = DEFAULT_WORKERS,
) -> ExtractResult:
    """Write every image in `archive_path` into `output_dir` as a PNG.

    `archive_line` is what the manifest should name as the archive to pack
    back into, and defaults to the one being read. Worth setting when
    reading a pristine copy, since a manifest naming a .orig-backup cannot
    be parsed again.
    """
    archive_path = Path(archive_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    is_afa = _looks_like_afa(archive_path)
    result = ExtractResult(archive_format="afa" if is_afa else "ald")

    opener = _open_afa if is_afa else _open_ald
    with opener(archive_path) as source:
        items = _plan(source, result)
        _write_all(items, output_dir, result, on_progress=on_progress, workers=workers)

    result.manifest_path = Path(manifest_path)
    _write_manifest(Path(archive_line or archive_path), items, result,
                    cache_dir=cache_dir)
    return result


class _Source:
    """Whatever the archive is, reduced to names, bytes and a lookup.

    A DCF names the image it differs from without a directory on the front,
    so the lookup is by the last part of the name only, which is how
    alice-tools finds it too.
    """

    def __init__(self, names_and_readers):
        self.entries = list(names_and_readers)
        self._by_basename = {}
        for name, read in self.entries:
            self._by_basename.setdefault(_basename(name), read)

    def base_for(self, name: str) -> bytes | None:
        read = self._by_basename.get(_basename(name))
        return read() if read else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _AfaSource(_Source):
    def __init__(self, reader: AfaReader):
        self._reader = reader
        super().__init__((e.name, (lambda e=e: reader.read(e))) for e in reader.entries)

    def __exit__(self, *exc):
        self._reader.close()
        return False


def _looks_like_afa(path: Path) -> bool:
    """Which kind of archive this is, read from the file rather than its name.

    The name cannot be trusted here. The pristine copy a repack reads from
    is called something.afa.orig-backup, whose extension says nothing, and
    an ALD has no magic of its own to check for so it is the fallback.
    """
    try:
        with path.open("rb") as fp:
            return fp.read(4) == MAGIC_AFA
    except OSError as exc:
        raise ExtractionError(f"could not open {path.name}, {exc}") from exc


def _open_afa(path: Path) -> _Source:
    try:
        return _AfaSource(AfaReader(path))
    except AfaError as exc:
        raise ExtractionError(f"could not read {path.name}, {exc}") from exc


def _open_ald(path: Path) -> _Source:
    try:
        archive = read_ald(path)
    except (OSError, ValueError) as exc:
        raise ExtractionError(f"could not read {path.name}, {exc}") from exc
    return _Source((e.name, (lambda e=e: e.data)) for e in archive.entries)


def _basename(name: str) -> str:
    """The key a DCF's base image is looked up by.

    Directory and extension both come off, because the name a DCF gives is
    not the name the archive stores. Rance 03 asks for
    コスプレＨ０１.bmp and the entry holding it is コスプレＨ０１.qnt, so
    matching on anything more than the stem finds nothing.

    Note that a fullwidth solidus is a letter here rather than a separator,
    which is why only the plain kind is split on.
    """
    return name.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()


def _as_png(name: str) -> str:
    return name.rsplit(".", 1)[0] + ".png"


def _plan(source: _Source, result: ExtractResult) -> list[_Item]:
    """Read every entry and work out what the manifest should say about it.

    Done before any decoding so that all the archive reading happens on one
    thread, and so a DCF has the image it differs from in hand already.
    """
    items = []
    for name, read in source.entries:
        data = read()
        extension = name.rsplit(".", 1)[-1] if "." in name else ""
        item = _Item(name=name, path=_as_png(name), data=data)

        if dcf.is_dcf(data):
            item.dst_format = DIFF_FORMAT
            try:
                base = dcf.base_name(data)
                item.extra = _as_png(base)
                item.base = source.base_for(base)
            except dcf.DcfError as exc:
                result.errors[item.path] = str(exc)
                continue
        elif qnt.is_qnt(data):
            # Kept exactly as the archive spells it, which alice-tools does
            # too, so a manifest from either can be compared to the other.
            item.dst_format = extension or PACKABLE
        items.append(item)
    return items


def _write_all(items, output_dir: Path, result: ExtractResult, *, on_progress, workers):
    workers = max(1, workers)
    wave = max(1, workers * 2)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="extract") as pool:
        for start in range(0, len(items), wave):
            batch = items[start:start + wave]
            pending = {pool.submit(_write_one, item, output_dir): item for item in batch}
            for future, item in pending.items():
                if on_progress:
                    on_progress(item.path)
                try:
                    future.result()
                except Exception as exc:  # noqa: BLE001 one image must not stop the rest
                    result.errors[item.path] = f"{type(exc).__name__}, {exc}"
                    continue
                result.written.append(item.path)
                # The bytes have done their job and an archive holds
                # thousands of them.
                item.data = b""
                item.base = None


def _write_one(item: _Item, output_dir: Path) -> None:
    destination = resolve_fs_path(output_dir, item.path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _decode(item).save(destination, "PNG")


def _decode(item: _Item) -> Image.Image:
    if qnt.is_qnt(item.data):
        return qnt.decode(item.data)
    if ajp.is_ajp(item.data):
        return ajp.decode(item.data)
    if dcf.is_dcf(item.data):
        base = item.base
        return dcf.decode(item.data, lambda name: _decode_base(base))
    raise ExtractionError(f"{item.name} is not an image this build can read")


def _decode_base(data: bytes | None) -> Image.Image | None:
    if data is None:
        return None
    if qnt.is_qnt(data):
        return qnt.decode(data)
    if ajp.is_ajp(data):
        return ajp.decode(data)
    return None


def _write_manifest(archive_path: Path, items, result: ExtractResult, *, cache_dir) -> None:
    entries = [
        ManifestEntry(path=item.path, dst_format=item.dst_format, extra=item.extra)
        for item in items
        if item.path not in result.errors
    ]
    manifest = Manifest(
        manifest_path=Path(result.manifest_path),
        magic="#ALICEPACK",
        options=ManifestOptions(),
        archive_line=str(archive_path),
        archive_format=(
            ManifestFormat.AFA if result.archive_format == "afa" else ManifestFormat.ALD
        ),
        entries=entries,
    )
    write_manifest(
        manifest,
        result.manifest_path,
        src_dir=Path(result.manifest_path).parent,
        cache_dir=cache_dir,
    )
