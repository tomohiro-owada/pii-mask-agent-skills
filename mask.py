#!/usr/bin/env python3
"""
privacy-mask: Detect and mask PII in text/PDF files using openai/privacy-filter.

Usage:
  privacy-mask <directory> [options]
  python mask.py <directory> [options]

Exit codes:
  0  No PII detected (scan/dry-run), or masking completed successfully
  1  PII detected (scan/dry-run) — use as CI gate
  2  Error (missing directory, model load failure, etc.)
"""
import sys
import json
import shutil
import argparse
import platform
from pathlib import Path
from datetime import datetime
from typing import Optional


MODEL_ID = "openai/privacy-filter"

LABEL_MAP = {
    "account_number": "[口座番号]",
    "private_address": "[住所]",
    "private_email": "[メールアドレス]",
    "private_person": "[氏名]",
    "private_phone": "[電話番号]",
    "private_url": "[URL]",
    "private_date": "[日付]",
    "secret": "[秘密情報]",
}

RISK_LABEL = {0: "✅ 検出なし", 1: "🟡 LOW", 2: "🟠 MEDIUM", 3: "🔴 HIGH", 4: "🚨 CRITICAL"}

DEFAULT_TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".eml", ".html", ".htm",
    ".xml", ".yaml", ".yml", ".log", ".rst", ".toml", ".ini",
    ".conf", ".py", ".js", ".ts", ".swift", ".go", ".rb",
}
PDF_EXTENSIONS = {".pdf"}


def detect_device() -> str:
    try:
        import torch
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def is_text_file(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return b"\x00" not in f.read(8192)
    except OSError:
        return False


def extract_pdf_text(path: Path) -> Optional[str]:
    try:
        import pypdf
        reader = pypdf.PdfReader(str(path))
        pages = [page.extract_text() or "" for page in reader.pages]
        text = "\n".join(p for p in pages if p.strip())
        if text.strip():
            return text
    except Exception:
        pass

    print(f"  → テキスト未検出。OCR を試みます...")
    try:
        import fitz
    except ImportError:
        print("  ⚠ pymupdf 未インストール。`pip install pymupdf` を実行してください。")
        return None

    try:
        doc = fitz.open(str(path))
        pages_text = []
        for page_num, page in enumerate(doc):
            mat = fitz.Matrix(200 / 72, 200 / 72)
            pix = page.get_pixmap(matrix=mat)
            img_bytes = pix.tobytes("png")
            text = _ocr(img_bytes, page_num + 1)
            if text:
                pages_text.append(text)
        doc.close()
        result = "\n".join(pages_text)
        return result if result.strip() else None
    except Exception as e:
        print(f"  ⚠ PDF レンダリング失敗: {e}")
        return None


def _ocr(img_bytes: bytes, page_num: int = 1) -> str:
    if platform.system() == "Darwin":
        return _ocr_vision(img_bytes)
    return _ocr_easyocr(img_bytes)


def _ocr_vision(img_bytes: bytes) -> str:
    try:
        import Vision
        import Quartz
        from Foundation import NSData

        ns_data = NSData.dataWithBytes_length_(img_bytes, len(img_bytes))
        cg_src = Quartz.CGImageSourceCreateWithData(ns_data, None)
        cg_image = Quartz.CGImageSourceCreateImageAtIndex(cg_src, 0, None)

        results = []
        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, {})
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLanguages_(["ja-JP", "en-US"])
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        handler.performRequests_error_([request], None)
        for obs in request.results() or []:
            results.append(obs.topCandidates_(1)[0].string())
        return "\n".join(results)
    except ImportError:
        print("  ⚠ pyobjc-framework-Vision 未インストール。")
        print("    pip install pyobjc-framework-Vision pyobjc-framework-Quartz")
        return ""
    except Exception as e:
        print(f"  ⚠ Vision OCR エラー: {e}")
        return ""


def _ocr_easyocr(img_bytes: bytes) -> str:
    try:
        import easyocr
        import numpy as np
        from PIL import Image
        import io

        img = Image.open(io.BytesIO(img_bytes))
        arr = np.array(img)
        reader = easyocr.Reader(["ja", "en"], gpu=False)
        results = reader.readtext(arr, detail=0)
        return "\n".join(results)
    except ImportError:
        print("  ⚠ easyocr 未インストール。`pip install easyocr` を実行してください。")
        return ""
    except Exception as e:
        print(f"  ⚠ EasyOCR エラー: {e}")
        return ""


