"""Googleニュースの転送URL（news.google.com/rss/articles/...）を元記事のURLに戻す
  元記事のURLがないと「元記事を日本語で読む」（Google翻訳のページ翻訳）が使えないため
"""
import logging
import time

log = logging.getLogger(__name__)


def resolve_google_urls(items, batch=25):
    targets = [it for it in items if "news.google.com" in (it.get("url") or "")]
    if not targets:
        return 0
    try:
        from googlenewsdecoder import gnewsdecoder
    except ImportError:
        log.warning("googlenewsdecoder が入っていないため、元記事URLへの変換を省略します")
        return 0
    done = 0
    for i in range(0, len(targets), batch):
        chunk = targets[i:i + batch]
        try:
            res = gnewsdecoder([it["url"] for it in chunk], timeout=20)
        except Exception as ex:
            log.warning("元記事URLへの変換に失敗（%d〜%d件目）: %s", i, i + len(chunk), ex)
            continue
        for it, r in zip(chunk, res if isinstance(res, list) else [res]):
            url = (r or {}).get("decoded_url") or ""
            if (r or {}).get("success") and url.startswith(("http://", "https://")):
                it["gn_url"] = it["url"]
                it["url"] = url
                done += 1
        time.sleep(1)
    log.info("元記事URLに変換 %d / %d 件", done, len(targets))
    return done
