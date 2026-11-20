"""記事の保存・重複排除"""
import csv
import hashlib
import json
import os
import re
from datetime import datetime, timedelta

FIELDS = ["id", "collected", "published", "region", "country", "categories", "score",
          "title", "title_ja", "source", "url", "summary"]


def norm_key(title):
    t = re.sub(r"[\W_]+", "", (title or "").lower())
    return t[:90]


def item_id(title):
    return hashlib.sha1(norm_key(title).encode("utf-8")).hexdigest()[:12]


def dedupe(items):
    """同じ見出しの記事をまとめる（Googleニュース経由より媒体RSSの元URLを優先）"""
    merged = {}
    for it in items:
        k = norm_key(it["title"])
        if len(k) < 8:
            continue
        cur = merged.get(k)
        if cur is None:
            merged[k] = it
            continue
        cur["via"] = sorted(set(cur["via"]) | set(it["via"]))
        if "news.google.com" in cur["url"] and "news.google.com" not in it["url"]:
            cur["url"] = it["url"]
        if not cur.get("body") and it.get("body"):
            cur["body"] = it["body"]
        if not cur.get("summary") and it.get("summary"):
            cur["summary"] = it["summary"]
        if not cur.get("hint_country") and it.get("hint_country"):
            cur["hint_country"] = it["hint_country"]
    return list(merged.values())


class Store:
    def __init__(self, path, keep_days=120):
        self.path = path
        self.keep_days = keep_days
        self.items = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for it in json.load(f):
                    self.items[it["id"]] = it

    def add_new(self, items, today):
        new = []
        for it in items:
            it["id"] = item_id(it["title"])
            if it["id"] in self.items:
                continue
            it["collected"] = today
            it.pop("hint_country", None)
            self.items[it["id"]] = it
            new.append(it)
        return new

    def prune(self, today):
        cutoff = (datetime.fromisoformat(today) - timedelta(days=self.keep_days)).date().isoformat()
        self.items = {k: v for k, v in self.items.items() if v["collected"] >= cutoff}

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        data = sorted(self.items.values(), key=lambda x: (x["collected"], x.get("published", "")), reverse=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=0)

    def recent(self, days, today):
        cutoff = (datetime.fromisoformat(today) - timedelta(days=days)).date().isoformat()
        return [v for v in self.items.values() if v["collected"] >= cutoff]


def flat_row(it):
    return {
        "id": it["id"], "collected": it["collected"], "published": (it.get("published") or "")[:10],
        "region": " / ".join(it.get("regions") or []), "country": " / ".join(it.get("country_names") or []),
        "categories": " / ".join(it.get("categories") or []), "score": it.get("score", 0),
        "title": it["title"], "title_ja": it.get("title_ja", ""), "source": it.get("source", ""),
        "url": it["url"], "summary": it.get("summary", ""),
    }


def append_archive(archive_dir, new_items, today):
    """月別CSV（Excelで開ける UTF-8 BOM 付き）に追記"""
    if not new_items:
        return None
    os.makedirs(archive_dir, exist_ok=True)
    path = os.path.join(archive_dir, f"{today[:7]}.csv")
    exists = os.path.exists(path)
    with open(path, "a", encoding="utf-8-sig" if not exists else "utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if not exists:
            w.writeheader()
        for it in new_items:
            w.writerow(flat_row(it))
    return path


def write_bodies(body_dir, items, keep_ids):
    """本文全文は閲覧画面から必要なときだけ読むよう、記事ごとのファイルに分けて保存"""
    os.makedirs(body_dir, exist_ok=True)
    for it in items:
        body = it.pop("body", "")
        if body:
            with open(os.path.join(body_dir, f'{it["id"]}.txt'), "w", encoding="utf-8") as f:
                f.write(body)
            it["has_body"] = True
    for name in os.listdir(body_dir):
        if name.endswith(".txt") and name[:-4] not in keep_ids:
            os.remove(os.path.join(body_dir, name))
