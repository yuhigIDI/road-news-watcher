"""見出しの和訳（任意）— 環境変数 ANTHROPIC_API_KEY があるときだけ動く"""
import json
import logging
import os
import re

import requests

log = logging.getLogger(__name__)
API = "https://api.anthropic.com/v1/messages"

PROMPT = """以下は海外の道路・交通関連ニュースの見出しです。国土交通省向けモニター記事の候補として、
各見出しを自然で簡潔な日本語に訳してください。固有名詞（地名・機関名・事業名）は一般的な日本語表記にし、
不明なものはカタカナ＋原綴りを括弧で残してください。通貨・金額は原文の単位のまま訳してください。

出力は JSON 配列のみ（説明文なし）：[{{"i": 番号, "ja": "和訳"}}, ...]

{lines}"""


def _has_japanese(s):
    return re.search(r"[぀-ヿ一-鿿]", s or "") is not None


def translate_titles(items, batch=40):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return 0
    model = os.environ.get("CLAUDE_MODEL", "claude-haiku-5-5")
    targets = [it for it in items if not _has_japanese(it["title"]) and not it.get("title_ja")]
    done = 0
    for s in range(0, len(targets), batch):
        chunk = targets[s:s + batch]
        lines = "\n".join(f"{i}. {it['title']}" for i, it in enumerate(chunk))
        try:
            r = requests.post(API, timeout=120, headers={
                "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json",
            }, json={"model": model, "max_tokens": 4000,
                     "messages": [{"role": "user", "content": PROMPT.format(lines=lines)}]})
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json().get("content", []))
            arr = json.loads(text[text.find("["): text.rfind("]") + 1])
            for row in arr:
                i = int(row["i"])
                if 0 <= i < len(chunk):
                    chunk[i]["title_ja"] = str(row["ja"]).strip()
                    done += 1
        except Exception as ex:
            log.warning("和訳に失敗（%d〜%d件目）: %s", s, s + len(chunk), ex)
    return done
