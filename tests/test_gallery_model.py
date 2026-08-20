from PySide6.QtCore import QCoreApplication, Qt

from alice_censor.gallery.gallery_model import (
    FolderRole,
    GalleryModel,
    GroupRole,
    HasLayersRole,
    PathRole,
    StatusRole,
)
from alice_censor.grouping import compute_groups
from alice_censor.manifest import parse_manifest
from alice_censor.project import CensorLayer, ImageStatus, ImageRecord, LayerType, ProjectState


def _make_project_and_manifest(tmp_path, names):
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    for name in names:
        (out_dir / name).write_bytes(b"fake png bytes")
    manifest_path = tmp_path / "manifest.txt"
    lines = ["#ALICEPACK --src-dir=out", "archive.afa"]
    lines += [f"{name},qnt" for name in names]
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    manifest = parse_manifest(manifest_path)
    project = ProjectState()
    for name in names:
        project.images[name] = ImageRecord()
    return project, manifest


def test_model_exposes_all_paths_initially(qapp, tmp_path):
    project, manifest = _make_project_and_manifest(
        tmp_path, ["a／b.png", "a／c.png", "other／d.png"]
    )
    groups = compute_groups(manifest)
    model = GalleryModel(project, manifest, groups, tmp_path / "thumbs")

    assert model.rowCount() == 3
    idx = model.index(0, 0)
    assert idx.data(PathRole) == "a／b.png"
    assert idx.data(FolderRole) == "a"
    assert idx.data(StatusRole) == ImageStatus.UNREVIEWED


def test_decoration_role_returns_placeholder_immediately(qapp, tmp_path):
    project, manifest = _make_project_and_manifest(tmp_path, ["a.png"])
    groups = compute_groups(manifest)
    model = GalleryModel(project, manifest, groups, tmp_path / "thumbs")

    from PySide6.QtCore import Qt

    pixmap = model.index(0, 0).data(Qt.DecorationRole)
    assert pixmap is not None
    assert not pixmap.isNull()


def test_decoration_role_eventually_loads_real_thumbnail(qapp, tmp_path):
    from PIL import Image
    from PySide6.QtCore import Qt

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    Image.new("RGB", (400, 300), (255, 0, 0)).save(out_dir / "a.png", "PNG")
    manifest_path = tmp_path / "manifest.txt"
    manifest_path.write_text(
        "#ALICEPACK --src-dir=out\narchive.afa\na.png,qnt\n", encoding="utf-8"
    )
    manifest = parse_manifest(manifest_path)
    project = ProjectState()
    project.images["a.png"] = ImageRecord()
    groups = compute_groups(manifest)
    model = GalleryModel(project, manifest, groups, tmp_path / "thumbs")

    placeholder = model.index(0, 0).data(Qt.DecorationRole)  # triggers async request

    model._pool.waitForDone(5000)  # the model owns its pool, see GalleryModel.shutdown
    for _ in range(50):
        QCoreApplication.processEvents()

    loaded = model.index(0, 0).data(Qt.DecorationRole)
    assert loaded.cacheKey() != placeholder.cacheKey()


def test_thumbnail_is_always_padded_to_a_fixed_uniform_size(qapp, tmp_path):
    # The grid uses setUniformItemSizes(True) for performance, which
    # *requires* every DecorationRole pixmap to actually be the same size.
    # Aspect-preserving thumbnailing alone produces a different size per
    # image (e.g. a very wide background image comes out ~192x24, not
    # 192x192) -- violating that assumption caused real, confirmed bugs:
    # stale grey rendering, wrong click hitboxes, and some images
    # rendering far larger on screen than others.
    from PIL import Image
    from PySide6.QtCore import Qt

    from alice_censor.gallery.thumbnail_cache import THUMBNAIL_SIZE

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    Image.new("RGB", (1600, 200), (255, 0, 0)).save(out_dir / "wide.png", "PNG")  # 8:1 aspect
    manifest_path = tmp_path / "manifest.txt"
    manifest_path.write_text(
        "#ALICEPACK --src-dir=out\narchive.afa\nwide.png,qnt\n", encoding="utf-8"
    )
    manifest = parse_manifest(manifest_path)
    project = ProjectState()
    project.images["wide.png"] = ImageRecord()
    groups = compute_groups(manifest)
    model = GalleryModel(project, manifest, groups, tmp_path / "thumbs")

    model.index(0, 0).data(Qt.DecorationRole)  # triggers async request
    model._pool.waitForDone(5000)  # the model owns its pool, see GalleryModel.shutdown
    for _ in range(50):
        QCoreApplication.processEvents()

    loaded = model.index(0, 0).data(Qt.DecorationRole)
    assert (loaded.width(), loaded.height()) == THUMBNAIL_SIZE


