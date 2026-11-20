"""出力：閲覧画面用データ、Excel、メール本文"""
import html
import json
import os
from collections import defaultdict

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from .store import flat_row


def _gnews_link(c):
    import urllib.parse
    hl, gl = (c.get("gn") or "en:US").split(":")
    q = f'"{c["name_en"]}" (road OR highway OR expressway OR bridge OR toll OR transport)'
    return ("https://news.google.com/search?q=" + urllib.parse.quote(q) +
            f"&hl={hl}&gl={gl}&ceid={gl}:{hl}")


def write_viewer_data(path, items, classifier, generated, sources_cfg=None):
    meta = {
        "generated": generated,
        "regions": {},
        "categories": [name for name, _ in classifier.categories],
    }
    for c in classifier.countries:
        meta["regions"].setdefault(c["region"], []).append(
            {"code": c["code"], "name": c["name_ja"], "gn": _gnews_link(c)})
    slim = [{
        "id": it["id"], "c": it["collected"], "p": (it.get("published") or "")[:10],
        "t": it["title"], "j": it.get("title_ja", ""), "s": it.get("source", ""), "u": it["url"],
        "co": it.get("countries", []), "ca": it.get("categories", []), "sc": it.get("score", 0),
        "su": (it.get("summary") or "")[:300], "sj": it.get("summary_ja", ""),
        "b": 1 if it.get("has_body") else 0,
    } for it in sorted(items, key=lambda x: (x["collected"], x.get("score", 0)), reverse=True)]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("window.META=" + json.dumps(meta, ensure_ascii=False) + ";\n")
        f.write("window.ARTICLES=" + json.dumps(slim, ensure_ascii=False, separators=(",", ":")) + ";\n")


def _sorted_for_report(items, classifier):
    order = {c["code"]: i for i, c in enumerate(classifier.countries)}
    return sorted(items, key=lambda x: (order.get((x.get("countries") or ["ZZ"])[0], 999), -x.get("score", 0)))


def write_excel(path, items, classifier):
    wb = Workbook()
    ws = wb.active
    ws.title = "候補記事"
    head = ["選定", "地域", "国", "分類", "関連度", "公開日", "見出し（原文）", "見出し（和訳）", "媒体", "URL", "概要", "概要（和訳）"]
    ws.append(head)
    for it in _sorted_for_report(items, classifier):
        r = flat_row(it)
        ws.append(["", r["region"], r["country"], r["categories"], r["score"], r["published"] or r["collected"],
                   r["title"], r["title_ja"], r["source"], r["url"], r["summary"], it.get("summary_ja", "")])
        cell = ws.cell(row=ws.max_row, column=10)
        cell.hyperlink = r["url"]
        cell.font = Font(color="1F4E79", underline="single")
    widths = [6, 14, 12, 28, 7, 11, 60, 50, 22, 40, 60, 60]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F4E79")
        c.alignment = Alignment(vertical="center")
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(vertical="top", wrap_text=c.column in (4, 7, 8, 11, 12))
    dv = DataValidation(type="list", formula1='"○,×,保留"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"A2:A{max(ws.max_row, 2)}")
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions
    os.makedirs(os.path.dirname(path), exist_ok=True)
    wb.save(path)
    return path


def email_html(items, classifier, today, viewer_url=None, per_country=8, errors=None):
    by = defaultdict(list)
    for it in _sorted_for_report(items, classifier):
        by[(it.get("countries") or ["ZZ"])[0]].append(it)
    e = html.escape
    parts = [f"<h2 style='font-family:sans-serif'>道路・交通モニター記事 候補（{today}）新着 {len(items)} 件</h2>"]
    if viewer_url:
        parts.append(f"<p><a href='{e(viewer_url)}'>閲覧画面で絞り込み・選定する</a>　／　全件はExcel添付</p>")
    for region in dict.fromkeys(c["region"] for c in classifier.countries):
        codes = [c["code"] for c in classifier.countries if c["region"] == region and by.get(c["code"])]
        if not codes:
            continue
        parts.append(f"<h3 style='font-family:sans-serif;border-bottom:2px solid #1F4E79'>{e(region)}</h3>")
        for code in codes:
            lst = by[code]
            name = classifier.by_code[code]["name_ja"]
            parts.append(f"<p style='font-family:sans-serif;margin:10px 0 4px'><b>{e(name)}</b>（{len(lst)}件）</p><ul>")
            for it in lst[:per_country]:
                ja = f"<br><span style='color:#444'>{e(it['title_ja'])}</span>" if it.get("title_ja") else ""
                cats = " / ".join(it.get("categories", [])[:4])
                parts.append(
                    f"<li style='font-family:sans-serif;margin-bottom:6px'><a href='{e(it['url'])}'>{e(it['title'])}</a>{ja}"
                    f"<br><small style='color:#777'>{e(it.get('source',''))}｜{e((it.get('published') or '')[:10])}｜{e(cats)}</small></li>")
            if len(lst) > per_country:
                parts.append(f"<li style='color:#777'>ほか {len(lst) - per_country} 件（Excel参照）</li>")
            parts.append("</ul>")
    if errors:
        parts.append(f"<p style='color:#999;font-size:11px'>取得できなかった収集元：{len(errors)} 件（GitHub Actions のログ参照）</p>")
    return "\n".join(parts)
