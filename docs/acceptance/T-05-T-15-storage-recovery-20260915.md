# T-05 / T-15 SQLite 迁移、备份和 WAL 验收

- 日期：2026-09-15
- 范围：版本迁移、自动备份、恢复演练、WAL checkpoint

## 实现结果

- 新增 `schema_migrations(version, checksum, applied_at)`。
- 初始 schema 记录为 migration v1。
- 迁移前使用 SQLite backup API 创建时间戳备份，默认保留最近 7 份。
- 每个 migration 在单事务内执行；失败时回滚并保留原库。
- 新增 `scripts/backup_db.py` 和 `scripts/restore_db.py`。
- `restore_db.py` 默认拒绝覆盖已存在数据库，只有显式 `--force` 才允许。
- 干净停机会执行 `PRAGMA wal_checkpoint(TRUNCATE)`。
- 启动时 WAL 超过 64 MiB 会写 WARN，不执行强制 checkpoint。

## 自动验证

```text
.venv\Scripts\python.exe -m pytest -q tests/test_storage_migrations.py tests/test_storage_wal.py
6 passed

.venv\Scripts\python.exe -m pytest -q
285 passed, 1 warning

.venv\Scripts\python.exe -m ruff check .
All checks passed!

.venv\Scripts\python.exe -m pyright
0 errors, 0 warnings, 0 informations
```

覆盖场景：

- 新库初始化并记录 migration v1。
- 旧库升级时先备份，再补充缺失列，旧数据保留。
- migration 失败时不写入 version，也不留下半成品表。
- 备份文件 integrity_check 为 ok。
- 恢复到已存在目标时默认拒绝，`--force` 后才覆盖。
- 干净关闭后 WAL 被 truncate 或消失。
- 大 WAL 启动时产生 WARN。

## 真实恢复演练

对当前真实状态库执行在线备份，并恢复到临时目标库：

```text
source_integrity=ok
backup_integrity=ok
restored_integrity=ok
backup_created=True
restored_created=True
```

演练没有覆盖原库。备份和恢复目标均位于 `.pytest-tmp/t05-recovery/`。

## 运行命令

```text
.venv\Scripts\python.exe scripts\backup_db.py --database state\qqf.db --backup-dir state\backups --keep 7
.venv\Scripts\python.exe scripts\restore_db.py --backup state\backups\<backup>.db --database state\qqf.db --force
```

恢复前必须先停止 QQF。`--force` 会先生成 pre-restore backup，再替换目标数据库。
