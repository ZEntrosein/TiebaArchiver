"""Storage preferences, kept separate from the recorded scraping configuration."""

import json
import os
import re
from pathlib import Path


def safe_filename(value: str, fallback: str = "未命名", max_length: int = 100) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", value).strip().rstrip(". ")
    value = value[:max_length].rstrip(". ") or fallback
    if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", value):
        value = "_" + value
    return value


def forum_directory_name(forum_name: str) -> str:
    # fname is the actual forum name: do not strip a real trailing “吧”.
    return safe_filename(forum_name + "吧" if forum_name else "未知贴吧", max_length=60)


class StorageConfig:
    OUTPUT_DIRECTORY = ""
    ARCHIVE_FORMAT = "zip"
    GROUP_BY_FORUM = True

    @classmethod
    def from_dict(cls, data: dict) -> None:
        if not isinstance(data, dict):
            raise ValueError("存储配置必须是 JSON 对象")
        directory = data.get("OUTPUT_DIRECTORY", "")
        archive_format = data.get("ARCHIVE_FORMAT", "zip")
        group = data.get("GROUP_BY_FORUM", True)
        if not isinstance(directory, str) or "\x00" in directory:
            raise ValueError("OUTPUT_DIRECTORY 必须是有效的路径字符串")
        if archive_format not in ("zip", "folder"):
            raise ValueError("ARCHIVE_FORMAT 必须是 zip 或 folder")
        if type(group) is not bool:
            raise ValueError("GROUP_BY_FORUM 必须为 true 或 false")
        cls.OUTPUT_DIRECTORY, cls.ARCHIVE_FORMAT, cls.GROUP_BY_FORUM = directory, archive_format, group

    @classmethod
    def to_dict(cls) -> dict:
        return {key: getattr(cls, key) for key in ("OUTPUT_DIRECTORY", "ARCHIVE_FORMAT", "GROUP_BY_FORUM")}

    @classmethod
    def load(cls, filename: str | Path = "storage_config.json") -> None:
        try:
            with open(filename, encoding="utf-8-sig") as source:
                data = json.load(source)
        except FileNotFoundError:
            data = {}
        cls.from_dict(data)

    @classmethod
    def save(cls, filename: str | Path = "storage_config.json") -> None:
        # Validate before persisting preferences edited by the CLI.
        cls.from_dict(cls.to_dict())
        with open(filename, "w", encoding="utf-8") as target:
            json.dump(cls.to_dict(), target, ensure_ascii=False, indent=2)
            target.write("\n")

    @classmethod
    def output_root(cls) -> Path:
        if not cls.OUTPUT_DIRECTORY.strip():
            return Path.cwd() / "scraped_data"
        return Path(os.path.expandvars(cls.OUTPUT_DIRECTORY)).expanduser().resolve()

    @classmethod
    def forum_output_directory(cls, forum_name: str, root: str | Path | None = None) -> Path:
        directory = Path(root).expanduser().resolve() if root is not None else cls.output_root()
        return directory / forum_directory_name(forum_name) if cls.GROUP_BY_FORUM else directory
