#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""英検2級 単語テストメーカー

- CSV (No, 単語, 意味) から指定範囲を抽出
- その範囲から10問をランダム出題
- A4横 1ページに A5縦のテストを2枚面付け
- 日本語→英単語 / 英単語→日本語
- 同一問題2枚 / 左右で別問題
- 任意で解答PDFも生成

GUI:
    python3 vocab_test_maker.py

CLI例:
    python3 vocab_test_maker.py --range 1-100 --output test.pdf
    python3 vocab_test_maker.py --range 101-200 --direction word-to-meaning \
        --two-sets different --answers --output test_101_200.pdf
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import random
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Sequence, Tuple

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

APP_NAME = "英検2級 単語テストメーカー"
DEFAULT_CSV_URL = "https://raw.githubusercontent.com/ddd3h/eiken-vocab-test-maker/main/data/eiken2_pass_tan_1700.csv"
QUESTIONS_PER_TEST = 10
HTTP_TIMEOUT = 15  # 秒
MAX_CSV_BYTES = 20 * 1024 * 1024
USER_AGENT = "EikenVocabTestMaker/1.0"

# ReportLab built-in Japanese CID font. No font file needs to be bundled.
JP_FONT = "HeiseiKakuGo-W5"
EN_FONT = "Helvetica"
EN_BOLD = "Helvetica-Bold"

try:
    pdfmetrics.registerFont(UnicodeCIDFont(JP_FONT))
except Exception:
    # Re-registration can happen in some frozen-app contexts.
    pass


@dataclass(frozen=True)
class VocabItem:
    no: int
    word: str
    meaning: str


def is_url(source: str) -> bool:
    return source.strip().lower().startswith(("http://", "https://"))


def normalize_csv_url(url: str) -> str:
    """GitHubの blob URL を raw.githubusercontent.com のURLに変換する。
    それ以外のURLはそのまま返す。"""
    m = re.match(
        r"^https://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)$",
        url.strip(),
    )
    if m:
        user, repo, ref, path = m.groups()
        return f"https://raw.githubusercontent.com/{user}/{repo}/{ref}/{path}"
    return url.strip()


