#!/usr/bin/env bash
# GitHub Pages に公開するファイルを _site/ に組み立てる。
# ローカル確認: scripts/build_site.sh && python3 -m http.server -d _site 8000
set -euo pipefail
cd "$(dirname "$0")/.."

rm -rf _site
mkdir -p _site/data _site/assets/icon
cp -r web/. _site/
cp data/*.csv _site/data/
cp assets/icon/EikenVocabTestMaker-256.png assets/icon/eiken_vocab_test_maker_icon.svg _site/assets/icon/
touch _site/.nojekyll
echo "built _site/"
