"""共有画面（Claudeアーティファクト）用のデータを作る — 毎朝のClaude定期タスクが使う

使い方:
  python tools/artifact_data.py prepare <repoのdata/articles.json> <前日のarticles.json> <todo.json>
      → Claudeがまだ処理していない記事（和訳・要点・判定が未作成）を todo.json に書き出す
  python tools/artifact_data.py build <repoのdata/articles.json> <前日のarticles.json> <enrich.json> <出力フォルダ>
      → 前日までの処理結果と今回の enrich.json をまとめ、articles.json と meta.json を出力フォルダに書く

enrich.json の形式（Claudeが作る）:
  {"<記事id>": {"j": "和訳見出し", "pts": "要点（3〜4文）", "rel": 1 または 0,
                "why": "関係なしの理由（関係ありなら空）", "repost": 1 または 0, "orig": "元記事URL（見つけた場合）"}, ...}
"""
import json
import os
import sys
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEEP_DAYS = 60
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
    return {x["id"]: x for x in (prev if isinstance(prev, list) else [])}


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


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "prepare" and len(sys.argv) == 5:
        prepare(*sys.argv[2:5])
    elif cmd == "build" and len(sys.argv) == 6:
        build(*sys.argv[2:6])
    else:
        print(__doc__)
        sys.exit(1)
