# /privacy-mask

指定ディレクトリ内のテキスト・PDF ファイルから PII（個人情報）を検出・マスキングするスキル。
`openai/privacy-filter` モデルをローカルで使用する。

## 呼び出し方

```
/privacy-mask <directory> [オプション]
```

**オプション:**
- `--dry-run` : 検出のみ。ファイルは変更しない（exit 1 if PII found）
- `--ext .txt,.md` : 対象拡張子を絞る
- `--no-backup` : バックアップをスキップ
- `--format json` : JSON 出力（CI 連携向け）
- `--device cpu` : 推論デバイスを指定（省略で自動検出）

## Claude がやること

以下の手順を **この順番通り** に実行する。

### 1. インストール確認

```bash
privacy-mask --help 2>/dev/null && echo "installed" || echo "not_installed"
```

`not_installed` の場合 → ステップ2へ。`installed` の場合 → ステップ4へ。

### 2. セットアップ（初回のみ）

リポジトリをクローンして pip install する:

```bash
INSTALL_DIR="$HOME/.claude/skills/privacy-mask"
git clone https://github.com/tomohiro-owada/pii-mask-agent-skills "$INSTALL_DIR" 2>/dev/null || true

python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install git+https://github.com/huggingface/transformers.git
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

OS=$(python3 -c "import platform; print(platform.system())")
if [[ "$OS" == "Darwin" ]]; then
  "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements-macos.txt"
else
  "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements-other.txt"
fi

MASK_CMD="$INSTALL_DIR/venv/bin/python $INSTALL_DIR/mask.py"
```

### 3. モデルのキャッシュ確認

```bash
"$INSTALL_DIR/venv/bin/python" - <<'PY'
from pathlib import Path
import os
cache = Path(os.environ.get("HF_HOME", Path.home() / ".cache/huggingface")) / "hub"
exists = any(p.name.startswith("models--openai--privacy-filter") for p in cache.iterdir()) if cache.exists() else False
print("cached" if exists else "not_cached")
PY
```

`not_cached` の場合:
- 「初回実行のため `openai/privacy-filter` モデルを HuggingFace からダウンロードします（約 3GB）。続行しますか?」と確認する（AskUserQuestion）
- キャンセルなら終了する

### 4. ディレクトリ確認

`args` に渡されたディレクトリパスを確認する。
- 未指定なら AskUserQuestion でディレクトリを聞く
- 存在しないパスならエラーを返す

### 5. 実行

```bash
INSTALL_DIR="$HOME/.claude/skills/privacy-mask"
"$INSTALL_DIR/venv/bin/python" "$INSTALL_DIR/mask.py" <directory> [options]
```

### 6. 結果を報告

スクリプトの出力をそのまま表示し、以下を補足する:
- バックアップのパス（mask モード時）
- 何件の PII が何ファイルで検出されたか
- エラーがあればその内容

## 検出カテゴリ

| ラベル | 置換後 |
|---|---|
| private_person | [氏名] |
| private_email | [メールアドレス] |
| private_phone | [電話番号] |
| private_address | [住所] |
| private_url | [URL] |
| private_date | [日付] |
| account_number | [口座番号] |
| secret | [秘密情報] |

## exit codes

| コード | 意味 |
|---|---|
| 0 | PII なし（scan/dry-run）またはマスキング完了 |
| 1 | PII 検出（scan/dry-run）← CI ゲートとして利用可 |
| 2 | エラー |
