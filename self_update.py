#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配布ビルド（PyInstaller onedir）の自己更新。

GitHub Releases から自分のOS向けのアーカイブをダウンロードし、検証し、
インストール先（macOS は .app バンドル、Windows/Linux は exe の入ったフォルダ）を
まるごと入れ替えて再起動する。標準ライブラリのみに依存する。

流れ:
  1. why_not_updatable() / install_root()  … 置き換え対象を特定し、可否を判定
  2. stage_update()      … 置き換え対象の隣に一時フォルダを作り、DL → SHA256 検証 → 展開
  3. apply_and_relaunch()… macOS/Linux は rename で入れ替えて新版を起動、
                           Windows は実行中の exe を置き換えられないので
                           PowerShell の小さなヘルパーに引き継いでから終了
  4. cleanup_leftovers() … 次回起動時に、前回の入れ替えで残った *.old 等を消す

ソースから実行している場合（frozen でない）は何もできない。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import ssl
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Optional

APP_EXE_NAME = "EikenVocabTestMaker"
APP_BUNDLE_NAME = "EikenVocabTestMaker.app"
# release.yml が作る配布ファイル名（OSごと）
ASSET_NAMES = {
    "darwin": "EikenVocabTestMaker-macOS.zip",
    "win32": "EikenVocabTestMaker-Windows.zip",
    "linux": "EikenVocabTestMaker-Linux.tar.gz",
}
CHECKSUMS_ASSET = "SHA256SUMS.txt"
STAGE_DIR_NAME = ".EikenVocabTestMaker-update"
BACKUP_SUFFIX = ".old"
MAX_ARCHIVE_BYTES = 200 * 1024 * 1024

ProgressFn = Callable[[int, Optional[int]], None]  # (受信済みバイト数, 合計バイト数 or None)
LogFn = Callable[[str], None]


class UpdateError(Exception):
    """ユーザーにそのまま見せられる更新エラー。"""


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size: int = 0


@dataclass(frozen=True)
class Staged:
    """ダウンロード・展開が終わり、入れ替えを待つ状態。"""

    root: Path  # 置き換え対象（現在のインストール先）
    new_root: Path  # 展開済みの新バージョン（root と同じ親フォルダ内の一時フォルダ配下）
    stage_dir: Path  # 一時フォルダ（後始末用）
    version: str


@dataclass
class Http:
    """urllib の薄いラッパー。証明書は呼び出し側（certifi）の SSLContext を使う。"""

    user_agent: str
    context: ssl.SSLContext = field(default_factory=ssl.create_default_context)
    timeout: float = 30.0

    def open(self, url: str):
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        return urllib.request.urlopen(req, timeout=self.timeout, context=self.context)

    def get_text(self, url: str, limit: int = 1024 * 1024) -> str:
        with self.open(url) as resp:
            return resp.read(limit).decode("utf-8", errors="replace")

    def download(
        self,
        url: str,
        dest: Path,
        progress: ProgressFn | None = None,
        expected_size: int | None = None,
        limit: int = MAX_ARCHIVE_BYTES,
    ) -> int:
        """url を dest に保存し、受信バイト数を返す。"""
        done = 0
        with self.open(url) as resp, open(dest, "wb") as out:
            total = expected_size or None
            length = resp.headers.get("Content-Length")
            if not total and length and length.isdigit():
                total = int(length)
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                done += len(chunk)
                if done > limit:
                    raise UpdateError(f"ダウンロードサイズが上限（{limit // (1024 * 1024)}MB）を超えました。")
                out.write(chunk)
                if progress:
                    progress(done, total)
        return done


# ---------------------------------------------------------------------------
# 置き換え対象の特定
# ---------------------------------------------------------------------------


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def exe_name() -> str:
    return APP_EXE_NAME + (".exe" if sys.platform == "win32" else "")


def install_root(executable: str | None = None) -> Optional[Path]:
    """置き換え対象。macOS: .app バンドル、その他: exe と _internal/ が入ったフォルダ。
    frozen でない場合や構成が想定外なら None。"""
    if executable is None:
        if not is_frozen():
            return None
        executable = sys.executable
    exe = Path(executable).resolve()
    if sys.platform == "darwin":
        for parent in exe.parents:
            if parent.suffix == ".app":
                return parent
        return None
    if (exe.parent / "_internal").is_dir():  # PyInstaller onedir
        return exe.parent
    return None


def is_translocated(root: Path) -> bool:
    """macOS の App Translocation（隔離属性付きアプリをランダムな読み取り専用パスで実行する仕組み）。"""
    return sys.platform == "darwin" and "/AppTranslocation/" in str(root)


