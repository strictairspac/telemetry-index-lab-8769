# rlw · 设备遥测补录/更正的读写一致性子系统

巡检补录或更正遥测后，紧接着的查询必须读到新值（read-your-writes）。本模块提供成对的读写接口：
**主库写入 → 签发设备凭据 → 携凭据查询时与真实只读副本对账 → 确认这笔写入已被副本应用才从副本返回；
副本没追上最多等 1 秒，可回源主库；回源并发槽满则返回"稍后重试"。**

零外部依赖，仅使用 Python 3.11 标准库，可直接在本机运行与验证。

## 需求与实现对照

| 需求 | 实现 |
|---|---|
| 读写接口 | `POST /api/telemetry/ingest`、`GET /api/telemetry`（同时有 Python 服务层 `TelemetryService`） |
| 主库写成后给出设备凭据 | 写入与复制日志同事务提交后，HMAC-SHA256 签名签发 `rlwv1.` 凭据，内含设备 ID 与该写入的复制水位 seq |
| 携凭据查询，用真实只读副本对账 | 副本独立数据文件、以 `mode=ro` 只读打开；查询时比较 `replica.applied_seq >= 凭据 seq`，确认已应用该笔写入才从副本返回 |
| 副本没追上最多等一秒 | `replica_wait_seconds=1.0`，窗口内每 20ms 重新对账一次，超时即止 |
| 可以回主库 | 超时未追上则回源主库，响应 `read_source=primary`、`fell_back_to_primary=true` |
| 回源并发已满请稍后重试 | 主库读受信号量限流（默认 8），非阻塞获取槽位失败立即返回 HTTP `503` + `Retry-After: 1`，错误码 `retry_later` |
| 凭据不能跨设备用 | 凭据 payload 绑定设备，查询设备不一致返回 HTTP `403 credential_device_mismatch`；伪造/篡改/过期返回 401 |
| 查询响应标明实际读库 | 每个读响应含 `read_source: primary|replica`，并附对账水位 `required_seq`、`replica_applied_seq`、实际等待时长 |

## 架构

```
                 写（补录/更正）                       读（携设备凭据）
                      │                                     │
                      ▼                                     ▼
              ┌───────────────┐                   ┌──────────────────────┐
              │   主库 primary │                   │ 对账：applied_seq    │
              │  telemetry    │                   │   >= 凭据 seq ?      │
              │  replication_ │  applier 线程     └─────────┬────────────┘
              │  log (binlog) │──按序回放(可暂停)──▶┌────────▼─────────┐
              └───────────────┘                     │ 只读副本 replica │
                    ▲                                │  (mode=ro 强制)  │
                    │ 1 秒窗口超时后回源（信号量限流） └──────────────────┘
              槽满 → 503 Retry-After: 1
```

单机双 SQLite 文件是 MySQL 主从拓扑的同构缩小版，概念一一对应：

| 本模块 | MySQL 主从对应 |
|---|---|
| `replication_log.seq` 单调递增 | binlog 事件 / GTID 序号 |
| 业务行与日志同事务提交 | 原子 binlog（事务与 binlog 一致提交） |
| applier 线程按序回放 + `replica_status.applied_seq` | SQL 线程回放 + `SHOW REPLICA STATUS` 的已应用 GTID 集 |
| `pause_replication()` / `resume_replication()` | `STOP REPLICA` / `START REPLICA` |
| 副本 `mode=ro` 连接 | 只读实例（应用侧无法写穿） |
| 水位对账 `applied_seq >= required_seq` | 读己之写：等待副本应用到指定 GTID 后再服务查询 |

存储抽象集中在 `storage.py`，生产环境换成真实 MySQL 主库/只读实例时，服务层、凭据与对账逻辑无需改动。

## HTTP 接口

### 写入（补录/更正）

```sh
curl -s -X POST http://127.0.0.1:19341/api/telemetry/ingest \
  -H 'Content-Type: application/json' \
  -d '{"device_id":"pump-17","metric":"bearing_temp","value":43.5,"recorded_at":"2026-11-18T09:05:00Z"}'
```

同主键（`device_id` + `metric`）再次写入即更正，`version` 递增。响应 201：

```json
{
  "row": {"device_id":"pump-17","metric":"bearing_temp","value":43.5,"recorded_at":"...","version":2},
  "seq": 7,
  "device_id": "pump-17",
  "credential": "rlwv1.eyJk...<签名>",
  "credential_expires_at": 1794960000,
  "primary_max_seq": 7
}
```

