#!/usr/bin/env bash
# 克隆公考脑库并合并分卷数据库到 data/kaogong.db
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="$ROOT/data"
TMP_DIR="${TMPDIR:-/tmp}/GongkaoNaoku-prepare"
REPO_URL="${GONGKAO_REPO_URL:-https://github.com/jangviktor-web/GongkaoNaoku.git}"

mkdir -p "$DATA_DIR"

if [[ -f "$DATA_DIR/kaogong.db" ]]; then
  echo "[OK] 已存在 $DATA_DIR/kaogong.db，跳过下载。"
  ls -lh "$DATA_DIR/kaogong.db"
  exit 0
fi

echo "[INFO] 克隆公考脑库到 $TMP_DIR ..."
rm -rf "$TMP_DIR"
git clone --depth 1 "$REPO_URL" "$TMP_DIR"

echo "[INFO] 合并分卷数据库 ..."
(
  cd "$TMP_DIR"
  python3 assemble_db.py
)

cp "$TMP_DIR/kaogong.db" "$DATA_DIR/kaogong.db"
echo "[OK] 已写入 $DATA_DIR/kaogong.db"
ls -lh "$DATA_DIR/kaogong.db"
