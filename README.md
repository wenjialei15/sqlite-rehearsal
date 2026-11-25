# SQLite Rehearsal · SQLite 升级演练

在旧数据库的临时副本上执行迁移，验证历史数据和业务规则，再决定是否发布。支持迁移 SQL、项目自己的迁移命令、文本输出和 JSON 报告。CLI 只使用 Python 3.9+ 标准库。

**[打开在线报告查看器](https://wenjialei15.github.io/sqlite-rehearsal/)** · **[查看 AgriPulse 迁移案例](#agripulse-案例)**

SQLite Rehearsal runs your migration against a disposable SQLite snapshot and reports schema changes, row differences, and project-defined assertions.

## 快速体验

在仓库根目录运行。下面的样例建立一个包含三条读数的合成旧库，在副本上新增索引，并检查读数是否完全一致。`example.db` 和 `report.json` 都需要使用尚不存在的文件名。

```sh
python examples/make_fixture.py example.db
python sqlite_rehearsal.py example.db --sql examples/add_index.sql --rules examples/preserve_readings.json --report report.json
```

输出会显示 `user_version` 从 `0` 变为 `1`，新增 `readings_source` 索引，三条读数保持一致。可以把 `report.json` 打开到在线报告查看器。浏览器在本地解析所选文件。实际迁移由本机 CLI 执行。

## 演练自己的迁移

```sh
python sqlite_rehearsal.py path/to/legacy.db --sql upgrade.sql --rules rules.json --report report.json
```

使用脚本或迁移框架时，把 `--command` 放在最后。`{db}` 会被替换为临时副本的绝对路径。命令从临时目录启动，脚本和其他输入请使用绝对路径。下面是命令写法示例，其中 `migrate.py` 由你的项目提供。

```sh
python sqlite_rehearsal.py legacy.db --rules rules.json --report report.json --command python /absolute/path/migrate.py "{db}"
```

CLI 以只读方式打开源库，用 SQLite Backup API 获取一致快照，包括已经提交的 WAL 数据，再从这份快照创建迁移副本。运行后自动检查 SQLite 完整性、外键以及配置的规则，并报告表、视图、索引、触发器和 `user_version` 的变化。结构差异用于审阅，本身不导致失败。副本在本次执行结束后清理。

`--sql` 执行本地 SQL 文件，默认启用外键，禁用 `ATTACH` 和 `DETACH`。如需显式事务，请在脚本内提交。`--command` 直接运行受信任的可执行程序，拥有当前用户权限。确保命令使用传入的副本路径并退出。`--timeout` 控制每次备份或迁移的超时，默认 30 秒。JSON 文件通过 `--report` 写入，只创建新文件，可能包含业务断言返回值和主键样本。

| 退出码 | 含义 |
| --- | --- |
| `0` | 迁移和所有配置检查通过 |
| `1` | 数据库、规则、迁移或写报告出错 |
| `2` | 检查未通过，或命令行参数无效。检查失败会生成结构化报告 |

### 规则文件

```json
{
  "preserve": [{"table": "readings", "key": ["id"]}],
  "assertions": [
    {
      "name": "至少注册一个设备",
      "query": "SELECT COUNT(*) > 0 FROM main.devices",
      "equals": 1
    }
  ]
}
```

- `preserve` 指定需要保持一致的表。`key` 支持复合键，在前后两个库中都必须唯一且没有 `NULL`。默认核对旧表的全部可见列，也可用 `columns` 指定要比较的列。报告分别统计新增、丢失和内容改动的行，并为每类最多列出 10 个主键样本。对大表，建议给比较键建立索引。
- `assertions` 运行只读 `SELECT` 或 `WITH` 查询，要求返回一行一列，并与 `equals` 比较。`main` 是升级后的库，`baseline` 是原始快照。使用 `SELECT 1` 和 `SELECT 0` 表示真假，规则中的 `equals` 使用 JSON 数字。
- 报告以字符串记录超出 JavaScript 安全整数范围的主键值或断言值，保留 64 位 SQLite 整数的完整精度。
- 不提供 `--rules` 时，报告覆盖数据库完整性、外键和结构变化。业务数据的预期由规则文件定义。

## AgriPulse 案例

示例使用合成的 v0.1.0 数据，通过 [AgriPulse](https://github.com/wenjialei15/agripulse) 的 `Store` 运行实际迁移。检查覆盖读数逐行保留、设备身份及时间戳、旧阈值复制到实机和模拟两种模式。带 `--simulate-loss` 的命令会在副本中额外删除读数 `102`，用于验证工具能发现业务数据丢失。

如果 AgriPulse 与本仓库在同一个父目录，可以在 PowerShell 中运行以下命令。若已做过快速体验，可复用 `example.db`。

```powershell
python examples/make_fixture.py example.db
$migration = (Resolve-Path examples/run_agripulse.py).Path
$agripulse = (Resolve-Path ../agripulse).Path
python sqlite_rehearsal.py example.db --rules examples/agripulse_rules.json --report report-pass.json --command python $migration '{db}' $agripulse
python sqlite_rehearsal.py example.db --rules examples/agripulse_rules.json --report report-fail.json --command python $migration '{db}' $agripulse --simulate-loss
```

## 本地验证

```sh
python -m unittest discover -s tests -v
python -m http.server 8000 --directory docs
```

浏览器打开 `http://localhost:8000` 查看报告演示。GitHub Pages 使用 `main` 分支的 `/docs` 目录。

[MIT License](LICENSE)
