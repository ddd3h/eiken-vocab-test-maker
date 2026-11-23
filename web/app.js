/* 単語テストメーカー（Web版）
 *
 * vocab_test_maker.py（デスクトップ版）と同じレイアウトのPDFを、ブラウザの中だけで作ります。
 * 入力したCSVや生成したPDFがサーバーへ送られることはありません。
 *
 * 依存: pdf-lib / @pdf-lib/fontkit（vendor/ に同梱）、Noto Sans JP のサブセット（fonts/ に同梱）
 */
(() => {
  "use strict";

  const APP_NAME = "単語テストメーカー";
  const QUESTIONS_PER_TEST = 10;
  const RANGE_STEP = 100;
  const MAX_CSV_BYTES = 20 * 1024 * 1024;
  const FONT_URL = "fonts/NotoSansJP-Regular-subset.ttf";
  const MM = 72 / 25.4;
  const A4_LANDSCAPE = [841.8898, 595.2756]; // [幅, 高さ] pt

  // 同梱の単語帳（Python版 DATASETS と同じ。CSVは data/ から同一オリジンで読む）
  const DATASETS = [
    { key: "eiken2", label: "英検2級 パス単（1700語）", file: "eiken2_pass_tan_1700.csv", total: 1700 },
    { key: "target1900", label: "ターゲット1900（1900語）", file: "target_1900.csv", total: 1900 },
    { key: "target1200", label: "ターゲット1200（1700語）", file: "target_1200.csv", total: 1700 },
    { key: "eikenpre1", label: "英検準1級 パス単（1900語）", file: "eiken_pre1_pass_tan_1900.csv", total: 1900 },
  ];

  // ---------------------------------------------------------------- CSV

  /** RFC4180 風の簡易CSVパーサ（引用符・引用符内のカンマ/改行に対応）。 */
  function parseCsvRows(text) {
    const rows = [];
    let row = [];
    let field = "";
    let inQuotes = false;
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (inQuotes) {
        if (ch === '"') {
          if (text[i + 1] === '"') { field += '"'; i++; } else { inQuotes = false; }
        } else {
          field += ch;
        }
      } else if (ch === '"') {
        inQuotes = true;
      } else if (ch === ",") {
        row.push(field); field = "";
      } else if (ch === "\n" || ch === "\r") {
        if (ch === "\r" && text[i + 1] === "\n") i++;
        row.push(field); field = "";
        rows.push(row); row = [];
      } else {
        field += ch;
      }
    }
    if (field !== "" || row.length) { row.push(field); rows.push(row); }
    return rows.filter((r) => r.some((c) => c.trim() !== ""));
  }

  function normalizeHeader(name) {
    const text = (name ?? "").trim().replace(/^﻿/, "");
    return text.replace(/\.+$/, "").toLowerCase() === "no" ? "No" : text;
  }

  function parseVocab(text) {
    const rows = parseCsvRows(text.replace(/^﻿/, ""));
    if (!rows.length) throw new Error("CSVから単語データを読み込めませんでした。");
    const header = rows[0].map(normalizeHeader);
    const iNo = header.indexOf("No");
    const iWord = header.indexOf("単語");
    const iMeaning = header.indexOf("意味");
    if (iNo < 0 || iWord < 0 || iMeaning < 0) {
      throw new Error("CSVの列名は『No, 単語, 意味』である必要があります。");
    }
    const items = [];
    for (const r of rows.slice(1)) {
      const no = Number.parseInt((r[iNo] ?? "").trim(), 10);
      if (!/^\s*-?\d+\s*$/.test(r[iNo] ?? "") || Number.isNaN(no)) continue;
      items.push({ no, word: (r[iWord] ?? "").trim(), meaning: (r[iMeaning] ?? "").trim() });
    }
    if (!items.length) throw new Error("CSVから単語データを読み込めませんでした。");
    return items.sort((a, b) => a.no - b.no);
  }

  function decodeCsv(buffer) {
    if (buffer.byteLength > MAX_CSV_BYTES) {
      throw new Error(`CSVのサイズが大きすぎます（上限 ${MAX_CSV_BYTES / 1048576}MB）。`);
    }
    let text;
    try {
      text = new TextDecoder("utf-8", { fatal: true }).decode(buffer);
    } catch {
      throw new Error("CSVの文字コードを判別できませんでした。UTF-8で保存してください。");
    }
    const head = text.replace(/^﻿/, "").trimStart().toLowerCase();
    if (head.startsWith("<!doctype html") || head.startsWith("<html")) {
      throw new Error("URLがCSVではなくWebページを指しています。GitHubなら Raw ボタンのURLを使ってください。");
    }
    return text;
  }

  /** GitHubの blob URL を raw.githubusercontent.com のURLに変換する。 */
  function normalizeCsvUrl(url) {
    const m = url.trim().match(/^https:\/\/github\.com\/([^/]+)\/([^/]+)\/blob\/([^/]+)\/(.+)$/);
    return m ? `https://raw.githubusercontent.com/${m[1]}/${m[2]}/${m[3]}/${m[4]}` : url.trim();
  }

  async function fetchCsv(url) {
    let resp;
    try {
      resp = await fetch(url, { cache: "no-cache" });
    } catch (e) {
      throw new Error(
        "CSVをダウンロードできませんでした。ネットワーク接続、またはそのURLがブラウザからのアクセス（CORS）を" +
          "許可しているか確認してください。GitHubの Raw URL なら使えます。\nURL: " + url
      );
    }
    if (!resp.ok) throw new Error(`CSVの取得に失敗しました（HTTP ${resp.status}）: ${url}`);
    return decodeCsv(await resp.arrayBuffer());
  }

  // ---------------------------------------------------------------- 出題ロジック

  function parseRange(text) {
    const norm = text.trim().replace(/[〜~–—−]/g, "-");
    const parts = norm.split("-").map((p) => p.trim()).filter(Boolean);
    if (parts.length !== 2 || !parts.every((p) => /^\d+$/.test(p))) {
      throw new Error("範囲は 1-100 の形式で指定してください。");
    }
    const start = Number(parts[0]);
    const end = Number(parts[1]);
    if (start <= 0 || end < start) throw new Error("範囲指定が不正です。");
    return [start, end];
  }

  function rangePresets(total, step = RANGE_STEP) {
    const out = [];
    for (let s = 1; s <= total; s += step) out.push(`${s}-${Math.min(s + step - 1, total)}`);
    return out;
  }

  function filterRange(items, start, end) {
    const pool = items.filter((x) => x.no >= start && x.no <= end);
    if (pool.length < QUESTIONS_PER_TEST) {
      throw new Error(`No.${start}-${end} に10語以上ありません（${pool.length}語）。`);
    }
    return pool;
  }

  /** seed 指定時に再現できる乱数（mulberry32）。 */
  function makeRng(seed) {
    let a = (seed ?? (crypto.getRandomValues(new Uint32Array(1))[0])) >>> 0;
    return () => {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function sample(arr, n, rng) {
    const a = arr.slice();
    for (let i = 0; i < n; i++) {
      const j = i + Math.floor(rng() * (a.length - i));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a.slice(0, n);
  }

  function chooseTests(pool, twoSets, rng) {
    if (twoSets === "same" || twoSets === "qa") {
      const test = sample(pool, QUESTIONS_PER_TEST, rng);
      return [test, test.slice()];
    }
    if (pool.length >= QUESTIONS_PER_TEST * 2) {
      const picked = sample(pool, QUESTIONS_PER_TEST * 2, rng);
      return [picked.slice(0, QUESTIONS_PER_TEST), picked.slice(QUESTIONS_PER_TEST)];
    }
    return [sample(pool, QUESTIONS_PER_TEST, rng), sample(pool, QUESTIONS_PER_TEST, rng)];
  }

  // ---------------------------------------------------------------- PDF描画

  /** 文字列が表示できるフォントを返す（Helvetica で表せない文字を含むなら日本語フォントに切り替える）。 */
  function pickFont(fonts, text, preferred) {
    if (preferred === fonts.jp) return fonts.jp;
    try {
      preferred.encodeText(text);
      return preferred;
    } catch {
      return fonts.jp;
    }
  }

  const textWidth = (text, font, size) => font.widthOfTextAtSize(text, size);

  function fitFontSize(text, font, maxWidth, initial, minimum) {
    let size = initial;
    while (size > minimum && textWidth(text, font, size) > maxWidth) size -= 0.5;
    return Math.max(size, minimum);
  }

  /** 短い語義向けの簡易な文字単位の折り返し（Python版 _split_to_fit と同じ）。 */
  function splitToFit(text, font, size, maxWidth, maxLines = 2) {
    const w = (s) => textWidth(s, font, size);
    if (w(text) <= maxWidth) return [text];

    const chars = Array.from(text);
    let lines = [];
    let current = "";
    for (const ch of chars) {
      const candidate = current + ch;
      if (current && w(candidate) > maxWidth) {
        lines.push(current);
        current = ch;
        if (lines.length >= maxLines - 1) break;
      } else {
        current = candidate;
      }
    }

    const done = lines.join("");
    if (Array.from(done + current).length < chars.length) {
      const remaining = chars.slice(Array.from(done).length);
      let cur = [];
      for (const ch of remaining) {
        const candidate = cur.concat(ch);
        const suffix = candidate.length < remaining.length ? "…" : "";
        if (cur.length && w(candidate.join("") + suffix) > maxWidth) break;
        cur = candidate;
      }
      current = cur.join("");
      if (lines.length >= maxLines) {
        lines = lines.slice(0, maxLines);
      } else if (lines.length === maxLines - 1) {
        if (cur.length < remaining.length) current = current.trimEnd() + "…";
        lines.push(current);
      }
    } else {
      lines.push(current);
    }
    return lines.slice(0, maxLines);
  }

  /** pdf-lib の描画を、Python版（ReportLab）と同じ呼び方で使うための薄いラッパ。 */
  function makePen(page, rgb) {
    const black = rgb(0, 0, 0);
    return {
      text(str, x, y, font, size, align = "left") {
        if (!str) return;
        const w = align === "left" ? 0 : textWidth(str, font, size);
        const dx = align === "right" ? w : align === "center" ? w / 2 : 0;
        page.drawText(str, { x: x - dx, y, size, font, color: black });
      },
      line(x1, y1, x2, y2, thickness, dash) {
        page.drawLine({
          start: { x: x1, y: y1 },
          end: { x: x2, y: y2 },
          thickness,
          color: black,
          ...(dash ? { dashArray: dash, dashPhase: 0 } : {}),
        });
      },
      rect(x, y, w, h, thickness) {
        page.drawRectangle({ x, y, width: w, height: h, borderWidth: thickness, borderColor: black });
      },
    };
  }

  function drawCutMark(pen, fonts, pageW, pageH) {
    const mid = pageW / 2;
    pen.line(mid, 8 * MM, mid, pageH - 8 * MM, 0.4, [2, 2]);
    pen.text("切り取り線", mid, 4.5 * MM, fonts.jp, 6.5, "center");
  }

  function drawTestPanel(pen, fonts, x0, y0, w, h, items, start, end, direction, label, answers, footer) {
    const marginX = 11 * MM;
    const top = h - 10 * MM;
    const left = x0 + marginX;
    const right = x0 + w - marginX;
    const jp = fonts.jp;

    // 2枚に切り分けるときの目安になる枠線
    pen.rect(x0 + 4 * MM, y0 + 4 * MM, w - 8 * MM, h - 8 * MM, 0.6);

    pen.text(answers ? "英単語テスト 解答" : "英単語テスト", left, y0 + top, jp, 15);
    const prefix = label ? `${label}  ` : "";
    pen.text(`${prefix}出題範囲 No.${start}-${end}`, right, y0 + top + 1.5, jp, 8.5, "right");

    const infoY = y0 + top - 9 * MM;
    if (answers) {
      const dir = direction === "meaning-to-word" ? "日本語 → 英単語" : "英単語 → 日本語";
      pen.text(`形式: ${dir}`, left, infoY, jp, 8.5);
    } else {
      pen.text("名前: ____________________", left, infoY, jp, 8.5);
      pen.text("得点: ______ / 10", right, infoY, jp, 8.5, "right");
    }

    const ruleY = infoY - 4 * MM;
    pen.line(left, ruleY, right, ruleY, 0.5);

    const qTop = ruleY - 6 * MM;
    const bottom = y0 + 11 * MM;
    const rowH = (qTop - bottom) / QUESTIONS_PER_TEST;

    items.forEach((item, idx) => {
      const i = idx + 1;
      const rowTop = qTop - idx * rowH;
      const baseline = rowTop - rowH * 0.47;

      pen.text(`${i}.`, left + 8 * MM, baseline, fonts.enBold, 9.5, "right");

      const m2w = direction === "meaning-to-word";
      const clue = m2w ? item.meaning : item.word;
      const answer = m2w ? item.word : item.meaning;
      const clueFont = pickFont(fonts, clue, m2w ? jp : fonts.en);
      const answerFont = pickFont(fonts, answer, m2w ? fonts.en : jp);

      const clueX = left + 12 * MM;
      const clueWidth = 58 * MM;
      const answerX = clueX + clueWidth + 5 * MM;
      const answerRight = right;

      const drawLines = (lines, font, size, x) => {
        if (lines.length === 1) {
          pen.text(lines[0], x, baseline, font, size);
        } else {
          pen.text(lines[0], x, baseline + 4.2, font, size);
          pen.text(lines[1], x, baseline - 5.5, font, size);
        }
      };

      const clueSize = fitFontSize(clue, clueFont, clueWidth, 10.5, 7.5);
      drawLines(splitToFit(clue, clueFont, clueSize, clueWidth, 2), clueFont, clueSize, clueX);

      if (answers) {
        const answerWidth = answerRight - answerX;
        const ansSize = fitFontSize(answer, answerFont, answerWidth, 10.5, 7.0);
        drawLines(splitToFit(answer, answerFont, ansSize, answerWidth, 2), answerFont, ansSize, answerX);
      } else {
        pen.line(answerX, baseline - 2, answerRight, baseline - 2, 0.45); // 解答を書く線
      }

      if (i < QUESTIONS_PER_TEST) {
        const sepY = rowTop - rowH;
        pen.line(left + 8 * MM, sepY, right, sepY, 0.18);
      }
    });

    pen.text(footer, right, y0 + 6.5 * MM, jp, 6.5, "right");
  }

  let fontBytesPromise = null;
  function loadFontBytes() {
    // 失敗したときに次回やり直せるよう、エラーならキャッシュを捨てる
    fontBytesPromise ??= fetch(FONT_URL)
      .then((r) => {
        if (!r.ok) throw new Error(`日本語フォントを読み込めませんでした（HTTP ${r.status}）。`);
        return r.arrayBuffer();
      })
      .catch((e) => {
        fontBytesPromise = null;
        throw e;
      });
    return fontBytesPromise;
  }

  async function generatePdf({ left, right, start, end, direction, leftAnswers, rightAnswers, labels, sourceName }) {
    const { PDFDocument, StandardFonts, rgb } = window.PDFLib;
    const doc = await PDFDocument.create();
    doc.registerFontkit(window.fontkit);

    const title =
      leftAnswers && rightAnswers ? "英単語テスト 解答" : !leftAnswers && !rightAnswers ? "英単語テスト" : "英単語テスト（問題+解答）";
    doc.setTitle(title);
    doc.setAuthor(APP_NAME);
    doc.setSubject(sourceName);
    doc.setProducer(APP_NAME);
    doc.setCreator(APP_NAME);

    const fonts = {
      jp: await doc.embedFont(await loadFontBytes(), { subset: true }),
      en: await doc.embedFont(StandardFonts.Helvetica),
      enBold: await doc.embedFont(StandardFonts.HelveticaBold),
    };

    const [pageW, pageH] = A4_LANDSCAPE;
    const panelW = pageW / 2;
    const page = doc.addPage([pageW, pageH]);
    const pen = makePen(page, rgb);

    drawTestPanel(pen, fonts, 0, 0, panelW, pageH, left, start, end, direction, labels[0], leftAnswers, sourceName);
    drawTestPanel(pen, fonts, panelW, 0, panelW, pageH, right, start, end, direction, labels[1], rightAnswers, sourceName);
    drawCutMark(pen, fonts, pageW, pageH);

    return doc.save();
  }

  /** Python版 make_test と同じ。生成したPDF（と、必要なら解答PDF）のバイト列を返す。 */
  async function makeTest({ items, rangeText, direction, twoSets, makeAnswers, seed, sourceName }) {
    const [start, end] = parseRange(rangeText);
    const pool = filterRange(items, start, end);
    const [left, right] = chooseTests(pool, twoSets, makeRng(seed));

    let labels;
    let leftAnswers = false;
    let rightAnswers = false;
    if (twoSets === "qa") {
      labels = ["", ""];
      rightAnswers = true;
    } else {
      labels = twoSets === "same" ? ["A", "A"] : ["A", "B"];
    }

    const common = { left, right, start, end, direction, labels, sourceName };
    const test = await generatePdf({ ...common, leftAnswers, rightAnswers });
    const answers = makeAnswers ? await generatePdf({ ...common, leftAnswers: true, rightAnswers: true }) : null;
    return { test, answers, start, end };
  }

  // ---------------------------------------------------------------- UI

  const $ = (id) => document.getElementById(id);

  const CUSTOM = "custom";
  const state = {
    customItems: null, // 自前CSVを読み込んだ結果 { items, name }
    cache: new Map(), // 同梱の単語帳のキャッシュ
    urls: [], // 作った Blob URL（作り直すときに解放）
  };

  function showError(message) {
    const el = $("error");
    el.textContent = message;
    el.hidden = !message;
  }

  function currentDataset() {
    return DATASETS.find((d) => d.key === $("dataset").value) ?? null;
  }

  function applyRanges(total) {
    const select = $("range-select");
    const values = rangePresets(total);
    const previous = select.value;
    select.replaceChildren(
      ...values.map((v) => new Option(v, v)),
      new Option("自分で入力…", "__free__")
    );
    // いまの範囲がこの単語帳に収まるなら維持、そうでなければ先頭に戻す
    let keep = null;
    try {
      const text = previous === "__free__" ? $("range-free").value : previous;
      if (parseRange(text)[1] <= total) keep = text;
    } catch { /* 先頭に戻す */ }
    if (keep && values.includes(keep)) {
      select.value = keep;
    } else if (keep && previous === "__free__") {
      select.value = "__free__";
    } else {
      select.value = values[0];
    }
    syncRangeInput();
  }

  function syncRangeInput() {
    const free = $("range-select").value === "__free__";
    $("range-free").hidden = !free;
    if (free) $("range-free").focus({ preventScroll: true });
  }

  function currentRangeText() {
    const v = $("range-select").value;
    return v === "__free__" ? $("range-free").value : v;
  }

  function onDatasetChanged() {
    const ds = currentDataset();
    const custom = !ds;
    $("custom-box").hidden = !custom;
    if (ds) {
      applyRanges(ds.total);
    } else {
      const total = state.customItems ? Math.max(...state.customItems.items.map((x) => x.no)) : 1000;
      applyRanges(total);
    }
  }

  async function loadItems() {
    const ds = currentDataset();
    if (ds) {
      if (!state.cache.has(ds.key)) {
        state.cache.set(ds.key, parseVocab(await fetchCsv(`data/${ds.file}`)));
      }
      return { items: state.cache.get(ds.key), name: ds.label };
    }
    if (!state.customItems) throw new Error("カスタムの単語帳（CSVファイル、またはURL）を指定してください。");
    return state.customItems;
  }

  function stemOf(name) {
    return decodeURIComponent(name.split("/").pop() || "").replace(/\.[^.]*$/, "");
  }

  function setCustom(items, name) {
    state.customItems = { items, name: name || APP_NAME };
    $("custom-status").textContent = `${items.length}語を読み込みました（${state.customItems.name}）`;
    $("custom-status").className = "status ok";
    applyRanges(Math.max(...items.map((x) => x.no)));
  }

  function setCustomError(message) {
    state.customItems = null;
    $("custom-status").textContent = message;
    $("custom-status").className = "status ng";
  }

  async function onCsvFile(file) {
    if (!file) return;
    try {
      setCustom(parseVocab(decodeCsv(await file.arrayBuffer())), stemOf(file.name));
    } catch (e) {
      setCustomError(e.message);
    }
  }

  async function onCsvUrl() {
    const raw = $("csv-url").value.trim();
    if (!/^https?:\/\//i.test(raw)) {
      setCustomError("httpまたはhttpsのURLを入力してください。");
      return;
    }
    const url = normalizeCsvUrl(raw);
    $("csv-url-load").disabled = true;
    $("custom-status").textContent = "読み込み中…";
    $("custom-status").className = "status";
    try {
      const text = await fetchCsv(url);
      setCustom(parseVocab(text), stemOf(new URL(url).pathname));
    } catch (e) {
      setCustomError(e.message);
    } finally {
      $("csv-url-load").disabled = false;
    }
  }

  function syncAnswersAvailability() {
    const qa = document.querySelector('input[name="two-sets"]:checked').value === "qa";
    // 「左に問題・右に解答」は1枚に解答も入るので、別ファイルの解答PDFは使わない
    $("answers").disabled = qa;
    if (qa) $("answers").checked = false;
    else if ($("answers").dataset.wasQa === "1") $("answers").checked = true;
    $("answers").dataset.wasQa = qa ? "1" : "0";
  }

  function stamp() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
  }

  function resetResults() {
    for (const u of state.urls) URL.revokeObjectURL(u);
    state.urls = [];
    $("results").replaceChildren();
    $("results").hidden = true;
  }

  function addResult(title, bytes, filename, withPreview) {
    const url = URL.createObjectURL(new Blob([bytes], { type: "application/pdf" }));
    state.urls.push(url);
    const card = document.createElement("section");
    card.className = "result";
    const h = document.createElement("h3");
    h.textContent = title;
    const actions = document.createElement("div");
    actions.className = "actions";
    const dl = document.createElement("a");
    dl.className = "btn primary";
    dl.href = url;
    dl.download = filename;
    dl.textContent = "PDFをダウンロード";
    const open = document.createElement("a");
    open.className = "btn";
    open.href = url;
    open.target = "_blank";
    open.rel = "noopener";
    open.textContent = "新しいタブで開く / 印刷";
    const size = document.createElement("span");
    size.className = "muted";
    size.textContent = `${filename}（${Math.max(1, Math.round(bytes.length / 1024))} KB）`;
    actions.append(dl, open);
    card.append(h, actions, size);
    if (withPreview) {
      const frame = document.createElement("iframe");
      frame.className = "preview";
      frame.title = `${title}のプレビュー`;
      frame.src = url;
      card.append(frame);
    }
    $("results").append(card);
  }

  async function onGenerate() {
    showError("");
    const button = $("generate");
    button.disabled = true;
    button.textContent = "作成中…";
    try {
      const rangeText = currentRangeText();
      parseRange(rangeText); // 先に形式を確認
      const seedText = $("seed").value.trim();
      if (seedText && !/^\d+$/.test(seedText)) throw new Error("シードは 0 以上の整数で指定してください。");

      const { items, name } = await loadItems();
      const twoSets = document.querySelector('input[name="two-sets"]:checked').value;
      const direction = document.querySelector('input[name="direction"]:checked').value;
      const makeAnswers = $("answers").checked && twoSets !== "qa";

      const result = await makeTest({
        items,
        rangeText,
        direction,
        twoSets,
        makeAnswers,
        seed: seedText ? Number(seedText) : undefined,
        sourceName: name,
      });

      resetResults();
      const base = `vocab_test_${result.start}-${result.end}_${stamp()}`;
      addResult(twoSets === "qa" ? "テスト（左: 問題 / 右: 解答）" : "テスト", result.test, `${base}.pdf`, true);
      if (result.answers) addResult("解答", result.answers, `${base}_answers.pdf`, false);
      $("results").hidden = false;
      $("results").scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (e) {
      showError(e.message || String(e));
    } finally {
      button.disabled = false;
      button.textContent = "PDFを作成";
    }
  }

  function init() {
    $("dataset").replaceChildren(
      ...DATASETS.map((d) => new Option(d.label, d.key)),
      new Option("カスタム（ファイル / URL を指定）", CUSTOM)
    );
    $("dataset").addEventListener("change", onDatasetChanged);
    $("range-select").addEventListener("change", syncRangeInput);
    $("csv-file").addEventListener("change", (e) => onCsvFile(e.target.files[0]));
    $("csv-url-load").addEventListener("click", onCsvUrl);
    $("csv-url").addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); onCsvUrl(); }
    });
    for (const r of document.querySelectorAll('input[name="two-sets"]')) {
      r.addEventListener("change", syncAnswersAvailability);
    }
    $("form").addEventListener("submit", (e) => { e.preventDefault(); onGenerate(); });

    $("answers").dataset.wasQa = "0";
    syncAnswersAvailability();
    onDatasetChanged();
    loadFontBytes().catch(() => { /* 作成時にもう一度試す */ }); // 先読み
  }

  // テストから使えるように最小限だけ公開する
  window.VocabTestMaker = { parseVocab, parseRange, makeTest, splitToFit, rangePresets };
  document.addEventListener("DOMContentLoaded", init);
})();
