"""共有画面（Claudeアーティファクト）用のデータを作る — 毎朝のClaude定期タスクが使う

使い方:
  python tools/artifact_data.py prepare <repoのdata/articles.json> <前日のarticles.json> <todo.json>
      → Claudeがまだ処理していない記事（和訳・要点・判定が未作成）を todo.json に書き出す
  python tools/artifact_data.py build <repoのdata/articles.json> <前日のarticles.json> <enrich.json> <出力フォルダ>
      → 直近1か月分の articles.json と meta.json を出力フォルダに書く
  python tools/artifact_data.py daily <出力フォルダ/articles.json> <enrich.json> <repoのdata/enrich>
      → 今回処理した記事だけを data/enrich/YYYY-MM-DD.json に書く（GitHubがこれを受けて要約付きメールを送る）

enrich.json の形式（Claudeが作る）:
  {"<記事id>": {"j": "和訳見出し", "pts": "要点（3〜4文）", "rel": 1 または 0,
                "why": "関係なしの理由（関係ありなら空）", "repost": 1 または 0, "orig": "元記事URL（見つけた場合）"}, ...}
"""
import json
import os
import sys
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEEP_DAYS = 60   # repoのデータから処理対象を探す範囲
SHOW_DAYS = 30   # 共有画面に載せる範囲（収集日がこの日数以内）
ENRICH_KEYS = ("j", "pts", "rel", "why", "repost", "orig")
# Claudeに回す（＝共有画面に載せる）記事の条件。処理量と利用量を抑えるため
MIN_SCORE = int(os.environ.get("ARTIFACT_MIN_SCORE", 6))   # 関連度がこれ未満の記事は載せない
START_DATE = os.environ.get("ARTIFACT_START_DATE", "2026-11-21")  # これより前に収集した記事は載せない


def _load(path, default):
    if path and os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def _recent(items):
    cutoff = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
    return [x for x in items if x.get("collected", "") >= cutoff]


def _target(x, prev):
    """共有画面に載せる記事か。すでにClaudeが処理した記事は条件にかかわらず残す"""
    p = prev.get(x["id"])
    if p and p.get("rel") in (0, 1):
        return True
    return x.get("score", 0) >= MIN_SCORE and x.get("collected", "") >= START_DATE


def _prev_map(prev):
    """前回の共有画面のデータに、data/enrich/*.json（これまでの処理結果の記録）を合わせる。
    共有画面は直近1か月分しか持たないので、処理済みかどうかは記録の方で判断する"""
    import glob
    done = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "enrich", "*.json"))):
        for x in _load(path, []):
            if isinstance(x, dict) and x.get("id"):
                done[x["id"]] = x
    for x in (prev if isinstance(prev, list) else []):
        if x.get("rel") in (0, 1) or x["id"] not in done:
            done[x["id"]] = x
    # GitHub Actions が本文を読んで作った要約（data/summaries/*.json）を合わせる。
    # 未処理の記事にはそのまま使い、処理済みでも本文を確認できていない要約なら、本文確認済みの要約で置き換える
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "summaries", "*.json"))):
        for i, s in _load(path, {}).items():
            cur = done.get(i)
            if (not cur or cur.get("rel") not in (0, 1)
                    or (s.get("basis") == "body" and cur.get("rel") == 1 and _rank(cur) < 3)):
                done[i] = dict(cur or {}, id=i, **{k: s[k] for k in ENRICH_KEYS if k in s})
    return done


def _rank(x):
    """要約の確かさ：3=本文確認、2=関連報道・同じ話題、1=概要のみ、0=要約不可・未処理"""
    p = x.get("pts") or ""
    if not p or "要約不可" in p:
        return 0
    if "提供された概要等に基づく" in p:
        return 1
    if "関連報道に基づく" in p or p.startswith("上の記事と同じ話題"):
        return 2
    return 3


def summaries_today():
    from datetime import datetime, timedelta, timezone
    day = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
    return day, _load(os.path.join(ROOT, "data", "summaries", f"{day}.json"), {})


def prepare(repo_json, prev_json, todo_json):
    items = _recent(_load(repo_json, []))
    prev = _prev_map(_load(prev_json, []))
    todo = []
    for x in items:
        p = prev.get(x["id"])
        if p and p.get("rel") in (0, 1):
            continue
        if not _target(x, prev):
            continue
        todo.append({"id": x["id"], "title": x["title"], "source": x.get("source", ""),
                     "url": x["url"], "published": (x.get("published") or "")[:10],
                     "countries": x.get("country_names", []), "categories": x.get("categories", []),
                     "summary": (x.get("summary") or "")[:400],
                     "url_is_google_redirect": "news.google.com" in x["url"]})
    with open(todo_json, "w", encoding="utf-8") as f:
        json.dump(todo, f, ensure_ascii=False, indent=1)
    print(f"未処理 {len(todo)} 件 / 対象 {len(items)} 件 → {todo_json}")


