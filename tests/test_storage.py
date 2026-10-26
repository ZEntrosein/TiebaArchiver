import hashlib
import io
import json
import os
import sqlite3
import stat
import sys
import tempfile
import unittest
import zipfile
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from archive_cli import export_archives
from config.path_config import ScrapeDataPathBuilder
from storage_config import StorageConfig, safe_filename
from utils.archive import ArchiveError, create_zip, export_archive, open_update_workspace


class StorageTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        config = StorageConfig.to_dict()
        self.addCleanup(StorageConfig.from_dict, config)
        StorageConfig.from_dict({"OUTPUT_DIRECTORY": str(self.root / "output")})

    def make_archive(self, name="[测试吧][42]中文标题_123", tid=42, *, full_db=False):
        directory = self.root / "input" / name
        thread = directory / "threads" / str(tid)
        thread.mkdir(parents=True)
        info = {"main_thread": tid, "create_time": 1, "update_time": 1, "scraper_version": "1.3.1", "scrape_records": []}
        (directory / "scrape_info.json").write_text(json.dumps(info), encoding="utf-8")
        (thread / "thread.json").write_text(json.dumps({"id": tid, "forum_name": "测试", "share_origin": 0}), encoding="utf-8")
        if full_db:
            from db.content_db import ContentDB
            with closing(ContentDB(str(thread / "content.db"), tid)) as db:
                db.execute("INSERT INTO post(id,contents,floor,user_id,create_time) VALUES (1,'[]',1,1,1)")
        else:
            with closing(sqlite3.connect(thread / "content.db")) as db:
                db.execute("CREATE TABLE post(id INTEGER PRIMARY KEY, contents TEXT)")
                db.execute("INSERT INTO post VALUES(1,'old post')")
                db.commit()
        media = thread / "post_assets" / "images"
        media.mkdir(parents=True)
        (media / "中文图片.jpeg").write_bytes(b"image-content")
        return directory

    def digest(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()


class StorageConfigTests(StorageTestCase):
    def test_missing_config_and_missing_keys_use_defaults(self):
        StorageConfig.load(self.root / "missing.json")
        self.assertEqual(StorageConfig.output_root(), Path.cwd() / "scraped_data")
        self.assertEqual(StorageConfig.ARCHIVE_FORMAT, "zip")
        self.assertTrue(StorageConfig.GROUP_BY_FORUM)

    def test_invalid_config_does_not_partially_change_settings(self):
        original = StorageConfig.to_dict()
        for invalid in ({"GROUP_BY_FORUM": 1}, {"ARCHIVE_FORMAT": "rar"}, {"OUTPUT_DIRECTORY": None}, []):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                StorageConfig.from_dict(invalid)
            self.assertEqual(StorageConfig.to_dict(), original)

    def test_settings_round_trip(self):
        filename = self.root / "settings.json"
        expected = {"OUTPUT_DIRECTORY": str(self.root / "我的存档"), "ARCHIVE_FORMAT": "folder", "GROUP_BY_FORUM": False}
        StorageConfig.from_dict(expected)
        StorageConfig.save(filename)
        StorageConfig.from_dict({})
        StorageConfig.load(filename)
        self.assertEqual(StorageConfig.to_dict(), expected)

    def test_new_path_respects_custom_root_grouping_and_filename_rules(self):
        builder = ScrapeDataPathBuilder.get_instance_scrape("a/b:c", 42, '标题<>:"/\\|?*')
        directory = Path(builder.get_item_dir())
        self.assertEqual(directory.parent, self.root / "output" / "abc吧")
        self.assertTrue(directory.name.startswith("[abc吧][42]标题_"))
        self.assertTrue(directory.is_dir())
        StorageConfig.GROUP_BY_FORUM = False
        builder = ScrapeDataPathBuilder.get_instance_scrape("测试", 43, "标题")
        self.assertEqual(Path(builder.get_item_dir()).parent, self.root / "output")

    def test_windows_reserved_and_empty_names(self):
        self.assertEqual(safe_filename("CON"), "_CON")
        self.assertEqual(safe_filename("<>... "), "未命名")


class ZipStorageTests(StorageTestCase):
    def test_export_preserves_original_tree_media_and_database(self):
        source = self.make_archive()
        destination = export_archive(source)
        self.assertEqual(destination.parent, self.root / "output" / "测试吧")
        self.assertTrue(source.is_dir())
        with zipfile.ZipFile(destination) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.read(f"{source.name}/threads/42/post_assets/images/中文图片.jpeg"), b"image-content")
        with open_update_workspace(destination) as workspace:
            with closing(sqlite3.connect(workspace.directory / "threads/42/content.db")) as db:
                self.assertEqual(db.execute("SELECT contents FROM post").fetchall(), [("old post",)])

    def test_duplicate_export_requires_explicit_overwrite(self):
        source = self.make_archive()
        destination = export_archive(source)
        original = self.digest(destination)
        with self.assertRaises(FileExistsError):
            export_archive(source)
        self.assertEqual(self.digest(destination), original)
        export_archive(source, overwrite=True)

    def test_pack_failure_does_not_damage_original_zip(self):
        source = self.make_archive()
        destination = export_archive(source)
        original = self.digest(destination)
        with patch("zipfile.ZipFile.write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                create_zip(source, destination, overwrite=True)
        self.assertEqual(self.digest(destination), original)
        self.assertEqual(list(destination.parent.glob(".tieba-*")), [])
        self.assertFalse(destination.with_name(destination.name + ".lock").exists())

    def test_export_includes_committed_wal_data(self):
        source = self.make_archive()
        with closing(sqlite3.connect(source / "threads/42/content.db")) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("INSERT INTO post VALUES (2,'committed WAL post')")
            writer.commit()
            destination = export_archive(source)
            with zipfile.ZipFile(destination) as archive:
                self.assertFalse(any(name.endswith(("-wal", "-shm")) for name in archive.namelist()))
            with open_update_workspace(destination) as workspace:
                with closing(sqlite3.connect(workspace.directory / "threads/42/content.db")) as db:
                    self.assertEqual(db.execute("SELECT count(*) FROM post").fetchone()[0], 2)

    def test_workspace_commit_and_rollback(self):
        destination = export_archive(self.make_archive())
        original = self.digest(destination)
        with self.assertRaises(RuntimeError):
            with open_update_workspace(destination) as workspace:
                (workspace.directory / "new.txt").write_text("uncommitted")
                raise RuntimeError("update failed")
        self.assertEqual(self.digest(destination), original)
        with open_update_workspace(destination) as workspace:
            (workspace.directory / "new.txt").write_text("committed")
            self.assertEqual(workspace.commit(), destination)
        with open_update_workspace(destination) as workspace:
            self.assertEqual((workspace.directory / "new.txt").read_text(), "committed")
        self.assertEqual(list(destination.parent.glob(".tieba-*")), [])

    def test_open_without_commit_leaves_original_unchanged(self):
        destination = export_archive(self.make_archive())
        original = self.digest(destination)
        with open_update_workspace(destination) as workspace:
            (workspace.directory / "new.txt").write_text("cancelled")
        self.assertEqual(self.digest(destination), original)

    def test_locked_archive_cannot_be_opened_for_update(self):
        destination = export_archive(self.make_archive())
        lock = destination.with_name(destination.name + ".lock")
        lock.write_text("other task")
        with self.assertRaises(ArchiveError):
            with open_update_workspace(destination):
                pass
        self.assertTrue(lock.exists())

    def test_external_modification_prevents_update_replacement(self):
        destination = export_archive(self.make_archive())
        with open_update_workspace(destination) as workspace:
            with zipfile.ZipFile(destination, "a") as archive:
                archive.writestr("external-change.txt", "another program changed this ZIP")
            modified = self.digest(destination)
            with self.assertRaises(ArchiveError):
                workspace.commit()
        self.assertEqual(self.digest(destination), modified)

    def test_external_modification_during_compression_prevents_replacement(self):
        destination = export_archive(self.make_archive())
        original_write = zipfile.ZipFile.write
        modified = []

        def write_and_modify(*args, **kwargs):
            result = original_write(*args, **kwargs)
            if not modified:
                with zipfile.ZipFile(destination, "a") as external:
                    external.writestr("external-change.txt", "changed during compression")
                modified.append(self.digest(destination))
            return result

        with open_update_workspace(destination) as workspace:
            with patch.object(zipfile.ZipFile, "write", autospec=True, side_effect=write_and_modify):
                with self.assertRaises(ArchiveError):
                    workspace.commit()
        self.assertEqual(self.digest(destination), modified[0])

    def test_zip_with_metadata_at_root_can_be_updated(self):
        source = self.make_archive()
        destination = self.root / "root-layout.zip"
        with zipfile.ZipFile(destination, "w") as archive:
            for file in source.rglob("*"):
                if file.is_file():
                    archive.write(file, file.relative_to(source).as_posix())
        with open_update_workspace(destination) as workspace:
            (workspace.directory / "new.txt").write_text("new")
            workspace.commit()
        with open_update_workspace(destination) as workspace:
            self.assertEqual(workspace.directory.name, destination.stem)
            self.assertEqual((workspace.directory / "new.txt").read_text(), "new")

    def test_rejects_traversal_absolute_paths_symlinks_and_case_collisions(self):
        for entries in (["../escaped.txt"], ["/escaped.txt"], ["C:/escaped.txt"], ["..\\escaped.txt"], ["a.txt", "A.txt"], ["link"], ["CON.txt"]):
            with self.subTest(entries=entries):
                destination = self.root / "malicious.zip"
                with zipfile.ZipFile(destination, "w") as archive:
                    for name in entries:
                        member = zipfile.ZipInfo(name)
                        if name == "link":
                            member.create_system = 3
                            member.external_attr = (stat.S_IFLNK | 0o777) << 16
                        archive.writestr(member, "bad")
                with self.assertRaises(ArchiveError):
                    with open_update_workspace(destination):
                        pass
                self.assertFalse((self.root / "escaped.txt").exists())
                self.assertEqual(list(self.root.glob(".tieba-*")), [])

    def test_rejects_output_inside_source(self):
        source = self.make_archive()
        with self.assertRaises(ArchiveError):
            create_zip(source, source / "bad.zip")

    def test_batch_export_continues_after_one_invalid_archive(self):
        good = self.make_archive()
        bad = self.make_archive("broken", 43)
        (bad / "threads/43/content.db").unlink()
        with redirect_stdout(io.StringIO()):
            result = export_archives(good.parent)
        self.assertEqual(len(result.exported), 1)
        self.assertEqual(len(result.failures), 1)
        self.assertTrue(good.exists())
        self.assertTrue(bad.exists())

    def test_missing_forum_metadata_falls_back_to_archive_name(self):
        source = self.make_archive()
        (source / "threads/42/thread.json").unlink()
        self.assertEqual(export_archive(source).parent.name, "测试吧")


if __name__ == "__main__":
    unittest.main()
