"""ZIP storage for the original TiebaScraper directory format.

Only the standard library is needed, so old archives can be exported offline.
"""

import json
import hashlib
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from contextlib import closing, contextmanager
from pathlib import Path, PurePosixPath, PureWindowsPath

from storage_config import StorageConfig, safe_filename


class ArchiveError(ValueError):
    pass


def normalize_source_path(value: str | Path) -> Path:
    # Windows users often paste a quoted path from Explorer.
    return Path(str(value).strip().strip('"')).expanduser().resolve()


def read_archive_info(directory: Path) -> dict:
    try:
        with (directory / "scrape_info.json").open(encoding="utf-8-sig") as source:
            info = json.load(source)
        tid = info["main_thread"]
        if type(tid) is not int or tid <= 0:
            raise ValueError("main_thread 必须是正整数")
        if not (directory / "threads" / str(tid) / "content.db").is_file():
            raise ValueError("未找到主帖的 content.db")
        return info
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ArchiveError(f"无效的帖子存档 {directory}: {exc}") from exc


def archive_forum_name(directory: Path) -> str:
    info = read_archive_info(directory)
    thread_directory = directory / "threads" / str(info["main_thread"])
    for filename, field in (("thread.json", "forum_name"), ("forum.json", "name")):
        try:
            with (thread_directory / filename).open(encoding="utf-8-sig") as source:
                name = json.load(source).get(field)
            if isinstance(name, str) and name:
                return name
        except (OSError, ValueError, AttributeError):
            pass
    # Older incomplete archives sometimes only retain the forum in the folder name.
    match = re.match(r"^\[(.*?)吧\]\[\d+\]", directory.name)
    return match.group(1) if match else ""


@contextmanager
def archive_lock(destination: Path):
    lock = destination.with_name(destination.name + ".lock")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ArchiveError(f"该压缩包正在被另一任务处理: {destination}") from exc
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        lock.unlink(missing_ok=True)