def fetch_csv_text(url: str) -> str:
    url = normalize_csv_url(url)
    if not is_url(url):
        raise ValueError(f"httpまたはhttpsのURLのみ指定できます: {url}")

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read(MAX_CSV_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise ValueError(f"CSVの取得に失敗しました（HTTP {e.code}）: {url}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ValueError(
            f"CSVをダウンロードできませんでした（ネットワーク接続を確認してください）: {url}"
        ) from e

    if len(raw) > MAX_CSV_BYTES:
        raise ValueError(f"CSVのサイズが大きすぎます（上限 {MAX_CSV_BYTES // (1024 * 1024)}MB）: {url}")

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ValueError("CSVの文字コードを判別できませんでした。UTF-8で保存してください。") from e

    if text.lstrip().lower().startswith("<!doctype html") or text.lstrip().lower().startswith("<html"):
        raise ValueError(
            "URLがCSVではなくWebページを指しています。GitHubなら Raw ボタンのURLを使ってください。"
        )
    return text


def read_csv_text(source: str | Path) -> str:
    text = str(source)
    if is_url(text):
        return fetch_csv_text(text)

    csv_path = Path(text)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSVが見つかりません: {csv_path}")
    return csv_path.read_text(encoding="utf-8-sig")


def parse_vocab(text: str) -> List[VocabItem]:
    items: List[VocabItem] = []
    reader = csv.DictReader(io.StringIO(text, newline=""))
    required = {"No", "単語", "意味"}
    if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
        raise ValueError("CSVの列名は『No, 単語, 意味』である必要があります。")
    for row in reader:
        try:
            no = int(str(row["No"]).strip())
        except (TypeError, ValueError):
            continue
        items.append(
            VocabItem(
                no=no,
                word=str(row["単語"]).strip(),
                meaning=str(row["意味"]).strip(),
            )
        )

    if not items:
        raise ValueError("CSVから単語データを読み込めませんでした。")
    return sorted(items, key=lambda x: x.no)


def load_vocab(source: str | Path) -> List[VocabItem]:
    return parse_vocab(read_csv_text(source))


def parse_range(range_text: str) -> Tuple[int, int]:
    text = range_text.strip().replace("〜", "-").replace("~", "-").replace("–", "-").replace("—", "-")
    parts = [p.strip() for p in text.split("-") if p.strip()]
    if len(parts) != 2:
        raise ValueError("範囲は 1-100 の形式で指定してください。")
    start, end = int(parts[0]), int(parts[1])
    if start <= 0 or end < start:
        raise ValueError("範囲指定が不正です。")
    return start, end


def filter_range(items: Sequence[VocabItem], start: int, end: int) -> List[VocabItem]:
    pool = [x for x in items if start <= x.no <= end]
    if len(pool) < QUESTIONS_PER_TEST:
        raise ValueError(f"No.{start}-{end} に10語以上ありません（{len(pool)}語）。")
    return pool


def choose_tests(
    pool: Sequence[VocabItem],
    two_sets: str,
    rng: random.Random,
) -> Tuple[List[VocabItem], List[VocabItem]]:
    if two_sets == "same":
        test = rng.sample(list(pool), QUESTIONS_PER_TEST)
        return test, list(test)

    if len(pool) >= QUESTIONS_PER_TEST * 2:
        picked = rng.sample(list(pool), QUESTIONS_PER_TEST * 2)
        return picked[:QUESTIONS_PER_TEST], picked[QUESTIONS_PER_TEST:]

    # Fallback for unusually small custom ranges: each test unique within itself.
    return (
        rng.sample(list(pool), QUESTIONS_PER_TEST),
        rng.sample(list(pool), QUESTIONS_PER_TEST),
    )


def _fit_font_size(text: str, font_name: str, max_width: float, initial: float, minimum: float) -> float:
    size = initial
    while size > minimum and pdfmetrics.stringWidth(text, font_name, size) > max_width:
        size -= 0.5
    return max(size, minimum)


def _split_to_fit(text: str, font_name: str, font_size: float, max_width: float, max_lines: int = 2) -> List[str]:
    """Simple character-based Japanese/English wrapping for short vocabulary meanings."""
    if pdfmetrics.stringWidth(text, font_name, font_size) <= max_width:
        return [text]

    lines: List[str] = []
    current = ""
    for ch in text:
        candidate = current + ch
        if current and pdfmetrics.stringWidth(candidate, font_name, font_size) > max_width:
            lines.append(current)
            current = ch
            if len(lines) >= max_lines - 1:
                break
        else:
            current = candidate

    consumed = "".join(lines) + current
    if len(consumed) < len(text):
        # Fit remaining text onto final line and add ellipsis only if absolutely necessary.
        remaining = text[len("".join(lines)):]
        current = ""
        for ch in remaining:
            candidate = current + ch
            suffix = "…" if len(candidate) < len(remaining) else ""
            if current and pdfmetrics.stringWidth(candidate + suffix, font_name, font_size) > max_width:
                break
            current = candidate
        if len(lines) >= max_lines:
            lines = lines[:max_lines]
        elif len(lines) == max_lines - 1:
            if len(current) < len(remaining):
                current = current.rstrip() + "…"
            lines.append(current)
    else:
        lines.append(current)

    return lines[:max_lines]


def draw_cut_mark(c: canvas.Canvas, page_w: float, page_h: float) -> None:
    c.saveState()
    c.setLineWidth(0.4)
    c.setDash(2, 2)
    mid = page_w / 2
    c.line(mid, 8 * mm, mid, page_h - 8 * mm)
    c.setFont(JP_FONT, 6.5)
    c.drawCentredString(mid, 4.5 * mm, "切り取り線")
    c.restoreState()


def draw_test_panel(
    c: canvas.Canvas,
    x0: float,
    y0: float,
    w: float,
    h: float,
    items: Sequence[VocabItem],
    start: int,
    end: int,
    direction: str,
    label: str,
    answers: bool = False,
) -> None:
    margin_x = 11 * mm
    top = h - 10 * mm
    left = x0 + margin_x
    right = x0 + w - margin_x

    c.saveState()

    # Border helps when cutting two A5 sheets from one A4 page.
    c.setLineWidth(0.6)
    c.rect(x0 + 4 * mm, y0 + 4 * mm, w - 8 * mm, h - 8 * mm)

    title = "英単語テスト 解答" if answers else "英単語テスト"
    c.setFont(JP_FONT, 15)
    c.drawString(left, y0 + top, title)

    c.setFont(JP_FONT, 8.5)
    c.drawRightString(right, y0 + top + 1.5, f"{label}  出題範囲 No.{start}-{end}")

    info_y = y0 + top - 9 * mm
    c.setFont(JP_FONT, 8.5)
    if answers:
        direction_text = "日本語 → 英単語" if direction == "meaning-to-word" else "英単語 → 日本語"
        c.drawString(left, info_y, f"形式: {direction_text}")
    else:
        c.drawString(left, info_y, "名前: ____________________")
        c.drawRightString(right, info_y, "得点: ______ / 10")

    rule_y = info_y - 4 * mm
    c.setLineWidth(0.5)
    c.line(left, rule_y, right, rule_y)

    q_top = rule_y - 6 * mm
    bottom = y0 + 11 * mm
    row_h = (q_top - bottom) / QUESTIONS_PER_TEST

    for i, item in enumerate(items, 1):
        row_top = q_top - (i - 1) * row_h
        baseline = row_top - row_h * 0.47

        c.setFont(EN_BOLD, 9.5)
        c.drawRightString(left + 8 * mm, baseline, f"{i}.")

        if direction == "meaning-to-word":
            clue = item.meaning
            clue_font = JP_FONT
            answer = item.word
            answer_font = EN_FONT
        else:
            clue = item.word
            clue_font = EN_FONT
            answer = item.meaning
            answer_font = JP_FONT

        clue_x = left + 12 * mm
        clue_width = 58 * mm
        answer_x = clue_x + clue_width + 5 * mm
        answer_right = right

        clue_size = _fit_font_size(clue, clue_font, clue_width, 10.5, 7.5)
        clue_lines = _split_to_fit(clue, clue_font, clue_size, clue_width, max_lines=2)
        if len(clue_lines) == 1:
            c.setFont(clue_font, clue_size)
            c.drawString(clue_x, baseline, clue_lines[0])
        else:
            c.setFont(clue_font, clue_size)
            c.drawString(clue_x, baseline + 4.2, clue_lines[0])
            c.drawString(clue_x, baseline - 5.5, clue_lines[1])

        if answers:
            answer_width = answer_right - answer_x
            ans_size = _fit_font_size(answer, answer_font, answer_width, 10.5, 7.0)
            ans_lines = _split_to_fit(answer, answer_font, ans_size, answer_width, max_lines=2)
            c.setFont(answer_font, ans_size)
            if len(ans_lines) == 1:
                c.drawString(answer_x, baseline, ans_lines[0])
            else:
                c.drawString(answer_x, baseline + 4.2, ans_lines[0])
                c.drawString(answer_x, baseline - 5.5, ans_lines[1])
        else:
            # Writing line for the answer.
            c.setLineWidth(0.45)
            c.line(answer_x, baseline - 2, answer_right, baseline - 2)

        # Light row separator.
        if i < QUESTIONS_PER_TEST:
            sep_y = row_top - row_h
            c.setLineWidth(0.18)
            c.line(left + 8 * mm, sep_y, right, sep_y)

    c.setFont(JP_FONT, 6.5)
    c.drawRightString(right, y0 + 6.5 * mm, f"{APP_NAME}")
    c.restoreState()


def generate_pdf(
    output_path: Path,
    left_items: Sequence[VocabItem],
    right_items: Sequence[VocabItem],
    start: int,
    end: int,
    direction: str,
    answers: bool = False,
    labels: Tuple[str, str] = ("A", "B"),
) -> None:
    page_size = landscape(A4)
    page_w, page_h = page_size
    panel_w = page_w / 2

    output_path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(output_path), pagesize=page_size)
    c.setTitle("英単語テスト" if not answers else "英単語テスト 解答")
    c.setAuthor(APP_NAME)

    draw_test_panel(c, 0, 0, panel_w, page_h, left_items, start, end, direction, labels[0], answers)
    draw_test_panel(c, panel_w, 0, panel_w, page_h, right_items, start, end, direction, labels[1], answers)
    draw_cut_mark(c, page_w, page_h)
    c.showPage()
    c.save()


