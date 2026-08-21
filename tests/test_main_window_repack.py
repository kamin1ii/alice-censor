from pathlib import Path

from PIL import Image
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QMessageBox

from alice_censor.gui.main_window import MainWindow
from alice_censor.manifest import parse_manifest
from alice_censor.project import CensorLayer, ImageRecord, LayerType, ProjectState
from alice_censor.scanning import scan_and_sync
from alice_censor.session import OpenProject


class FakeAliceTools:
    """Stands in for AliceTools.repack/list_archive so this test doesn't
    need a real alice.exe. Records what manifest it was asked to pack
    instead of shelling out, and answers list_archive (used by the
    post-repack verification stage) consistently with what was packed."""

    def __init__(self):
        self.repack_calls: list = []
        self.clear_cache_flags: list[bool] = []
        self._packed_manifest = None

    def check_available(self):
        pass

    def check_supported(self):
        # A real one probes `ar extract --help` for the manifest flags. The
        # fake is by definition a build that has them.
        pass

    def repack(self, manifest, *, backup_original=True, clear_cache=True,
               extra_args=None, on_output=None):
        # Recorded so a test can assert the export path does not wipe the
        # cache it just curated.
        self.clear_cache_flags.append(clear_cache)
        self.repack_calls.append(manifest)
        self._packed_manifest = manifest
        archive_path = manifest.resolved_archive_path()
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        archive_path.write_bytes(b"fake repacked archive")

        class Result:
            returncode = 0

        return Result()

    def list_archive(self, archive_path):
        from alice_censor.verify import _expected_archive_name

        class Result:
            pass

        result = Result()
        lines = [
            f"{i}: {_expected_archive_name(entry.path, entry.dst_format)}"
            for i, entry in enumerate(self._packed_manifest.entries)
        ]
        result.stdout = "\n".join(lines) + "\n"
        return result


def _pump_until(condition, timeout_s=5.0):
    import time

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        QCoreApplication.processEvents()
        if condition():
            return True
    return False


def test_run_repack_exports_then_repacks_with_export_manifest(qapp, tmp_path, monkeypatch):
    # _on_repack_done shows a modal QMessageBox summarizing where the
    # archive went -- desirable in the real app (directly answers "where
    # did the file go"), but it would block this test's event loop
    # waiting for a click that will never come.
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    Image.new("RGB", (40, 40), (255, 255, 255)).save(out_dir / "a.png", "PNG")

    archive_path = tmp_path / "archive.afa"
    archive_path.write_bytes(b"original archive bytes")

    # Archive line written as an absolute path, matching what
    # AliceTools.extract() actually produces for a real project (see its
    # docstring) -- avoids the archive line resolving relative to
    # manifest_path.parent, which isn't where this test's archive lives.
    manifest_path = out_dir / "manifest.txt"
    manifest_path.write_text(f"#ALICEPACK\n{archive_path.resolve()}\na.png,qnt\n", encoding="utf-8")
    manifest = parse_manifest(manifest_path)

    project = ProjectState(
        archive_path=str(archive_path.resolve()),
        manifest_path=str(manifest_path.resolve()),
        alice_exe_path="dummy.exe",
        extract_dir=str(manifest.resolved_src_dir()),
        output_dir=str(tmp_path / "censored_out"),
        archive_format=manifest.archive_format.value,
    )
    project.images["a.png"] = ImageRecord(
        layers=[CensorLayer(id="l1", type=LayerType.SOLID, rect=(0, 0, 1, 1), params={"color": "#FF0000"})]
    )
    scan_and_sync(project, manifest)
    project.save(manifest_path.with_suffix(".acproj.json"))

    window = MainWindow()
    fake_tools = FakeAliceTools()
    window.session = OpenProject(project, manifest, fake_tools)
    window._refresh_summary()

    window.run_repack()

    ok = _pump_until(lambda: len(fake_tools.repack_calls) == 1 and window._worker is None)
    assert ok, "repack chain did not complete in time"

    # the export step should have rendered the censored image...
    exported = tmp_path / "censored_out" / "a.png"
    with Image.open(exported) as im:
        assert im.getpixel((5, 5))[:3] == (255, 0, 0)

    # ...and repack() must have been called with a manifest pointing at the
    # EXPORT directory, not the original raw-extraction manifest.
    assert fake_tools.clear_cache_flags == [False], (
        "the export curates the pack cache, so repack must not wipe it"
    )
    used_manifest = fake_tools.repack_calls[0]
    assert used_manifest.resolved_src_dir() == tmp_path / "censored_out"
    assert used_manifest is not window.session.manifest

    # the fake "repack" wrote to the resolved archive path -- confirm it's
    # the same archive the project was configured with.
    assert used_manifest.resolved_archive_path() == archive_path.resolve()
    assert archive_path.read_bytes() == b"fake repacked archive"

    assert window.repack_button.isEnabled()


def test_run_repack_warns_when_verification_finds_a_missing_file(qapp, tmp_path, monkeypatch):
    # Simulates the real alice-tools bug this verification step exists for
    # (github.com/nunuhara/alice-tools/issues/92): `ar pack` can exit 0
    # while a file silently didn't make it into the archive under its
    # expected name. A clean exit code must not be treated as proof the
    # archive is correct.
    warnings = []
    infos = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a)))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: infos.append(a)))

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    Image.new("RGB", (40, 40), (255, 255, 255)).save(out_dir / "a.png", "PNG")
    archive_path = tmp_path / "archive.afa"
    archive_path.write_bytes(b"original archive bytes")
    manifest_path = out_dir / "manifest.txt"
    manifest_path.write_text(f"#ALICEPACK\n{archive_path.resolve()}\na.png,qnt\n", encoding="utf-8")
    manifest = parse_manifest(manifest_path)

    project = ProjectState(
        archive_path=str(archive_path.resolve()),
        manifest_path=str(manifest_path.resolve()),
        alice_exe_path="dummy.exe",
        extract_dir=str(manifest.resolved_src_dir()),
        output_dir=str(tmp_path / "censored_out"),
        archive_format=manifest.archive_format.value,
    )
    scan_and_sync(project, manifest)
    project.save(manifest_path.with_suffix(".acproj.json"))

    window = MainWindow()
    fake_tools = FakeAliceTools()
    fake_tools.list_archive = lambda archive_path: type("R", (), {"stdout": ""})()  # nothing packed
    window.session = OpenProject(project, manifest, fake_tools)
    window._refresh_summary()

    window.run_repack()

    ok = _pump_until(lambda: (warnings or infos) and window._worker is None)
    assert ok, "repack chain did not complete in time"

    assert warnings, "verification failure must show a warning dialog, not a success one"
    assert not infos
    assert window.repack_button.isEnabled()  # button must not stay stuck disabled
