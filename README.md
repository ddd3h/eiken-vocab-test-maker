# 英検2級 単語テストメーカー

CSV（`No,単語,意味`）から出題範囲を指定し、A4横1枚にA5縦の単語テストを2枚面付けして
PDFで書き出すツールです（GUI / CLI）。同一問題2枚 or 左右で別問題、解答PDFも作れます。

サンプル: [`samples/sample_test_1-100.pdf`](samples/sample_test_1-100.pdf) /
[`samples/sample_test_1-100_answers.pdf`](samples/sample_test_1-100_answers.pdf)

## いちばん簡単な使い方（Mac）

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

## CSVの出典について

同梱の `data/eiken2_pass_tan_1700.csv` は、市販の単語帳（旺文社『英検2級 でる順パス単』）
の見出し語・語義をもとにしたデータです。公開リポジトリでの再配布が権利者の許諾範囲を
超える可能性があるため、公開・再配布する際はご自身の判断・責任で行ってください。
不安がある場合は `data/eiken2_pass_tan_1700.csv` を削除し、同じ列名（`No,単語,意味`）の
自作CSVに差し替えて利用してください。

## ライセンス

ソースコードは [MIT License](LICENSE) です（同梱CSVを除く。CSVのライセンス・著作権については
上記「CSVの出典について」を参照してください）。