def make_test(
    csv_source: str | Path,
    range_text: str,
    output_path: Path,
    direction: str = "meaning-to-word",
    two_sets: str = "same",
    make_answers: bool = True,
    seed: int | None = None,
) -> Tuple[Path, Path | None]:
    items = load_vocab(csv_source)
    start, end = parse_range(range_text)
    pool = filter_range(items, start, end)

    rng = random.Random(seed)
    left, right = choose_tests(pool, two_sets, rng)
    labels = ("A", "A") if two_sets == "same" else ("A", "B")

    output_path = output_path.with_suffix(".pdf")
    generate_pdf(output_path, left, right, start, end, direction, answers=False, labels=labels)

    answer_path: Path | None = None
    if make_answers:
        answer_path = output_path.with_name(output_path.stem + "_answers.pdf")
        generate_pdf(answer_path, left, right, start, end, direction, answers=True, labels=labels)

    return output_path, answer_path


def default_output_name(range_text: str) -> str:
    safe_range = range_text.replace("~", "-").replace("〜", "-")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"vocab_test_{safe_range}_{stamp}.pdf"


def launch_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk

    root = tk.Tk()
    root.title(APP_NAME)
    root.geometry("610x410")
    root.resizable(False, False)

    main = ttk.Frame(root, padding=18)
    main.pack(fill="both", expand=True)

    ttk.Label(main, text=APP_NAME, font=("Helvetica", 17, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 14))

    csv_var = tk.StringVar(value=DEFAULT_CSV_URL)
    range_var = tk.StringVar(value="1-100")
    direction_var = tk.StringVar(value="meaning-to-word")
    two_sets_var = tk.StringVar(value="same")
    answers_var = tk.BooleanVar(value=True)

    ttk.Label(main, text="CSV（ファイル or URL）").grid(row=1, column=0, sticky="w", pady=6)
    ttk.Entry(main, textvariable=csv_var, width=54).grid(row=1, column=1, sticky="ew", pady=6)

    def choose_csv() -> None:
        p = filedialog.askopenfilename(title="CSVを選択", filetypes=[("CSV", "*.csv"), ("すべて", "*")])
        if p:
            csv_var.set(p)

    def choose_url() -> None:
        current = csv_var.get()
        prefill = current if is_url(current) else ""
        url = simpledialog.askstring(
            "CSVのURL",
            "CSVのURLを入力してください（例: GitHubの Raw ボタンのURL）",
            initialvalue=prefill,
            parent=root,
        )
        if url:
            csv_var.set(url.strip())

    button_frame = ttk.Frame(main)
    button_frame.grid(row=1, column=2, padx=(8, 0), pady=6)
    ttk.Button(button_frame, text="選択", command=choose_csv).pack(side="left")
    ttk.Button(button_frame, text="URL", command=choose_url).pack(side="left", padx=(6, 0))

    ttk.Label(main, text="出題範囲").grid(row=2, column=0, sticky="w", pady=6)
    ranges = [f"{s}-{s+99}" for s in range(1, 1701, 100)]
    range_box = ttk.Combobox(main, textvariable=range_var, values=ranges, width=18, state="normal")
    range_box.grid(row=2, column=1, sticky="w", pady=6)

    ttk.Label(main, text="出題形式").grid(row=3, column=0, sticky="nw", pady=6)
    direction_frame = ttk.Frame(main)
    direction_frame.grid(row=3, column=1, columnspan=2, sticky="w", pady=6)
    ttk.Radiobutton(direction_frame, text="日本語 → 英単語", variable=direction_var, value="meaning-to-word").pack(anchor="w")
    ttk.Radiobutton(direction_frame, text="英単語 → 日本語", variable=direction_var, value="word-to-meaning").pack(anchor="w")

    ttk.Label(main, text="A4の左右").grid(row=4, column=0, sticky="nw", pady=6)
    sets_frame = ttk.Frame(main)
    sets_frame.grid(row=4, column=1, columnspan=2, sticky="w", pady=6)
    ttk.Radiobutton(sets_frame, text="同じ10問を2枚（切って配布向け）", variable=two_sets_var, value="same").pack(anchor="w")
    ttk.Radiobutton(sets_frame, text="別々の10問をA/B 2セット", variable=two_sets_var, value="different").pack(anchor="w")

    ttk.Checkbutton(main, text="解答PDFも同時に作る", variable=answers_var).grid(row=5, column=1, sticky="w", pady=(8, 12))

    note = "PDF: A4横 1ページ / 左右それぞれA5縦 / 中央に切り取り線\nCSV欄にはURLも指定できます（GitHubのRaw URLなど）"
    ttk.Label(main, text=note, justify="left").grid(row=6, column=0, columnspan=3, sticky="w", pady=(0, 12))

    generate_button = ttk.Button(main, text="PDFを作成")

    def generate() -> None:
        csv_input = csv_var.get().strip()
        csv_source: str | Path = csv_input if is_url(csv_input) else Path(csv_input).expanduser()
        try:
            range_text = range_var.get().strip()
            parse_range(range_text)  # validate before save dialog
            save_path = filedialog.asksaveasfilename(
                title="テストPDFの保存先",
                defaultextension=".pdf",
                initialfile=default_output_name(range_text),
                filetypes=[("PDF", "*.pdf")],
            )
            if not save_path:
                return

            generate_button.config(state="disabled", text="作成中…")
            root.update_idletasks()

            out, ans = make_test(
                csv_source=csv_source,
                range_text=range_text,
                output_path=Path(save_path),
                direction=direction_var.get(),
                two_sets=two_sets_var.get(),
                make_answers=answers_var.get(),
            )
            msg = f"作成しました:\n{out}"
            if ans:
                msg += f"\n{ans}"
            messagebox.showinfo("完了", msg)
        except Exception as e:
            messagebox.showerror("エラー", str(e))
        finally:
            generate_button.config(state="normal", text="PDFを作成")

    generate_button.config(command=generate)
    generate_button.grid(row=7, column=0, columnspan=3, pady=(8, 0), ipadx=28, ipady=7)

    main.columnconfigure(1, weight=1)
    root.mainloop()


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=APP_NAME)
    p.add_argument("--csv", default=DEFAULT_CSV_URL, help="CSVファイルのパス、またはURL（既定値はGitHubのRaw URL）")
    p.add_argument("--range", dest="range_text", help="例: 1-100, 101-200")
    p.add_argument(
        "--direction",
        choices=["meaning-to-word", "word-to-meaning"],
        default="meaning-to-word",
        help="meaning-to-word=日本語→英単語, word-to-meaning=英単語→日本語",
    )
    p.add_argument(
        "--two-sets",
        choices=["same", "different"],
        default="same",
        help="same=同じ問題を左右2枚, different=左右で別問題",
    )
    p.add_argument("--answers", action="store_true", help="解答PDFも作る")
    p.add_argument("--no-answers", action="store_true", help="解答PDFを作らない")
    p.add_argument("--seed", type=int, default=None, help="乱数seed（同じ問題を再現したい場合）")
    p.add_argument("--output", help="出力PDF")
    p.add_argument("--gui", action="store_true", help="GUIを起動")
    return p


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    # No CLI range -> GUI by default.
    if args.gui or not args.range_text:
        launch_gui()
        return

    make_answers_flag = True
    if args.no_answers:
        make_answers_flag = False
    elif args.answers:
        make_answers_flag = True

    csv_source: str | Path = args.csv if is_url(args.csv) else Path(args.csv).expanduser()
    output = Path(args.output or default_output_name(args.range_text))
    out, ans = make_test(
        csv_source=csv_source,
        range_text=args.range_text,
        output_path=output,
        direction=args.direction,
        two_sets=args.two_sets,
        make_answers=make_answers_flag,
        seed=args.seed,
    )
    print(out)
    if ans:
        print(ans)


if __name__ == "__main__":
    main()
