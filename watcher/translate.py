"""見出し・概要の和訳
  - 既定：Google翻訳の無料エンドポイント（APIキー不要。旧ASEANモニターと同じ方式）
  - ANTHROPIC_API_KEY があれば見出しは Claude で訳す（固有名詞・行政用語の訳が安定）
"""
import json
import logging
import os
import re
import time

import requests

log = logging.getLogger(__name__)
GTX = "https://translate.googleapis.com/translate_a/single"
CLAUDE_API = "https://api.anthropic.com/v1/messages"

PROMPT = """以下は海外の道路・交通関連ニュースの見出しです。国土交通省向けモニター記事の候補として、
各見出しを自然で簡潔な日本語に訳してください。固有名詞（地名・機関名・事業名）は一般的な日本語表記にし、
不明なものはカタカナ＋原綴りを括弧で残してください。"Highway" は文脈上明らかな場合を除き「高速道路」と訳さず
「幹線道路」「ハイウェイ」等とし、通貨・金額は原文の単位のまま訳してください。

出力は JSON 配列のみ（説明文なし）：[{{"i": 番号, "ja": "和訳"}}, ...]

{lines}"""


def _has_japanese(s):
    return re.search(r"[぀-ヿ一-鿿]", s or "") is not None


def _gtx(text):
    r = requests.get(GTX, timeout=20, params={"client": "gtx", "sl": "auto", "tl": "ja", "dt": "t", "q": text},
                     headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return "".join(seg[0] for seg in (r.json()[0] or []) if seg and seg[0])


def gtx_many(texts, max_chars=1500):
    """複数の短文を改行でつないでまとめて訳す（件数が合わなければ1件ずつ訳し直す）"""
    out = [""] * len(texts)
    i = 0
    while i < len(texts):
        j, size = i, 0
        while j < len(texts) and (j == i or size + len(texts[j]) < max_chars):
            size += len(texts[j]) + 1
            j += 1
        chunk = [t.replace("\n", " ") for t in texts[i:j]]
        try:
            parts = _gtx("\n".join(chunk)).split("\n")
            if len(parts) == len(chunk):
                out[i:j] = [p.strip() for p in parts]
            else:
                for k, t in enumerate(chunk):
                    out[i + k] = _gtx(t).strip()
                    time.sleep(0.3)
        except Exception as ex:
            log.warning("無料翻訳に失敗（%d〜%d件目）: %s", i, j, ex)
        i = j
        time.sleep(0.5)
    return out


def _claude_titles(items, key, batch=40):
    model = os.environ.get("CLAUDE_MODEL", "claude-haiku-5-5")
    done = 0
    for s in range(0, len(items), batch):
        chunk = items[s:s + batch]
        lines = "\n".join(f"{i}. {it['title']}" for i, it in enumerate(chunk))
        try:
            r = requests.post(CLAUDE_API, timeout=120, headers={
                "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json",
            }, json={"model": model, "max_tokens": 4000,
                     "messages": [{"role": "user", "content": PROMPT.format(lines=lines)}]})
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", []))
            for row in json.loads(text[text.find("["): text.rfind("]") + 1]):
                i = int(row["i"])
                if 0 <= i < len(chunk):
                    chunk[i]["title_ja"] = str(row["ja"]).strip()
                    done += 1
        except Exception as ex:
            log.warning("Claudeでの和訳に失敗（%d〜%d件目）: %s", s, s + len(chunk), ex)
    return done


def translate_items(items):
    """新着記事の見出し（title_ja）と概要（summary_ja）を和訳。戻り値は和訳した見出し数"""
    if os.environ.get("TRANSLATE", "off").lower() == "off":
        return 0
    targets = [it for it in items if not _has_japanese(it["title"])]
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key and targets:
        _claude_titles(targets, key)
    rest = [it for it in targets if not it.get("title_ja")]
    if rest:
        for it, ja in zip(rest, gtx_many([it["title"] for it in rest])):
            if ja:
                it["title_ja"] = ja
    sums = [it for it in items if it.get("summary") and not _has_japanese(it["summary"])]
    if sums:
        for it, ja in zip(sums, gtx_many([it["summary"][:300] for it in sums])):
            if ja:
                it["summary_ja"] = ja
    return sum(1 for it in targets if it.get("title_ja"))