def _write_zip(source: Path, destination: Path, *, overwrite: bool, root_name: str | None = None,
               expected_stat: tuple[int, int] | None = None) -> Path:
    read_archive_info(source)
    if destination.is_relative_to(source):
        raise ArchiveError("压缩包不能保存在待导出的帖子目录内部")
    if destination.exists() and not overwrite:
        raise FileExistsError(f"压缩包已存在: {destination}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".tieba-", suffix=".zip.tmp", dir=destination.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    root_name = root_name or source.name
    try:
        with tempfile.TemporaryDirectory(prefix=".tieba-db-", dir=destination.parent) as db_directory:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                for directory, subdirectories, files in os.walk(source):
                    folder = Path(directory)
                    if any((folder / name).is_symlink() for name in subdirectories + files):
                        raise ArchiveError("帖子存档内不能包含符号链接")
                    # Keep the original top-level directory for TiebaReader after extraction.
                    archive.write(folder, (Path(root_name) / folder.relative_to(source)).as_posix())
                    for name in sorted(files):
                        path = folder / name
                        relative = (Path(root_name) / path.relative_to(source)).as_posix()
                        if name in ("content.db-wal", "content.db-shm", "content.db-journal"):
                            continue
                        if name == "content.db":
                            # Include committed WAL data even when an old archive is open in a reader.
                            snapshot = Path(db_directory) / "content.db"
                            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as original:
                                with closing(sqlite3.connect(snapshot)) as copy:
                                    original.backup(copy)
                            archive.write(snapshot, relative)
                            snapshot.unlink()
                        else:
                            archive.write(path, relative)
        if destination.exists() and not overwrite:
            raise FileExistsError(f"压缩包已存在: {destination}")
        if expected_stat is not None:
            current = destination.stat()
            if (current.st_mtime_ns, current.st_size) != expected_stat:
                raise ArchiveError("打包期间原 ZIP 被其他程序修改，已取消替换，请重新更新")
        os.replace(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def create_zip(source: str | Path, destination: str | Path, *, overwrite: bool = False) -> Path:
    source, destination = normalize_source_path(source), normalize_source_path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with archive_lock(destination):
        return _write_zip(source, destination, overwrite=overwrite)


def export_archive(source: str | Path, output_root: str | Path | None = None, *, overwrite: bool = False) -> Path:
    source = normalize_source_path(source)
    forum_name = archive_forum_name(source)
    filename = safe_filename(source.name, max_length=240)
    if len(source.name) > 240:
        filename = filename[:220] + "_" + hashlib.sha256(source.name.encode("utf-8")).hexdigest()[:12]
    destination = StorageConfig.forum_output_directory(forum_name, output_root) / (filename + ".zip")
    return create_zip(source, destination, overwrite=overwrite)


def find_archives(source: str | Path) -> list[Path]:
    source = normalize_source_path(source)
    if not source.is_dir():
        raise ArchiveError(f"存档目录不存在: {source}")
    result = []
    for directory, subdirectories, files in os.walk(source, followlinks=False):
        subdirectories[:] = sorted(name for name in subdirectories if not (Path(directory) / name).is_symlink() and not name.startswith(".tieba-"))
        if "scrape_info.json" in files:
            result.append(Path(directory))
            subdirectories.clear()
    if not result:
        raise ArchiveError(f"没有找到包含 scrape_info.json 的帖子存档: {source}")
    return result


def _extract_zip(source: Path, destination: Path) -> Path:
    with zipfile.ZipFile(source) as archive:
        entries = []
        seen = set()
        for member in archive.infolist():
            name = member.filename.replace("\\", "/")
            parts = PurePosixPath(name)
            if (parts.is_absolute() or PureWindowsPath(name).drive or ".." in parts.parts
                    or any(re.search(r'[<>:"|?*\x00-\x1f]', part) or part.endswith((".", " ")) for part in parts.parts)
                    or any(re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part) for part in parts.parts)
                    or stat.S_ISLNK(member.external_attr >> 16)):
                raise ArchiveError(f"压缩包包含不安全的路径: {member.filename}")
            target = destination.joinpath(*parts.parts).resolve()
            key = str(target).casefold()
            if not target.is_relative_to(destination.resolve()) or key in seen:
                raise ArchiveError(f"压缩包包含冲突的路径: {member.filename}")
            seen.add(key)
            entries.append((member, target))
        for member, target in entries:
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as incoming, target.open("wb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
    if (destination / "scrape_info.json").is_file():
        root = destination
    else:
        children = list(destination.iterdir())
        if len(children) != 1 or not children[0].is_dir():
            raise ArchiveError("ZIP 必须只包含一份帖子存档")
        root = children[0]
    read_archive_info(root)
    return root


class UpdateWorkspace:
    def __init__(self, directory: Path, archive: Path | None = None):
        self.directory = directory
        self.archive = archive
        self.original_stat = self._archive_stat() if archive is not None else None

    def _archive_stat(self) -> tuple[int, int]:
        info = self.archive.stat()
        return info.st_mtime_ns, info.st_size

    def commit(self) -> Path:
        if self.archive is None:
            return self.directory
        if self.original_stat != self._archive_stat():
            raise ArchiveError("更新期间原 ZIP 被其他程序修改，已取消替换，请重新更新")
        root_name = self.archive.stem if self.directory.name.startswith(".tieba-update-") else self.directory.name
        return _write_zip(self.directory, self.archive, overwrite=True, root_name=root_name, expected_stat=self.original_stat)


@contextmanager
def open_update_workspace(source: str | Path):
    source = normalize_source_path(source)
    if source.is_dir():
        read_archive_info(source)
        yield UpdateWorkspace(source)
    elif source.is_file() and source.suffix.lower() == ".zip":
        with archive_lock(source):
            with tempfile.TemporaryDirectory(prefix=".tieba-update-", dir=source.parent) as directory:
                original_stat = source.stat()
                root = _extract_zip(source, Path(directory))
                workspace = UpdateWorkspace(root, source)
                if workspace.original_stat != (original_stat.st_mtime_ns, original_stat.st_size):
                    raise ArchiveError("解压期间原 ZIP 被其他程序修改，请重新更新")
                yield workspace
    else:
        raise ArchiveError(f"请输入帖子存档目录或 ZIP 文件的路径: {source}")
