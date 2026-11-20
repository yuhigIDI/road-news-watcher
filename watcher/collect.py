"""Googleニュース検索と媒体RSSから記事を集める"""
import html
import logging
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import feedparser
import requests

log = logging.getLogger(__name__)
UA = "Mozilla/5.0 (compatible; road-news-watcher/1.0)"
GN_URL = "https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={gl}:{ceid_lang}"


def _strip_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def _html_to_text(s):
    """本文用：段落の区切りを残してテキスト化"""
    s = re.sub(r"(?i)<\s*(br|/p|/div|/li|/h[1-6])\s*/?>", "\n", s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    lines = [re.sub(r"[ \t\u00a0]+", " ", ln).strip() for ln in s.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def _published(entry):
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


def _fetch(url, timeout=20):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    return feedparser.parse(r.content)


def parse_feed(feed, *, via, hint_country=None, lang=None, since=None, is_google=False):
    items = []
    for e in feed.entries:
        title = _strip_html(e.get("title"))
        source = ""
        if is_google:
            src = e.get("source") or {}
            source = src.get("title") or ""
            if source and title.endswith(" - " + source):
                title = title[: -len(" - " + source)]
        else:
            source = (feed.feed.get("title") or "").strip()
        pub = _published(e)
        if since and pub and pub < since:
            continue
        summary = _strip_html(e.get("summary") or e.get("description"))
        body = ""
        if not is_google and e.get("content"):
            body = _html_to_text(e["content"][0].get("value", ""))
            if len(body) < 300:  # 概要の繰り返し程度なら本文扱いしない
                body = ""
        if is_google and summary.startswith(title):
            summary = ""  # Googleニュースの概要は見出しの繰り返しなので捨てる
        items.append({
            "title": title,
            "summary": summary[:600],
            "body": body[:8000],
            "url": link if (link := (e.get("link") or "")).startswith(("http://", "https://")) else "",
            "source": source,
            "published": pub.isoformat() if pub else "",
            "lang": lang or "",
            "via": [via],
            "hint_country": hint_country,
        })
    return items


def google_news(query, hl, gl, *, since, hint_country=None, days=3):
    q = f"{query} when:{max(1, days)}d"
    lang = hl.split("-")[0]
    url = GN_URL.format(q=urllib.parse.quote(q), hl=hl, gl=gl, ceid_lang=lang)
    feed = _fetch(url)
    return parse_feed(feed, via=f"GN[{gl}:{lang}] {query}", hint_country=hint_country,
                      lang=lang, since=since, is_google=True)


def collect_all(countries_cfg, sources_cfg, *, limit_countries=None):
    days = int(sources_cfg.get("lookback_days", 3))
    since = datetime.now(timezone.utc) - timedelta(days=days)
    gn = sources_cfg.get("google_news") or {}
    sleep = float(gn.get("sleep_seconds", 1.5))
    items, errors = [], []

    def run(label, fn):
        try:
            got = fn()
            items.extend(got)
            log.info("%4d件  %s", len(got), label)
        except Exception as ex:  # 1件の失敗で全体を止めない
            errors.append(f"{label}: {ex}")
            log.warning("失敗  %s: %s", label, ex)

    if gn.get("enabled", True):
        for region, clist in countries_cfg["regions"].items():
            for c in clist:
                if limit_countries and c["code"] not in limit_countries:
                    continue
                hl_gl = (c.get("gn") or "en:US").split(":")
                hl, gl = hl_gl[0], hl_gl[1]
                for tmpl in gn.get("en_queries") or []:
                    q = tmpl.format(country=c["name_en"])
                    run(q, lambda q=q: google_news(q, hl, gl, since=since, hint_country=c["code"], days=days))
                    time.sleep(sleep)
                if c.get("name_fr"):
                    for tmpl in gn.get("fr_queries") or []:
                        q = tmpl.format(country=c["name_fr"])
                        run(q, lambda q=q: google_news(q, "fr", "FR", since=since, hint_country=c["code"], days=days))
                        time.sleep(sleep)
                for tmpl in gn.get("ja_queries") or []:
                    q = tmpl.format(country=c["name_ja"])
                    run(q, lambda q=q: google_news(q, "ja", "JP", since=since, hint_country=c["code"], days=days))
                    time.sleep(sleep)
                for q in c.get("extra_queries") or []:  # 現地語検索など（任意）
                    run(q["q"], lambda q=q: google_news(q["q"], q.get("hl", "en"), q.get("gl", "US"),
                                                        since=since, hint_country=c["code"], days=days))
                    time.sleep(sleep)
        if not limit_countries:
            for q in gn.get("extra_queries") or []:
                run(q["q"], lambda q=q: google_news(q["q"], q.get("hl", "en"), q.get("gl", "US"), since=since, days=days))
                time.sleep(sleep)

    if not limit_countries:
        for f in sources_cfg.get("rss_feeds") or []:
            def fetch_rss(f=f):
                got = parse_feed(_fetch(f["url"]), via=f'RSS {f["name"]}', since=since,
                                 hint_country=f.get("country"))
                for it in got:
                    it["source"] = it["source"] or f["name"]
                return got
            run(f'RSS {f["name"]}', fetch_rss)

    return items, errors
