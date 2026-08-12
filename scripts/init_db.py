"""创建本地 SQLite 并写入固定 seed 合成数据。

用法：python scripts/init_db.py --database data/generated/inventory_agent.db --seed 20260812
"""

import argparse
from pathlib import Path

from app.main import initialize_database


def main() -> None:
    """解析命令行参数、创建目标目录并输出五张表记录数。"""
    parser = argparse.ArgumentParser(description="Initialize synthetic inventory SQLite data")
    parser.add_argument("--database", default="data/generated/inventory_agent.db")
    parser.add_argument("--seed", type=int, default=20260812)
    args = parser.parse_args()

    database_path = Path(args.database).resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    counts = initialize_database(
        f"sqlite+pysqlite:///{database_path.as_posix()}",
        seed=args.seed,
    )
    print(f"database={database_path}")
    print(f"seed={args.seed}")
    for name, count in counts.items():
        print(f"{name}={count}")


if __name__ == "__main__":
    main()
