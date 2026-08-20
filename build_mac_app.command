#!/bin/bash
# Mac上でダブルクリックして、配布可能な .app を作成します。
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 が必要です。"
  exit 1
fi

python3 -m venv .buildvenv
source .buildvenv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller

rm -rf build dist

pyinstaller --noconfirm --clean EikenVocabTestMaker.spec

echo
echo "完成: dist/EikenVocabTestMaker.app"
echo "Finder で dist フォルダを開きます。"
open dist
