import asyncio
import os
import sys
import sqlite3
import zipfile
from enum import IntEnum, auto
from pathlib import Path

import orjson
import questionary

from archive_cli import configure_console, export_archives, main as archive_main
from modules.scrape_module import scrape
from modules.scrape_update_module import scrape_update
from scrape_config import DownloadUserAvatarMode, ScrapeConfig, ScrapeConfigKeys, PostFilterType
from tieba_auth import TiebaAuth
from storage_config import StorageConfig
from utils.cli_questionary import InfoStyle
from utils.common import counter_gen, json_dumps
from utils.msg_printer import MsgPrinter

counter = counter_gen()
next(counter)  # 预激一次生成器

TIEBA_AUTH_FILENAME = "tieba_auth.json"


def read_tieba_auth() -> None:
    tieba_auth_file_path = os.path.join(os.getcwd(), TIEBA_AUTH_FILENAME)

    try:
        with open(tieba_auth_file_path, "r", encoding="utf-8") as f:
            TiebaAuth.from_dict(orjson.loads(f.read()))
    except Exception:
        BDUSS = questionary.text("未配置BDUSS, 请输入: ").ask()
        TiebaAuth.BDUSS = BDUSS
        with open(tieba_auth_file_path, "w", encoding="utf-8") as f:
            f.write(json_dumps(TiebaAuth.to_dict()))


SCRAPE_CONFIG_FILENAME = "scrape_config.json"
scrape_config_file_path = os.path.join(os.getcwd(), SCRAPE_CONFIG_FILENAME)


def read_scrape_config() -> None:
    try:
        with open(scrape_config_file_path, "r", encoding="utf-8") as f:
            ScrapeConfig.from_dict(orjson.loads(f.read()))
    except FileNotFoundError:
        if questionary.confirm("未找到配置文件, 是否使用默认配置并生成文件?").ask():
            write_scrape_config()
        else:
            sys.exit()
    except orjson.JSONDecodeError:
        if questionary.confirm("配置文件格式错误导致解析失败, 是否使用默认配置并生成文件?").ask():
            write_scrape_config()
        else:
            sys.exit()
    except ValueError as err:
        if questionary.confirm(f"配置变量错误: {str(err)}, 是否使用默认配置并生成文件?").ask():
            write_scrape_config()
        else:
            sys.exit()


def write_scrape_config() -> None:
    with open(scrape_config_file_path, "w", encoding="utf-8") as f:
        f.write(json_dumps(ScrapeConfig.to_dict()))


