"""命令行入口：python -m rlw --data-dir ./rlw-data --port 19341"""
from __future__ import annotations

import argparse
import logging

from rlw.http_app import build_server


def main() -> None:
    parser = argparse.ArgumentParser(description="设备遥测读写一致性服务（主库+只读副本）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19341)
    parser.add_argument("--data-dir", default="./rlw-data")
    parser.add_argument("--replica-wait-seconds", type=float, default=1.0)
    parser.add_argument("--max-primary-readers", type=int, default=8)
    parser.add_argument(
        "--primary-read-delay",
        type=float,
        default=0.0,
        help="演练用：模拟每次主库回源读取耗时（秒），用于复现回源并发满载",
    )
    args = parser.parse_args()

    server = build_server(
        args.data_dir,
        host=args.host,
        port=args.port,
        replica_wait_seconds=args.replica_wait_seconds,
        max_primary_readers=args.max_primary_readers,
        primary_read_delay=args.primary_read_delay,
    )
    logging.info(
        "rlw listening on http://%s:%s (data=%s, replica wait=%.2fs, primary slots=%d)",
        args.host,
        args.port,
        args.data_dir,
        args.replica_wait_seconds,
        args.max_primary_readers,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.service.store.close()
        server.server_close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
