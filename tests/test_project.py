import pytest

from alice_censor.project import (
    SCHEMA_VERSION,
    CensorLayer,
    ImageRecord,
    ImageStatus,
    ProjectState,
)


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


def test_a_project_from_a_newer_build_is_refused_not_silently_stripped(tmp_path):
    """from_dict drops fields it does not know and the app saves straight
    after almost every action, so opening one of these would rewrite it
    without whatever the newer version put there."""
    import json

    from alice_censor.project import ProjectTooNew

    p = tmp_path / "future.acproj.json"
    p.write_text(json.dumps({
        "schema_version": SCHEMA_VERSION + 1,
        "images": {},
        "something_new": {"kept": True},
    }), encoding="utf-8")

    with pytest.raises(ProjectTooNew, match="newer version"):
        ProjectState.load(p)

    assert "something_new" in json.loads(p.read_text(encoding="utf-8")), (
        "and refusing must leave the file exactly as it was"
    )


def test_a_project_from_this_build_or_older_opens_normally(tmp_path):
    import json

    for version in (SCHEMA_VERSION, SCHEMA_VERSION - 1):
        p = tmp_path / f"v{version}.acproj.json"
        p.write_text(json.dumps({"schema_version": version, "images": {}}), encoding="utf-8")
        assert ProjectState.load(p).schema_version == version
