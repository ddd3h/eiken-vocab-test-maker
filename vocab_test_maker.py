#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""単語テストメーカー

- 同梱の単語帳（英検2級 パス単1700 / ターゲット1900 / 英検準1級 パス単1900）から選択、または任意のCSV/URL
- CSV (No, 単語, 意味) から指定範囲を抽出
- その範囲から10問をランダム出題
- A4横 1ページに A5縦のテストを2枚面付け
- 日本語→英単語 / 英単語→日本語
- 左=問題・右=解答を1枚に（既定） / 同一問題2枚 / 左右で別問題
- 任意で解答PDFも生成
- 起動時にGitHub Releasesを見て、新しいバージョンがあればGUIに知らせる
- 配布ビルドなら「今すぐ更新」でダウンロード → 検証 → 入れ替え → 再起動まで行う（self_update.py）

GUI:
    python3 vocab_test_maker.py

CLI例:
    python3 vocab_test_maker.py --range 1-100 --output test.pdf
    python3 vocab_test_maker.py --dataset target1900 --range 1801-1900 --output test.pdf
    python3 vocab_test_maker.py --range 101-200 --direction word-to-meaning \
        --two-sets different --answers --output test_101_200.pdf
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import random
import re
import ssl
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

import self_update
from self_update import Asset

APP_NAME = "単語テストメーカー"
APP_VERSION = "1.4.0"  # リリース時は git タグ vX.Y.Z と揃える
GITHUB_REPO = "ddd3h/eiken-vocab-test-maker"
DATA_BASE_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/data/"
RELEASES_PAGE_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
QUESTIONS_PER_TEST = 10
RANGE_STEP = 100
HTTP_TIMEOUT = 15  # 秒
UPDATE_CHECK_TIMEOUT = 5  # 秒。起動時のバックグラウンド確認なので短め
MAX_CSV_BYTES = 20 * 1024 * 1024
USER_AGENT = f"EikenVocabTestMaker/{APP_VERSION}"
ICON_PNG_PATH = "assets/icon/EikenVocabTestMaker-256.png"


