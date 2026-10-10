import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from quick_loadimg.index import (
    INDEX_FILENAME,
    INDEX_VERSION,
    PastedIndex,
    format_slot_name,
    iter_memberships,
)
from quick_loadimg.listing import (
    PASTED_COMBO_LIMIT,
    apply_image_list,
    clear_session_pastes,
    merge_image_choices,
    note_session_paste,
    scan_pasted,
    session_pastes,
    visible_choices,
)
import update_index
from quick_loadimg.upload import (
    is_fast_pasted,
    is_pasted_overwrite,
    prepare_pasted_upload,
)


def _hasher_factory(counter):
    def factory():
        counter[0] += 1
        return hashlib.sha256()

    return factory


class _Upload:
    def __init__(self, filename, data):
        self.filename = filename
        self.file = io.BytesIO(data)


def _post(filename, data, **fields):
    body = {"image": _Upload(filename, data), "subfolder": "pasted"}
    body.update(fields)
    return body


class ChainNameTests(unittest.TestCase):
    def test_native_slot_names_and_memberships(self):
        self.assertEqual(format_slot_name("image", ".png", 0), "image.png")
        self.assertEqual(format_slot_name("image", ".png", 2), "image (2).png")
        self.assertEqual(format_slot_name("image (5)", ".png", 1), "image (5) (1).png")

        self.assertEqual(list(iter_memberships("image.png")), [("image", ".png", 0)])
        self.assertEqual(
            list(iter_memberships("image (12).png")),
            [("image (12)", ".png", 0), ("image", ".png", 12)],
        )
        self.assertEqual(
            list(iter_memberships("image (5) (1).png")),
            [("image (5) (1)", ".png", 0), ("image (5)", ".png", 1)],
        )
        self.assertEqual(
            list(iter_memberships("my file (3).jpeg")),
            [("my file (3)", ".jpeg", 0), ("my file", ".jpeg", 3)],
        )
        # Native never emits " (0)" or zero-padded counters.
        self.assertEqual(list(iter_memberships("image (0).png")), [("image (0)", ".png", 0)])
        self.assertNotIn(("image", ".png", 0), list(iter_memberships("image (2) (1).png")))


class PastedIndexTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = self._tmp.name
        self.hashes = [0]
        self.index = PastedIndex(self.folder, hasher_factory=_hasher_factory(self.hashes))

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, data):
        with open(os.path.join(self.folder, name), "wb") as handle:
            handle.write(data)

    def image_names(self):
        return sorted(name for name in os.listdir(self.folder) if not name.startswith("."))

    def test_earliest_contiguous_file_wins(self):
        self.write("image.png", b"aaa")
        self.write("image (1).png", b"bbb")
        self.write("image (2).png", b"aaa")
        self.index.rebuild()

        self.assertEqual(self.index.find_duplicate("image.png", self.index.digest(b"aaa")), "image.png")
        self.assertEqual(self.index.find_duplicate("image.png", self.index.digest(b"bbb")), "image (1).png")
        self.assertIsNone(self.index.find_duplicate("image.png", self.index.digest(b"ccc")))
        self.assertEqual(self.index.next_free_name("image.png"), "image (3).png")

    def test_historical_match_is_reused_and_new_content_fills_first_gap(self):
        self.write("image (5).png", b"same")
        self.write("image (2).png", b"same")
        self.index.rebuild()
        digest = self.index.digest(b"same")
        self.assertEqual(self.index.find_duplicate("image.png", digest), "image (2).png")

        name, is_duplicate = self.index.store_bytes("image.png", b"same")
        self.assertEqual((name, is_duplicate), ("image (2).png", True))
        self.assertFalse(os.path.exists(os.path.join(self.folder, "image.png")))

        name, is_duplicate = self.index.store_bytes("image.png", b"other")
        self.assertEqual((name, is_duplicate), ("image.png", False))
        self.assertEqual(self.index.next_free_name("image.png"), "image (1).png")

    def test_nested_counter_is_not_on_the_image_chain(self):
        self.write("image (2) (1).png", b"aaa")
        self.index.rebuild()
        self.assertIsNone(self.index.find_duplicate("image.png", self.index.digest(b"aaa")))

    def test_allocate_first_hole_then_advance_past_existing_tail(self):
        self.write("image.png", b"a")
        self.write("image (1).png", b"b")
        self.write("image (3).png", b"c")
        self.index.rebuild()
        self.assertEqual(self.index.next_free_name("image.png"), "image (2).png")

        name, is_duplicate = self.index.store_bytes("image.png", b"new")
        self.assertEqual((name, is_duplicate), ("image (2).png", False))
        self.assertEqual(self.image_names(), ["image (1).png", "image (2).png", "image (3).png", "image.png"])
        self.assertEqual(self.index.next_free_name("image.png"), "image (4).png")

        before = self.hashes[0]
        again, duplicate = self.index.store_bytes("image.png", b"a")
        self.assertEqual((again, duplicate), ("image.png", True))
        self.assertEqual(self.hashes[0] - before, 1)
        self.assertNotIn("image (5).png", self.image_names())

    def test_chains_do_not_share_hashes(self):
        self.write("foo.png", b"aaa")
        self.index.rebuild()
        self.assertIsNone(self.index.find_duplicate("image.png", self.index.digest(b"aaa")))
        self.assertEqual(self.index.find_duplicate("foo.png", self.index.digest(b"aaa")), "foo.png")

        name, is_duplicate = self.index.store_bytes("image.png", b"aaa")
        self.assertEqual((name, is_duplicate), ("image.png", False))
        self.assertEqual(self.index.find_duplicate("foo.png", self.index.digest(b"aaa")), "foo.png")

    def test_incremental_rebuild_skips_unchanged_and_drops_missing(self):
        self.write("image.png", b"aaa")
        self.write("image (1).png", b"bbb")
        self.index.rebuild()
        self.assertEqual(self.hashes[0], 2)
        self.assertNotIn(INDEX_FILENAME, self.index.files)

        self.hashes[0] = 0
        self.index.rebuild()
        self.assertEqual(self.hashes[0], 0)

        self.write("image (1).png", b"bbb-changed")
        self.index.rebuild()
        self.assertEqual(self.hashes[0], 1)
        self.assertEqual(
            self.index.find_duplicate("image.png", self.index.digest(b"bbb-changed")),
            "image (1).png",
        )

        os.remove(os.path.join(self.folder, "image.png"))
        self.hashes[0] = 0
        self.index.rebuild()
        self.assertEqual(self.hashes[0], 0)
        self.assertNotIn("image.png", self.index.files)
        self.assertEqual(self.index.next_free_name("image.png"), "image.png")

    def test_reload_reuses_saved_digests(self):
        self.write("image.png", b"aaa")
        self.index.rebuild()
        with open(self.index.index_path, encoding="utf-8") as handle:
            saved = json.loads(handle.read())
        self.assertEqual(saved["version"], INDEX_VERSION)
        self.assertEqual(saved["files"]["image.png"]["digest"], self.index.digest(b"aaa"))

        self.hashes[0] = 0
        reloaded = PastedIndex(self.folder, hasher_factory=_hasher_factory(self.hashes))
        reloaded.rebuild()
        self.assertEqual(self.hashes[0], 0)
        self.assertEqual(reloaded.find_duplicate("image.png", reloaded.digest(b"aaa")), "image.png")

    def test_incompatible_or_corrupt_index_is_rebuilt(self):
        self.write("image.png", b"aaa")
        with open(self.index.index_path, "w", encoding="utf-8") as handle:
            handle.write("{")
        self.index.rebuild()
        self.assertEqual(self.index.find_duplicate("image.png", self.index.digest(b"aaa")), "image.png")

        with open(self.index.index_path, "w", encoding="utf-8") as handle:
            json.dump({"version": 99, "files": {}}, handle)
        self.hashes[0] = 0
        self.index.rebuild()
        self.assertEqual(self.hashes[0], 1)

    def test_overwrite_keeps_the_next_earliest_hash(self):
        self.write("image.png", b"aaa")
        self.write("image (1).png", b"aaa")
        self.index.rebuild()
        self.write("image.png", b"bbb")
        self.index.note_file("image.png")
        self.assertEqual(self.index.find_duplicate("image.png", self.index.digest(b"aaa")), "image (1).png")
        self.assertEqual(self.index.find_duplicate("image.png", self.index.digest(b"bbb")), "image.png")

    def test_background_build_indexes_existing_files(self):
        self.write("image.png", b"aaa")
        self.assertTrue(self.index.start() is not None)
        self.assertIsNone(self.index.start())
        self.assertTrue(self.index.wait_ready(5))
        self.assertEqual(self.index.find_duplicate("image.png", self.index.digest(b"aaa")), "image.png")


class LoadImageListTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = self._tmp.name
        clear_session_pastes()

    def tearDown(self):
        clear_session_pastes()
        self._tmp.cleanup()

    def write(self, name, stamp):
        path = os.path.join(self.folder, name)
        with open(path, "wb") as handle:
            handle.write(name.encode("utf-8"))
        os.utime(path, (stamp, stamp))

    def test_combo_keeps_root_files_and_ten_newest_pasted(self):
        self.assertEqual(PASTED_COMBO_LIMIT, 10)
        for slot in range(12):
            name = "image.png" if slot == 0 else f"image ({slot}).png"
            self.write(name, slot + 1)
        native = ["root.png", "pasted/image (1).png", "other.jpg", "root.png"]
        merged = merge_image_choices(native, self.folder)
        self.assertEqual(merged[:2], ["root.png", "other.jpg"])
        pasted = [name for name in merged if name.startswith("pasted/")]
        self.assertEqual(len(pasted), 10)
        self.assertEqual(pasted[0], "pasted/image (11).png")
        self.assertNotIn("pasted/image.png", pasted)
        self.assertNotIn("pasted/image (1).png", pasted)

        spec = apply_image_list(
            {"required": {"image": (native, {"image_upload": True})}},
            self.folder,
        )
        files, extra = spec["required"]["image"]
        self.assertEqual(extra, {"image_upload": True})
        self.assertEqual(files, merged)

    def test_filename_search_includes_older_pasted_and_keeps_current(self):
        for slot in range(12):
            name = "image.png" if slot == 0 else f"image ({slot}).png"
            self.write(name, slot + 1)
        self.write("special-old.png", 0)
        records = scan_pasted(self.folder)
        native = [f"shot-{index}.png" for index in range(15)]

        old_match = visible_choices(native, records, query="SPECIAL")
        self.assertEqual(old_match, ["pasted/special-old.png"])

        image_matches = [
            name for name in visible_choices(["root.png"], records, query="image") if name.startswith("pasted/")
        ]
        self.assertEqual(len(image_matches), 12)
        self.assertEqual(image_matches[0], "pasted/image (11).png")
        self.assertIn("pasted/image.png", image_matches)
        self.assertNotIn("pasted/special-old.png", image_matches)

        roots = [name for name in visible_choices(native, records, query="shot-") if name.startswith("shot-")]
        self.assertEqual(len(roots), 15)

        with_current = visible_choices(["root.png"], records, current="pasted/special-old.png")
        self.assertEqual(with_current[0], "root.png")
        self.assertEqual(with_current[-1], "pasted/special-old.png")
        pasted = [name for name in with_current if name.startswith("pasted/")]
        self.assertEqual(len(pasted), PASTED_COMBO_LIMIT + 1)

    def test_session_pastes_keep_the_default_ten_and_the_first_paste(self):
        for slot in range(12):
            name = "image.png" if slot == 0 else f"image ({slot}).png"
            self.write(name, slot + 1)
        historical = [
            name
            for name in visible_choices(["root.png"], scan_pasted(self.folder), session=[])
            if name.startswith("pasted/")
        ]
        self.assertEqual(len(historical), 10)
        self.assertNotIn("pasted/image.png", historical)

        session = []
        for index in range(11):
            name = f"session-{index}.png"
            self.write(name, 100 + index)
            session.append(note_session_paste(name))
        pasted = [
            name
            for name in visible_choices(["root.png"], scan_pasted(self.folder))
            if name.startswith("pasted/")
        ]
        for name in historical:
            self.assertIn(name, pasted)
        self.assertEqual(session_pastes()[0], "pasted/session-0.png")
        self.assertEqual(pasted[10:], session)
        self.assertEqual(len(pasted), 21)

        found = [
            name
            for name in visible_choices(["root.png"], scan_pasted(self.folder), query="session-0")
            if name.startswith("pasted/")
        ]
        self.assertEqual(found, ["pasted/session-0.png"])