def test_filters_by_folder_status_and_group(qapp, tmp_path):
    project, manifest = _make_project_and_manifest(
        tmp_path, ["evt／sceneＨ０１.png", "evt／sceneＨ０２.png", "bg／room.png"]
    )
    project.images["evt／sceneＨ０１.png"].status = ImageStatus.FLAGGED
    groups = compute_groups(manifest)
    model = GalleryModel(project, manifest, groups, tmp_path / "thumbs")

    model.set_filters(folder="evt")
    assert model.rowCount() == 2
    assert model.index(0, 0).data(GroupRole) == "evt/scene"

    model.set_filters(folder=None, status=ImageStatus.FLAGGED)
    assert model.rowCount() == 1
    assert model.index(0, 0).data(PathRole) == "evt／sceneＨ０１.png"

    model.set_filters(group_substr="scene")
    assert model.rowCount() == 2

    model.set_filters(search="room")
    assert model.rowCount() == 1
    assert model.index(0, 0).data(PathRole) == "bg／room.png"


def test_filters_by_has_edits(qapp, tmp_path):
    project, manifest = _make_project_and_manifest(tmp_path, ["a.png", "b.png", "c.png"])
    project.images["a.png"].layers.append(
        CensorLayer(id="l1", type=LayerType.SOLID, rect=(0, 0, 1, 1), params={})
    )
    groups = compute_groups(manifest)
    model = GalleryModel(project, manifest, groups, tmp_path / "thumbs")

    model.set_filters(has_edits=True)
    assert model.rowCount() == 1
    assert model.index(0, 0).data(PathRole) == "a.png"

    model.set_filters(has_edits=False)
    assert sorted(model.index(i, 0).data(PathRole) for i in range(model.rowCount())) == [
        "b.png",
        "c.png",
    ]

    model.set_filters(has_edits=None)
    assert model.rowCount() == 3


def test_set_status_for_paths_updates_records_and_emits_dirty(qapp, tmp_path):
    project, manifest = _make_project_and_manifest(tmp_path, ["a.png", "b.png"])
    groups = compute_groups(manifest)
    model = GalleryModel(project, manifest, groups, tmp_path / "thumbs")

    received = []
    model.project_dirty.connect(lambda: received.append(True))

    model.set_status_for_paths(["a.png", "b.png"], ImageStatus.CLEAN)

    assert project.images["a.png"].status == ImageStatus.CLEAN
    assert project.images["b.png"].status == ImageStatus.CLEAN
    assert received == [True]


def test_unreviewed_thumbnail_is_undecorated(qapp, tmp_path):
    from PySide6.QtCore import Qt

    project, manifest = _make_project_and_manifest(tmp_path, ["a.png"])
    groups = compute_groups(manifest)
    model = GalleryModel(project, manifest, groups, tmp_path / "thumbs")

    pixmap = model.index(0, 0).data(Qt.DecorationRole)
    assert pixmap.toImage() == model._placeholder.toImage()


def test_has_layers_role_reflects_record_layers(qapp, tmp_path):
    project, manifest = _make_project_and_manifest(tmp_path, ["a.png"])
    groups = compute_groups(manifest)
    model = GalleryModel(project, manifest, groups, tmp_path / "thumbs")

    assert model.index(0, 0).data(HasLayersRole) is False

    project.images["a.png"].layers.append(
        CensorLayer(id="l1", type=LayerType.SOLID, rect=(0, 0, 1, 1), params={})
    )

    assert model.index(0, 0).data(HasLayersRole) is True