def resource_path(relative: str) -> Path:
    """同梱リソースの絶対パス。PyInstallerで固めた場合は sys._MEIPASS 配下を見る。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative

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


@dataclass(frozen=True)
class Dataset:
    """同梱の単語帳。data/ 配下のCSVをGitHubのRaw URLで参照する。"""

    key: str  # CLI --dataset の値
    label: str  # GUI表示名
    filename: str  # data/ 内のファイル名
    total: int  # 収録語数（出題範囲プルダウンの生成に使う）

    @property
    def url(self) -> str:
        return DATA_BASE_URL + self.filename


DATASETS: Tuple[Dataset, ...] = (
    Dataset("eiken2", "英検2級 パス単（1700語）", "eiken2_pass_tan_1700.csv", 1700),
    Dataset("target1900", "ターゲット1900（1900語）", "target_1900.csv", 1900),
    Dataset("eikenpre1", "英検準1級 パス単（1900語）", "eiken_pre1_pass_tan_1900.csv", 1900),
)
DEFAULT_DATASET = DATASETS[0]
DEFAULT_CSV_URL = DEFAULT_DATASET.url


def dataset_by_key(key: str) -> Dataset:
    for ds in DATASETS:
        if ds.key == key:
            return ds
    raise ValueError(f"未知の単語帳です: {key}")


def find_dataset(source: str) -> Dataset | None:
    """CSV欄の値（URL or ファイルパス）が同梱の単語帳のどれかに一致すればそれを返す。"""
    text = source.strip()
    if not text:
        return None
    if is_url(text):
        text = normalize_csv_url(text)
        return next((ds for ds in DATASETS if ds.url == text), None)
    name = Path(text).name
    return next((ds for ds in DATASETS if ds.filename == name), None)


def source_display_name(source: str | Path) -> str:
    """PDFに載せる単語帳名。同梱の単語帳ならその表示名、自前のCSVならファイル名（拡張子なし）。"""
    text = str(source).strip()
    ds = find_dataset(text)
    if ds:
        return ds.label
    if is_url(text):
        stem = Path(urllib.parse.urlsplit(normalize_csv_url(text)).path).stem
        stem = urllib.parse.unquote(stem)
    else:
        stem = Path(text).stem
    return stem or APP_NAME


def range_presets(total: int, step: int = RANGE_STEP) -> List[str]:
    """1-100, 101-200, ... のように total 語までの出題範囲候補を作る。"""
    return [f"{s}-{min(s + step - 1, total)}" for s in range(1, total + 1, step)]


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


def ssl_context() -> ssl.SSLContext:
    """HTTPS用のSSLコンテキスト。

    PyInstallerで固めたアプリ（特にmacOS）はOSの証明書ストアを参照できず、
    urlopen が CERTIFICATE_VERIFY_FAILED になる。certifi のCAバンドルを同梱して
    それを明示的に使う。certifi が無い環境では Python 既定に任せる。
    """
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def _describe_network_error(e: BaseException) -> str:
    """URLError などから、ユーザー向けメッセージに添える短い原因説明を作る。"""
    reason = getattr(e, "reason", e)
    return str(reason) or e.__class__.__name__


def fetch_csv_text(url: str) -> str:
    url = normalize_csv_url(url)
    if not is_url(url):
        raise ValueError(f"httpまたはhttpsのURLのみ指定できます: {url}")

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT, context=ssl_context()) as resp:
            raw = resp.read(MAX_CSV_BYTES + 1)
    except urllib.error.HTTPError as e:
        raise ValueError(f"CSVの取得に失敗しました（HTTP {e.code}）: {url}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ValueError(
            "CSVをダウンロードできませんでした（ネットワーク接続を確認してください）\n"
            f"原因: {_describe_network_error(e)}\nURL: {url}"
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


def _normalize_header(name: str | None) -> str:
    """列名の前後空白・BOMを除き、『No.』『no』などは『No』に寄せる。"""
    if name is None:
        return ""
    text = name.strip().lstrip("\ufeff")
    if text.rstrip(".").lower() == "no":
        return "No"
    return text


def parse_vocab(text: str) -> List[VocabItem]:
    items: List[VocabItem] = []
    reader = csv.DictReader(io.StringIO(text, newline=""))
    reader.fieldnames = [_normalize_header(f) for f in (reader.fieldnames or [])]
    required = {"No", "単語", "意味"}
    if not required.issubset(set(reader.fieldnames)):
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


@dataclass(frozen=True)
class ReleaseInfo:
    version: str  # 例: "1.2.0"（先頭の v は除く）
    url: str  # そのリリースのページ
    assets: Dict[str, Asset] = field(default_factory=dict)  # 配布ファイル名 → Asset


def parse_version(text: str) -> Tuple[int, ...]:
    """'v1.2.3' / '1.2.3-beta' などを (1, 2, 3) にする。末尾の 0 は落として比較しやすくする。"""
    m = re.match(r"\s*v?(\d+(?:\.\d+)*)", text or "", re.IGNORECASE)
    if not m:
        return ()
    parts = [int(x) for x in m.group(1).split(".")]
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def fetch_latest_release(timeout: float = UPDATE_CHECK_TIMEOUT) -> ReleaseInfo | None:
    """GitHub Releases の最新版を取得する。オフライン等で取れなければ None（例外は出さない）。"""
    req = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_context()) as resp:
            data = json.loads(resp.read(1024 * 1024).decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    tag = str(data.get("tag_name") or "").strip()
    if not parse_version(tag):
        return None
    assets: Dict[str, Asset] = {}
    for a in data.get("assets") or []:
        if not isinstance(a, dict):
            continue
        name = str(a.get("name") or "")
        url = str(a.get("browser_download_url") or "")
        if name and url:
            assets[name] = Asset(name=name, url=url, size=int(a.get("size") or 0))
    return ReleaseInfo(version=tag.lstrip("vV"), url=str(data.get("html_url") or RELEASES_PAGE_URL), assets=assets)


def check_for_update(current: str = APP_VERSION) -> ReleaseInfo | None:
    """現在より新しいリリースがあればそれを返す。なければ（確認できなければ）None。"""
    latest = fetch_latest_release()
    if latest and parse_version(latest.version) > parse_version(current):
        return latest
    return None


def make_http() -> self_update.Http:
    return self_update.Http(user_agent=USER_AGENT, context=ssl_context())


def run_self_update_cli(current: str, relaunch: bool) -> None:
    """--update: 新版があればダウンロードして入れ替える（配布ビルドのみ）。"""
    latest = fetch_latest_release()
    if latest is None:
        print("更新を確認できませんでした（ネットワーク接続を確認してください）")
        sys.exit(1)
    if parse_version(latest.version) <= parse_version(current):
        print(f"v{current} は最新です")
        return
    reason = self_update.why_not_updatable()
    if reason:
        print(f"自動更新できません: {reason}\nダウンロードページ: {latest.url}")
        sys.exit(1)

    inline = [False]  # 進捗を同じ行に上書き表示中なら、次のログの前で改行する

    def progress(done: int, total: int | None) -> None:
        if total:
            print(f"\r  {done / 1048576:.1f} / {total / 1048576:.1f} MB", end="", flush=True)
            inline[0] = True

    def log(text: str) -> None:
        if inline[0]:
            print()
            inline[0] = False
        print(text)

    try:
        staged = self_update.stage_update(latest.version, latest.assets, make_http(), progress=progress, log=log)
        self_update.apply_and_relaunch(staged, relaunch=relaunch)
    except self_update.UpdateError as e:
        print(f"更新に失敗しました: {e}")
        sys.exit(1)
    print(f"v{latest.version} に更新しました: {staged.root}")


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
    if two_sets in ("same", "qa"):
        # "qa" も左右は同じ10問（左=問題, 右=解答として使う）。
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
    footer: str = APP_NAME,
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
    prefix = f"{label}  " if label else ""
    c.drawRightString(right, y0 + top + 1.5, f"{prefix}出題範囲 No.{start}-{end}")

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
    c.drawRightString(right, y0 + 6.5 * mm, footer)
    c.restoreState()


def generate_pdf(
    output_path: Path,
    left_items: Sequence[VocabItem],
    right_items: Sequence[VocabItem],
    start: int,
    end: int,
    direction: str,
    left_answers: bool = False,
    right_answers: bool = False,
    labels: Tuple[str, str] = ("A", "B"),
    source_name: str = APP_NAME,
) -> None:
    page_size = landscape(A4)
    page_w, page_h = page_size
    panel_w = page_w / 2

    output_path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(output_path), pagesize=page_size)
    if left_answers and right_answers:
        title = "英単語テスト 解答"
    elif not left_answers and not right_answers:
        title = "英単語テスト"
    else:
        title = "英単語テスト（問題+解答）"
    c.setTitle(title)
    c.setAuthor(APP_NAME)
    c.setSubject(source_name)

    draw_test_panel(c, 0, 0, panel_w, page_h, left_items, start, end, direction, labels[0], left_answers, footer=source_name)
    draw_test_panel(c, panel_w, 0, panel_w, page_h, right_items, start, end, direction, labels[1], right_answers, footer=source_name)
    draw_cut_mark(c, page_w, page_h)
    c.showPage()
    c.save()


def make_test(
    csv_source: str | Path,
    range_text: str,
    output_path: Path,
    direction: str = "word-to-meaning",
    two_sets: str = "qa",
    make_answers: bool = True,
    seed: int | None = None,
    source_name: str | None = None,
) -> Tuple[Path, Path | None]:
    items = load_vocab(csv_source)
    start, end = parse_range(range_text)
    pool = filter_range(items, start, end)
    if source_name is None:
        source_name = source_display_name(csv_source)  # PDF右下に載せる単語帳名

    rng = random.Random(seed)
    left, right = choose_tests(pool, two_sets, rng)
    if two_sets == "qa":
        # 同じ10問を左=問題（空欄）、右=解答として1枚に収める。
        labels = ("", "")
        left_answers, right_answers = False, True
    else:
        labels = ("A", "A") if two_sets == "same" else ("A", "B")
        left_answers = right_answers = False

    output_path = output_path.with_suffix(".pdf")
    generate_pdf(
        output_path, left, right, start, end, direction,
        left_answers=left_answers, right_answers=right_answers, labels=labels, source_name=source_name,
    )

    answer_path: Path | None = None
    if make_answers:
        answer_path = output_path.with_name(output_path.stem + "_answers.pdf")
        generate_pdf(
            answer_path, left, right, start, end, direction,
            left_answers=True, right_answers=True, labels=labels, source_name=source_name,
        )

    return output_path, answer_path


def default_output_name(range_text: str) -> str:
    safe_range = range_text.replace("~", "-").replace("〜", "-")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"vocab_test_{safe_range}_{stamp}.pdf"


def launch_gui(initial_csv: str | None = None, check_update: bool = True) -> None:
    import tkinter as tk
    from tkinter import filedialog, font as tkfont, messagebox, simpledialog, ttk

    root = tk.Tk()
    root.title(APP_NAME)
    root.geometry("610x534")
    root.resizable(False, False)
    try:
        icon_img = tk.PhotoImage(file=str(resource_path(ICON_PNG_PATH)))
        root.iconphoto(True, icon_img)
        root._icon_img_ref = icon_img  # GC避け
    except Exception:
        pass  # アイコンが無くても起動は継続する

    main = ttk.Frame(root, padding=18)
    main.pack(fill="both", expand=True)

    ttk.Label(main, text=APP_NAME, font=("Helvetica", 17, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 14))
    ttk.Label(main, text=f"v{APP_VERSION}", foreground="gray").grid(row=0, column=2, sticky="e", pady=(0, 14))

    CUSTOM_LABEL = "カスタム（ファイル / URL を指定）"

    dataset_var = tk.StringVar(value=DEFAULT_DATASET.label)
    csv_var = tk.StringVar(value=initial_csv or DEFAULT_CSV_URL)
    range_var = tk.StringVar(value="1-100")
    direction_var = tk.StringVar(value="word-to-meaning")
    two_sets_var = tk.StringVar(value="qa")
    answers_var = tk.BooleanVar(value=True)

    ttk.Label(main, text="単語帳").grid(row=1, column=0, sticky="w", pady=6)
    dataset_box = ttk.Combobox(
        main,
        textvariable=dataset_var,
        values=[ds.label for ds in DATASETS] + [CUSTOM_LABEL],
        width=34,
        state="readonly",
    )
    dataset_box.grid(row=1, column=1, sticky="w", pady=6)

    ttk.Label(main, text="CSV（ファイル or URL）").grid(row=2, column=0, sticky="w", pady=6)
    csv_entry = ttk.Entry(main, textvariable=csv_var, width=54)
    csv_entry.grid(row=2, column=1, sticky="ew", pady=6)

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
    button_frame.grid(row=2, column=2, padx=(8, 0), pady=6)
    ttk.Button(button_frame, text="選択", command=choose_csv).pack(side="left")
    ttk.Button(button_frame, text="URL", command=choose_url).pack(side="left", padx=(6, 0))

    ttk.Label(main, text="出題範囲").grid(row=3, column=0, sticky="w", pady=6)
    range_box = ttk.Combobox(
        main, textvariable=range_var, values=range_presets(DEFAULT_DATASET.total), width=18, state="normal"
    )
    range_box.grid(row=3, column=1, sticky="w", pady=6)

    def apply_ranges(total: int) -> None:
        values = range_presets(total)
        range_box["values"] = values
        try:
            if parse_range(range_var.get())[1] <= total:
                return  # 現在の範囲がこの単語帳に収まるなら維持
        except ValueError:
            pass
        range_var.set(values[0])

    def on_csv_changed(*_args) -> None:
        # CSV欄が同梱の単語帳を指していれば「単語帳」表示と出題範囲候補を合わせる。
        ds = find_dataset(csv_var.get())
        dataset_var.set(ds.label if ds else CUSTOM_LABEL)
        if ds:
            apply_ranges(ds.total)

    def on_dataset_selected(_event=None) -> None:
        label = dataset_var.get()
        ds = next((d for d in DATASETS if d.label == label), None)
        if ds:
            csv_var.set(ds.url)  # trace 経由で出題範囲も更新される
        else:
            csv_entry.focus_set()  # カスタム: CSV欄に自前のファイル/URLを入れてもらう

    dataset_box.bind("<<ComboboxSelected>>", on_dataset_selected)
    csv_var.trace_add("write", on_csv_changed)
    on_csv_changed()

    ttk.Label(main, text="出題形式").grid(row=4, column=0, sticky="nw", pady=6)
    direction_frame = ttk.Frame(main)
    direction_frame.grid(row=4, column=1, columnspan=2, sticky="w", pady=6)
    ttk.Radiobutton(direction_frame, text="日本語 → 英単語", variable=direction_var, value="meaning-to-word").pack(anchor="w")
    ttk.Radiobutton(direction_frame, text="英単語 → 日本語", variable=direction_var, value="word-to-meaning").pack(anchor="w")

    ttk.Label(main, text="A4の左右").grid(row=5, column=0, sticky="nw", pady=6)
    sets_frame = ttk.Frame(main)
    sets_frame.grid(row=5, column=1, columnspan=2, sticky="w", pady=6)
    ttk.Radiobutton(sets_frame, text="左に問題・右に解答（1枚で完結）", variable=two_sets_var, value="qa").pack(anchor="w")
    ttk.Radiobutton(sets_frame, text="別々の10問をA/B 2セット", variable=two_sets_var, value="different").pack(anchor="w")
    ttk.Radiobutton(sets_frame, text="同じ10問を2枚（切って配布向け）", variable=two_sets_var, value="same").pack(anchor="w")

    answers_check = ttk.Checkbutton(main, text="解答PDFも同時に作る", variable=answers_var)
    answers_check.grid(row=6, column=1, sticky="w", pady=(8, 12))

    def sync_answers_availability(*_args) -> None:
        # 「左に問題・右に解答」モードは1枚に解答も入るので、別ファイルの解答PDFは無効化する。
        if two_sets_var.get() == "qa":
            answers_var.set(False)
            answers_check.config(state="disabled")
        else:
            answers_check.config(state="normal")
            answers_var.set(True)

    two_sets_var.trace_add("write", sync_answers_availability)
    sync_answers_availability()

    note = (
        "PDF: A4横 1ページ / 左右それぞれA5縦 / 中央に切り取り線\n"
        "単語帳を選ぶとCSV欄と出題範囲の候補が切り替わります（自前のCSVはファイル / URLで指定）"
    )
    ttk.Label(main, text=note, justify="left").grid(row=7, column=0, columnspan=3, sticky="w", pady=(0, 12))

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
    generate_button.grid(row=8, column=0, columnspan=3, pady=(8, 0), ipadx=28, ipady=7)

    # 更新通知（新しいリリースがある時だけ表示）。
    # 配布ビルドなら「今すぐ更新」でその場で入れ替え、ソース実行ならダウンロードページへのリンクのみ。
    link_font = tkfont.nametofont("TkDefaultFont").copy()
    link_font.configure(underline=True)
    update_frame = ttk.Frame(main)
    update_frame.grid(row=9, column=0, columnspan=3, pady=(10, 0))
    update_msg_var = tk.StringVar(value="")
    ttk.Label(update_frame, textvariable=update_msg_var).pack(side="left")
    update_button = ttk.Button(update_frame, text="今すぐ更新")
    update_link = ttk.Label(update_frame, text="ダウンロードページ", foreground="#1a73e8", font=link_font, cursor="hand2")
    progress_frame = ttk.Frame(main)
    progress_var = tk.StringVar(value="")
    progress_bar = ttk.Progressbar(progress_frame, length=300, mode="determinate", maximum=1000)
    progress_bar.pack(side="left")
    ttk.Label(progress_frame, textvariable=progress_var).pack(side="left", padx=(8, 0))
    found: List[ReleaseInfo] = []  # ワーカースレッド → メインスレッドの受け渡し用

    def open_release_page(_event=None) -> None:
        if found:
            webbrowser.open(found[0].url)

    update_link.bind("<Button-1>", open_release_page)

    def show_update_notice(info: ReleaseInfo) -> None:
        update_msg_var.set(f"新しいバージョン v{info.version} があります（現在 v{APP_VERSION}）")
        if self_update.is_frozen():
            update_button.pack(side="left", padx=(10, 0))
        update_link.pack(side="left", padx=(10, 0))

    def set_updating(active: bool) -> None:
        state = "disabled" if active else "normal"
        update_button.config(state=state)
        generate_button.config(state=state)
        if active:
            progress_frame.grid(row=10, column=0, columnspan=3, pady=(6, 0))
        else:
            progress_frame.grid_remove()

    def start_self_update() -> None:
        info = found[0]
        reason = self_update.why_not_updatable()
        if reason:
            messagebox.showwarning("更新できません", f"{reason}\n\n「ダウンロードページ」から手動で更新してください。")
            return
        if not messagebox.askyesno(
            "更新",
            f"v{info.version} をダウンロードしてアプリを置き換えます。\n完了するとアプリは自動的に再起動します。\n\n続けますか？",
        ):
            return

        set_updating(True)
        state: dict = {"done": 0, "total": None, "phase": "準備中…", "staged": None, "error": None}

        def on_progress(done: int, total: int | None) -> None:
            state["done"], state["total"] = done, total

        def on_phase(text: str) -> None:
            state["phase"] = text

        def worker() -> None:
            try:
                state["staged"] = self_update.stage_update(
                    info.version, info.assets, make_http(), progress=on_progress, log=on_phase
                )
            except Exception as e:  # ユーザーに見せて終わる
                state["error"] = e

        def poll() -> None:
            if state["error"] is not None:
                set_updating(False)
                if messagebox.askyesno(
                    "更新に失敗しました",
                    f"{state['error']}\n\nダウンロードページを開いて手動で更新しますか？",
                ):
                    webbrowser.open(info.url)
                return
            if state["staged"] is not None:
                progress_bar["value"] = 1000
                update_msg_var.set("入れ替えて再起動しています…")
                root.update_idletasks()
                try:
                    self_update.apply_and_relaunch(state["staged"])
                except Exception as e:
                    set_updating(False)
                    messagebox.showerror("更新に失敗しました", str(e))
                    return
                root.destroy()
                os._exit(0)  # 旧バージョンのプロセスはここで終わる（後片付けは新版の起動時）
            done, total = state["done"], state["total"]
            update_msg_var.set(state["phase"])  # 「ダウンロードしています…」などの段階表示
            if total:
                progress_bar["value"] = int(1000 * done / total)
                progress_var.set(f"{done / 1048576:.1f} / {total / 1048576:.1f} MB")
            else:
                progress_var.set(f"{done / 1048576:.1f} MB")
            root.after(100, poll)

        threading.Thread(target=worker, daemon=True).start()
        root.after(100, poll)

    update_button.config(command=start_self_update)

    def check_update_worker() -> None:
        info = check_for_update()
        if info:
            found.append(info)

    def poll_update(thread: threading.Thread) -> None:
        # tkinter はメインスレッド以外から触れないので、結果は after() で拾う
        if found:
            show_update_notice(found[0])
        elif thread.is_alive():
            root.after(300, poll_update, thread)

    if check_update:
        worker = threading.Thread(target=check_update_worker, daemon=True)
        worker.start()
        root.after(300, poll_update, worker)

    main.columnconfigure(1, weight=1)
    root.mainloop()


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=APP_NAME)
    source = p.add_mutually_exclusive_group()
    source.add_argument(
        "--dataset",
        choices=[ds.key for ds in DATASETS],
        default=None,
        help="同梱の単語帳を選ぶ: " + " / ".join(f"{ds.key}={ds.label}" for ds in DATASETS) + f"（既定: {DEFAULT_DATASET.key}）",
    )
    source.add_argument("--csv", default=None, help="自前のCSVファイルのパス、またはURL（--dataset の代わりに使う）")
    p.add_argument("--range", dest="range_text", help="例: 1-100, 101-200")
    p.add_argument(
        "--direction",
        choices=["meaning-to-word", "word-to-meaning"],
        default="word-to-meaning",
        help="meaning-to-word=日本語→英単語, word-to-meaning=英単語→日本語（既定）",
    )
    p.add_argument(
        "--two-sets",
        choices=["qa", "same", "different"],
        default="qa",
        help="qa=左に問題・右に解答を1枚に（既定）, same=同じ問題を左右2枚, different=左右で別問題",
    )
    p.add_argument("--answers", action="store_true", help="解答PDFも作る")
    p.add_argument("--no-answers", action="store_true", help="解答PDFを作らない")
    p.add_argument("--seed", type=int, default=None, help="乱数seed（同じ問題を再現したい場合）")
    p.add_argument("--output", help="出力PDF")
    p.add_argument("--gui", action="store_true", help="GUIを起動")
    p.add_argument("--no-update-check", action="store_true", help="GUI起動時に新バージョンの確認をしない")
    p.add_argument("--check-update", action="store_true", help="新しいバージョンがあるか確認して終了")
    p.add_argument("--update", action="store_true", help="新しいバージョンがあればダウンロードして置き換える（配布ビルドのみ）")
    p.add_argument("--assume-version", default=None, help=argparse.SUPPRESS)  # テスト用: 現在のバージョンを偽る
    p.add_argument("--no-relaunch", action="store_true", help=argparse.SUPPRESS)  # テスト用: 置き換え後に再起動しない
    p.add_argument("--version", action="version", version=f"{APP_NAME} v{APP_VERSION}")
    return p


def main() -> None:
    self_update.cleanup_leftovers()  # 前回の更新で残った旧バージョンを片付ける
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.update:
        run_self_update_cli(current=args.assume_version or APP_VERSION, relaunch=not args.no_relaunch)
        return

    if args.check_update:
        latest = fetch_latest_release()
        if latest is None:
            print("更新を確認できませんでした（ネットワーク接続を確認してください）")
            sys.exit(1)
        if parse_version(latest.version) > parse_version(APP_VERSION):
            print(f"新しいバージョン v{latest.version} があります（現在 v{APP_VERSION}）: {latest.url}")
        else:
            print(f"v{APP_VERSION} は最新です")
        return

    if args.csv:
        csv_source: str | Path = args.csv if is_url(args.csv) else Path(args.csv).expanduser()
    else:
        csv_source = dataset_by_key(args.dataset or DEFAULT_DATASET.key).url

    # No CLI range -> GUI by default.
    if args.gui or not args.range_text:
        launch_gui(initial_csv=str(csv_source), check_update=not args.no_update_check)
        return

    # qa（左に問題・右に解答）は1枚に解答も入るので、既定では別ファイルの解答PDFを作らない。
    # --answers / --no-answers を明示した場合はそちらを優先する。
    make_answers_flag = args.two_sets != "qa"
    if args.no_answers:
        make_answers_flag = False
    elif args.answers:
        make_answers_flag = True

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
