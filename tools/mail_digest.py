"""Claudeの処理結果（data/enrich/YYYY-MM-DD.json）から、要約付きのメールとExcelを作って送る
（メール本文はその日の分、Excelはその月の1日からその日までの分）

  python tools/mail_digest.py data/enrich/2026-11-21.json [--dry-run 出力フォルダ]

ファイルの形式：artifactの articles.json と同じ1件ずつのレコードのリスト
  id, c(収集日), p(公開日), t(原文見出し), j(和訳見出し), pts(要点), rel(1=関係あり/0=関係なし),
  why(関係なしの理由), repost, orig, s(媒体), u(URL), gu(元URL未取得), co(国コード), ca(分類), sc
"""
import html
import json
import os
import sys
from collections import defaultdict

import yaml
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from watcher.notify import send_mail  # noqa: E402

FONT = "font-family:'Yu Gothic','Meiryo',sans-serif"


def _countries():
    with open(os.path.join(ROOT, "config", "countries.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    out = []
    for region, lst in cfg["regions"].items() if isinstance(cfg.get("regions"), dict) else []:
        for c in lst:
            out.append({"code": c["code"], "name": c["name_ja"], "region": region})
    return out


def _sort(items, countries):
    order = {c["code"]: i for i, c in enumerate(countries)}
    return sorted(items, key=lambda x: (order.get((x.get("co") or ["ZZ"])[0], 999), -x.get("sc", 0)))


def email_html(items, countries, day, viewer_url=""):
    e = html.escape
    names = {c["code"]: c["name"] for c in countries}
    # 前日までの記事で、Perplexityの要約を取り込んだもの（別枠で載せる）
    upd = [x for x in items if x.get("px") and x.get("c") != day and x.get("rel") == 1]
    items = [x for x in items if x not in upd]
    rel_all = [x for x in items if x.get("rel") == 1]
    rel = [x for x in rel_all if not (x.get("pts") or "").startswith("上の記事と同じ話題")]
    dup = len(rel_all) - len(rel)
    other = len(items) - len(rel_all)
    by = defaultdict(list)
    for x in _sort(rel, countries):
        by[(x.get("co") or ["ZZ"])[0]].append(x)
    p = [f"<div style=\"{FONT};font-size:14px;color:#222;max-width:760px\">",
         f"<h2 style='font-size:18px;margin:0 0 6px'>道路・交通モニター記事 候補（{e(day)}）</h2>",
         f"<p style='margin:0 0 14px;color:#555'>関係ありと判定 <b>{len(rel)}件</b>"
         + (f"（ほかに同じ話題の記事 {dup}件）" if dup else "")
         + f"　／　関係なし {other}件（理由はExcel添付を参照）</p>"]
    if viewer_url:
        p.append(f"<p style='margin:0 0 14px'><a href='{e(viewer_url)}'>共有画面を開く（選定・メモ・翻訳の貼り付け）</a></p>")
    if not rel:
        p.append("<p>本日は関係ありの記事はありませんでした。</p>")
    for region in dict.fromkeys(c["region"] for c in countries):
        codes = [c["code"] for c in countries if c["region"] == region and by.get(c["code"])]
        if not codes:
            continue
        p.append(f"<h3 style='font-size:15px;border-bottom:2px solid #1F4E79;padding-bottom:2px;margin:18px 0 6px'>{e(region)}</h3>")
        for code in codes:
            p.append(f"<p style='margin:10px 0 4px;font-weight:bold'>■ {e(names.get(code, code))}（{len(by[code])}件）</p>")
            for x in by[code]:
                url = x.get("orig") or x.get("u")
                title = x.get("j") or x.get("t")
                flags = []
                if x.get("repost"):
                    flags.append("転載サイト")
                if x.get("gu") and not x.get("orig"):
                    flags.append("元URL未取得")
                flag = f"　<span style='color:#B45309'>［{'・'.join(flags)}］</span>" if flags else ""
                p.append("<div style='margin:0 0 12px 12px'>"
                         f"<a href='{e(url)}' style='font-weight:bold;color:#1F4E79'>{e(title)}</a>{flag}<br>"
                         f"<span style='color:#888;font-size:12px'>{e(x.get('t',''))}</span><br>"
                         f"<span style='line-height:1.6'>{e(x.get('pts') or '（要点なし）')}</span><br>"
                         f"<span style='color:#777;font-size:12px'>{e(x.get('s',''))}｜{e(x.get('p') or x.get('c',''))}"
                         f"｜{e(' / '.join((x.get('ca') or [])[:4]))}</span></div>")
    if upd:
        p.append("<h3 style='font-size:15px;border-bottom:2px solid #B45309;padding-bottom:2px;margin:22px 0 6px'>"
                 f"前日までの記事の要約更新（Perplexityで確認）{len(upd)}件</h3>")
        for x in _sort(upd, countries):
            url = x.get("orig") or x.get("u")
            p.append("<div style='margin:0 0 12px 12px'>"
                     f"<a href='{e(url)}' style='font-weight:bold;color:#1F4E79'>{e(x.get('j') or x.get('t'))}</a>"
                     f"　<span style='color:#777;font-size:12px'>［{e('・'.join(names.get(c, c) for c in (x.get('co') or [])))}｜収集 {e(x.get('c',''))}］</span><br>"
                     f"<span style='line-height:1.6'>{e(x.get('pts') or '')}</span><br>"
                     f"<span style='color:#777;font-size:12px'>{e(x.get('s',''))}｜{e(x.get('p') or x.get('c',''))}</span></div>")
    p.append("<p style='color:#999;font-size:11px;margin-top:20px'>要点はClaudeが見出し・概要・本文から作成したものです"
             "（末尾に「Perplexityの要約」とあるものは、共有画面でPerplexityの答えを取り込んだもの）。"
             "数値・固有名詞は元記事で確認してください。</p></div>")
    return "\n".join(p)


def write_excel(path, items, countries):
    names = {c["code"]: c["name"] for c in countries}
    region = {c["code"]: c["region"] for c in countries}
    wb = Workbook()
    ws = wb.active
    ws.title = "候補記事"
    head = ["収集日", "判定", "地域", "国", "見出し（和訳）", "要点", "関係なしの理由", "見出し（原文）", "媒体", "公開日", "分類", "URL", "注意"]
    ws.append(head)
    # 新しい収集日が上、同じ日の中は関係あり→関係なし
    rows = sorted(_sort(items, countries), key=lambda x: -(x.get("rel") or 0))
    rows = sorted(rows, key=lambda x: x.get("c", ""), reverse=True)
    for x in rows:
        co = x.get("co") or []
        url = x.get("orig") or x.get("u")
        note = "・".join(f for f, on in (("転載サイト", x.get("repost")), ("元URL未取得", x.get("gu") and not x.get("orig")),
                                          ("Perplexityの要約", x.get("px"))) if on)
        ws.append([x.get("c", ""), "関係あり" if x.get("rel") == 1 else "関係なし", region.get(co[0], "") if co else "",
                   "・".join(names.get(c, c) for c in co), x.get("j", ""), x.get("pts", ""), x.get("why", ""),
                   x.get("t", ""), x.get("s", ""), x.get("p") or x.get("c", ""), " / ".join(x.get("ca") or []), url, note])
        cell = ws.cell(row=ws.max_row, column=12)
        cell.hyperlink = url
        cell.font = Font(color="1F4E79", underline="single")
    for i, w in enumerate([11, 9, 12, 12, 45, 70, 30, 45, 18, 11, 24, 30, 12], 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F4E79")
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(vertical="top", wrap_text=c.column in (5, 6, 7, 8, 11))
    ws.freeze_panes = "E2"
    ws.auto_filter.ref = ws.dimensions
    wb.save(path)
    return path


def month_items(path):
    """その月（1日〜この日）の data/enrich/YYYY-MM-DD.json をまとめる。月が替わると新しいExcelになる"""
    import glob
    day = os.path.splitext(os.path.basename(path))[0]
    files = [f for f in sorted(glob.glob(os.path.join(os.path.dirname(path), f"{day[:7]}-*.json")))
             if os.path.basename(f)[:10] <= day]
    by = {}
    for f in files:
        with open(f, encoding="utf-8") as fh:
            for x in json.load(fh):
                by[x["id"]] = x
    return list(by.values())


def main(argv):
    path = argv[1]
    dry = argv[3] if len(argv) > 3 and argv[2] == "--dry-run" else None
    with open(path, encoding="utf-8") as f:
        items = json.load(f)
    day = os.path.splitext(os.path.basename(path))[0]
    countries = _countries()
    body = email_html(items, countries, day, os.environ.get("ARTIFACT_URL", ""))
    out = dry or os.path.join(ROOT, "output")
    os.makedirs(out, exist_ok=True)
    # Excelはその月の分をまとめたもの（例 monitor_2026-11.xlsx）。メール本文はその日の分
    xlsx = write_excel(os.path.join(out, f"monitor_{day[:7]}.xlsx"), month_items(path), countries)
    n = sum(1 for x in items if x.get("rel") == 1 and not (x.get("px") and x.get("c") != day))
    subject = f"【モニター記事】{day} 関係あり{n}件（要約付き）"
    if dry:
        with open(os.path.join(out, f"mail_{day}.html"), "w", encoding="utf-8") as f:
            f.write(f"<html><head><meta charset='utf-8'><title>{html.escape(subject)}</title></head><body>{body}</body></html>")
        print(subject, "→", out)
        return
    send_mail(subject, body, xlsx)


if __name__ == "__main__":
    main(sys.argv)
