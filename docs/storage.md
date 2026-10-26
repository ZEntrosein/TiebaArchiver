# 存储与 ZIP 导出

## 默认行为

新抓取的帖子默认保存为一个 ZIP，并按主帖所属的吧创建目录：

```text
scraped_data/
└── 示例吧/
    └── [示例吧][123456789]帖子标题_时间戳.zip
```

压缩包内保留原有的帖子目录、SQLite 数据库、JSON 信息、图片、头像、语音、视频和日志；转发原帖也放在同一份 ZIP 中。数据库格式仍为上游 v1.3.1，无需迁移旧数据。用 TiebaReader 阅读时，先解压 ZIP，再打开原有格式的帖子目录。

## 设置保存位置和格式

启动程序，选择「修改存储设置（位置、格式、按吧分类）」，可以设置：

| 设置 | 可选值 | 默认值 |
| --- | --- | --- |
| 保存位置 | 默认位置 / 手动输入根目录 | 当前工作目录下的 `scraped_data` |
| 存档格式 | ZIP / 文件夹 | ZIP |
| 按吧名创建目录 | 是 / 否 | 是 |

设置保存在当前工作目录的 `storage_config.json` 中，与 `scrape_config.json` 分开，不会触发抓取过滤配置变更的提示。不存在配置文件时直接使用上述默认值。选择手动位置时，路径会保存为绝对路径；不存在的目录会在实际保存时创建。

也可以复制项目根目录的 `storage_config.example.json` 为 `storage_config.json`，例如：

```json
{
  "OUTPUT_DIRECTORY": "E:\\我的贴吧存档",
  "ARCHIVE_FORMAT": "zip",
  "GROUP_BY_FORUM": true
}
```

`OUTPUT_DIRECTORY` 为空字符串时使用默认位置。`ARCHIVE_FORMAT` 支持 `zip` 和 `folder`。旧版目录仍可直接更新；修改存储设置不会自动移动或转换已有文件。

## 更新 ZIP

选择「更新本地帖子（文件夹 / ZIP）」，输入 ZIP 的完整路径即可，包括从资源管理器复制的带双引号的路径。

程序在原 ZIP 所在目录创建临时工作目录，解压后使用原有抓取逻辑补充新回复及附件。成功后先生成完整的新 ZIP，再替换同一路径的原包。更新会保持 ZIP 的文件名和保存位置，不受当前默认保存根目录、格式和吧分类设置的影响。

取消配置确认、主帖无法获取、抛出异常或重新打包失败时，原 ZIP 保持不变。已保存的历史内容继续保留。原目录下需要有足够空间容纳临时解压的数据和一份新 ZIP。原抓取逻辑对个别楼层或附件的失败仍记录在日志中；任务完成不代表服务端返回了所有历史内容。

同一个 ZIP 正在处理时会存在 `<文件名>.zip.lock`，用于防止重复任务同时替换它。如果程序被强制终止，应确认没有任务仍在运行，再删除残留的 `.lock` 文件重试；`.tieba-update-*` 和 `.tieba-db-*` 是临时目录。新抓取任务若打包失败，会保留已经抓取到的文件夹并显示位置。

## 将旧存档批量导出为 ZIP

选择「将已有存档导出为 ZIP（支持批量）」，输入一份帖子的存档目录，或者包含多份帖子的父目录。程序按当前保存位置与吧分类设置，每个帖子目录生成一个 ZIP，并保留所有原始目录。

同一个帖子不同时间抓取的目录分别导出，保留各自的时间戳，不合并数据。遇到同名 ZIP 时默认保留已有 ZIP 并报告失败；更新已有 ZIP 请使用更新功能。某份存档导出失败时，会显示原因并继续处理其他存档。

也可以使用不依赖登录和抓取依赖的命令行工具。从项目根目录运行：

```powershell
python src/archive_cli.py export "E:\旧存档\scraped_data" --output "E:\我的贴吧存档"
```

取消吧目录分组或明确替换同名 ZIP：

```powershell
python src/archive_cli.py export "E:\旧存档\scraped_data" --output "E:\导出" --flat
python src/archive_cli.py export "E:\一份帖子存档" --output "E:\导出" --overwrite
```

Windows 可执行程序也接受相同的 `export` 参数。此操作只导出当前已有数据，不向贴吧发送请求。SQLite 通过备份接口获取一致的快照，包含已提交的 WAL 数据；ZIP 中不携带临时 WAL/SHM 文件。

## 从源码运行与验证

推荐使用 Python 3.12，在项目根目录运行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe src/cli_entry.py
```

抓取或在线更新时需要自行配置 `tieba_auth.json`；可以把旧程序同名配置文件复制到当前工作目录，避免重新输入。不要提交账号凭据、私人存档或个人配置；这些文件已加入 `.gitignore`。

测试使用临时合成存档并模拟贴吧请求，不会访问真实账号或修改已有私人存档：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Windows 下构建独立程序：

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\scripts\build.ps1
```

输出位于 `release/TiebaScraper_zip_win-x64.exe`。
