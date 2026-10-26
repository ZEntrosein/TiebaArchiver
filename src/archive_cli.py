"""Export existing TiebaScraper archives without a login or scraping dependencies."""

import argparse
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

from storage_config import StorageConfig
from utils.archive import ArchiveError, export_archive, find_archives


def configure_console() -> None:
    # Frozen Python otherwise uses the Windows ANSI code page for redirected output.
    # Archive names may contain Chinese characters or emoji even in batch scripts.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


@dataclass
class ExportResult:
    exported: list[Path] = field(default_factory=list)
    failures: list[tuple[Path, str]] = field(default_factory=list)


def export_archives(source: str | Path, output_root: str | Path | None = None, *, overwrite: bool = False) -> ExportResult:
    # Collect the inputs before creating output, including when the output root is under source.
    archives = find_archives(source)
    result = ExportResult()
    for index, directory in enumerate(archives, 1):
        try:
            destination = export_archive(directory, output_root, overwrite=overwrite)
            result.exported.append(destination)
            print(f"[{index}/{len(archives)}] 已导出: {destination}")
        except (OSError, ValueError, sqlite3.Error) as exc:
            result.failures.append((directory, str(exc)))
            print(f"[{index}/{len(archives)}] 导出失败: {directory}\n  {exc}")
    print(f"导出完成: 成功 {len(result.exported)} 份，失败 {len(result.failures)} 份；原始目录保留。")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="将已有贴吧存档逐帖导出为 ZIP，保留原始目录。")
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="导出单份帖子目录或批量导出存档根目录")
    export.add_argument("source", help="帖子存档目录，或包含多份存档的父目录")
    export.add_argument("--output", help="保存根目录；默认使用 storage_config.json 的设置")
    group = export.add_mutually_exclusive_group()
    group.add_argument("--flat", dest="group", action="store_false", help="不创建吧名目录")
    group.add_argument("--group-by-forum", dest="group", action="store_true", help="按吧名创建目录")
    export.set_defaults(group=None)
    export.add_argument("--overwrite", action="store_true", help="替换同名 ZIP；默认遇到同名文件会报错并保留原包")
    args = parser.parse_args(argv)
    try:
        StorageConfig.load()
        if args.group is not None:
            StorageConfig.GROUP_BY_FORUM = args.group
        result = export_archives(args.source, args.output, overwrite=args.overwrite)
        return 1 if result.failures else 0
    except (ArchiveError, OSError, ValueError) as exc:
        parser.exit(1, f"导出失败: {exc}\n")


if __name__ == "__main__":
    configure_console()
    raise SystemExit(main())