### 查询

```sh
curl -s "http://127.0.0.1:19341/api/telemetry?device_id=pump-17&metric=bearing_temp" \
  -H "X-Device-Credential: rlwv1.eyJk..."
```

```json
{
  "row": {"device_id":"pump-17","metric":"bearing_temp","value":43.5,"recorded_at":"...","version":2},
  "read_source": "replica",
  "required_seq": 7,
  "replica_applied_seq": 9,
  "waited_seconds": 0.0,
  "fell_back_to_primary": false,
  "credential_device": "pump-17"
}
```

- 不带凭据：普通只读流量，直接读副本，不等待、不回源；
- 带凭据且副本已追上：`read_source=replica`；
- 带凭据但副本 1 秒内未追上：回源主库，`read_source=primary`、`fell_back_to_primary=true`；
- 回源槽满：`503`，响应头 `Retry-After: 1`，体含 `error.code=retry_later`；
- 凭据跨设备：`403 credential_device_mismatch`；伪造/篡改/过期：`401`。

### 复制控制与观测（运维/试验）

| 方法与路径 | 作用 |
|---|---|
| `POST /admin/replication/pause` | 暂停副本回放（模拟复制延迟/停同步） |
| `POST /admin/replication/resume` | 恢复回放并等待追平 |
| `GET /replication/status` | 主库水位、副本水位、滞后事件数、是否暂停 |
| `GET /health` | 健康与复制状态 |

## 运行

```sh
# 启动（默认 127.0.0.1:19341，数据目录 ./rlw-data）
python3 -m rlw --data-dir ./rlw-data --port 19341

# 可选参数
#   --replica-wait-seconds 1.0     副本对账最长等待
#   --max-primary-readers 8        主库回源并发槽
# 环境变量 RLW_CREDENTIAL_SECRET   凭据 HMAC 密钥（生产必须设置）
```

## 测试

```sh
python3 -m unittest rlw.tests.test_consistency -v
```

7 个用例覆盖：

1. **暂停复制时补录/更正**：副本停在旧水位，新值仅主库可见；携凭据查询等待窗口后回主库读到新值（含补录新行与更正旧行两类），无凭据查询仍读副本旧值；
2. **恢复复制后回读**：`resume` 后水位追平，携凭据查询直接从真实副本返回、无等待无回源；
3. **主库回源满载**：单并发槽 + 主库读延迟制造确定性竞争，第二个回源请求拿到 `RetryLater`（HTTP 503 + `Retry-After:1`）；恢复复制后重试即在副本成功；
4. **凭据设备隔离**：跨设备 403、篡改/异密钥/过期 401；
5. **副本强制只读**：对副本连接发起 INSERT/DELETE 直接抛 `OperationalError`；
6. 参数校验；
7. HTTP 端到端冒烟（暂停→写入→回源→503→403→恢复→副本回读）。

## 目录

```
rlw/
├── __init__.py
├── __main__.py          # CLI 入口 python -m rlw
├── errors.py            # CredentialError / RetryLater / ValidationError
├── credentials.py       # 设备凭据签发与验真（HMAC-SHA256）
├── storage.py           # 主库、复制日志、只读副本、可暂停 applier
├── service.py           # 读写接口、水位对账、1 秒等待、回源限流
├── http_app.py          # 标准库 HTTP 适配
└── tests/test_consistency.py
```

## 设计说明与边界

- **为什么用 SQLite 双文件**：当前环境无 Docker/MySQL，双独立数据库文件 + binlog 式日志 + 独立回放线程构成真实的主从数据通路（不是 mock），同时零依赖可复跑；接口按主从抽象设计，可平移到 MySQL。
- **凭据不是数据本身**：凭据只携带"设备 + 已提交水位 seq"，对账通过后读库返回真实行；凭据 TTL 默认 1 小时，过期需重新写入或由主库侧换发。
- **等待上限是硬边界**：对账循环按 deadline 退出，默认最多 1 秒，不会无限阻塞查询线程。
- **回源限流是 fail-fast**：槽位获取不排队，满了立即 503，由调用方按 `Retry-After` 重试，避免复制故障时主库被回源流量打穿。
- 无凭据查询只读副本，不触发回源，防止匿名流量绕过副本保护主库。