def set_scrape_config() -> None:
    counter.send((0, 1))
    set_scrape_config_choice = [
        questionary.Choice(
            f"{next(counter)}. 过滤帖子({ScrapeConfigKeys.POST_FILTER_TYPE})",
            ScrapeConfigKeys.POST_FILTER_TYPE,
        ),
        questionary.Choice(
            f"{next(counter)}. 头像保存模式({ScrapeConfigKeys.DOWNLOAD_USER_AVATAR_MODE})",
            ScrapeConfigKeys.DOWNLOAD_USER_AVATAR_MODE,
        ),
        questionary.Choice(
            f"{next(counter)}. 是否爬取转发的原帖({ScrapeConfigKeys.SCRAPE_SHARE_ORIGIN})",
            ScrapeConfigKeys.SCRAPE_SHARE_ORIGIN,
        ),
        questionary.Choice(
            f"{next(counter)}. 是否更新转发的原帖({ScrapeConfigKeys.UPDATE_SHARE_ORIGIN})",
            ScrapeConfigKeys.UPDATE_SHARE_ORIGIN,
        ),
        questionary.Choice(
            f"{next(counter)}. 退出",
            "exit",
        ),
    ]

    while True:
        scrape_config_key = questionary.select("选择配置项", choices=set_scrape_config_choice).ask()
        if ScrapeConfigKeys.POST_FILTER_TYPE == scrape_config_key:
            counter.send((0, 1))
            post_filter_type_choices = [
                questionary.Choice(
                    f"{next(counter)}. '所有的 post' + 'post 下的所有 subpost'({PostFilterType.ALL})",
                    PostFilterType.ALL,
                ),
                questionary.Choice(
                    f"{next(counter)}. 'thread_author 的 post' + 'post 下的所有 subpost'({PostFilterType.AUTHOR_POSTS_WITH_SUBPOSTS})",
                    PostFilterType.AUTHOR_POSTS_WITH_SUBPOSTS,
                ),
                questionary.Choice(
                    f"{next(counter)}. 'thread_author 的 post' + 'post 下 thread_author 的 subpost'({PostFilterType.AUTHOR_POSTS_WITH_AUTHOR_SUBPOSTS})",
                    PostFilterType.AUTHOR_POSTS_WITH_AUTHOR_SUBPOSTS,
                ),
                questionary.Choice(
                    f"{next(counter)}. 'thread_author 的 post 和 thread_author 回复过的 post' + 'post 下所有的 subpost'({PostFilterType.AUTHOR_AND_REPLIED_POSTS_WITH_SUBPOSTS})",
                    PostFilterType.AUTHOR_AND_REPLIED_POSTS_WITH_SUBPOSTS,
                ),
                questionary.Choice(
                    f"{next(counter)}. 'thread_author 的 post 和 thread_author 回复过的 post' + 'post 下 thread_author 的 subpost'({PostFilterType.AUTHOR_AND_REPLIED_POSTS_WITH_AUTHOR_SUBPOSTS})",
                    PostFilterType.AUTHOR_AND_REPLIED_POSTS_WITH_AUTHOR_SUBPOSTS,
                ),
            ]
            post_filter_type = questionary.select("选择帖子过滤模式", choices=post_filter_type_choices).ask()
            ScrapeConfig.POST_FILTER_TYPE = post_filter_type
            write_scrape_config()
        elif ScrapeConfigKeys.DOWNLOAD_USER_AVATAR_MODE == scrape_config_key:
            counter.send((0, 1))
            download_user_avatar_mode_choices = [
                questionary.Choice(
                    f"{next(counter)}. 不保存({DownloadUserAvatarMode.NONE})", DownloadUserAvatarMode.NONE
                ),
                questionary.Choice(
                    f"{next(counter)}. 保存低清({DownloadUserAvatarMode.LOW})", DownloadUserAvatarMode.LOW
                ),
                questionary.Choice(
                    f"{next(counter)}. 保存高清({DownloadUserAvatarMode.HIGH})", DownloadUserAvatarMode.HIGH
                ),
            ]
            download_user_avatar_mode = questionary.select(
                "选择头像保存模式", choices=download_user_avatar_mode_choices
            ).ask()
            ScrapeConfig.DOWNLOAD_USER_AVATAR_MODE = download_user_avatar_mode
            write_scrape_config()
        elif ScrapeConfigKeys.SCRAPE_SHARE_ORIGIN == scrape_config_key:
            scrape_share_origin = questionary.confirm("是否爬取转发的原帖?").ask()
            ScrapeConfig.SCRAPE_SHARE_ORIGIN = scrape_share_origin
            write_scrape_config()
        elif ScrapeConfigKeys.UPDATE_SHARE_ORIGIN == scrape_config_key:
            update_share_origin = questionary.confirm("是否更新转发的原帖?").ask()
            ScrapeConfig.UPDATE_SHARE_ORIGIN = update_share_origin
            write_scrape_config()
        elif "exit" == scrape_config_key:
            break


