"""記事の判定・和訳・要約を GitHub Actions の中で作る（Claude API）

なぜここでやるか：
  毎朝のClaude定期タスク（クラウド）からは、記事URLを開く操作が「許可の確認が時間切れ」でほぼ全件失敗し、
  本文を確認できないまま要約することになっていた。GitHub Actions はネットの制限がないので、
  ここで元記事を開いて本文を読み、その場で Claude API に要約させる。

守ること：
  - 記事本文はメモリ上で要約に使うだけで、ファイルにもリポジトリにも保存しない（公開リポジトリのため）。
  - 保存するのは要約結果だけ（data/summaries/YYYY-MM-DD.json）。形式は data/enrich と同じ項目＋basis。
    data/enrich/*.json には書かない（そこに書くとメールが飛ぶため。メールは共有画面の更新後に送る）。

手順：
  1) 対象（関連度 ARTIFACT_MIN_SCORE 以上）の見出し・概要で、和訳見出し・関係あり/なし・転載判定をまとめて作る
  2) 関係ありの記事だけ元記事を開いて本文を取り出し、要約の決まりに沿って要約する
     本文が取れなければ概要の範囲で要約し、その旨を注記する

単体で動かす（今日収集した記事をやり直す等）：
  python -m watcher.summarize [--day YYYY-MM-DD] [--force]
"""
import argparse
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests

log = logging.getLogger(__name__)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://api.anthropic.com/v1/messages"
JST = timezone(timedelta(hours=9))
MIN_SCORE = int(os.environ.get("ARTIFACT_MIN_SCORE", 6))
BODY_CHARS = 6000          # 要約に渡す本文の上限（文字）
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
REPOST = ("yahoo", "msn", "biggo", "newswav", "infoseek", "magzter", "facebook", "inshorts",
          "head topics", "livedoor", "ライブドア", "x.com")

NOTE_REL = "（原文本文を確認できないため、提供された概要等に基づく）"
NOTE_NONE = "本文・概要を確認できないため、要約不可。"

JUDGE_PROMPT = """あなたは国土交通省向け「道路・交通モニター記事」の候補を仕分ける担当です。
次の各記事（番号・国・媒体・見出し・概要）について、JSON配列だけを出力してください（説明文なし）。
[{{"i": 番号, "j": "見出しの日本語訳", "rel": 1または0, "why": "rel=0の理由（短く）", "grp": 同じ話題の代表記事の番号または-1}}]

■ j：原文の意味に忠実な日本語訳。日本語の見出しはそのまま。固有名詞は一般的な日本語表記。
  "Highway" は文脈上明らかに高速道路を指す場合を除き「高速道路」と訳さず「幹線道路」「道路」等とする。金額は原文の通貨のまま。
■ rel=0（関係なし）：道路が主題でない記事（鉄道・地下鉄・空港・港湾・倉庫・物流施設・発電・通信など）／
  企業の決算・株価・資金調達・新車発表・自動車販売が主題／政策や制度に関わらない事件・個別の事故速報（理由は「個別の事故速報」）／
  対象国と無関係な記事（国の判定違い）／比喩的な「bridge」「road」
■ rel=1（関係あり）：道路が主題（計画・整備・維持管理・料金・PPP・行政改革・災害対策など）／道路を使う交通（バス・BRT、EV充電、
  自転車、新モビリティ、交通安全の政策や統計など）／道路上の物流（トラック輸送、国境道路など）／道路と一体の案件で道路部分が中心のもの
■ grp：同じ出来事を扱う記事が複数あるとき、2件目以降に代表記事（最初の1件）の番号を入れる。それ以外は -1。

{lines}"""

