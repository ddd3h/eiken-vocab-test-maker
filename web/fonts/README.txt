NotoSansJP-Regular-subset.ttf は Noto Sans JP Regular（SIL OFL 1.1, OFL.txt 参照）から、
JIS X 0208 の全文字・ASCII・同梱CSVで使われる文字だけを残したサブセットです。
pdf-lib 同梱の fontkit は、グリフデータが4バイト境界に揃っていないフォントをサブセット化すると
glyf/loca が壊れて一部の文字が欠けるため、glyf を4バイトでパディングして作っています。
作り直し手順の例（fonttools）:
  opts = subset.Options(); opts.layout_features=[]; opts.hinting=False
  f = subset.load_font(src, opts); s = subset.Subsetter(opts); s.populate(text=chars); s.subset(f)
  f["glyf"].padding = 4; subset.save_font(f, out, opts)