def why_not_updatable() -> Optional[str]:
    """自己更新できない理由。できるなら None。"""
    if not is_frozen():
        return "ソースから実行中のため、自動更新は使えません（git pull などで更新してください）。"
    root = install_root()
    if root is None:
        return "アプリの配置場所を特定できませんでした。"
    if is_translocated(root):
        return (
            "macOS がアプリを一時的な場所で実行しているため、この場所では更新できません。\n"
            "Finder でアプリを「アプリケーション」フォルダなどに移動してから、もう一度起動してください。"
        )
    if sys.platform not in ASSET_NAMES:
        return f"このOS（{sys.platform}）向けの配布ファイルがありません。"
    parent = root.parent
    if not os.access(parent, os.W_OK) or not os.access(root, os.W_OK):
        return f"フォルダに書き込めないため更新できません: {parent}"
    return None


def can_self_update() -> bool:
    return why_not_updatable() is None


# ---------------------------------------------------------------------------
# ダウンロード・検証・展開
# ---------------------------------------------------------------------------


def parse_sha256sums(text: str) -> Dict[str, str]:
    """sha256sum の出力（`<hash>  <name>` / `<hash> *<name>`）を {ファイル名: hash} にする。"""
    result: Dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and len(parts[0]) == 64:
            name = parts[-1].lstrip("*")
            result[Path(name).name] = parts[0].lower()
    return result


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_archive(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    name = archive.name.lower()
    if sys.platform == "darwin" and name.endswith(".zip"):
        # .app のシンボリックリンク・実行権限・署名を壊さないよう ditto で展開する
        subprocess.run(["/usr/bin/ditto", "-x", "-k", str(archive), str(dest)], check=True, capture_output=True)
    elif name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest)
    elif name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive, "r:gz") as tf:
            try:
                tf.extractall(dest, filter="data")
            except TypeError:  # Python < 3.12
                tf.extractall(dest)
    else:
        raise UpdateError(f"対応していない形式のファイルです: {archive.name}")


def find_new_root(extract_dir: Path) -> Path:
    """展開先から、置き換えに使うアプリ本体（.app / exe入りフォルダ）を探す。"""
    if sys.platform == "darwin":
        exe_rel = Path("Contents") / "MacOS" / APP_EXE_NAME
        candidates = [p for p in _walk_shallow(extract_dir) if p.is_dir() and p.name == APP_BUNDLE_NAME]
    else:
        exe_rel = Path(exe_name())
        candidates = [p for p in _walk_shallow(extract_dir) if p.is_dir() and (p / "_internal").is_dir()]
    for c in sorted(candidates, key=lambda p: len(p.parts)):
        if (c / exe_rel).exists():
            return c
    raise UpdateError("ダウンロードしたファイルの中にアプリが見つかりませんでした。")


def _walk_shallow(base: Path, depth: int = 2):
    """base 直下〜depth 階層までのパスを列挙する（.app の中身までは潜らない）。"""
    frontier = [base]
    for _ in range(depth):
        next_frontier = []
        for d in frontier:
            try:
                children = list(d.iterdir())
            except OSError:
                continue
            for c in children:
                yield c
                if c.is_dir() and c.suffix != ".app":
                    next_frontier.append(c)
        frontier = next_frontier


def stage_update(
    version: str,
    assets: Dict[str, Asset],
    http: Http,
    progress: ProgressFn | None = None,
    log: LogFn | None = None,
) -> Staged:
    """新バージョンをダウンロード・検証・展開して、入れ替え待ちの状態にする。"""
    reason = why_not_updatable()
    if reason:
        raise UpdateError(reason)
    root = install_root()
    assert root is not None

    asset_name = ASSET_NAMES[sys.platform]
    asset = assets.get(asset_name)
    if asset is None:
        raise UpdateError(f"このOS向けのファイル（{asset_name}）がリリースに含まれていません。")

    stage_dir = root.parent / STAGE_DIR_NAME
    shutil.rmtree(stage_dir, ignore_errors=True)
    stage_dir.mkdir()
    archive = stage_dir / asset.name

    if log:
        log(f"v{version} をダウンロードしています…")
    http.download(asset.url, archive, progress=progress, expected_size=asset.size or None)

    if log:
        log("ダウンロードしたファイルを検証しています…")
    if asset.size and archive.stat().st_size != asset.size:
        raise UpdateError("ダウンロードしたファイルのサイズが一致しません。もう一度お試しください。")
    sums_asset = assets.get(CHECKSUMS_ASSET)
    if sums_asset is not None:
        sums = parse_sha256sums(http.get_text(sums_asset.url))
        expected = sums.get(asset.name)
        if expected is None:
            raise UpdateError(f"{CHECKSUMS_ASSET} に {asset.name} のハッシュがありません。")
        if sha256_of(archive) != expected:
            raise UpdateError("ダウンロードしたファイルの検証に失敗しました（SHA256 が一致しません）。")

    if log:
        log("展開しています…")
    extract_dir = stage_dir / "extracted"
    extract_archive(archive, extract_dir)
    new_root = find_new_root(extract_dir)
    return Staged(root=root, new_root=new_root, stage_dir=stage_dir, version=version)


