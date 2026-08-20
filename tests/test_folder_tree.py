from alice_censor.gallery.folder_tree import ALL_IMAGES_LABEL, NO_FOLDER_LABEL, FolderTree


def test_all_images_node_always_present_and_selected_by_default(qapp):
    tree = FolderTree()
    tree.set_folders({"": 0, "evt": 2}, total_count=2)

    assert tree.topLevelItem(0).text(0) == f"{ALL_IMAGES_LABEL} (2)"
    assert tree.currentItem() is tree.topLevelItem(0)


def test_root_bucket_only_shown_when_nonzero(qapp):
    tree = FolderTree()
    tree.set_folders({"": 0, "evt": 2}, total_count=2)
    labels_without_root = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
    assert not any(l.startswith(NO_FOLDER_LABEL) for l in labels_without_root)

    tree.set_folders({"": 1, "evt": 2}, total_count=3)
    labels_with_root = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
    assert any(l.startswith(NO_FOLDER_LABEL) for l in labels_with_root)


def test_nested_folders_build_real_parent_child_hierarchy(qapp):
    tree = FolderTree()
    tree.set_folders({"": 0, "evt": 3, "evt/char": 2}, total_count=3)

    evt_item = next(
        tree.topLevelItem(i)
        for i in range(tree.topLevelItemCount())
        if tree.topLevelItem(i).text(0).startswith("evt")
    )
    assert evt_item.text(0) == "evt (3)"
    assert evt_item.childCount() == 1
    child = evt_item.child(0)
    assert child.text(0) == "char (2)"  # label shows only the last segment, not the full path


def test_deep_nesting_builds_multi_level_hierarchy(qapp):
    tree = FolderTree()
    tree.set_folders({"": 0, "a": 5, "a/b": 3, "a/b/c": 1}, total_count=5)

    a_item = next(
        tree.topLevelItem(i)
        for i in range(tree.topLevelItemCount())
        if tree.topLevelItem(i).text(0).startswith("a ")
    )
    b_item = a_item.child(0)
    c_item = b_item.child(0)
    assert b_item.text(0) == "b (3)"
    assert c_item.text(0) == "c (1)"


def test_set_folders_emits_all_images_on_build(qapp):
    tree = FolderTree()
    received = []
    tree.folder_selected.connect(received.append)

    tree.set_folders({"": 0, "evt": 2}, total_count=2)

    assert received == [None]


def test_selecting_a_folder_emits_its_full_prefix(qapp):
    tree = FolderTree()
    tree.set_folders({"": 0, "evt": 3, "evt/char": 2}, total_count=3)
    received = []
    tree.folder_selected.connect(received.append)

    evt_item = next(
        tree.topLevelItem(i)
        for i in range(tree.topLevelItemCount())
        if tree.topLevelItem(i).text(0).startswith("evt")
    )
    tree.setCurrentItem(evt_item.child(0))

    assert received == ["evt/char"]


def test_selecting_root_bucket_emits_empty_string_not_none(qapp):
    tree = FolderTree()
    tree.set_folders({"": 2, "evt": 3}, total_count=5)
    received = []
    tree.folder_selected.connect(received.append)

    root_item = next(
        tree.topLevelItem(i)
        for i in range(tree.topLevelItemCount())
        if tree.topLevelItem(i).text(0).startswith(NO_FOLDER_LABEL)
    )
    tree.setCurrentItem(root_item)

    assert received == [""]  # distinct from None ("all images") -- exact root-only match


def test_switching_back_to_all_images_emits_none(qapp):
    tree = FolderTree()
    tree.set_folders({"": 0, "evt": 2}, total_count=2)
    evt_item = tree.topLevelItem(1)
    tree.setCurrentItem(evt_item)
    received = []
    tree.folder_selected.connect(received.append)

    tree.setCurrentItem(tree.topLevelItem(0))  # back to "All Images"

    assert received == [None]
