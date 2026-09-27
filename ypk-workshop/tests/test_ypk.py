from __future__ import annotations
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ypk_workshop", ROOT / "skills" / "ypk-workshop" / "scripts" / "ypk.py")
ypk = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ypk
SPEC.loader.exec_module(ypk)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ypk_test_")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def project(self, name="test-pack", card_id=260000000, **changes):
        root = self.root / name
        root.mkdir()
        (root / "script").mkdir()
        (root / "images").mkdir()
        (root / "script" / f"c{card_id}.lua").write_text("local s,id=GetID()\nfunction s.initial_effect(c) end\n", encoding="utf-8")
        Image.new("RGB", (60, 90), (24, 70, 120)).save(root / "images" / "source.webp")
        card = {"id": card_id, "name": "合成测试卡", "desc": "仅用于封包测试，不是实际发行卡。", "type": 33, "atk": 1600, "def": 1200, "level": 4, "race": 1, "attribute": 1, "strings": ["提示零", "提示一"], "script": f"script/c{card_id}.lua", "image": {"path": "images/source.webp"}, "source": {"original_text": "Synthetic fixture; no real card effect", "temporary_id": True}, "unresolved": []}
        card.update(changes)
        data = {"schema_version": 1, "package": {"name": name, "display_name": "合成测试"}, "target": {"family": "ygopro-fluorohydride", "script_api": "synthetic test"}, "cards": [card]}
        (root / "project.json").write_bytes(ypk.json_bytes(data))
        return root

    def existing_base(self):
        project = ypk.load_project(self.project("base-source", 100))
        path = self.root / "existing.ypk"
        with zipfile.ZipFile(path, "w") as archive:
            archive.comment = b"existing comment"
            archive.writestr("existing.cdb", ypk.make_database(project["cards"]))
            archive.writestr("script/c100.lua", b"existing script")
            archive.writestr("pics/100.jpg", b"existing bytes preserved")
            archive.writestr("corres_srv.ini", b"[YGOProExpansionPack]\r\nFileName = existing.ypk\r\n")
            archive.writestr("custom/unknown.bin", bytes(range(256)))
            archive.writestr("manifest.json", b'{"existing":true}')
            archive.writestr("pack/custom.ydk", b"#main\n100\n#extra\n!side\n")
        return path

    def test_create_database_strings_images_and_honest_status(self):
        output = self.root / "new.ypk"
        report = ypk.build(self.project(), output)
        entries, _, _ = ypk.read_archive(output)
        self.assertEqual(report["card_count"], 1)
        self.assertEqual(report["lua_syntax"]["status"], "not_checked")
        self.assertEqual(report["effect_runtime"], "not_verified")
        self.assertEqual(report["engine_collisions"], "not_checked")
        with Image.open(io.BytesIO(entries["pics/260000000.jpg"])) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, (60, 90))
        db = self.root / "check.cdb"
        db.write_bytes(entries[report["databases"][0]])
        with contextlib.closing(sqlite3.connect(db)) as con:
            self.assertEqual(con.execute("SELECT str1,str2,str16 FROM texts").fetchone(), ("提示零", "提示一", ""))
        self.assertTrue(ypk.check_entries(entries)["workshop_manifest"])

    def test_append_preserves_existing_members_comment_and_source_archive(self):
        base = self.existing_base()
        source_bytes = base.read_bytes()
        output = self.root / "expanded.ypk"
        result = ypk.build(self.project(), output, base)
        before, infos_before, comment_before = ypk.read_archive(base)
        after, infos_after, comment_after = ypk.read_archive(output)
        self.assertEqual(result["card_count"], 2)
        self.assertEqual(base.read_bytes(), source_bytes)
        self.assertEqual(comment_after, comment_before)
        for name, data in before.items():
            self.assertEqual(after[name], data)
            self.assertEqual(infos_after[name].compress_type, infos_before[name].compress_type)

    def test_repeated_append_preserves_prior_sources_and_history(self):
        first, second, third = (self.root / n for n in ("first.ypk", "second.ypk", "third.ypk"))
        ypk.build(self.project("one", 100), first)
        ypk.build(self.project("two", 101), second, first)
        ypk.build(self.project("three", 102), third, second)
        entries, _, _ = ypk.read_archive(third)
        manifest = json.loads(entries[ypk.MANIFEST])
        self.assertEqual([c["id"] for c in manifest["cards"]], [100, 101, 102])
        self.assertTrue(all(c["source"]["original_text"] for c in manifest["cards"]))
        self.assertEqual(len(manifest["history"]), 3)

    def test_duplicate_id_rejected_without_mutating_base(self):
        base = self.existing_base()
        original = base.read_bytes()
        output = self.root / "failure.ypk"
        with self.assertRaisesRegex(ypk.YPKError, "already exist"):
            ypk.build(self.project(card_id=100), output, base)
        self.assertEqual(base.read_bytes(), original)
        self.assertFalse(output.exists())

    def test_existing_output_cannot_be_overwritten(self):
        output = self.root / "new.ypk"
        output.write_bytes(b"keep this")
        with self.assertRaisesRegex(ypk.YPKError, "Output exists"):
            ypk.build(self.project(), output)
        self.assertEqual(output.read_bytes(), b"keep this")

    def test_unresolved_text_rejected(self):
        project = self.project(unresolved=["unknown target restriction"])
        with self.assertRaisesRegex(ypk.YPKError, "unresolved"):
            ypk.build(project, self.root / "new.ypk")

    def test_missing_original_text_rejected(self):
        with self.assertRaisesRegex(ypk.YPKError, "original_text"):
            ypk.build(self.project(source={}), self.root / "new.ypk")

    def test_project_cannot_reference_external_files(self):
        (self.root / "secret.lua").write_text("external", encoding="utf-8")
        with self.assertRaisesRegex(ypk.YPKError, "inside the card project"):
            ypk.build(self.project(script="../secret.lua"), self.root / "new.ypk")

    def test_tampered_script_detected(self):
        output = self.root / "new.ypk"
        ypk.build(self.project(), output)
        entries, _, _ = ypk.read_archive(output)
        entries["script/c260000000.lua"] += b"--tamper"
        with self.assertRaisesRegex(ypk.YPKError, "hash mismatch"):
            ypk.check_entries(entries)

    def test_archive_case_insensitive_duplicate_rejected(self):
        output = self.root / "bad.ypk"
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("pics/100.jpg", b"one")
            archive.writestr("PICS/100.JPG", b"two")
        with self.assertRaisesRegex(ypk.YPKError, "Duplicate archive"):
            ypk.read_archive(output)

    def test_unsafe_archive_path_rejected(self):
        output = self.root / "bad.ypk"
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("../outside.cdb", b"not a database")
        with self.assertRaisesRegex(ypk.YPKError, "Unsafe"):
            ypk.read_archive(output)

    def test_duplicate_id_in_multiple_cdbs_rejected(self):
        project = ypk.load_project(self.project())
        data = ypk.make_database(project["cards"])
        with self.assertRaisesRegex(ypk.YPKError, "multiple CDBs"):
            ypk.inventory({"one.cdb": data, "two.cdb": data})

    def test_engine_scan_includes_archives_and_loose_scripts(self):
        engine = self.root / "client"
        engine.mkdir()
        (engine / "expansions").mkdir()
        (engine / "script").mkdir()
        project = ypk.load_project(self.project("engine-source", 500))
        (engine / "cards.cdb").write_bytes(ypk.make_database(project["cards"]))
        with zipfile.ZipFile(engine / "expansions" / "test.ypk", "w") as archive:
            archive.writestr("pack.cdb", ypk.make_database(project["cards"]))
            archive.writestr("script/c700.lua", b"stub")
        (engine / "script" / "c600.lua").write_bytes(b"stub")
        found, names = ypk.engine_inventory(engine)
        self.assertEqual(set(found), {500, 600, 700})
        self.assertEqual(names, {"cards.cdb", "pack.cdb"})
        with self.assertRaisesRegex(ypk.YPKError, "conflict"):
            ypk.build(self.project("conflicting", 700), self.root / "new.ypk", engine_root=engine)

    def test_corrupt_client_database_not_silently_skipped(self):
        engine = self.root / "client"
        engine.mkdir()
        (engine / "cards.cdb").write_bytes(b"invalid cdb")
        with self.assertRaises(ypk.YPKError):
            ypk.engine_inventory(engine)

    def test_image_crop_dimensions_and_region(self):
        source = self.root / "poster.png"
        image = Image.new("RGB", (100, 100), "red")
        image.paste("blue", (20, 10, 80, 90))
        image.save(source)
        with Image.open(io.BytesIO(ypk.render_image(source, [20, 10, 80, 90]))) as result:
            self.assertEqual(result.size, (60, 80))
            r, g, b = result.getpixel((30, 40))
            self.assertLess(r, 5)
            self.assertLess(g, 5)
            self.assertGreater(b, 250)

    def test_crop_outside_bounds_rejected(self):
        source = self.root / "card.png"
        Image.new("RGB", (60, 90)).save(source)
        with self.assertRaisesRegex(ypk.YPKError, "outside"):
            ypk.render_image(source, [-1, 0, 60, 90])

    def test_transparency_flattens_to_white(self):
        source = self.root / "transparent.png"
        Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(source)
        with Image.open(io.BytesIO(ypk.render_image(source))) as result:
            self.assertEqual(result.getpixel((5, 5)), (255, 255, 255))

    def test_temporary_ids_skip_existing_base_ids(self):
        base = self.existing_base()
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            status = ypk.main(["ids", "--base", str(base), "--start", "100", "--count", "2"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(stream.getvalue())["temporary_ids"], [101, 102])

    def test_missing_luac_is_error(self):
        with self.assertRaisesRegex(ypk.YPKError, "not found"):
            ypk.lua_syntax({"script/c100.lua": b"text"}, ["script/c100.lua"], "__not_a_lua_compiler__")


if __name__ == "__main__":
    unittest.main()