SUM_PROMPT = """次の各記事について、記事本文に基づく日本語の要約を作ってください。全文翻訳ではなく要約です。
JSON配列だけを出力してください（説明文なし）：[{{"i": 番号, "pts": "要約"}}]

【要約の内容】
・本文は原則として3〜5文。重要な情報が多い記事は必要に応じて長くしてよい。
・対象国・地域、関係機関、事業の内容、規模、金額、日程、進捗、今後の予定など、記事の主要な事実を優先する。
・契約・調達方式、資金調達、料金制度、制度変更、技術的特徴などが記載されている場合は、その要点を残す。
・計画、検討、承認、契約締結、着工、完成、供用開始を明確に区別する。
・原文にない評価、背景説明、業務上の示唆は加えない。長い引用はしない。確認できない内容を推測で補わない。
【訳語・表記】
・"Highway" は文脈上明らかに高速道路を指す場合を除き「高速道路」と訳さず「幹線道路」「道路」等とする。
・金額は原文の通貨単位のまま（日本円などに換算しない）。数値・単位・年月日は正確に。
・固有名詞（地名・機関名・企業名・事業名）は一般的な日本語表記とし、各記事の初出に原綴りを括弧書きする。
・掲載日が分かる場合は「昨日」「来月」などを具体的な年月日に置き換える。特定できなければ推測しない。
【材料の種類】各記事の「材料」欄が
・「本文」：本文に基づいて要約する（注記は不要。出典は別途付けるので書かない）。
・「概要」：本文が取れなかった記事。見出しと概要の範囲だけで要約する（注記は別途付けるので書かない）。
  内容が書けないほど情報がなければ pts を空文字にする。

{blocks}"""


def _today():
    return datetime.now(JST).date().isoformat()


def _call(key, model, prompt, max_tokens):
    for attempt in range(3):
        try:
            r = requests.post(API, timeout=180, headers={
                "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json",
            }, json={"model": model, "max_tokens": max_tokens,
                     "messages": [{"role": "user", "content": prompt}]})
            if r.status_code in (429, 500, 502, 503, 529):
                time.sleep(10 * (attempt + 1))
                continue
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", []))
            return json.loads(text[text.find("["): text.rfind("]") + 1])
        except Exception as ex:
            log.warning("Claude API の呼び出しに失敗（%d回目）: %s", attempt + 1, ex)
            time.sleep(5)
    return []


def fetch_body(url):
    """元記事を開いて本文を取り出す。取れなければ空文字。結果は保存しない"""
    if not url.startswith(("http://", "https://")) or "news.google.com" in url:
        return ""
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": UA, "Accept-Language": "en,ja;q=0.8"})
        if r.status_code != 200 or "html" not in r.headers.get("content-type", "html"):
            return ""
        import trafilatura
        text = trafilatura.extract(r.text, include_comments=False, include_tables=False, favor_precision=True) or ""
    except Exception as ex:
        log.info("本文を取得できず: %s (%s)", url[:80], type(ex).__name__)
        return ""
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:BODY_CHARS] if len(text) >= 400 else ""


def _source_name(it):
    s = (it.get("source") or "").strip()
    s = re.sub(r"\s*(RSS|rss)$", "", s)
    return s or re.sub(r"^www\.", "", (re.findall(r"https?://([^/]+)", it.get("url", "")) or [""])[0])


def _is_repost(it):
    s = f'{it.get("source", "")} {it.get("url", "")}'.lower()
    return 1 if any(k in s for k in REPOST) else 0