def test_has_layers_badge_shows_even_when_unreviewed(qapp, tmp_path):
    # A key case: status is UNREVIEWED (no status decoration at all), but
    # there's still a visible edit -- the badge must show independent of
    # status, since "reviewed" and "has edits" are different facts.
    from PySide6.QtCore import Qt

    project, manifest = _make_project_and_manifest(tmp_path, ["a.png"])
    groups = compute_groups(manifest)
    model = GalleryModel(project, manifest, groups, tmp_path / "thumbs")
    undecorated = model.index(0, 0).data(Qt.DecorationRole).toImage()

    project.images["a.png"].layers.append(
        CensorLayer(id="l1", type=LayerType.SOLID, rect=(0, 0, 1, 1), params={})
    )

    decorated = model.index(0, 0).data(Qt.DecorationRole).toImage()
    assert decorated != undecorated


def test_has_layers_and_status_badges_are_independent(qapp, tmp_path):
    from PySide6.QtCore import Qt

    project, manifest = _make_project_and_manifest(tmp_path, ["a.png", "b.png"])
    groups = compute_groups(manifest)
    model = GalleryModel(project, manifest, groups, tmp_path / "thumbs")

    # a.png: flagged, no layers yet
    model.set_status_for_paths(["a.png"], ImageStatus.FLAGGED)
    # b.png: unreviewed, but already has layers from a previous session
    project.images["b.png"].layers.append(
        CensorLayer(id="l1", type=LayerType.SOLID, rect=(0, 0, 1, 1), params={})
    )

    a_pixmap = model.index(0, 0).data(Qt.DecorationRole).toImage()
    b_pixmap = model.index(1, 0).data(Qt.DecorationRole).toImage()
    assert a_pixmap != b_pixmap  # different decorations for genuinely different states


def test_notify_layers_changed_emits_data_changed_for_decoration(qapp, tmp_path):
    from PySide6.QtCore import Qt

    project, manifest = _make_project_and_manifest(tmp_path, ["a.png"])
    groups = compute_groups(manifest)
    model = GalleryModel(project, manifest, groups, tmp_path / "thumbs")

    changed_roles = []
    model.dataChanged.connect(lambda _tl, _br, roles: changed_roles.append(list(roles)))

    project.images["a.png"].layers.append(
        CensorLayer(id="l1", type=LayerType.SOLID, rect=(0, 0, 1, 1), params={})
    )
    model.notify_layers_changed("a.png")

    assert changed_roles and Qt.DecorationRole in changed_roles[0]


