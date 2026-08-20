from alice_censor.project import CensorLayer, ImageRecord, ImageStatus, ProjectState


def test_round_trip_save_and_load(tmp_path):
    state = ProjectState(
        archive_path="C:/game/Rance01CG.afa",
        manifest_path="C:/game/out/manifest.txt",
        alice_exe_path="C:/tools/alice.exe",
        extract_dir="C:/game/out",
        output_dir="C:/game/out/censored_out",
        sticker_dir="C:/game/out/stickers",
        archive_format="afa",
    )
    state.images["イベント／シィルH01.png"] = ImageRecord(
        status=ImageStatus.FLAGGED,
        group_key="イベント/シィル",
        layers=[
            CensorLayer(id="abc123", type="solid", rect=(0.1, 0.2, 0.3, 0.4),
                        params={"color": "#000000"})
        ],
    )

    path = tmp_path / "project.acproj.json"
    state.save(path)

    loaded = ProjectState.load(path)
    assert loaded.archive_path == state.archive_path
    assert loaded.archive_format == "afa"
    rec = loaded.images["イベント／シィルH01.png"]
    assert rec.status == ImageStatus.FLAGGED
    assert rec.effective_group == "イベント/シィル"
    assert len(rec.layers) == 1
    assert rec.layers[0].type == "solid"
    assert rec.layers[0].rect == (0.1, 0.2, 0.3, 0.4)


def test_group_override_wins_over_group_key():
    rec = ImageRecord(group_key="suggested", group_override="manual")
    assert rec.effective_group == "manual"


def test_sync_with_paths_adds_new_and_reports_missing():
    state = ProjectState()
    state.images["a.png"] = ImageRecord(status=ImageStatus.CLEAN)
    state.images["b.png"] = ImageRecord(status=ImageStatus.FLAGGED)

    new_paths, missing = state.sync_with_paths(["a.png", "c.png"], group_keys={"c.png": "grp1"})

    assert new_paths == ["c.png"]
    assert missing == ["b.png"]
    # existing status preserved, not reset
    assert state.images["a.png"].status == ImageStatus.CLEAN
    # missing record kept (not deleted) so review work isn't lost
    assert "b.png" in state.images
    assert state.images["c.png"].group_key == "grp1"


def test_save_without_path_raises():
    state = ProjectState()
    try:
        state.save()
        assert False, "expected ValueError"
    except ValueError:
        pass
