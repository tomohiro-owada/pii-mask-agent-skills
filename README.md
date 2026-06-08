# pii-mask-agent-skills

テキスト・PDF ファイルから個人情報（PII）を検出・マスキングする CLI ツール。  
[openai/privacy-filter](https://huggingface.co/openai/privacy-filter) モデルをローカルで使用するため、データを外部に送信しません。

## 注意事項

本ツールは `openai/privacy-filter` モデルによる**検出**と、自前コードによる**置換**の2段階で動作します。モデルは文脈や表記によって検出漏れが発生することがあります。自動マスキング後は目視確認を推奨します。

## 検出カテゴリ

| 種別 | 置換後 |
|------|--------|
| 氏名 | `[氏名]` |
| メールアドレス | `[メールアドレス]` |
| 電話番号 | `[電話番号]` |
| 住所 | `[住所]` |
| URL | `[URL]` |
| 日付 | `[日付]` |
| 口座番号 | `[口座番号]` |
| 秘密情報 | `[秘密情報]` |

## 必要な環境

- Python 3.10 以上
- （初回）HuggingFace から約 3GB のモデルをダウンロード

## インストール

```bash
# 1. リポジトリをクローン
git clone https://github.com/YOUR_ORG/privacy-mask
cd privacy-mask

# 2. 仮想環境を作成
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. transformers 5.x をインストール（openai/privacy-filter に必要）
pip install git+https://github.com/huggingface/transformers.git

# 4. その他の依存パッケージ
pip install -r requirements.txt

# 5. OS 別 OCR バックエンド（PDF の OCR が必要な場合のみ）
# macOS
pip install -r requirements-macos.txt
# Windows / Linux
pip install -r requirements-other.txt

# 6. CLI コマンドとしてインストール（任意）
pip install -e .
```

## 使い方

### 検出のみ（ファイルは変更しない）

```bash
privacy-mask ./path/to/dir --dry-run
```

### マスキング実行（バックアップを自動作成）

```bash
privacy-mask ./path/to/dir
```

### PDF も含めてスキャン

```bash
privacy-mask ./path/to/dir --dry-run --ext .txt,.md,.pdf
```

### JSON 出力（CI・スクリプト連携向け）

```bash
privacy-mask ./path/to/dir --dry-run --format json
```

```json
{
  "summary": { "scanned": 3, "detected": 2 },
  "files": [
    {
      "path": "report.txt",
      "type": "TXT",
      "risk_level": 4,
      "pii": { "private_person": 2, "private_email": 1 }
    }
  ]
}
```

## オプション

| オプション | 説明 |
|-----------|------|
| `--dry-run` | 検出のみ。ファイルは変更しない |
| `--ext .txt,.md` | 対象拡張子を絞る（省略で標準セット） |
| `--no-backup` | バックアップをスキップ |
| `--format json` | JSON 出力 |
| `--device mps\|cuda\|cpu` | 推論デバイスを指定（省略で自動検出） |

## exit codes

| コード | 意味 |
|--------|------|
| `0` | PII なし（dry-run）またはマスキング完了 |
| `1` | PII 検出（dry-run） |
| `2` | エラー |

exit code `1` を CI ゲートとして利用できます：

```yaml
# GitHub Actions の例
- name: PII チェック
  run: |
    source venv/bin/activate
    privacy-mask ./docs --dry-run --format json
  # PII が検出されると exit 1 でジョブが失敗します
```

## Claude Code スキル

`skill.md` を Claude Code のスキルとして登録することで、`/privacy-mask` コマンドとして使えます。

```bash
cp skill.md ~/.claude/skills/privacy-mask.md
```

## デバイスについて

| 環境 | 自動選択されるデバイス |
|------|-----------------------|
| Apple Silicon Mac | MPS |
| CUDA GPU | CUDA |
| その他 | CPU |

`--device cpu` で強制的に CPU 推論できます（CI 環境など）。

## OCR について

テキストが埋め込まれていない PDF（スキャン PDF）は OCR で処理します。

| OS | バックエンド |
|----|------------|
| macOS | Vision framework（追加ダウンロード不要） |
| Windows / Linux | EasyOCR（初回 ~500MB ダウンロード） |

## Claude Code hooks との連携（任意）

`hooks/pre-read-check.sh` を使うと、Claude がファイルを読み込む前に自動で PII チェックを走らせることができます。

### セットアップ

```bash
cp hooks/pre-read-check.sh ~/.claude/skills/privacy-mask/hooks/pre-read-check.sh
chmod +x ~/.claude/skills/privacy-mask/hooks/pre-read-check.sh
```

`~/.claude/settings.json`（または `.claude/settings.json`）に追加:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Read",
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/.claude/skills/privacy-mask/hooks/pre-read-check.sh"
          }
        ]
      }
    ]
  }
}
```

### 動作

| 状況 | 挙動 |
|------|------|
| PII なし | 何もせず通過 |
| PII あり | 警告を表示（Claude がファイルを読む前に内容を認識） |
| `BLOCK=1` 設定時 | PII 検出でファイル読み込みをブロック |

警告のみ（デフォルト）とブロックは `BLOCK` 環境変数で切り替えできます:

```json
"command": "BLOCK=1 bash ~/.claude/skills/privacy-mask/hooks/pre-read-check.sh"
```