def set_storage_config() -> None:
    StorageConfig.load()
    while True:
        print(f"当前保存位置: {StorageConfig.output_root()}")
        selected = questionary.select("存储设置", choices=[
            questionary.Choice("保存位置：默认位置 / 手动设置", "directory"),
            questionary.Choice(f"存档格式：{StorageConfig.ARCHIVE_FORMAT}", "format"),
            questionary.Choice(f"按吧名创建目录：{'是' if StorageConfig.GROUP_BY_FORUM else '否'}", "group"),
            questionary.Choice("返回", "back"),
        ]).ask()
        if selected in (None, "back"):
            return
        if selected == "directory":
            mode = questionary.select("选择保存位置", choices=[
                questionary.Choice("默认位置（当前工作目录下的 scraped_data）", "default"),
                questionary.Choice("手动指定目录", "custom"),
            ]).ask()
            if mode == "default":
                StorageConfig.OUTPUT_DIRECTORY = ""
            elif mode == "custom":
                directory = questionary.text("请输入保存根目录：", default=str(StorageConfig.output_root())).ask()
                if not directory or not directory.strip():
                    continue
                directory = directory.strip().strip('"')
                resolved = Path(os.path.expandvars(directory)).expanduser().resolve()
                if resolved.exists() and not resolved.is_dir():
                    MsgPrinter.print_error("该路径是文件，请选择目录", "StorageConfig")
                    continue
                StorageConfig.OUTPUT_DIRECTORY = str(resolved)
            else:
                continue
        elif selected == "format":
            value = questionary.select("选择存档格式", choices=[
                questionary.Choice("ZIP 压缩包（每个帖子一个包，支持直接更新）", "zip"),
                questionary.Choice("文件夹（原始存档格式）", "folder"),
            ]).ask()
            if value is None:
                continue
            StorageConfig.ARCHIVE_FORMAT = value
        elif selected == "group":
            value = questionary.confirm("是否在保存根目录下按吧名创建目录？", default=StorageConfig.GROUP_BY_FORUM).ask()
            if value is None:
                continue
            StorageConfig.GROUP_BY_FORUM = value
        StorageConfig.save()
        MsgPrinter.print_success("存储设置已保存", "StorageConfig")


class ProgramFeatures(IntEnum):
    SCRAPE = auto()
    SCRAPE_UPDATE = auto()
    MODIFY_SCRAPE_CONFIG = auto()
    EXPORT_ZIP = auto()
    MODIFY_STORAGE_CONFIG = auto()
    EXIT = auto()


def main():
    counter.send((0, 1))

    features_choices = [
        questionary.Choice(
            f"{next(counter)}. 爬取帖子",
            ProgramFeatures.SCRAPE,
        ),
        questionary.Choice(
            f"{next(counter)}. 更新本地帖子（文件夹 / ZIP）",
            ProgramFeatures.SCRAPE_UPDATE,
        ),
        questionary.Choice(
            f"{next(counter)}. 将已有存档导出为 ZIP（支持批量）",
            ProgramFeatures.EXPORT_ZIP,
        ),
        questionary.Choice(
            f"{next(counter)}. 修改存储设置（位置、格式、按吧分类）",
            ProgramFeatures.MODIFY_STORAGE_CONFIG,
        ),
        questionary.Choice(
            f"{next(counter)}. 修改爬取配置",
            ProgramFeatures.MODIFY_SCRAPE_CONFIG,
        ),
        questionary.Choice(f"{next(counter)}. 退出", ProgramFeatures.EXIT),
    ]
    while True:
        try:
            selected_features = questionary.select("选择功能", choices=features_choices, style=InfoStyle).ask()
            if selected_features in (None, ProgramFeatures.EXIT):
                return
            if ProgramFeatures.SCRAPE == selected_features:
                read_tieba_auth()
                read_scrape_config()
                StorageConfig.load()
                value = questionary.text("请输入要爬取的帖子的tid: ").ask()
                if value is None:
                    continue
                try:
                    tid = int(value)
                    if tid <= 0:
                        raise ValueError
                except ValueError:
                    MsgPrinter.print_error("tid 必须为正整数", "Scrape")
                    continue
                asyncio.run(scrape(tid))
            elif ProgramFeatures.SCRAPE_UPDATE == selected_features:
                read_tieba_auth()
                read_scrape_config()
                StorageConfig.load()
                path = questionary.text("请输入帖子存档目录或 ZIP 文件的路径: ").ask()
                if path:
                    asyncio.run(scrape_update(path))
            elif ProgramFeatures.EXPORT_ZIP == selected_features:
                StorageConfig.load()
                path = questionary.text("请输入单份帖子目录或存档根目录: ").ask()
                if path:
                    export_archives(path)
            elif ProgramFeatures.MODIFY_STORAGE_CONFIG == selected_features:
                set_storage_config()
            elif ProgramFeatures.MODIFY_SCRAPE_CONFIG == selected_features:
                read_scrape_config()
                set_scrape_config()
        except (ValueError, OSError, sqlite3.Error, zipfile.BadZipFile) as exc:
            MsgPrinter.print_error(str(exc), "Operation")
        except KeyboardInterrupt:
            return


if __name__ == "__main__":
    configure_console()
    if len(sys.argv) > 1:
        raise SystemExit(archive_main())
    main()
