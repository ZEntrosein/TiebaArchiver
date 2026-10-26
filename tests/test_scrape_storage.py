import asyncio
import io
import json
import sqlite3
import sys
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from test_storage import StorageTestCase
from container.container import Container
from modules import scrape_module, scrape_update_module
from scrape_config import ScrapeConfig
from storage_config import StorageConfig
from utils.archive import export_archive, open_update_workspace


class ScrapeStorageTests(StorageTestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(Container.close_thread_resources)
        self.output = io.StringIO()
        self.stdout = redirect_stdout(self.output)
        self.stdout.__enter__()
        self.addCleanup(self.stdout.__exit__, None, None, None)

    async def fake_scrape_thread(self, tid):
        builder = Container.get_scrape_data_path_builder()
        thread = Path(builder.get_thread_dir(tid))
        thread.mkdir(parents=True, exist_ok=True)
        (thread / "thread.json").write_text(json.dumps({"id": tid, "forum_name": "测试", "share_origin": 0}))
        Container.set_tid(tid)
        Container.get_content_db().execute("INSERT INTO post(id,contents,floor,user_id,create_time) VALUES(1,'[]',1,1,1)")
        Container.get_scrape_logger().info("finished")
        return True

    def run_scrape(self):
        preload = SimpleNamespace(forum=SimpleNamespace(fname="测试"), thread=SimpleNamespace(title="标题", share_origin=SimpleNamespace(tid=0)))
        with patch.object(scrape_module, "get_posts", AsyncMock(return_value=preload)), patch.object(scrape_module, "scrape_thread", side_effect=self.fake_scrape_thread):
            return asyncio.run(scrape_module.scrape(42))

    def test_new_scrape_produces_one_grouped_zip_and_releases_handles(self):
        destination = self.run_scrape()
        self.assertEqual(destination.suffix, ".zip")
        self.assertEqual(destination.parent, self.root / "output" / "测试吧")
        self.assertEqual(list(destination.parent.iterdir()), [destination])
        self.assertIsNone(Container.content_db)
        self.assertIsNone(Container.scrape_logger)
        with open_update_workspace(destination) as workspace:
            with closing(sqlite3.connect(workspace.directory / "threads/42/content.db")) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM post").fetchone()[0], 1)

    def test_folder_mode_still_supported(self):
        StorageConfig.ARCHIVE_FORMAT = "folder"
        destination = self.run_scrape()
        self.assertTrue(destination.is_dir())
        self.assertTrue((destination / "scrape_info.json").exists())

    def test_failed_pack_preserves_downloaded_data(self):
        with patch.object(scrape_module, "create_zip", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.run_scrape()
        directories = list((self.root / "output" / "测试吧").iterdir())
        self.assertEqual(len(directories), 1)
        self.assertTrue((directories[0] / "threads/42/content.db").is_file())

    def test_zip_update_preserves_old_rows_and_adds_new_rows_in_same_zip(self):
        source = self.make_archive(full_db=True)
        destination = export_archive(source)
        original = self.digest(destination)
        StorageConfig.OUTPUT_DIRECTORY = str(self.root / "another-output")

        async def update(tid):
            Container.set_tid(tid)
            builder = Container.get_scrape_data_path_builder()
            self.assertTrue(Path(builder.get_thread_info_path(tid)).is_file())
            Container.get_content_db().execute("INSERT INTO post(id,contents,floor,user_id,create_time) VALUES(2,'[]',2,1,2)")
            Container.get_scrape_logger().info("updated")
            return True

        with patch.object(scrape_update_module, "update_thread", side_effect=update):
            self.assertEqual(asyncio.run(scrape_update_module.scrape_update(str(destination))), destination)
        self.assertNotEqual(self.digest(destination), original)
        with open_update_workspace(destination) as workspace:
            with closing(sqlite3.connect(workspace.directory / "threads/42/content.db")) as db:
                self.assertEqual(db.execute("SELECT id FROM post ORDER BY id").fetchall(), [(1,), (2,)])
            info = json.loads((workspace.directory / "scrape_info.json").read_text())
            self.assertEqual(len(info["scrape_records"]), 1)
            self.assertEqual((workspace.directory / "threads/42/post_assets/images/中文图片.jpeg").read_bytes(), b"image-content")
        self.assertFalse((self.root / "another-output").exists())

    def test_zip_update_exception_retains_original_and_cleans_temp_files(self):
        destination = export_archive(self.make_archive(full_db=True))
        original = self.digest(destination)

        async def fail(tid):
            Container.set_tid(tid)
            Container.get_content_db().execute("DELETE FROM post")
            Container.get_scrape_logger().info("failed update")
            raise OSError("connection failed")

        with patch.object(scrape_update_module, "update_thread", side_effect=fail):
            with self.assertRaises(OSError):
                asyncio.run(scrape_update_module.scrape_update(str(destination)))
        self.assertEqual(self.digest(destination), original)
        self.assertEqual(list(destination.parent.glob(".tieba-*")), [])
        self.assertIsNone(Container.content_db)
        self.assertIsNone(Container.scrape_logger)

    def test_cancelled_update_does_not_rewrite_zip(self):
        source = self.make_archive(full_db=True)
        info_path = source / "scrape_info.json"
        info = json.loads(info_path.read_text())
        info["scrape_records"] = [{"scrape_time": 1, "scrape_config": ScrapeConfig.to_dict()}]
        info_path.write_text(json.dumps(info))
        destination = export_archive(source)
        original = self.digest(destination)
        with patch.object(scrape_update_module, "confirm_config", AsyncMock(return_value=False)), patch.object(scrape_update_module, "update_thread", AsyncMock()) as update:
            self.assertIsNone(asyncio.run(scrape_update_module.scrape_update(str(destination))))
            update.assert_not_called()
        self.assertEqual(self.digest(destination), original)

    def test_deleted_or_unreachable_thread_does_not_rewrite_zip(self):
        destination = export_archive(self.make_archive(full_db=True))
        original = self.digest(destination)
        with patch.object(scrape_update_module, "get_posts", AsyncMock(return_value=None)):
            self.assertIsNone(asyncio.run(scrape_update_module.scrape_update(str(destination))))
        self.assertEqual(self.digest(destination), original)

    def test_disabled_shared_thread_update_does_not_fetch(self):
        source = self.make_archive(full_db=True)
        from config.path_config import ScrapeDataPathBuilder
        Container.set_scrape_data_path_builder(ScrapeDataPathBuilder(str(source)))
        Container.set_scrape_timestamp(100)
        with patch.object(ScrapeConfig, "UPDATE_SHARE_ORIGIN", False), patch.object(scrape_update_module, "get_posts", AsyncMock()) as get_posts:
            self.assertTrue(asyncio.run(scrape_update_module.update_thread(42, is_share_origin=True)))
            get_posts.assert_not_called()


if __name__ == "__main__":
    unittest.main()
