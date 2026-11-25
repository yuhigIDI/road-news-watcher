"""実行：python -m watcher [--countries VN,IN] [--min-score 4] [--no-mail]"""
import argparse
import logging
import os
from datetime import datetime, timedelta, timezone

import yaml

from . import collect
from .classify import Classifier
from .notify import send_mail
from .report import email_html, write_excel, write_viewer_data
from .store import Store, append_archive, dedupe
from .translate import translate_items
from .resolve import resolve_google_urls

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JST = timezone(timedelta(hours=9))


def load(name):
    with open(os.path.join(ROOT, "config", name), encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--countries", help="国コードを絞って試す（例 VN,IN）")
    ap.add_argument("--min-score", type=int, default=int(os.environ.get("MIN_SCORE", 4)))
    ap.add_argument("--no-mail", action="store_true")
    ap.add_argument("--fixture", help="テスト用：ローカルのRSSファイルを読む")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    countries, keywords, sources = load("countries.yaml"), load("keywords.yaml"), load("sources.yaml")
    clf = Classifier(countries, keywords, sources)
    now = datetime.now(JST)
    today = now.date().isoformat()

    if args.fixture:
        import feedparser
        raw = []
        for fx in args.fixture.split(","):
            feed = feedparser.parse(fx)
            is_g = "news.google.com" in open(fx, encoding="utf-8").read()
            raw += collect.parse_feed(feed, via="fixture", is_google=is_g, hint_country=None if is_g else "VN")
        errors = []
    else:
        limit = set(args.countries.split(",")) if args.countries else None
        raw, errors = collect.collect_all(countries, sources, limit_countries=limit)

    merged = dedupe(raw)
    kept = [it for it in (clf.classify(x) for x in merged) if it and it["score"] >= args.min_score]
    logging.info("取得 %d 件 → 重複除去 %d 件 → 関連記事 %d 件", len(raw), len(merged), len(kept))

    store = Store(os.path.join(ROOT, "data", "articles.json"))
    new = store.add_new(kept, today)
    resolve_google_urls(new)
    n_ja = translate_items(new)
    if n_ja:
        logging.info("見出し和訳 %d 件", n_ja)
    store.prune(today)
    for it in new:  # 本文全文は保存しない（公開リポジトリに記事本文を載せないため）
        it.pop("body", None)
    store.save()
    append_archive(os.path.join(ROOT, "data", "archive"), new, today)
    write_viewer_data(os.path.join(ROOT, "docs", "data", "articles.js"), store.recent(60, today), clf,
                      now.strftime("%Y-%m-%d %H:%M"), sources)
    logging.info("新着 %d 件", len(new))

    xlsx = os.path.join(ROOT, "output", f"{today.replace('-', '')}_モニター記事候補.xlsx")
    write_excel(xlsx, new, clf)
    if new and not args.no_mail:
        body = email_html(new, clf, today, os.environ.get("VIEWER_URL"), errors=errors)
        send_mail(f"【モニター記事候補】{today} 新着{len(new)}件", body, xlsx)


if __name__ == "__main__":
    main()
