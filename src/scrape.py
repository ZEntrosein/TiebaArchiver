# 爬取一个帖子现存的全部内容并保存到本地

import asyncio

from modules.scrape_module import scrape


def main():
    from cli_entry import read_tieba_auth, read_scrape_config
    from storage_config import StorageConfig

    read_tieba_auth()
    read_scrape_config()
    StorageConfig.load()
    tid = int(input("请输入要爬取的帖子的tid: "))
    asyncio.run(scrape(tid))


if __name__ == "__main__":
    main()