def summarize(items, key=None, model=None):
    """items（記事 dict の一覧）を判定・要約し、{id: {j, pts, rel, why, repost, orig, basis}} を返す。
    basis：body=本文確認／summary=概要のみ／none=要約不可／same=同じ話題／""=関係なし"""
    key = key or os.environ.get("ANTHROPIC_API_KEY")
    if not key or not items:
        if not key:
            log.warning("ANTHROPIC_API_KEY が無いため、要約を作りません")
        return {}
    model = model or os.environ.get("SUMMARY_MODEL") or os.environ.get("CLAUDE_MODEL", "claude-haiku-5-5")
    out = {}

    # 1) 見出し・概要で判定（40件ずつ）
    for s in range(0, len(items), 40):
        chunk = items[s:s + 40]
        lines = "\n".join(
            f'{i}. [{"・".join(it.get("country_names") or it.get("countries") or [])}] [{_source_name(it)}] '
            f'{it["title"]}' + (f'\n   概要：{(it.get("summary") or "")[:300]}' if it.get("summary") else "")
            for i, it in enumerate(chunk))
        rows = _call(key, model, JUDGE_PROMPT.format(lines=lines), 8000)
        for row in rows:
            try:
                i = int(row["i"])
            except Exception:
                continue
            if not 0 <= i < len(chunk):
                continue
            it = chunk[i]
            rel = 1 if int(row.get("rel", 0)) == 1 else 0
            grp = int(row.get("grp", -1) if str(row.get("grp", -1)).lstrip("-").isdigit() else -1)
            out[it["id"]] = {"j": str(row.get("j", "")).strip(), "pts": "", "rel": rel,
                             "why": "" if rel else str(row.get("why", "")).strip(),
                             "repost": _is_repost(it), "orig": "", "basis": "",
                             "_grp": chunk[grp]["id"] if 0 <= grp < len(chunk) and grp != i else ""}
    log.info("判定 %d / %d 件（関係あり %d 件）", len(out), len(items),
             sum(1 for v in out.values() if v["rel"] == 1))

    # 同じ話題の2件目以降は要約しない
    rel_items = []
    for it in items:
        e = out.get(it["id"])
        if not e or e["rel"] != 1:
            continue
        g = e.pop("_grp", "")
        if g and out.get(g, {}).get("rel") == 1:
            e["pts"], e["basis"] = f"上の記事と同じ話題（{_source_name(it)}）", "same"
        else:
            rel_items.append(it)
    for e in out.values():
        e.pop("_grp", None)

    # 2) 関係ありの記事は元記事を開いて本文を読む（RSSで本文が来ていればそれを使う）
    def _body(it):
        return (it.get("body") or "")[:BODY_CHARS] or fetch_body(it.get("url", ""))
    with ThreadPoolExecutor(max_workers=8) as ex:
        bodies = list(ex.map(_body, rel_items))
    log.info("本文を取得 %d / %d 件", sum(1 for b in bodies if b), len(rel_items))

    # 3) 要約（本文ありは4件ずつ、概要のみは15件ずつ）
    pairs = list(zip(rel_items, bodies))
    with_body = [p for p in pairs if p[1]]
    without = [p for p in pairs if not p[1]]
    for group, size in ((with_body, 4), (without, 15)):
        for s in range(0, len(group), size):
            chunk = group[s:s + size]
            blocks = "\n\n".join(
                f'--- {i}. 国：{"・".join(it.get("country_names") or [])}／媒体：{_source_name(it)}／'
                f'掲載日：{(it.get("published") or "")[:10] or "不明"}／材料：{"本文" if body else "概要"}\n'
                f'見出し：{it["title"]}\n'
                + (f"本文：\n{body}" if body else f'概要：{(it.get("summary") or "（なし）")[:400]}')
                for i, (it, body) in enumerate(chunk))
            rows = _call(key, model, SUM_PROMPT.format(blocks=blocks), 6000)
            got = {}
            for row in rows:
                try:
                    got[int(row["i"])] = str(row.get("pts", "")).strip()
                except Exception:
                    pass
            for i, (it, body) in enumerate(chunk):
                e = out[it["id"]]
                pts = got.get(i, "")
                if body and pts:
                    e["pts"], e["basis"] = f"{pts}　出典：{_source_name(it)}", "body"
                elif pts:
                    e["pts"], e["basis"] = pts + NOTE_REL, "summary"
                else:
                    e["pts"], e["basis"] = NOTE_NONE, "none"
    cnt = {}
    for e in out.values():
        cnt[e["basis"] or "rel0"] = cnt.get(e["basis"] or "rel0", 0) + 1
    log.info("要約の内訳 %s", cnt)
    return out


def save(result, day):
    """data/summaries/YYYY-MM-DD.json に追記（同じ id は上書き）"""
    path = os.path.join(ROOT, "data", "summaries", f"{day}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cur = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cur = json.load(f)
    cur.update(result)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=0)
    return path


def run(items, day=None):
    targets = [it for it in items if int(it.get("score", 0)) >= MIN_SCORE]
    if os.environ.get("SUMMARIZE", "on").lower() == "off" or not targets:
        return {}
    res = summarize(targets)
    if res:
        log.info("要約を保存 → %s", save(res, day or _today()))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=_today(), help="この収集日の記事をやり直す")
    ap.add_argument("--force", action="store_true", help="要約済みの記事もやり直す")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY が設定されていません（Settings → Secrets and variables → Actions）")
    with open(os.path.join(ROOT, "data", "articles.json"), encoding="utf-8") as f:
        items = [x for x in json.load(f) if x.get("collected") == args.day]
    done = set()
    if not args.force:
        import glob
        for p in glob.glob(os.path.join(ROOT, "data", "summaries", "*.json")):
            with open(p, encoding="utf-8") as f:
                done |= set(json.load(f))
    run([x for x in items if x["id"] not in done], args.day)


if __name__ == "__main__":
    main()