def detect_pii(classifier, text: str) -> dict:
    CHUNK = 50_000
    counts: dict = {}
    for i in range(0, len(text), CHUNK):
        chunk = text[i:i + CHUNK]
        if not chunk.strip():
            continue
        try:
            entities = classifier(chunk, aggregation_strategy="simple")
            for ent in entities:
                label = ent["entity_group"]
                counts[label] = counts.get(label, 0) + 1
        except Exception:
            pass
    return counts


def risk_level(counts: dict) -> int:
    n_categories = len(counts)
    n_total = sum(counts.values())
    if n_categories == 0:
        return 0
    if n_categories >= 4 or n_total >= 10:
        return 4
    if n_categories >= 3 or n_total >= 5:
        return 3
    if n_categories >= 2 or n_total >= 3:
        return 2
    return 1


def mask_text(classifier, text: str) -> tuple:
    if not text.strip():
        return text, 0

    CHUNK = 50_000
    chunks = [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)]
    offsets = [i for i in range(0, len(text), CHUNK)]

    masked = list(text)
    replacements = 0

    for chunk, offset in zip(chunks, offsets):
        try:
            entities = classifier(chunk, aggregation_strategy="simple")
        except Exception as e:
            print(f"  ⚠ 推論エラー (チャンク {offset}–{offset+len(chunk)}): {e}", file=sys.stderr)
            continue

        for ent in sorted(entities, key=lambda x: x["start"], reverse=True):
            label = ent["entity_group"]
            placeholder = LABEL_MAP.get(label, f"[{label.upper()}]")
            abs_start = offset + ent["start"]
            abs_end = offset + ent["end"]
            masked[abs_start:abs_end] = list(placeholder)
            replacements += 1

    return "".join(masked), replacements


def setup_classifier(device: str):
    from transformers import (
        AutoConfig, AutoTokenizer,
        AutoModelForTokenClassification, pipeline
    )
    print(f"モデル読み込み中: {MODEL_ID} (device={device})", file=sys.stderr)
    config = AutoConfig.from_pretrained(MODEL_ID, trust_remote_code=True)
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, trust_remote_code=True)
    model = AutoModelForTokenClassification.from_pretrained(
        MODEL_ID, config=config, trust_remote_code=True
    )
    clf = pipeline("token-classification", model=model, tokenizer=tokenizer, device=device)
    print("モデル準備完了。", file=sys.stderr)
    return clf


def run_scan(classifier, target: Path, exts: set, fmt: str) -> int:
    text_exts = exts if exts else DEFAULT_TEXT_EXTENSIONS
    all_files = sorted(
        f for f in target.rglob("*")
        if f.is_file()
        and "_backup_" not in str(f)
        and (f.suffix.lower() in text_exts or f.suffix.lower() in PDF_EXTENSIONS)
    )
    if not all_files:
        print("対象ファイルが見つかりませんでした。", file=sys.stderr)
        return 2

    results = []
    for i, filepath in enumerate(all_files, 1):
        suffix = filepath.suffix.lower()
        label_str = f"[{i}/{len(all_files)}]"

        if suffix in PDF_EXTENSIONS:
            text = extract_pdf_text(filepath)
            if text is None:
                if fmt == "text":
                    print(f"{label_str} ⏭ テキスト抽出不可: {filepath.relative_to(target)}")
                results.append({"path": str(filepath.relative_to(target)), "type": "PDF",
                                "risk_level": None, "pii": None})
                continue
            ftype = "PDF"
        else:
            if not is_text_file(filepath):
                continue
            try:
                text = filepath.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            ftype = "TXT"

        counts = detect_pii(classifier, text)
        level = risk_level(counts)
        rel = str(filepath.relative_to(target))

        if fmt == "text":
            badge = RISK_LABEL[level]
            if counts:
                detail = "  " + "  ".join(
                    f"{LABEL_MAP.get(k, k)}: {v}件" for k, v in sorted(counts.items())
                )
                print(f"{label_str} {badge} [{ftype}] {rel}\n{detail}")
            else:
                print(f"{label_str} {badge} [{ftype}] {rel}")

        results.append({"path": rel, "type": ftype, "risk_level": level, "pii": counts})

    detected = [r for r in results if r["pii"]]

    if fmt == "text":
        print()
        print("── スキャン結果 ──────────────────────────")
        print(f"  スキャン済み : {len(results)} ファイル")
        print(f"  PII 検出     : {len(detected)} ファイル")
        if detected:
            print()
            print("  リスク別内訳:")
            for lv in (4, 3, 2, 1):
                matched = [r for r in detected if r["risk_level"] == lv]
                if matched:
                    print(f"  {RISK_LABEL[lv]}: {len(matched)} ファイル")
                    for r in matched:
                        cats = ", ".join(LABEL_MAP.get(k, k) for k in sorted(r["pii"]))
                        print(f"     • {r['path']} ({cats})")
        print("─────────────────────────────────────────")
    else:
        output = {
            "summary": {"scanned": len(results), "detected": len(detected)},
            "files": results,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))

    return 1 if detected else 0


