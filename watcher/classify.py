"""国・分類の判定と関連度スコア"""
import re

WORD_CHARS = r"A-Za-z0-9À-ÖØ-öø-ÿ"


def _is_latin(term: str) -> bool:
    return re.search(r"[　-鿿＀-￯]", term) is None


def _is_acronym(term: str) -> bool:
    # 全て大文字・数字・記号（EV, PPP, ITS, PM2.5 など）は大文字小文字を区別
    letters = [c for c in term if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters) and len(term) <= 8


def _latin_regex(terms, flags):
    if not terms:
        return None
    alts = sorted({re.escape(t) for t in terms}, key=len, reverse=True)
    pat = rf"(?<![{WORD_CHARS}])(?:{'|'.join(alts)})(?:s|es)?(?![{WORD_CHARS}])"
    return re.compile(pat, flags)


class TermMatcher:
    """英字（単語単位）と日本語（部分一致）を混在して照合する"""

    def __init__(self, terms):
        terms = [str(t).strip() for t in (terms or []) if str(t).strip()]
        latin = [t for t in terms if _is_latin(t)]
        self.cs = _latin_regex([t for t in latin if _is_acronym(t)], 0)
        self.ci = _latin_regex([t for t in latin if not _is_acronym(t)], re.IGNORECASE)
        self.ja = [t for t in terms if not _is_latin(t)]

    def find(self, text: str):
        hits = []
        for rx in (self.cs, self.ci):
            if rx:
                hits += [m.group(0) for m in rx.finditer(text)]
        hits += [t for t in self.ja if t in text]
        return hits


class Classifier:
    def __init__(self, countries_cfg, keywords_cfg, sources_cfg):
        self.countries = []  # list of dict(code, name_ja, region, matcher)
        for region, items in countries_cfg["regions"].items():
            for c in items:
                names = [c["name_en"], c["name_ja"]] + ([c["name_fr"]] if c.get("name_fr") else [])
                self.countries.append({
                    **c, "region": region,
                    "matcher": TermMatcher(names + list(c.get("aliases") or [])),
                })
        self.by_code = {c["code"]: c for c in self.countries}

        self.categories = []
        for cat in keywords_cfg["categories"]:
            terms = (cat.get("en") or []) + (cat.get("ja") or []) + (cat.get("fr") or [])
            self.categories.append((cat["name"], TermMatcher(terms)))

        core = keywords_cfg["core_terms"]
        self.core = TermMatcher((core.get("en") or []) + (core.get("ja") or []) + (core.get("fr") or []))
        self.exclude = [re.compile(p, re.IGNORECASE) for p in keywords_cfg.get("exclude_patterns") or []]
        self.priority = [s.lower() for s in sources_cfg.get("priority_sources") or []]

    def classify(self, item):
        """item: dict(title, summary, source, url, hint_country) -> 判定結果を item に追記。除外なら None"""
        title = item.get("title") or ""
        body = f'{title}\n{item.get("summary") or ""}'

        if any(rx.search(body) for rx in self.exclude):
            return None

        core_title = self.core.find(title)
        core_all = self.core.find(body)
        if not core_all:
            return None

        found, in_title = [], set()
        for c in self.countries:
            if c["matcher"].find(title):
                found.append(c["code"]); in_title.add(c["code"])
            elif c["matcher"].find(body):
                found.append(c["code"])
        hint = item.get("hint_country")
        country_from_hint = False
        if not found and hint:
            found = [hint]
            country_from_hint = True
        if not found:
            return None

        cats = []
        for name, m in self.categories:
            if m.find(body):
                cats.append(name)

        src = f'{item.get("source") or ""} {item.get("url") or ""}'.lower()
        priority = any(p in src for p in self.priority)

        score = 0
        score += min(len(set(core_title)), 3) * 2
        score += 1 if core_all else 0
        score += min(len(cats), 5)
        score += 2 if in_title else (0 if country_from_hint else 1)
        score += 3 if priority else 0

        item["countries"] = found[:4]
        item["regions"] = sorted({self.by_code[c]["region"] for c in item["countries"]})
        item["country_names"] = [self.by_code[c]["name_ja"] for c in item["countries"]]
        item["categories"] = cats
        item["score"] = score
        item["priority_source"] = priority
        return item
