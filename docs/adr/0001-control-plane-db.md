# ADR 0001: 控制面数据与单 worker 约束

状态: Accepted (2026-07-15) · 依据 spec §8.2 用户裁决

## 决策
1. 控制面数据（异步 job 状态 / API key / request log）存**独立 SQLite 库**
   （`sodamem_control.db`），与 per-user 记忆 store 物理分离。
   理由: 放进用户 store 会让 GDPR `forget(user_id)` 连带删除 job/用量记录;
   job 逻辑上不属于任何单一用户。
2. **单 worker 是正确性约束,不是性能取舍。** per-user SQLite 无 WAL 跨进程写不安全,
   多 worker 会损坏同一用户 store。server 文档必须写死 `--workers 1`;
   水平扩展留待外部 job store(P2)。
3. **异步 ingest 的失败必须是可检索终态**（no silent failure 的分布式版本）:
   - `POST /ingest` → `202 { job_id }`
   - `GET /jobs/{job_id}` → `{ status: pending|running|failed|done, error: {code, message, details} }`
   - `IngestError` 在 worker 内被捕获,序列化进 job 的 `error` 字段;`failed` 是终态且必读。
   （error 字段与 sodamem/errors.py 的 SodaMemError(code/message/details) 一一对应,勿再漂移。）
4. v1 用进程内 asyncio task + 控制面 SQLite 状态表(~150 行),**不上 celery/redis**。
   队列 key = `user_id`。理由: ADD-only + 读时 currency 派生使图 ingest 的顺序 race
   不存在(对比 Graphiti 的 per-group_id 队列),故队列只为延迟而建,不为顺序。

## 后果
- server 部署形态锁定: 1 个 uvicorn worker + 2 个 SQLite 面(控制面 + per-user)。
- Phase 4 实现直接引用本 ADR;job 失败契约进契约测试。

## 实现状态 (2026-07-29)

已落地,四条决策逐条对应:

| 决策 | 实现 |
|---|---|
| 1 独立控制面库 | `server/control.py` → `<data_root>/.control/sodamem_control.db`。目录以点开头,而 `server/stores.py` 的 `_USER_ID_RE` 要求 user_id 以字母数字开头 —— 隔离是结构性的,不是命名约定。 |
| 2 单 worker | Dockerfile CMD 显式 `--workers 1`;`acquire_data_root_lock()` 对 data root 加 flock,第二个进程以 `data_root_locked` 拒绝启动。此前只是"uvicorn 默认值恰好是 1"。 |
| 3 失败即可检索终态 | job 行持久化;`reconcile_orphaned_jobs()` 在启动时把上个进程遗留的 `pending`/`running` 关成 `failed` + `server_restarted`。`error` 取 SodaMemError 的 `code`(如 `config_invalid: ...`)而非 Python 类名。 |
| 4 不上 celery/redis | 仍是进程内线程池,只把 registry 换成控制面表。 |

两处 ADR 未写、实现时补的约束:

- `request_logs` / `jobs` 都有行数上限,裁剪与 insert 同事务。依据是 audit_bundles
  事故(无上限写入,325 个库 1,871 行,回收 ~500MB):运维表没有天花板就是延时引信。
- 成功的 `/health` 不入库。Docker 每 30s 探一次 = 每天 2,880 行,10,000 行窗口
  不到四天就只剩自己的心跳。失败的探针照记。

对应测试: `tests/test_control_plane.py`(27 条)。
