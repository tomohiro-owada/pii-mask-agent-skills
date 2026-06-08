#!/bin/bash
# Claude Code pre-tool-use hook: Read ツールの実行前に PII を検出して警告する。
#
# セットアップ:
#   ~/.claude/settings.json の hooks に以下を追加する
#
#   "hooks": {
#     "PreToolUse": [
#       {
#         "matcher": "Read",
#         "hooks": [{ "type": "command", "command": "bash ~/.claude/skills/privacy-mask/hooks/pre-read-check.sh" }]
#       }
#     ]
#   }
#
# 動作:
#   - PII なし    → 何もせず終了（Claude はそのままファイルを読む）
#   - PII あり    → 警告メッセージを stderr に出力（Claude が内容を受け取って判断）
#   - BLOCK=1 設定 → PII 検出時に exit 2 でツール呼び出しをブロック

SKILL_DIR="$HOME/.claude/skills/privacy-mask"
PYTHON="$SKILL_DIR/venv/bin/python"
MASK="$SKILL_DIR/mask.py"

# venv が見つからなければスキップ
if [[ ! -x "$PYTHON" ]]; then
  exit 0
fi

# stdin から tool_input.file_path を取得
FILE_PATH=$(python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('tool_input', {}).get('file_path', ''))
except Exception:
    print('')
")

# ファイルが存在しない・ディレクトリ・バイナリはスキップ
if [[ -z "$FILE_PATH" || ! -f "$FILE_PATH" ]]; then
  exit 0
fi

# 一時ディレクトリにコピーしてスキャン
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT
cp "$FILE_PATH" "$TMPDIR/"

RESULT=$("$PYTHON" "$MASK" "$TMPDIR" --dry-run --format json 2>/dev/null)
EXIT_CODE=$?

if [[ $EXIT_CODE -eq 1 ]]; then
  # PII 検出: 警告を stderr に出す（Claude が読める）
  DETECTED=$(echo "$RESULT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
files = d.get('files', [])
if files:
    pii = files[0].get('pii') or {}
    labels = ', '.join(pii.keys())
    print(labels)
" 2>/dev/null)

  echo "⚠ PII detected in: $FILE_PATH" >&2
  echo "  Categories: $DETECTED" >&2
  echo "  Consider running \`privacy-mask\` to mask before sharing." >&2

  # BLOCK=1 の場合はツール呼び出しをブロック
  if [[ "${BLOCK:-0}" == "1" ]]; then
    exit 2
  fi
fi

exit 0