# ---------------------------------------------------------------------------
# 入れ替えと再起動
# ---------------------------------------------------------------------------


def swap_dirs(root: Path, new_root: Path) -> Path:
    """root を root.old に退避し、new_root を root の位置へ移す。失敗時は元に戻す。
    退避先のパスを返す（削除は呼び出し側の責任）。"""
    backup = root.parent / (root.name + BACKUP_SUFFIX)
    shutil.rmtree(backup, ignore_errors=True)
    os.rename(root, backup)  # 実行中でも rename はできる（プロセスは inode を掴んでいる）
    try:
        os.rename(new_root, root)
    except OSError:
        os.rename(backup, root)
        raise
    return backup


def apply_and_relaunch(staged: Staged, relaunch: bool = True) -> None:
    """入れ替えて新版を起動する。戻ったら呼び出し側は速やかに終了すること。

    旧バージョンのファイルは、この（旧）プロセスからは消さない。まだ読み込んでいない
    モジュールや DLL を後から開けなくなるため。掃除は次回起動時の cleanup_leftovers() に任せる。"""
    if sys.platform == "win32":
        _apply_windows(staged, relaunch)
        return

    root = staged.root
    swap_dirs(root, staged.new_root)
    if relaunch:
        if sys.platform == "darwin":
            subprocess.Popen(["/usr/bin/open", "-n", str(root)])
        else:
            subprocess.Popen(
                [str(root / exe_name())],
                cwd=str(root),
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


def _ps_quote(path: Path | str) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _apply_windows(staged: Staged, relaunch: bool) -> None:
    """Windows は実行中の exe/DLL を移動できないので、PowerShell に引き継いで終了後に入れ替える。"""
    root, new_root, stage_dir = staged.root, staged.new_root, staged.stage_dir
    script = stage_dir / "apply_update.ps1"
    script.write_text(
        f"""
$ErrorActionPreference = 'SilentlyContinue'
$procId = {os.getpid()}
$root = {_ps_quote(root)}
$new = {_ps_quote(new_root)}
$stage = {_ps_quote(stage_dir)}
$exe = {_ps_quote(root / exe_name())}
$old = $root + '{BACKUP_SUFFIX}'
$relaunch = ${'true' if relaunch else 'false'}

try {{ Wait-Process -Id $procId -Timeout 120 }} catch {{ }}
Start-Sleep -Milliseconds 500
if (Test-Path -LiteralPath $old) {{ Remove-Item -LiteralPath $old -Recurse -Force }}

$moved = $false
for ($i = 0; $i -lt 20; $i++) {{
    try {{
        Move-Item -LiteralPath $root -Destination $old -ErrorAction Stop
        $moved = $true
        break
    }} catch {{ Start-Sleep -Milliseconds 500 }}
}}
if ($moved) {{
    try {{
        Move-Item -LiteralPath $new -Destination $root -ErrorAction Stop
        Remove-Item -LiteralPath $old -Recurse -Force
    }} catch {{
        if (-not (Test-Path -LiteralPath $root)) {{ Move-Item -LiteralPath $old -Destination $root }}
    }}
}}
if ($relaunch) {{ Start-Process -FilePath $exe -WorkingDirectory $root }}
Remove-Item -LiteralPath $stage -Recurse -Force
""".lstrip(),
        encoding="utf-8-sig",
    )
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(script),
        ],
        cwd=str(root.parent),
        creationflags=flags,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def cleanup_leftovers() -> None:
    """前回の更新で残った旧バージョン（*.old）と一時フォルダを消す。起動時に呼ぶ。失敗しても無視。"""
    root = install_root()
    if root is None:
        return
    for leftover in (root.parent / (root.name + BACKUP_SUFFIX), root.parent / STAGE_DIR_NAME):
        if leftover.exists():
            shutil.rmtree(leftover, ignore_errors=True)
