#!/usr/bin/env bash
# 一键启动阵容模拟器：检查环境 -> 必要时采集数据 -> 启动本地服务并打开浏览器
# 用法：./start.sh [--port 8787] [--host 127.0.0.1] [--db data/jcc.db] [--no-open]
set -euo pipefail

cd "$(dirname "$0")"

# 选一个 3.11+ 的解释器：优先 $PYTHON，其次常见版本名，最后 python3
pick_python() {
  for candidate in ${PYTHON:-} python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

if ! PYTHON="$(pick_python)"; then
  echo "找不到 Python 3.11 或更高版本，请安装后重试（也可用 PYTHON=/path/to/python ./start.sh 指定）" >&2
  exit 1
fi

# 解析出 --db 与 --no-open，其余参数原样透传给服务
DB="data/jcc.db"
OPEN="--open"
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --db) DB="$2"; shift 2 ;;
    --db=*) DB="${1#*=}"; shift ;;
    --no-open) OPEN=""; shift ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

export PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}"

if [ ! -f "$DB" ]; then
  echo "未找到数据库 $DB，先从官网采集一次数据…"
  "$PYTHON" -m jcc.cli --db "$DB"
elif ! "$PYTHON" - "$DB" <<'PY'
import sqlite3, sys
from contextlib import closing
# 旧库缺少新增字段时需要重新采集；database.py 会在写入时自动迁移表结构。
# 这里只列采集才能补齐的字段：compositions 等新表由服务端 ensure_schema 自动创建，无需重采。
required = {
    "heroes": {"local_picture", "picture_big", "local_picture_big"},
    "equipment": {"equipment_key", "fetter_id"},
    "augments": {"augment_key", "level"},
}
try:
    with closing(sqlite3.connect(sys.argv[1])) as connection:
        for table, columns in required.items():
            have = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            if not have or columns - have:
                sys.exit(1)
except sqlite3.Error:
    sys.exit(1)
PY
then
  echo "数据库 $DB 结构过旧，重新采集一次数据…"
  "$PYTHON" -m jcc.cli --db "$DB"
fi

# macOS 自带 bash 3.2 下空数组展开会触发 set -u，需要保护
exec "$PYTHON" -m jcc.web.server --db "$DB" ${OPEN:+$OPEN} ${ARGS[@]+"${ARGS[@]}"}
