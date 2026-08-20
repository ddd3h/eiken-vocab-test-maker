# 英検2級 単語テストメーカー

CSV（`No,単語,意味`）から出題範囲を指定し、A4横1枚にA5縦の単語テストを2枚面付けして
PDFで書き出すツールです（GUI / CLI）。同一問題2枚 or 左右で別問題、解答PDFも作れます。

出力イメージ（A4横1枚に、A5縦のテストが左右2枚。中央の点線で切り取り）:

| テスト（配布用） | 解答 |
|---|---|
| ![テストPDFのサンプル](docs/images/screenshot_test.png) | ![解答PDFのサンプル](docs/images/screenshot_answers.png) |

サンプルPDFそのもの: [`samples/sample_test_1-100.pdf`](samples/sample_test_1-100.pdf) /
[`samples/sample_test_1-100_answers.pdf`](samples/sample_test_1-100_answers.pdf)

## ダウンロード（ビルド済みアプリ）

[Releases](../../releases/latest) から、Windows / macOS / Linux 用のビルド済みアプリを
ダウンロードできます。Pythonのインストールは不要です。

| OS | ファイル |
|---|---|
| Windows | `EikenVocabTestMaker-Windows.zip` を解凍し `EikenVocabTestMaker.exe` を実行 |
| macOS | `EikenVocabTestMaker-macOS.zip` を解凍し `EikenVocabTestMaker.app` を実行 |
| Linux | `EikenVocabTestMaker-Linux.tar.gz` を展開し `EikenVocabTestMaker` を実行 |

※ 署名なしビルドのため、初回起動時にOSの警告が出ることがあります
（macOS: control + クリック →「開く」/ Windows: 「詳細情報」→「実行」）。

## いちばん簡単な使い方（ソースから・Mac）

1. このリポジトリをダウンロード（Code → Download ZIP）して展開します。
2. `run_mac.command` をダブルクリックします。
3. 初回だけ必要なPythonパッケージが自動で入ります（要インターネット接続）。
4. GUIで出題範囲を選び、「PDFを作成」を押します。

※ macOSのセキュリティで初回起動を止められた場合:
Finderで `run_mac.command` を control + クリック →「開く」

## CSVをファイルパスの代わりにURLで指定する

GUIのCSV欄・CLIの `--csv` は、ローカルのファイルパスに加えて **URL** も指定できます。
指定すると起動のたびにダウンロードして使うので、CSVファイルを事前にダウンロードしておく
必要がありません（ローカルにはキャッシュしません）。

- 対応: `http://` / `https://` のURL
- GitHubの通常のファイル表示URL（`.../blob/...`）を渡した場合は、自動的にRaw URLに変換します
- GUIでは「URL」ボタンから入力できます

```bash
python3 vocab_test_maker.py --range 1-100 \
  --csv https://raw.githubusercontent.com/ddd3h/eiken-vocab-test-maker/main/data/eiken2_pass_tan_1700.csv \
  --output test.pdf
```

## CLI例

```bash
python3 vocab_test_maker.py --range 1-100 --output test.pdf
python3 vocab_test_maker.py --range 101-200 --direction word-to-meaning \
  --two-sets different --answers --output test_101_200.pdf

# 同じ問題を再現したい場合
python3 vocab_test_maker.py --range 1-100 --seed 12345 --output test.pdf
```

主なオプション:

| オプション | 説明 |
|---|---|
| `--csv` | CSVファイルのパス、またはURL |
| `--range` | 出題範囲。例: `1-100` |
| `--direction` | `meaning-to-word`（日本語→英単語）/ `word-to-meaning`（英単語→日本語） |
| `--two-sets` | `same`（左右とも同じ10問）/ `different`（左右で別の10問） |
| `--answers` / `--no-answers` | 解答PDFを作る / 作らない |
| `--seed` | 乱数seed（同じ問題を再現したい場合） |
| `--output` | 出力PDFのパス |
| `--gui` | GUIを起動 |

## GUIの機能

- 出題範囲: 1-100, 101-200, ... 1601-1700（自由入力も可）
- 100語の範囲からランダムに10問
- 日本語 → 英単語 / 英単語 → 日本語
- A4横1ページにA5縦を左右2枚
- 「同じ10問を2枚」または「A/B別々の10問」
- 解答PDFも同時生成可能
- CSVはファイル選択またはURL指定

## CSVフォーマット

列名は `No,単語,意味` の3列（UTF-8）である必要があります。

```csv
No,単語,意味
1,let,Oに～させる
2,create,(を)つくり出す
```

同梱の `data/eiken2_pass_tan_1700.csv` を自前の単語リストに差し替えれば、
英検2級に限らず汎用の単語テストメーカーとして使えます。

## .app（macOSアプリ）化

`build_mac_app.command` をMacでダブルクリックすると、PyInstallerで
`dist/EikenVocabTestMaker.app` を作ります。macOS用の実行バイナリ/.appは、
macOS上でビルドする必要があります。

## 必要環境

- macOS
- Python 3
- インターネット接続（初回の依存パッケージインストール時、およびCSVをURLで指定する場合）

## ライセンス

[MIT License](LICENSE)