class UpdateIndexScriptTests(unittest.TestCase):
    def test_resolve_comfyui_input_or_pasted_directory(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "input", "pasted"))
            input_dir = os.path.join(root, "input")
            pasted = os.path.join(input_dir, "pasted")
            self.assertEqual(update_index.resolve_pasted_dir(root), os.path.abspath(pasted))
            self.assertEqual(update_index.resolve_pasted_dir(input_dir), os.path.abspath(pasted))
            self.assertEqual(update_index.resolve_pasted_dir(pasted), os.path.abspath(pasted))
            self.assertEqual(update_index.resolve_pasted_dir(None, default_dir=pasted), pasted)

    def test_script_writes_index_for_existing_files(self):
        with tempfile.TemporaryDirectory() as pasted:
            with open(os.path.join(pasted, "image.png"), "wb") as handle:
                handle.write(b"aaa")
            self.assertEqual(update_index.main([pasted]), 0)
            index_path = os.path.join(pasted, ".quick-loadimg-index.json")
            with open(index_path, encoding="utf-8") as handle:
                saved = json.loads(handle.read())
            self.assertEqual(saved["version"], 1)
            self.assertIn("image.png", saved["files"])

            self.assertEqual(update_index.main([os.path.join(pasted, "missing")]), 1)


class UploadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.input_dir = os.path.join(self._tmp.name, "input")
        self.pasted = os.path.join(self.input_dir, "pasted")
        os.makedirs(self.pasted)
        self.index = PastedIndex(self.pasted, hasher_factory=lambda: hashlib.sha256())

    def tearDown(self):
        self._tmp.cleanup()

    def test_fast_path_gates(self):
        pasted = _post("image.png", b"a")
        self.assertTrue(is_fast_pasted(pasted))
        self.assertFalse(is_pasted_overwrite(pasted))
        self.assertFalse(is_fast_pasted(_post("image.png", b"a", overwrite="true")))
        self.assertTrue(is_pasted_overwrite(_post("image.png", b"a", overwrite="1")))
        self.assertFalse(is_fast_pasted(_post("image.png", b"a", subfolder="")))
        self.assertFalse(is_fast_pasted(_post("image.png", b"a", type="temp")))
        self.assertFalse(is_fast_pasted(_post("../evil.png", b"a")))
        self.assertFalse(is_fast_pasted(pasted, image_save_function=lambda *_args: None))

    def test_prepare_reuses_then_allocates_and_registers_assets(self):
        recorded = []

        class _Asset:
            id = "asset-1"
            name = "image.png"

        class _View:
            asset = _Asset()
            asset_hash = "hash"
            size = 3
            mime_type = "image/png"
            tags = ["input"]

        class _Manager:
            def register_upload(self, **kwargs):
                recorded.append(kwargs)
                return _View()

        server = SimpleNamespace(asset_manager=_Manager())
        first = prepare_pasted_upload(_post("image.png", b"aaa"), self.index, self.input_dir, server)
        self.assertEqual(first.status, 200)
        self.assertEqual(first.body["name"], "image.png")
        self.assertEqual(first.body["subfolder"], "pasted")
        self.assertEqual(first.body["type"], "input")
        self.assertEqual(first.body["asset"]["id"], "asset-1")
        self.assertTrue(recorded[0]["content_written"])
        self.assertEqual(os.path.basename(recorded[0]["abs_path"]), "image.png")

        second = prepare_pasted_upload(_post("image.png", b"aaa"), self.index, self.input_dir, server)
        self.assertEqual(second.body["name"], "image.png")
        self.assertFalse(recorded[1]["content_written"])

        third = prepare_pasted_upload(_post("image.png", b"zzz"), self.index, self.input_dir, server)
        self.assertEqual(third.body["name"], "image (1).png")
        self.assertTrue(recorded[2]["content_written"])
        with open(os.path.join(self.pasted, "image (1).png"), "rb") as handle:
            self.assertEqual(handle.read(), b"zzz")


if __name__ == "__main__":
    unittest.main()