def build(repo_json, prev_json, enrich_json, out_dir):
    import yaml
    items = _recent(_load(repo_json, []))
    prev = _prev_map(_load(prev_json, []))
    enrich = _load(enrich_json, {})
    out = []
    for x in items:
        if not _target(x, prev):
            continue
        e = {k: v for k, v in (prev.get(x["id"]) or {}).items() if k in ENRICH_KEYS}
        e.update({k: v for k, v in (enrich.get(x["id"]) or {}).items() if k in ENRICH_KEYS})
        url = x["url"]
        if e.get("orig", "").startswith(("http://", "https://")) and "news.google.com" in url:
            url = e["orig"]
        out.append({
            "id": x["id"], "c": x["collected"], "p": (x.get("published") or "")[:10],
            "t": x["title"], "j": e.get("j", ""), "pts": e.get("pts", ""),
            "rel": e.get("rel"), "why": e.get("why", ""), "repost": e.get("repost", 0),
            "orig": e.get("orig", ""),
            "s": x.get("source", ""), "u": url, "gu": 1 if "news.google.com" in url else 0,
            "co": x.get("countries", []), "ca": x.get("categories", []), "sc": x.get("score", 0),
            "su": (x.get("summary") or "")[:300],
        })
    # 共有画面に載せるのは直近1か月分（画面で収集日を絞り込める。初期表示は最新の日）
    cutoff = (date.today() - timedelta(days=SHOW_DAYS)).isoformat()
    out = [a for a in out if a["id"] in enrich or a["c"] >= cutoff]
    out.sort(key=lambda a: (a["c"], a["sc"]), reverse=True)
    with open(os.path.join(ROOT, "config", "countries.yaml"), encoding="utf-8") as f:
        countries = yaml.safe_load(f)
    with open(os.path.join(ROOT, "config", "keywords.yaml"), encoding="utf-8") as f:
        cats = [c["name"] for c in yaml.safe_load(f)["categories"]]
    done = sum(1 for a in out if a["rel"] in (0, 1))
    meta = {
        "generated": f'{date.today().isoformat()}（{len(out)}件中 {done}件をClaudeが処理済み）',
        "regions": {r: [{"code": c["code"], "name": c["name_ja"]} for c in lst]
                    for r, lst in countries["regions"].items()},
        "categories": cats,
    }
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "articles.json"), "w", encoding="utf-8") as f:
        f.write("[\n" + ",\n".join(json.dumps(a, ensure_ascii=False, separators=(",", ":")) for a in out) + "\n]\n")
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    missing = [a["id"] for a in out if a["rel"] not in (0, 1)]
    print(f"出力 {len(out)} 件（未処理 {len(missing)} 件）→ {out_dir}")
    _, pre = summaries_today()
    news = [a for a in out if a["id"] in enrich or a["id"] in pre]
    r = [a for a in news if a["rel"] == 1]
    kinds = {"本文確認": 3, "関連報道・同じ話題": 2, "概要のみ": 1, "要約不可": 0}
    print("今回分：関係あり %d 件／関係なし %d 件　" % (len(r), sum(1 for a in news if a["rel"] == 0))
          + "／".join(f"{k} {sum(1 for a in r if _rank(a) == v)}件" for k, v in kinds.items())
          + f"　元URL未取得 {sum(1 for a in r if a['gu'])}件")


def daily(articles_json, enrich_json, dest_dir):
    """今回Claudeが処理した記事と、今日 GitHub Actions が要約した記事を data/enrich/YYYY-MM-DD.json（日本時間の日付）に書く。
    公開リポジトリなので、元記事の概要（su）は入れない。これが届くとGitHubがメールを送る。"""
    day, pre = summaries_today()
    ids = set(_load(enrich_json, {})) | set(pre)   # Claudeが処理した記事＋GitHub Actionsが今日要約した記事
    rows = [{k: v for k, v in a.items() if k != "su"} for a in _load(articles_json, []) if a["id"] in ids]
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, f"{day}.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("[\n" + ",\n".join(json.dumps(a, ensure_ascii=False, separators=(",", ":")) for a in rows) + "\n]\n")
    print(f"{len(rows)} 件 → {path}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "prepare" and len(sys.argv) == 5:
        prepare(*sys.argv[2:5])
    elif cmd == "build" and len(sys.argv) == 6:
        build(*sys.argv[2:6])
    elif cmd == "daily" and len(sys.argv) == 5:
        daily(*sys.argv[2:5])
    else:
        print(__doc__)
        sys.exit(1)
