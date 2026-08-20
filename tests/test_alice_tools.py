from pathlib import Path

import pytest

from alice_censor.alice_tools import (
    AliceTools,
    AliceToolsError,
    clear_cache_dir,
    ensure_archive_backup,
)
from alice_censor.manifest import parse_manifest

FIXTURES = Path(__file__).parent / "fixtures"


class FakeStream:
    def __init__(self, lines: list[str]):
        self._lines = lines

    def __iter__(self):
        return iter(self._lines)

    def close(self):
        pass


class FakePopen:
    """Stand-in for subprocess.Popen that records the invocation and
    returns scripted output/return code, so we can test argument
    construction and error handling without shelling out to a real exe.

    Since production code now passes stderr=subprocess.STDOUT (see _run's
    docstring -- merging avoids a two-pipe deadlock that hung for real on a
    large repack), there's only one stream to fake: `scripted_stderr_text`
    is folded into the same stdout line stream a real merged pipe would
    produce, not read separately.
    """

    last_instance: "FakePopen | None" = None

    def __init__(self, args, cwd=None, stdout=None, stderr=None, stdin=None, text=None,
                 encoding=None, errors=None, bufsize=None, **kwargs):
        self.args = args
        self.cwd = cwd
        merged = list(FakePopen.scripted_stdout_lines)
        if FakePopen.scripted_stderr_text:
            merged += [line + "\n" for line in FakePopen.scripted_stderr_text.splitlines()]
        self.stdout = FakeStream(merged)
        FakePopen.last_instance = self

    def wait(self, timeout=None) -> int:
        return FakePopen.scripted_returncode

    scripted_stdout_lines: list[str] = []
    scripted_stderr_text: str = ""
    scripted_returncode: int = 0


@pytest.fixture
def fake_popen(monkeypatch):
    FakePopen.scripted_stdout_lines = ["extracting foo.png\n", "extracting bar.png\n"]
    FakePopen.scripted_stderr_text = ""
    FakePopen.scripted_returncode = 0
    monkeypatch.setattr("alice_censor.alice_tools.subprocess.Popen", FakePopen)
    return FakePopen


@pytest.fixture
def exe_path(tmp_path):
    p = tmp_path / "alice.exe"
    p.write_bytes(b"")
    return p


def test_check_available_raises_for_missing_exe(tmp_path):
    tools = AliceTools(tmp_path / "does_not_exist.exe")
    with pytest.raises(FileNotFoundError):
        tools.check_available()


def test_extract_builds_expected_args(fake_popen, exe_path, tmp_path):
    tools = AliceTools(exe_path)
    manifest_path = tmp_path / "manifest.txt"
    archive_path = tmp_path / "archive.afa"
    archive_path.write_bytes(b"")
    lines = []
    result = tools.extract(
        archive_path, tmp_path / "out", manifest_path=manifest_path, on_output=lines.append
    )
    assert result.returncode == 0
    args = FakePopen.last_instance.args
    assert args[0] == str(exe_path)
    assert "ar" in args and "extract" in args
    assert f"--manifest={manifest_path}" in args
    assert "--images-only" in args
    assert "--cache" in args
    # archive path and -o dir are resolved to absolute so later `pack` runs
    # (from a different cwd) don't misresolve the manifest's --src-dir /
    # --cache-dir / archive line -- see extract()'s docstring.
    assert args[-1] == str(archive_path.resolve())
    assert str((tmp_path / "out").resolve()) in args
    assert lines == ["extracting foo.png", "extracting bar.png"]


def test_pack_runs_with_cwd_at_manifest_dir(fake_popen, exe_path, tmp_path):
    tools = AliceTools(exe_path)
    manifest_path = tmp_path / "sub" / "manifest.txt"
    manifest_path.parent.mkdir()
    tools.pack(manifest_path)
    assert FakePopen.last_instance.cwd == str(manifest_path.parent)
    assert FakePopen.last_instance.args[-1] == "manifest.txt"


def test_nonzero_returncode_raises(fake_popen, exe_path, tmp_path):
    FakePopen.scripted_returncode = 1
    FakePopen.scripted_stderr_text = "boom"
    tools = AliceTools(exe_path)
    with pytest.raises(AliceToolsError) as excinfo:
        tools.pack(tmp_path / "manifest.txt")
    assert "boom" in str(excinfo.value)


def test_repack_clears_cache_dir_before_packing(fake_popen, exe_path, tmp_path):
    manifest_path = tmp_path / "manifest.txt"
    manifest_path.write_text(
        "#ALICEPACK --src-dir=out --cache-dir=out/alice-tools-cache\n"
        "archive.afa\nfoo.png,QNT\n",
        encoding="utf-8",
    )
    manifest = parse_manifest(manifest_path)
    cache_dir = manifest.resolved_cache_dir()
    cache_dir.mkdir(parents=True)
    (cache_dir / "stale.dat").write_bytes(b"old")

    tools = AliceTools(exe_path)
    tools.repack(manifest, backup_original=False)

    assert cache_dir.exists()
    assert list(cache_dir.iterdir()) == []
    assert FakePopen.last_instance.args[-1] == "manifest.txt"


def test_repack_backs_up_original_archive_once(fake_popen, exe_path, tmp_path):
    manifest_path = tmp_path / "manifest.txt"
    manifest_path.write_text(
        "#ALICEPACK\narchive.afa\nfoo.png,QNT\n", encoding="utf-8"
    )
    archive_path = tmp_path / "archive.afa"
    archive_path.write_bytes(b"original bytes")
    manifest = parse_manifest(manifest_path)

    tools = AliceTools(exe_path)
    lines = []
    tools.repack(manifest, on_output=lines.append)

    backup_path = tmp_path / "archive.afa.orig-backup"
    assert backup_path.read_bytes() == b"original bytes"
    assert any("backed up original archive" in line for line in lines)

    # Simulate the (destructive, in-place) repack having changed the
    # archive, then repack again -- the pristine backup must not be
    # clobbered by the now-modified file.
    archive_path.write_bytes(b"repacked bytes")
    tools.repack(manifest)
    assert backup_path.read_bytes() == b"original bytes"


def test_ensure_archive_backup_noop_when_source_missing(tmp_path):
    assert ensure_archive_backup(tmp_path / "nope.afa") is None


def test_repack_skips_cache_clear_when_no_cache_dir(fake_popen, exe_path, tmp_path):
    manifest_path = tmp_path / "manifest.txt"
    manifest_path.write_text("#ALICEPACK\narchive.afa\nfoo.png,QNT\n", encoding="utf-8")
    manifest = parse_manifest(manifest_path)
    assert manifest.resolved_cache_dir() is None

    tools = AliceTools(exe_path)
    tools.repack(manifest)  # should not raise
    assert FakePopen.last_instance.args[-1] == "manifest.txt"


def test_clear_cache_dir_removes_contents_not_dir_itself(tmp_path):
    cache_dir = tmp_path / "cache"
    (cache_dir / "nested").mkdir(parents=True)
    (cache_dir / "file.dat").write_bytes(b"x")
    (cache_dir / "nested" / "inner.dat").write_bytes(b"y")

    removed = clear_cache_dir(cache_dir)

    assert removed == 2
    assert cache_dir.exists()
    assert list(cache_dir.iterdir()) == []


def test_clear_cache_dir_missing_dir_is_noop(tmp_path):
    assert clear_cache_dir(tmp_path / "nope") == 0