def main():
    parser = argparse.ArgumentParser(
        description="PII 検出・マスキングツール (openai/privacy-filter)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
exit codes:
  0  PII なし（scan/dry-run）、またはマスキング完了
  1  PII 検出（scan/dry-run） ← CI ゲートとして使用可
  2  エラー
        """,
    )
    parser.add_argument("directory", help="対象ディレクトリ")
    parser.add_argument("--dry-run", action="store_true",
                        help="検出のみ。ファイルは変更しない")
    parser.add_argument("--ext", default="",
                        help="対象拡張子 (例: .txt,.md,.pdf)。省略で標準セット")
    parser.add_argument("--no-backup", action="store_true",
                        help="バックアップをスキップ")
    parser.add_argument("--device", default=None,
                        help="推論デバイス (mps/cuda/cpu)。省略で自動検出")
    parser.add_argument("--format", choices=["text", "json"], default="text",
                        dest="fmt", help="出力形式 (default: text)")
    args = parser.parse_args()

    target = Path(args.directory).resolve()
    if not target.is_dir():
        print(f"エラー: ディレクトリが見つかりません: {target}", file=sys.stderr)
        sys.exit(2)

    exts = set()
    if args.ext:
        exts = {e.strip() if e.strip().startswith(".") else "." + e.strip()
                for e in args.ext.split(",")}

    device = args.device or detect_device()

    try:
        classifier = setup_classifier(device)
    except Exception as e:
        print(f"エラー: モデルの読み込みに失敗しました: {e}", file=sys.stderr)
        sys.exit(2)

    if args.dry_run:
        sys.exit(run_scan(classifier, target, exts, args.fmt))

    # --- mask mode ---
    text_exts = exts if exts else DEFAULT_TEXT_EXTENSIONS
    all_files = sorted(
        f for f in target.rglob("*")
        if f.is_file()
        and (not text_exts or f.suffix.lower() in text_exts)
        and is_text_file(f)
        and "_backup_" not in str(f)
    )

    if not all_files:
        print("対象ファイルが見つかりませんでした。", file=sys.stderr)
        sys.exit(2)

    if args.fmt == "text":
        print(f"対象: {target}")
        print(f"ファイル数: {len(all_files)} 件\n")

    backup_dir = None
    if not args.no_backup:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = target.parent / f"{target.name}_backup_{ts}"
        if args.fmt == "text":
            print(f"バックアップ作成中: {backup_dir} ...")
        shutil.copytree(target, backup_dir)
        if args.fmt == "text":
            print("バックアップ完了。\n")

    total_replacements = 0
    modified_files = 0
    errors = 0
    json_results = []

    for i, filepath in enumerate(all_files, 1):
        prefix = f"[{i}/{len(all_files)}]"
        try:
            text = filepath.read_text(encoding="utf-8", errors="ignore")
            masked, count = mask_text(classifier, text)

            if args.fmt == "text":
                if count == 0:
                    print(f"{prefix} PII なし: {filepath.relative_to(target)}")
                else:
                    print(f"{prefix} {count} 件マスク: {filepath.relative_to(target)}")

            if count > 0:
                total_replacements += count
                modified_files += 1
                filepath.write_text(masked, encoding="utf-8")

            json_results.append({
                "path": str(filepath.relative_to(target)),
                "masked": count,
            })
        except Exception as e:
            print(f"{prefix} エラー: {filepath.relative_to(target)}: {e}", file=sys.stderr)
            errors += 1

    if args.fmt == "text":
        print()
        print("── 完了 ──────────────────────────")
        print(f"  対象ファイル : {len(all_files)} 件")
        print(f"  PII 検出     : {modified_files} ファイル / {total_replacements} 箇所")
        if errors:
            print(f"  エラー       : {errors} ファイル")
        if backup_dir:
            print(f"  バックアップ : {backup_dir}")
        print("─────────────────────────────────")
    else:
        print(json.dumps({
            "summary": {
                "scanned": len(all_files),
                "modified": modified_files,
                "replacements": total_replacements,
                "errors": errors,
            },
            "backup": str(backup_dir) if backup_dir else None,
            "files": json_results,
        }, ensure_ascii=False, indent=2))

    sys.exit(2 if errors and modified_files == 0 else 0)


if __name__ == "__main__":
    main()