def test_notify_layers_changed_updates_the_actual_thumbnail_pixels(qapp, tmp_path):
    # The bug this guards against: after saving an edit in the region
    # editor, the gallery grid kept showing the *original* image -- only
    # the status/has-edits badge updated, because the underlying cached
    # pixmap was never invalidated (a layer edit doesn't touch the source
    # file's mtime, so nothing else noticed it was stale).
    from PIL import Image
    from PySide6.QtCore import Qt

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    Image.new("RGB", (100, 100), (255, 255, 255)).save(out_dir / "a.png", "PNG")
    manifest_path = tmp_path / "manifest.txt"
    manifest_path.write_text("#ALICEPACK --src-dir=out\narchive.afa\na.png,qnt\n", encoding="utf-8")
    manifest = parse_manifest(manifest_path)
    project = ProjectState()
    project.images["a.png"] = ImageRecord()
    groups = compute_groups(manifest)
    model = GalleryModel(project, manifest, groups, tmp_path / "thumbs")

    def load_current_pixmap():
        _ = model.index(0, 0).data(Qt.DecorationRole)  # triggers async request if needed
        model._pool.waitForDone(5000)  # the model owns its pool, see GalleryModel.shutdown
        for _ in range(50):
            QCoreApplication.processEvents()
        return model.index(0, 0).data(Qt.DecorationRole)

    before = load_current_pixmap().toImage()

    project.images["a.png"].layers.append(
        CensorLayer(id="l1", type=LayerType.SOLID, rect=(0, 0, 1, 1), params={"color": "#00FF00"})
    )
    model.notify_layers_changed("a.png")

    after = load_current_pixmap().toImage()
    assert after != before

    # confirm it's not just the badge -- the underlying image content
    # (well away from the badge corners) is actually green now.
    center = after.pixelColor(after.width() // 2, after.height() // 2)
    assert center.green() > center.red() + 50


def test_flagged_thumbnail_gets_visible_border(qapp, tmp_path):
    from PySide6.QtCore import Qt

    project, manifest = _make_project_and_manifest(tmp_path, ["a.png"])
    groups = compute_groups(manifest)
    model = GalleryModel(project, manifest, groups, tmp_path / "thumbs")

    model.set_status_for_paths(["a.png"], ImageStatus.FLAGGED)
    pixmap = model.index(0, 0).data(Qt.DecorationRole)

    assert pixmap.toImage() != model._placeholder.toImage()
    # a border pixel near the top-left edge should now be the flagged-red color
    image = pixmap.toImage()
    from PySide6.QtGui import QColor

    border_pixel = QColor(image.pixel(2, image.height() // 2))
    assert (border_pixel.red(), border_pixel.green(), border_pixel.blue()) == (0xE5, 0x39, 0x35)


def test_different_statuses_get_different_decorations(qapp, tmp_path):
    from PySide6.QtCore import Qt

    project, manifest = _make_project_and_manifest(tmp_path, ["a.png", "b.png"])
    groups = compute_groups(manifest)
    model = GalleryModel(project, manifest, groups, tmp_path / "thumbs")

    model.set_status_for_paths(["a.png"], ImageStatus.FLAGGED)
    model.set_status_for_paths(["b.png"], ImageStatus.CLEAN)

    flagged_pixmap = model.index(0, 0).data(Qt.DecorationRole)
    clean_pixmap = model.index(1, 0).data(Qt.DecorationRole)
    assert flagged_pixmap.toImage() != clean_pixmap.toImage()


def test_folder_tree_counts_lists_unique_dir_prefixes(qapp, tmp_path):
    project, manifest = _make_project_and_manifest(
        tmp_path, ["evt／a.png", "evt／b.png", "bg／c.png", "root.png"]
    )
    groups = compute_groups(manifest)
    model = GalleryModel(project, manifest, groups, tmp_path / "thumbs")

    counts = model.folder_tree_counts()
    assert counts == {"": 1, "bg": 1, "evt": 2}


def test_folder_tree_counts_are_recursive_across_nesting_levels(qapp, tmp_path):
    # Real manifests commonly nest 2-3 levels deep, e.g.
    # "イベント／シィル／学生服／笑う.png" -- the top-level folder's count
    # should include everything nested under it, not just direct children.
    project, manifest = _make_project_and_manifest(
        tmp_path,
        ["evt／char／sceneＨ０１.png", "evt／char／scene笑う.png", "bg／c.png"],
    )
    groups = compute_groups(manifest)
    model = GalleryModel(project, manifest, groups, tmp_path / "thumbs")

    counts = model.folder_tree_counts()
    assert counts == {"": 0, "bg": 1, "evt": 2, "evt/char": 2}

    model.set_filters(folder="evt")
    assert model.rowCount() == 2  # both files nested under evt/char match

    model.set_filters(folder="evt/char")
    assert model.rowCount() == 2

    model.set_filters(folder="bg")
    assert model.rowCount() == 1


def test_folder_tree_counts_includes_singleton_folders(qapp, tmp_path):
    # Unlike the old flat dropdown, the tree doesn't need near-singleton
    # folders hidden -- they just tuck quietly under their parent node.
    project, manifest = _make_project_and_manifest(
        tmp_path, ["evt／a.png", "evt／b.png", "lonely／only.png"]
    )
    groups = compute_groups(manifest)
    model = GalleryModel(project, manifest, groups, tmp_path / "thumbs")

    assert model.folder_tree_counts()["lonely"] == 1


def test_folder_tree_counts_root_bucket_present_even_when_zero(qapp, tmp_path):
    project, manifest = _make_project_and_manifest(tmp_path, ["evt／a.png", "evt／b.png"])
    groups = compute_groups(manifest)
    model = GalleryModel(project, manifest, groups, tmp_path / "thumbs")

    assert model.folder_tree_counts()[""] == 0


# ===== removing edits in bulk


def _layer(color="#000000"):
    return CensorLayer(id="l1", type=LayerType.SOLID, rect=(0, 0, 0.5, 0.5),
                       params={"color": color})


def _model_with_edits(tmp_path, edited=("a.png",)):
    project, manifest = _make_project_and_manifest(tmp_path, ["a.png", "b.png", "c.png"])
    for name in edited:
        project.images[name].layers = [_layer(), _layer("#FF0000")]
    groups = compute_groups(manifest)
    return project, GalleryModel(project, manifest, groups, tmp_path / "thumbs")


def test_paths_with_layers_reports_only_the_edited_ones(qapp, tmp_path):
    project, model = _model_with_edits(tmp_path, edited=("a.png", "c.png"))
    assert model.paths_with_layers(["a.png", "b.png", "c.png"]) == ["a.png", "c.png"]


def test_clear_layers_removes_them_and_reports_how_many_images_changed(qapp, tmp_path):
    project, model = _model_with_edits(tmp_path, edited=("a.png", "c.png"))

    cleared = model.clear_layers_for_paths(["a.png", "b.png", "c.png"])

    assert cleared == 2, "b.png had nothing to remove and must not be counted"
    assert project.images["a.png"].layers == []
    assert project.images["c.png"].layers == []


def test_clear_layers_leaves_review_status_alone(qapp, tmp_path):
    project, model = _model_with_edits(tmp_path)
    project.images["a.png"].status = ImageStatus.FLAGGED

    model.clear_layers_for_paths(["a.png"])

    assert project.images["a.png"].status == ImageStatus.FLAGGED


def test_clear_layers_marks_the_project_dirty_so_it_gets_saved(qapp, tmp_path):
    project, model = _model_with_edits(tmp_path)
    seen = []
    model.project_dirty.connect(lambda: seen.append(True))

    model.clear_layers_for_paths(["a.png"])

    assert seen == [True]


def test_clearing_nothing_does_not_mark_the_project_dirty(qapp, tmp_path):
    project, model = _model_with_edits(tmp_path, edited=())
    seen = []
    model.project_dirty.connect(lambda: seen.append(True))

    assert model.clear_layers_for_paths(["a.png", "b.png"]) == 0
    assert seen == []


def test_clear_layers_updates_the_has_edits_role(qapp, tmp_path):
    project, model = _model_with_edits(tmp_path)
    row = [model.path_at(i) for i in range(model.rowCount())].index("a.png")
    assert model.data(model.index(row, 0), HasLayersRole) is True

    model.clear_layers_for_paths(["a.png"])

    assert model.data(model.index(row, 0), HasLayersRole) is False


def test_cleared_images_drop_out_of_a_has_edits_filter(qapp, tmp_path):
    """Otherwise the grid keeps showing rows that no longer match, and
    clicking one opens an image the filter says has edits."""
    project, model = _model_with_edits(tmp_path, edited=("a.png", "c.png"))
    model.set_filters(has_edits=True)
    assert model.rowCount() == 2

    model.clear_layers_for_paths(["a.png"])

    assert model.rowCount() == 1
    assert model.path_at(0) == "c.png"


def test_clear_layers_evicts_the_cached_thumbnail(qapp, tmp_path):
    """The cached pixmap still shows the censored render, and nothing else
    would notice it is stale, since clearing layers does not touch the
    source file's mtime."""
    project, model = _model_with_edits(tmp_path)
    model._pixmaps["a.png"] = object()

    model.clear_layers_for_paths(["a.png"])

    assert "a.png" not in model._pixmaps


def test_paths_with_layers_ignores_repeats_so_the_count_matches_what_is_cleared(qapp, tmp_path):
    """The number goes in front of the user in a confirmation, so it has to
    agree with how many images actually change."""
    project, model = _model_with_edits(tmp_path, edited=("a.png", "c.png"))
    selection = ["a.png", "c.png", "a.png"]

    with_layers = model.paths_with_layers(selection)

    assert with_layers == ["a.png", "c.png"]
    assert model.clear_layers_for_paths(with_layers) == len(with_layers)


# ===== shutting the gallery down
#
# Hundreds of thumbnails can be in flight when a project is reloaded or the
# window closes. Tasks that finish into a destroyed model make Qt print a
# traceback per task from a thread the user cannot see.


def _busy_model(tmp_path, count=12):
    from PIL import Image

    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    names = [f"i{i:02}.png" for i in range(count)]
    for n in names:
        Image.new("RGB", (400, 400), (10, 20, 30)).save(out / n, "PNG")
    mp = tmp_path / "manifest.txt"
    mp.write_text(
        f"#ALICEPACK --src-dir={out}\narchive.afa\n"
        + "\n".join(f"{n},qnt" for n in names) + "\n",
        encoding="utf-8",
    )
    manifest = parse_manifest(mp)
    project = ProjectState()
    for n in names:
        project.images[n] = ImageRecord()
    model = GalleryModel(project, manifest, compute_groups(manifest), tmp_path / "thumbs")
    for row in range(model.rowCount()):
        model.data(model.index(row, 0), Qt.DecorationRole)
    return model


def test_the_model_does_not_share_the_global_thread_pool(qapp, tmp_path):
    """Shutting one gallery down must not touch work belonging to anything
    else in the process."""
    from PySide6.QtCore import QThreadPool

    model = _busy_model(tmp_path)
    assert model._pool is not QThreadPool.globalInstance()
    model.shutdown()


def test_shutdown_drains_outstanding_thumbnail_work(qapp, tmp_path):
    model = _busy_model(tmp_path)
    assert model._pending, "there should be work in flight to drain"

    model.shutdown()

    assert model._pool.activeThreadCount() == 0


def test_shutdown_stops_new_thumbnails_being_requested(qapp, tmp_path):
    model = _busy_model(tmp_path, count=4)
    model.shutdown()
    model._pending.clear()

    model.data(model.index(0, 0), Qt.DecorationRole)

    assert model._pending == set(), "a shut down model must not queue more work"


def test_a_cancelled_task_reports_nothing_back(qapp, tmp_path):
    """The task checks as late as possible, so one already decoding when
    shutdown starts is dropped rather than delivered."""
    import threading

    from alice_censor.gallery.thumbnail_cache import ThumbnailCache
    from alice_censor.gallery.thumbnail_worker import ThumbnailSignals, ThumbnailTask
    from PIL import Image

    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    Image.new("RGB", (40, 40), (1, 2, 3)).save(out / "a.png", "PNG")

    signals = ThumbnailSignals()
    got = []
    signals.ready.connect(lambda *a: got.append(a))
    signals.failed.connect(lambda *a: got.append(a))
    cancelled = threading.Event()
    cancelled.set()

    ThumbnailTask("a.png", out / "a.png", ThumbnailCache(tmp_path / "c"), signals,
                  cancelled=cancelled).run()

    assert got == []


def test_setting_a_new_model_shuts_the_previous_one_down(qapp, tmp_path):
    from alice_censor.gallery.gallery_widget import GalleryWidget

    widget = GalleryWidget()
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    first = _busy_model(one, count=6)
    second = _busy_model(two, count=6)

    widget.set_model(first)
    widget.set_model(second)

    assert first._cancelled.is_set(), "the outgoing model must stop thumbnailing"
    assert not second._cancelled.is_set(), "the incoming one must not"
    second.shutdown()
