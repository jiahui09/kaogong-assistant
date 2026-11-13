#!/usr/bin/env bash
# 克隆公考脑库并合并分卷数据库到 data/kaogong.db
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="$ROOT/data"
TMP_DIR="${TMPDIR:-/tmp}/GongkaoNaoku-prepare"
REPO_URL="${GONGKAO_REPO_URL:-https://github.com/jangviktor-web/GongkaoNaoku.git}"

mkdir -p "$DATA_DIR"

need_clone=0
if [[ ! -f "$DATA_DIR/kaogong.db" ]]; then
  need_clone=1
fi
if [[ ! -d "$DATA_DIR/90-图片" ]]; then
  need_clone=1
fi

if [[ "$need_clone" -eq 1 ]]; then
  echo "[INFO] 克隆公考脑库到 $TMP_DIR ..."
  rm -rf "$TMP_DIR"
  git clone --depth 1 "$REPO_URL" "$TMP_DIR"

  if [[ ! -f "$DATA_DIR/kaogong.db" ]]; then
    echo "[INFO] 合并分卷数据库 ..."
    (
      cd "$TMP_DIR"
      python3 assemble_db.py
    )
    cp "$TMP_DIR/kaogong.db" "$DATA_DIR/kaogong.db"
    echo "[OK] 已写入 $DATA_DIR/kaogong.db"
  else
    echo "[OK] 已存在 $DATA_DIR/kaogong.db，跳过数据库合并。"
  fi

  if [[ ! -d "$DATA_DIR/90-图片" ]]; then
    echo "[INFO] 复制配图目录 90-图片/ ..."
    cp -a "$TMP_DIR/90-图片" "$DATA_DIR/90-图片"
    echo "[OK] 已写入 $DATA_DIR/90-图片"
  fi
else
  echo "[OK] 已存在题库与配图，跳过下载。"
fi

ls -lh "$DATA_DIR/kaogong.db"
du -sh "$DATA_DIR/90-图片" 2>/dev/null || true
