"""Build the Orario Ledger static site.

    py tools/build.py            build into dist/ and run the publication checks
    py tools/build.py --serve    build, then serve dist/ on http://127.0.0.1:8770/

Standard library only. The output is plain static files with relative links, so it
works from any static host, a sub-directory, or straight from disk.

Checks run on every build (the build fails on errors):
  * broken internal links                      * citations to undefined references
  * citations to works outside the covered English releases
  * uncited paragraphs, list items or table rows in articles marked complete
  * leakage into the public output: CJK text, untranslated-volume identifiers,
    local file paths, or copyrighted source formats (.txt/.epub/.pdf)
"""
from __future__ import annotations

import html
import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path

WIKI = Path(__file__).resolve().parent.parent
CONTENT = WIKI / "content"
STATIC = WIKI / "static"
DIST = WIKI / "dist"

ERRORS: list[str] = []
WARNINGS: list[str] = []


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def slugify(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = text.lower().replace("ö", "o").replace("’", "").replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def plain_text(fragment: str) -> str:
    """Searchable text of rendered HTML: drop citation markers, join inline tags
    without spaces and separate block tags with one."""
    fragment = re.sub(r'<sup class="cite">.*?</sup>', "", fragment)
    fragment = JA_SPAN.sub("", fragment)  # the search index stays free of Japanese script; the romanisation remains
    fragment = re.sub(r"</?(?:a|span|strong|em|cite|mark|code|b|i)\b[^>]*>", "", fragment)
    fragment = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(fragment)).strip()


def strip_markup(text: str) -> str:
    """Plain text of an inline value: [[target|label]] -> label, drop emphasis."""
    text = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]", r"\1", text)
    return re.sub(r"\*+", "", text)


def fmt_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%B')} {d.year}"


# --------------------------------------------------------------------------- data

SITE = json.loads((CONTENT / "site.json").read_text(encoding="utf-8"))
# Official artwork chosen by tools/import_media.py; illustrations only, never evidence for a claim.
MEDIA_DIR = CONTENT / "media"
MEDIA = json.loads((CONTENT / "media.json").read_text(encoding="utf-8")) if (CONTENT / "media.json").exists() else {"images": {}, "pages": {}}
WORKS = json.loads((CONTENT / "works.json").read_text(encoding="utf-8"))
SERIES = {s["id"]: s for s in WORKS["series"]}
CATEGORIES = {c["id"]: c for c in SITE["categories"]}
PLANNED = {p["slug"]: p for p in SITE["planned"]}

FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
REFDEF = re.compile(r"^\[@([\w.-]+)\]:\s*(.+?)\s*$", re.M)


def work_info(code: str):
    """'FM17' -> (series, 17). Returns (None, None) for anything outside coverage."""
    m = re.fullmatch(r"([A-Z]{2})(\d{2})", code)
    if not m or m.group(1) not in SERIES:
        return None, None
    series, vol = SERIES[m.group(1)], int(m.group(2))
    lo, hi = series["covered"]
    if not (lo <= vol <= hi):
        return None, None
    return series, vol


def works_compact(codes) -> str:
    """['FM01','FM02','FM03','FC01'] -> 'DanMachi 1–3 · Familia Chronicle 1'."""
    groups = []
    for sid, series in SERIES.items():
        vols = sorted(int(c[2:]) for c in codes if c.startswith(sid))
        if not vols:
            continue
        runs, start = [], vols[0]
        for prev, cur in zip(vols, vols[1:] + [None]):
            if cur != prev + 1:
                runs.append(str(start) if start == prev else f"{start}–{prev}")
                start = cur
        groups.append(f"{series['short']} {', '.join(runs)}")
    return " · ".join(groups)


def readable_codes(text: str) -> str:
    """'as told in SO03' -> 'as told in Sword Oratoria 3' for research text shown to readers."""
    def sub(m):
        series = SERIES.get(m.group(1))
        return f"{series['short']} {int(m.group(2))}" if series else m.group(0)
    return re.sub(r"\b(FM|SO|FC|AR|SS)(\d{2})\b", sub, text)


def work_name(code: str, long: bool = False) -> str:
    series, vol = work_info(code)
    if series is None:
        return code
    sub = (series.get("volume_titles") or {}).get(str(vol))
    base = f"{series['title'] if long else series['short']}, Vol. {vol}"
    return f"{base}: {sub}" if sub else base


class Article:
    def __init__(self, path: Path, kind: str = "article"):
        raw = path.read_text(encoding="utf-8")
        m = FRONT.match(raw)
        if not m:
            raise SystemExit(f"{path.name}: missing JSON front matter")
        self.meta = json.loads(m.group(1))
        body = raw[m.end():]
        self.refs = {}
        for rm in REFDEF.finditer(body):
            parts = [p.strip() for p in rm.group(2).split("|")]
            parts += [""] * (3 - len(parts))
            self.refs[rm.group(1)] = {"code": parts[0], "section": parts[1], "note": parts[2]}
        self.body = REFDEF.sub("", body)
        self.slug = self.meta.get("slug") or path.stem
        self.kind = kind
        self.title = self.meta["title"]
        self.category = self.meta.get("category")
        self.status = self.meta.get("status", "in-progress")
        self.summary = self.meta.get("summary", "")
        self.url = f"wiki/{self.slug}.html" if kind == "article" else f"{self.slug}.html"
        self.html = ""
        self.toc: list[tuple[int, str, str]] = []
        self.plain = ""
        self.cited_works: list[str] = []


def load_articles():
    arts = [Article(p) for p in sorted((CONTENT / "articles").glob("*.md"))]
    pages = [Article(p, "page") for p in sorted((CONTENT / "pages").glob("*.md"))]
    return arts, pages


# ----------------------------------------------------------------------- markdown

CITE = re.compile(r"\[@([^\]]+)\]")
WIKILINK = re.compile(r"\[\[([^\]|]*?)(?:\|([^\]]+))?\]\]")
EXTLINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
BOLD = re.compile(r"\*\*(.+?)\*\*")
ITAL = re.compile(r"(?<![\*\w])\*(?!\s)(.+?)(?<!\s)\*(?![\*\w])")
BADGE = re.compile(r"\{\{(statement|inference|unresolved|unverified)\}\}")
# Japanese original for names and chants only (user decision, 1 Oct 2026): {{ja|日本語}} or {{ja|日本語|romanisation}}.
JA = re.compile(r"\{\{ja\|([^{}|]+?)(?:\|([^{}|]+?))?\}\}")
JA_SPAN = re.compile(r'<span lang="ja" class="ja">[^<]*</span>')
HEADING = re.compile(r"^(#{2,4})\s+(.*?)(?:\s+\{#([\w-]+)\})?\s*$")
LISTITEM = re.compile(r"^(\s*)(?:[-*]|(\d+)\.)\s+(.*)$")
DIRECTIVE = re.compile(r"^\{\{([\w-]+)\}\}$")

LABELS = {
    "statement": ("Character statement", "Said by a character in the story; the narration does not confirm it."),
    "inference": ("Editorial inference", "Our reading of the cited evidence; the text does not state it outright."),
    "unresolved": ("Unresolved", "The covered English volumes do not settle this."),
    "unverified": ("Unverified", "Not yet supported by a checked passage in the covered volumes."),
    "note": ("Note", ""),
}
CHECKED_CALLOUTS = {"statement", "inference"}


class Renderer:
    def __init__(self, art: Article, ctx: "Site", root: str):
        self.art, self.ctx, self.root = art, ctx, root
        self.order: list[str] = []
        self.uses: dict[str, int] = {}
        self._cited = False
        self.check = art.kind == "article" and art.status == "complete"

    # inline ---------------------------------------------------------------
    def inline(self, text: str) -> str:
        t = html.escape(text, quote=False)
        t = CITE.sub(self._cite, t)
        t = WIKILINK.sub(self._wikilink, t)
        t = EXTLINK.sub(lambda m: f'<a class="ext" href="{m.group(2)}" rel="noopener">{m.group(1)}</a>', t)
        t = BOLD.sub(r"<strong>\1</strong>", t)
        t = ITAL.sub(r"<em>\1</em>", t)
        t = BADGE.sub(self._badge, t)
        t = JA.sub(self._ja, t)
        return t

    def _ja(self, m):
        jp, roman = m.group(1).strip(), (m.group(2) or "").strip()
        out = f'<span lang="ja" class="ja">{jp}</span>'
        return out + (f' (<em>{roman}</em>)' if roman else "")

    def _badge(self, m):
        kind = m.group(1)
        label, tip = LABELS[kind]
        return f'<span class="badge badge--{kind}" title="{esc(tip)}">{label}</span>'

    def cite_keys(self, keys) -> str:
        marks = []
        for key in keys:
            if key not in self.art.refs:
                ERRORS.append(f"{self.art.slug}: citation [@{key}] has no reference definition")
                continue
            ref = self.art.refs[key]
            if work_info(ref["code"])[0] is None:
                ERRORS.append(f"{self.art.slug}: [@{key}] cites {ref['code']}, which is outside the covered English releases")
            if key not in self.order:
                self.order.append(key)
            n = self.order.index(key) + 1
            self.uses[key] = self.uses.get(key, 0) + 1
            marks.append(f'<sup class="cite"><a href="#ref-{n}" id="cite-{n}-{self.uses[key]}" data-ref="{n}">[{n}]</a></sup>')
        self._cited = True
        return "".join(marks)

    def _cite(self, m):
        return self.cite_keys([k.strip().lstrip("@") for k in re.split(r"[,;]", m.group(1)) if k.strip()])

    def _wikilink(self, m):
        target, text = m.group(1).strip(), m.group(2)
        page, _, anchor = target.partition("#")
        if not page:
            return f'<a href="#{esc(anchor)}">{text or anchor}</a>'
        art = self.ctx.find(page)
        label = text or (art.title if art else PLANNED.get(slugify(page), {}).get("title", page))
        if art:
            href = self.root + art.url + (f"#{anchor}" if anchor else "")
            return f'<a class="wikilink" href="{esc(href)}" data-preview="{esc(art.url)}">{label}</a>'
        if slugify(page) in PLANNED:
            return f'<span class="redlink" title="Not yet written">{label}</span>'
        ERRORS.append(f"{self.art.slug}: broken link [[{target}]]")
        return f'<span class="redlink">{label}</span>'

    # blocks ---------------------------------------------------------------
    def need_cite(self, what: str, text: str, in_callout: str | None):
        if self.check and not self._cited and (in_callout is None or in_callout in CHECKED_CALLOUTS):
            ERRORS.append(f"{self.art.slug}: uncited {what}: {text[:70]!r}")

    def render(self, text: str) -> str:
        return self.blocks(text.split("\n"), None)

    def blocks(self, lines: list[str], callout: str | None) -> str:
        out: list[str] = []
        para: list[str] = []
        i = 0

        def flush():
            if para:
                txt = " ".join(s.strip() for s in para)
                # {{nocite}} marks framing text (e.g. "The table records…") that makes no factual claim.
                framing = txt.startswith("{{nocite}}")
                txt = txt.removeprefix("{{nocite}}").strip()
                self._cited = framing
                rendered = self.inline(txt)
                self.need_cite("paragraph", txt, callout)
                out.append(f"<p>{rendered}</p>")
                para.clear()

        while i < len(lines):
            line = lines[i].rstrip()
            if not line.strip():
                flush()
                i += 1
                continue
            if (m := HEADING.match(line)):
                flush()
                level, text, hid = len(m.group(1)), m.group(2), m.group(3)
                hid = hid or slugify(text)
                inner = self.inline(text)
                if level <= 3:
                    self.art.toc.append((level, hid, re.sub(r"<[^>]+>", "", inner)))
                out.append(f'<h{level} id="{hid}">{inner}<a class="anchor" href="#{hid}" aria-hidden="true" tabindex="-1">#</a></h{level}>')
                i += 1
                continue
            if (m := DIRECTIVE.match(line.strip())):
                flush()
                out.append(self.ctx.directive(m.group(1), self))
                i += 1
                continue
            if line.startswith(">"):
                flush()
                block = []
                while i < len(lines) and lines[i].startswith(">"):
                    block.append(re.sub(r"^> ?", "", lines[i]))
                    i += 1
                out.append(self.callout(block))
                continue
            if line.lstrip().startswith("|"):
                flush()
                block = []
                while i < len(lines) and lines[i].lstrip().startswith("|"):
                    block.append(lines[i].strip())
                    i += 1
                out.append(self.table(block, callout))
                continue
            if LISTITEM.match(line):
                flush()
                block = []
                while i < len(lines) and lines[i].strip() and (LISTITEM.match(lines[i]) or lines[i].startswith("  ")):
                    block.append(lines[i].rstrip())
                    i += 1
                out.append(self.list(block, callout))
                continue
            para.append(line)
            i += 1
        flush()
        return "\n".join(out)

    def callout(self, lines: list[str]) -> str:
        m = re.match(r"^\[!(\w+)\]\s*(.*)$", lines[0]) if lines else None
        if not m:
            return f"<blockquote>{self.blocks(lines, 'note')}</blockquote>"
        kind = m.group(1).lower()
        if kind not in LABELS:
            ERRORS.append(f"{self.art.slug}: unknown callout type {kind}")
            kind = "note"
        label, tip = LABELS[kind]
        title = self.inline(m.group(2)) if m.group(2) else ""
        inner = self.blocks(lines[1:], kind)
        head = f'<span class="callout__label">{label}</span>' + (f'<span class="callout__title">{title}</span>' if title else "")
        tipline = f'<p class="callout__tip">{esc(tip)}</p>' if tip else ""
        return f'<div class="callout callout--{kind}" role="note"><div class="callout__head">{head}</div>{inner}{tipline}</div>'

    def list(self, lines: list[str], callout) -> str:
        items: list[dict] = []
        for line in lines:
            m = LISTITEM.match(line)
            if m:
                indent = len(m.group(1))
                item = {"text": m.group(3), "children": [], "ordered": bool(m.group(2))}
                if indent >= 2 and items:
                    items[-1]["children"].append(item)
                else:
                    items.append(item)
            elif items:
                target = items[-1]["children"][-1] if items[-1]["children"] else items[-1]
                target["text"] += " " + line.strip()

        def render(group):
            tag = "ol" if group and group[0]["ordered"] else "ul"
            parts = []
            for it in group:
                self._cited = False
                body = self.inline(it["text"])
                if not it["children"]:
                    self.need_cite("list item", it["text"], callout)
                sub = render(it["children"]) if it["children"] else ""
                parts.append(f"<li>{body}{sub}</li>")
            return f"<{tag}>{''.join(parts)}</{tag}>"

        return render(items)

    @staticmethod
    def split_row(line: str) -> list[str]:
        """Split a table row on |, ignoring the | inside [[target|label]] links and {{ja|…|…}} templates."""
        cells, buf, depth, i = [], [], 0, 0
        line = line.strip().removeprefix("|").removesuffix("|")
        while i < len(line):
            two = line[i:i + 2]
            if two in ("[[", "]]", "{{", "}}"):
                depth += 1 if two in ("[[", "{{") else -1
                buf.append(two)
                i += 2
                continue
            if line[i] == "|" and depth == 0:
                cells.append("".join(buf).strip())
                buf = []
            else:
                buf.append(line[i])
            i += 1
        cells.append("".join(buf).strip())
        return cells

    def table(self, lines: list[str], callout) -> str:
        rows = [self.split_row(l) for l in lines]
        head, body = rows[0], [r for r in rows[1:] if not all(re.fullmatch(r":?-{2,}:?", c) for c in r)]
        th = "".join(f"<th scope=\"col\">{self.inline(c)}</th>" for c in head)
        trs = []
        for r in body:
            self._cited = False
            cells = "".join(f"<td>{self.inline(c)}</td>" for c in r)
            self.need_cite("table row", " | ".join(r), callout)
            trs.append(f"<tr>{cells}</tr>")
        return f'<div class="table-wrap"><table class="wikitable"><thead><tr>{th}</tr></thead><tbody>{"".join(trs)}</tbody></table></div>'

    def references(self) -> str:
        if not self.order:
            return ""
        items = []
        for n, key in enumerate(self.order, 1):
            ref = self.art.refs[key]
            series, vol = work_info(ref["code"])
            if self.uses[key] == 1:
                backs = f'<a href="#cite-{n}-1" aria-label="Back to citation">^</a>'
            else:
                backs = "^ " + " ".join(
                    f'<sup><a href="#cite-{n}-{k}" aria-label="Back to citation {k}">{chr(96 + k)}</a></sup>'
                    for k in range(1, self.uses[key] + 1)
                )
            where = f' — <span class="ref-section">{esc(ref["section"])}</span>' if ref["section"] else ' — <span class="ref-section ref-section--vol">chapter not recorded</span>'
            note = f' <span class="ref-note">{self.inline(ref["note"])}</span>' if ref["note"] else ""
            items.append(
                f'<li id="ref-{n}" data-work="{esc(ref["code"])}" data-continuity="{esc(series["continuity"])}" data-eligibility="official-english">'
                f'<span class="ref-back">{backs}</span> <span class="ref-body"><cite>{esc(work_name(ref["code"]))}</cite>{where}.{note}</span></li>'
            )
            if ref["code"] not in self.art.cited_works:
                self.art.cited_works.append(ref["code"])
        unused = set(self.art.refs) - set(self.order)
        for key in sorted(unused):
            WARNINGS.append(f"{self.art.slug}: reference [@{key}] is defined but never cited")
        return f'<section class="references" aria-labelledby="references">{heading(2, "references", "References")}<ol class="reflist">{"".join(items)}</ol></section>'


# ----------------------------------------------------------------------- the site
# Every page uses one layout that follows MediaWiki's classic Vector (2010) conventions:
# left navigation panel, tabs, serif headings, infoboxes, category bar. No branding is copied.

SEARCH_ICON = '<svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><circle cx="8.5" cy="8.5" r="5.5"/><path d="m12.5 12.5 5 5"/></svg>'
MENU_ICON = '<svg viewBox="0 0 20 20" aria-hidden="true" focusable="false"><path d="M2 5h16M2 10h16M2 15h16"/></svg>'
TAGLINE = "the unofficial DanMachi encyclopedia"


def heading(level: int, hid: str, text: str) -> str:
    return f'<h{level} id="{hid}">{text}<a class="anchor" href="#{hid}" aria-hidden="true" tabindex="-1">#</a></h{level}>'


def split_lead(body_html: str):
    """Lead section first, then the contents box, as on MediaWiki."""
    cut = body_html.find("<h2")
    return (body_html[:cut], body_html[cut:]) if cut > 0 else ("", body_html)


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


class Site:
    def __init__(self):
        self.articles, self.pages = load_articles()
        self.series_pages = [self.series_article(s) for s in WORKS["series"]]
        self.all = self.articles + self.series_pages
        self.by_slug = {a.slug: a for a in self.all + self.pages}
        self.by_title = {a.title.lower(): a for a in self.all + self.pages}
        for a in self.all:
            for alias in a.meta.get("aliases", []):
                self.by_title.setdefault(alias.lower(), a)
        # A planned page stops being "planned" as soon as its article exists.
        for slug in list(PLANNED):
            if slug in self.by_slug:
                del PLANNED[slug]
        self.search_docs = []

    def find(self, name: str):
        return self.by_slug.get(name) or self.by_slug.get(slugify(name)) or self.by_title.get(name.lower())

    def in_category(self, cid: str):
        return sorted([a for a in self.all if a.category == cid], key=lambda a: a.title)

    # ---------------------------------------------------------- publications
    def series_article(self, s) -> Article:
        art = Article.__new__(Article)
        art.meta = {"title": s["title"], "category": "publications", "status": "complete"}
        art.refs, art.kind, art.slug, art.title = {}, "article", s["slug"], s["short"] if s["id"] != "FM" else "DanMachi (main series)"
        art.meta["aliases"] = [s["title"]]
        art.category, art.status = "publications", "complete"
        lo, hi = s["covered"]
        art.summary = f"{s['kind']} published in English by Yen Press. Volumes {lo}–{hi} are released in English and covered here."
        art.url = f"wiki/{s['slug']}.html"
        art.toc, art.cited_works, art.plain = [], [], ""
        art.series = s
        art.body = ""
        return art

    def series_body(self, art: Article, root: str) -> tuple[str, str]:
        """(infobox, body) for a publication page. Data comes from content/works.json."""
        s = art.series
        lo, hi = s["covered"]
        latest = s["latest"]
        vt = s.get("volume_titles") or {}
        infobox = f"""
<table class="infobox" aria-label="{esc(art.title)} summary"><tbody>
  <tr><th colspan="2" class="infobox-above"><i>{esc(s['short'])}</i></th></tr>
  <tr><th colspan="2" class="infobox-header" scope="colgroup">English release</th></tr>
  <tr><th scope="row" class="infobox-label">Author</th><td class="infobox-data">Fujino Omori</td></tr>
  <tr><th scope="row" class="infobox-label">Publisher</th><td class="infobox-data">{esc(WORKS['publisher'])}</td></tr>
  <tr><th scope="row" class="infobox-label">Type</th><td class="infobox-data">{esc(s['kind'])}</td></tr>
  <tr><th scope="row" class="infobox-label">Volumes</th><td class="infobox-data">{s['english_volumes']}</td></tr>
  <tr><th scope="row" class="infobox-label">Latest</th><td class="infobox-data">Vol. {latest['volume']} ({fmt_date(latest['date'])})</td></tr>
  <tr><th colspan="2" class="infobox-header" scope="colgroup">On this wiki</th></tr>
  <tr><th scope="row" class="infobox-label">Covered</th><td class="infobox-data">Vols. {lo}–{hi}</td></tr>
  <tr><th scope="row" class="infobox-label">Checked</th><td class="infobox-data">{fmt_date(WORKS['verified_on'])}</td></tr>
  <tr><td colspan="2" class="infobox-below"><a class="ext" href="{esc(s['listing'])}" rel="noopener">Yen Press series listing</a></td></tr>
</tbody></table>"""
        sub_th = '<th scope="col">Subtitle</th>' if vt else ""
        rows = "".join(
            f"<tr><td>Vol. {v}</td>{f'<td><i>{esc(vt[str(v)])}</i></td>' if vt else ''}<td>Covered</td></tr>"
            for v in range(lo, hi + 1)
        )
        up = s.get("upcoming")
        upcoming = ""
        if up:
            label = up.get("label") or f"Vol. {up['volume']}"
            upcoming = (
                f'<div class="callout callout--unresolved" role="note"><div class="callout__head"><span class="callout__label">Not yet in English</span>'
                f'<span class="callout__title">{esc(label)}, scheduled for {fmt_date(up["date"])}</span></div>'
                f'<p>{esc(up["note"])} Nothing from it appears on this wiki. <a class="ext" href="{esc(up["url"])}" rel="noopener">Yen Press listing</a>.</p></div>'
            )
        notes = f"<p>{esc(s['notes'])}</p>" if s.get("notes") else ""
        art.toc = [(2, "english-release", "English release"), (2, "volumes", "Volumes")]
        art.plain = f"{s['title']} {s['kind']} Yen Press English release volumes {lo} to {hi}"
        body = f"""
<p><b><i>{esc(s['title'])}</i></b> is {esc(s['phrase'])} by Fujino Omori, published in English by {esc(WORKS['publisher'])}.
This page records only its English publication status; story content is covered in the individual articles.</p>
{heading(2, "english-release", "English release")}
<p>As of {fmt_date(WORKS['verified_on'])}, {plural(s['english_volumes'], 'volume')} had been released in English. The latest, Vol. {latest['volume']},
came out on {fmt_date(latest['date'])} (<a class="ext" href="{esc(latest['url'])}" rel="noopener">Yen Press</a>). Vols. {lo}–{hi} are covered by this wiki.</p>
{upcoming}{notes}
{heading(2, "volumes", "Volumes")}
<div class="table-wrap"><table class="wikitable"><thead><tr><th scope="col">Volume</th>{sub_th}<th scope="col">Status on this wiki</th></tr></thead><tbody>{rows}</tbody></table></div>
"""
        return infobox, body

    # ------------------------------------------------------------ directives
    def directive(self, name: str, r: Renderer) -> str:
        if name == "coverage-table":
            rows = []
            for s in WORKS["series"]:
                lo, hi = s["covered"]
                latest = s["latest"]
                up = s.get("upcoming")
                nxt = (f'{esc(up.get("label") or "Vol. " + str(up["volume"]))} ({fmt_date(up["date"])})' if up else "None announced on the listing")
                href = r.root + f"wiki/{s['slug']}.html"
                rows.append(
                    f'<tr><td><a class="wikilink" href="{href}" data-preview="wiki/{s["slug"]}.html"><i>{esc(s["short"])}</i></a></td>'
                    f"<td>Vols. {lo}–{hi}</td><td>Vol. {latest['volume']} ({fmt_date(latest['date'])})</td><td>{nxt}</td></tr>"
                )
            return (
                '<div class="table-wrap"><table class="wikitable"><thead><tr><th scope="col">Series</th><th scope="col">Covered</th>'
                '<th scope="col">Latest English release</th><th scope="col">Next, excluded until released</th></tr></thead>'
                f'<tbody>{"".join(rows)}</tbody></table></div>'
            )
        if name == "verified-date":
            return f'<p><b>Publication boundaries checked on {fmt_date(WORKS["verified_on"])}.</b></p>'
        ERRORS.append(f"{r.art.slug}: unknown directive {{{{{name}}}}}")
        return ""

    # ------------------------------------------------------------ components
    def toc_html(self, art: Article) -> str:
        if len(art.toc) < 3:
            return ""
        items, n1, n2 = [], 0, 0
        for level, hid, text in art.toc:
            if level == 2:
                n1, n2 = n1 + 1, 0
                num = f"{n1}"
            else:
                n2 += 1
                num = f"{n1}.{n2}"
            items.append(f'<li class="toc__l{level}"><a href="#{hid}"><span class="toc__num">{num}</span> {esc(text)}</a></li>')
        return f'<details class="toc" open><summary>Contents</summary><ol>{"".join(items)}</ol></details>'

    def infobox_cells(self, art: Article, r: Renderer):
        """Rendered infobox rows: ('section', title) or ('row', label, value_html)."""
        for row in art.meta.get("infobox", {}).get("rows", []):
            if "section" in row:
                yield ("section", row["section"])
                continue
            r._cited = False
            value = r.inline(row["value"]) + r.cite_keys(row.get("refs", []))
            if r.check and not row.get("refs"):
                ERRORS.append(f"{art.slug}: infobox row {row['label']!r} has no citation")
            yield ("row", row["label"], value)

    def article_infobox(self, art: Article, r: Renderer) -> str:
        ib = art.meta.get("infobox")
        if not ib:
            return ""
        pic = MEDIA["pages"].get(art.slug, {}).get("infobox")
        if pic:
            image_cell = self.media_html(art, pic, "ib", r.root)
        else:
            image_cell = ('<div class="infobox-noimage">No image</div>'
                          f'<div class="infobox-caption">{esc(ib.get("image_note", "No image has been cleared for publication."))}</div>')
        rows = [
            f'<tr><th colspan="2" class="infobox-above">{esc(ib.get("title", art.title))}</th></tr>',
            f'<tr><td colspan="2" class="infobox-image">{image_cell}</td></tr>',
        ]
        for cell in self.infobox_cells(art, r):
            if cell[0] == "section":
                rows.append(f'<tr><th colspan="2" class="infobox-header" scope="colgroup">{esc(cell[1])}</th></tr>')
            else:
                rows.append(f'<tr><th scope="row" class="infobox-label">{esc(cell[1])}</th><td class="infobox-data">{cell[2]}</td></tr>')
        rows.append('<tr><td colspan="2" class="infobox-below">Light-novel continuity · official English releases</td></tr>')
        return f'<table class="infobox" aria-label="{esc(art.title)} summary"><tbody>{"".join(rows)}</tbody></table>'

    def media_html(self, art: Article, pic: dict, size: str, root: str) -> str:
        """An image with its caption and source line; size 'ib' (infobox) or 'th' (gallery)."""
        img = MEDIA["images"].get(pic["id"])
        f = img and img["files"].get(size)
        if not f or not (MEDIA_DIR / f["file"]).exists():
            ERRORS.append(f"{art.slug}: media {pic['id']} ({size}) is missing")
            return ""
        full = img["files"].get("full", f)["file"]
        alt = f"{art.title}: {pic.get('caption') or img['source']}"
        cap = f'{esc(pic["caption"])}<br>' if pic.get("caption") else ""
        src = (f'<span class="media-source">{esc(img["source"])}</span>'
               f' · <a class="ext" href="{esc(img["fandom"])}" rel="noopener" title="{esc(img["name"])} on the DanMachi Fandom wiki">file</a>')
        pic_html = (f'<a class="image" href="{root}media/{full}"><img src="{root}media/{f["file"]}" alt="{esc(alt)}" '
                    f'width="{f["w"]}" height="{f["h"]}" loading="lazy" decoding="async"></a>')
        if size == "ib":
            return f'{pic_html}<div class="infobox-caption">{cap}{src}</div>'
        return (f'<li class="gallerybox"><div class="thumb">{pic_html}</div>'
                f'<div class="gallerytext">{cap}{src}</div></li>')

    def gallery(self, art: Article, root: str) -> str:
        pics = MEDIA["pages"].get(art.slug, {}).get("gallery", [])
        if not pics:
            return ""
        items = "".join(self.media_html(art, p, "th", root) for p in pics)
        note = ('<p class="gallery-note">Official artwork from the anime, light novels, manga and games, shown for identification. '
                'Adaptation designs can differ from the novels\' descriptions and are not a source for this article.</p>')
        return heading(2, "gallery", "Gallery") + note + f'<ul class="gallery">{items}</ul>'

    def see_also(self, art: Article, root: str) -> str:
        items = []
        for slug in art.meta.get("related", []):
            other = self.find(slug)
            if other:
                items.append(f'<li><a class="wikilink" href="{root}{other.url}" data-preview="{esc(other.url)}">{esc(other.title)}</a></li>')
            elif slug in PLANNED:
                items.append(f'<li><span class="redlink" title="Not yet written">{esc(PLANNED[slug]["title"])}</span></li>')
            else:
                ERRORS.append(f"{art.slug}: related article {slug!r} does not exist and is not planned")
        if not items:
            return ""
        return heading(2, "see-also", "See also") + f'<div class="div-col"><ul>{"".join(items)}</ul></div>'

    def catlinks(self, root: str, cats: list[str]) -> str:
        items = "".join(f'<li><a href="{root}category/{c}.html">{esc(CATEGORIES[c]["title"])}</a></li>' for c in cats if c in CATEGORIES)
        if not items:
            return ""
        return (f'<div id="catlinks" class="catlinks"><div class="mw-normal-catlinks">'
                f'<a href="{root}category/index.html">Categories</a>: <ul>{items}</ul></div></div>')

    # ---------------------------------------------------------------- chrome
    def sidebar(self, root: str, active: str | None, tools: list[tuple[str, str]]) -> str:
        def li(href, label, key=None):
            cur = ' class="selected"' if key and key == active else ""
            return f'<li{cur}><a href="{root}{href}">{label}</a></li>'

        cats = "".join(li(f"category/{c['id']}.html", esc(c["title"]), c["id"]) for c in SITE["categories"])
        coverage = "".join(f"<li>{esc(s['short'])} {s['covered'][0]}–{s['covered'][1]}</li>" for s in WORKS["series"])
        tool_items = "".join(f'<li><a href="{href}">{label}</a></li>' for href, label in tools)
        return f"""
<div id="sidebar" class="mw-panel" aria-label="Site navigation">
  <div id="p-logo" role="banner"><a class="mw-wiki-logo" href="{root}index.html" title="Visit the main page"><span class="mw-wordmark">{esc(SITE['name'])}</span><span class="mw-tagline">The unofficial DanMachi encyclopedia</span></a></div>
  <nav class="portal portal-first" aria-label="Navigation"><div class="body"><ul>
    {li('index.html', 'Main page', 'home')}
    {li('category/index.html', 'All categories', 'categories')}
    {li('timeline.html', 'Timeline', 'timeline')}
    {li('sources.html', 'Sources &amp; coverage', 'sources')}
    <li><a href="{root}search.html" data-random="{root}">Random article</a></li>
    {li('search.html', 'Search', 'search')}
  </ul></div></nav>
  <nav class="portal" aria-labelledby="p-cat-label"><h3 id="p-cat-label">Categories</h3><div class="body"><ul>{cats}</ul></div></nav>
  <nav class="portal" aria-labelledby="p-page-label"><h3 id="p-page-label">This page</h3><div class="body"><ul>
    {tool_items}<li><a href="#" data-print>Printable version</a></li>
  </ul></div></nav>
  <nav class="portal" aria-labelledby="p-cov-label"><h3 id="p-cov-label">Coverage</h3><div class="body"><ul class="plainlist">
    <li>Checked {fmt_date(WORKS['verified_on'])}</li>{coverage}
  </ul></div></nav>
</div>
<div class="scrim" hidden></div>"""

    def layout(self, *, title: str, root: str, content: str, active: str | None = None, tabs: str = "",
               description: str = "", meta_json: dict | None = None, reviewed: str | None = None,
               tools: list[tuple[str, str]] | None = None, doc_title: str | None = None) -> str:
        meta_script = f'<script type="application/json" id="page-meta">{json.dumps(meta_json, ensure_ascii=False)}</script>' if meta_json else ""
        last = f"<li>This page was last reviewed on {fmt_date(reviewed)}.</li>" if reviewed else ""
        tabs = tabs or '<li class="selected"><a href="">Page</a></li>'
        return f"""<!doctype html>
<html lang="en" class="skin-classic">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(doc_title or f"{title} - {SITE['name']}")}</title>
<meta name="description" content="{esc(description or SITE['tagline'])}">
<meta name="color-scheme" content="light dark">
<link rel="icon" href="{root}assets/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="{root}assets/classic.css">
<script>try{{var t=localStorage.getItem('ol-theme');if(t==='light'||t==='dark')document.documentElement.setAttribute('data-theme',t)}}catch(e){{}}</script>
{meta_script}
</head>
<body>
<a class="skip" href="#content">Jump to content</a>
<div class="mw-page">
<div class="mw-mobile-bar">
  <button class="nav-toggle" type="button" aria-label="Open main menu" aria-expanded="false" aria-controls="sidebar">{MENU_ICON}</button>
  <a class="mw-mobile-wordmark" href="{root}index.html">{esc(SITE['name'])}</a>
</div>
<div id="mw-page-base"></div>
<div id="mw-head-base"></div>
<main id="content" class="mw-body" tabindex="-1">
{content}
</main>
<div id="mw-navigation">
  <div id="mw-head">
    <nav id="p-personal" aria-label="Personal tools"><ul>
      <li><button class="theme-toggle linklike" type="button"><span class="tt-to-dark">Dark mode</span><span class="tt-to-light">Light mode</span></button></li>
      <li><a href="{root}sources.html">Sources &amp; coverage</a></li>
    </ul></nav>
    <div id="left-navigation"><nav class="vector-tabs" aria-label="Namespaces"><ul>{tabs}</ul></nav></div>
    <div id="right-navigation">
      <nav class="vector-tabs" aria-label="Views"><ul><li class="selected"><a href="">Read</a></li></ul></nav>
      <form class="search" id="p-search" role="search" action="{root}search.html" data-root="{root}">
        <input class="search__input" type="search" name="q" placeholder="Search {esc(SITE['name'])}" autocomplete="off" aria-label="Search {esc(SITE['name'])}" aria-autocomplete="list" aria-controls="search-suggest">
        <button class="search__go" type="submit" aria-label="Search">{SEARCH_ICON}</button>
        <div class="search__suggest" id="search-suggest" role="listbox" hidden></div>
      </form>
    </div>
  </div>
  {self.sidebar(root, active, tools or [])}
</div>
<footer id="footer" role="contentinfo">
  <ul id="footer-info">{last}
    <li>Articles are original summaries of the official English editions, cited claim by claim. No book text or ebook files are distributed here.</li>
    <li>Images are official artwork from the DanMachi anime, light novels, manga and games, shown for identification and commentary; rights remain with their owners. Each image links to its file page on the DanMachi Fandom wiki, where it was sourced.</li>
    <li>{esc(SITE['disclaimer'])}</li>
  </ul>
  <ul id="footer-places">
    <li><a href="{root}sources.html">Sources &amp; coverage</a></li>
    <li><a href="{root}category/index.html">All categories</a></li>
    <li><a href="{root}search.html">Search</a></li>
  </ul>
</footer>
</div>
<script src="{root}assets/search-index.js" defer></script>
<script src="{root}assets/site.js" defer></script>
</body>
</html>
"""

    def frame(self, title_html: str, body: str, *, sub: str = "", indicator: str = "", indicator_kind: str = "",
              catlinks: str = "", title_class: str = "") -> str:
        """The content area: indicators, first heading, site subtitle, body and category bar."""
        ind = f'<div class="mw-indicators"><span class="mw-indicator mw-indicator--{indicator_kind}">{indicator}</span></div>' if indicator else ""
        sub_html = f'<div id="contentSub">{sub}</div>' if sub else ""
        return f"""
{ind}<h1 id="firstHeading" class="firstHeading{f' {title_class}' if title_class else ''}">{title_html}</h1>
<div id="bodyContent" class="mw-body-content">
  <div id="siteSub">From {esc(SITE['name'])}, {TAGLINE}</div>
  {sub_html}
  <div class="mw-parser-output">
{body}
  </div>
  {catlinks}
</div>"""

    # ---------------------------------------------------------------- pages
    def article_page(self, art: Article) -> str:
        root = "../"
        r = Renderer(art, self, root)
        cat = CATEGORIES[art.category]
        series = getattr(art, "series", None)
        if series:
            infobox, body_html = self.series_body(art, root)
        else:
            infobox = self.article_infobox(art, r)
            body_html = r.render(art.body)
        refs = r.references()
        for leftover in ("[[", "]]", "[@", "{{"):
            if leftover in body_html + infobox:
                ERRORS.append(f"{art.slug}: unparsed markup {leftover!r} in rendered article")
        art.html = body_html
        art.plain = art.plain or plain_text(body_html)
        lead, rest = split_lead(body_html)
        works = works_compact(art.cited_works)
        reviewed = art.meta.get("reviewed")
        meta_json = {
            "title": art.title,
            "category": art.category,
            "status": art.status,
            "continuity": art.meta.get("continuity", "light-novel"),
            "source_eligibility": "official English releases only",
            "cited_works": art.cited_works,
            "reviewed": reviewed,
        }

        gallery = "" if series else self.gallery(art, root)
        if gallery:
            art.toc.append((2, "gallery", "Gallery"))
        see_also = self.see_also(art, root)
        if see_also:
            art.toc.append((2, "see-also", "See also"))
        if refs:
            art.toc.append((2, "references", "References"))
        if series:
            indicator, kind = f"Publication record · checked {fmt_date(WORKS['verified_on'])}", "complete"
        elif art.status == "complete":
            indicator, kind = "Complete · every claim cited", "complete"
        else:
            indicator, kind = "In progress — not yet complete", "in-progress"
        hat = art.meta.get("hatnote")
        hat_html = f'<div class="hatnote" role="note">{r.inline(hat)}</div>' if hat else ""
        spoiler = art.meta.get("spoilers")
        spoiler_html = (
            f'<div class="ambox ambox-spoiler" role="note"><div class="mbox-text"><b>Spoiler warning:</b> this article covers {r.inline(spoiler)}. '
            f'Nothing on this wiki comes from volumes not yet published in English (<a href="{root}sources.html">coverage</a>).</div></div>'
            if spoiler else ""
        )
        page_media = MEDIA["pages"].get(art.slug, {})
        classes = [MEDIA["images"][p["id"]]["class"] for p in ([page_media["infobox"]] if page_media.get("infobox") else []) + page_media.get("gallery", []) if p["id"] in MEDIA["images"]]
        kinds = [k for k, lbl in (("anime", "anime"), ("ln", "light novels"), ("manga", "manga"), ("game", "games"), ("other", "other")) if k in classes]
        names = [dict(anime="anime", ln="light novels (Japanese editions)", manga="manga", game="games", other="other official art")[k] for k in kinds]
        images_row = (f'  <tr><th scope="row" class="navbox-group">Images</th><td class="navbox-list">Official art from the {", ".join(names)}; '
                      f'illustration only, not cited as evidence</td></tr>') if names else ""
        sourcing = "" if series else f"""
<table class="navbox" id="sourcing" aria-label="Sourcing">
  <tr><th colspan="2" class="navbox-title">Sourcing</th></tr>
  <tr><th scope="row" class="navbox-group">Continuity</th><td class="navbox-list">Light novels · official English releases (Yen Press)</td></tr>
  <tr><th scope="row" class="navbox-group">Volumes cited</th><td class="navbox-list">{esc(works) or '—'}</td></tr>
{images_row}
  <tr><th scope="row" class="navbox-group">Last reviewed</th><td class="navbox-list">{fmt_date(reviewed) if reviewed else '—'}</td></tr>
</table>"""
        body = f"{hat_html}{spoiler_html}{infobox}{lead}{self.toc_html(art)}{rest}{gallery}{see_also}{refs}{sourcing}"
        content = self.frame(
            esc(art.title), body,
            sub=f'<span class="subpages">&lt; <a href="{root}category/{cat["id"]}.html">{esc(cat["title"])}</a></span>',
            indicator=indicator, indicator_kind=kind,
            catlinks=self.catlinks(root, [art.category] + art.meta.get("tags", [])),
        )
        tabs = '<li class="selected"><a href="">Article</a></li>'
        tools = []
        if refs:
            tabs += f'<li><a href="#references">Sources ({len(r.order)})</a></li>'
            tools.append(("#references", "References"))
        if sourcing:
            tools.append(("#sourcing", "Sourcing details"))
        self.search_docs.append(self.doc(art))
        # Front-matter "sections" make named sections (e.g. each spell on the Magic page) searchable.
        for sec in art.meta.get("sections", []):
            if f'id="{sec["anchor"]}"' not in content:
                ERRORS.append(f"{art.slug}: section anchor #{sec['anchor']} not found on the page")
            self.search_docs.append({"t": sec["title"], "u": f"{art.url}#{sec['anchor']}",
                                     "c": f"Section of {art.title}", "s": sec.get("summary", ""),
                                     "a": sec.get("aliases", []), "h": [], "x": ""})
        return self.layout(title=art.title, root=root, content=content, active=art.category, tabs=tabs,
                           description=art.summary, meta_json=meta_json, reviewed=reviewed, tools=tools)

    def doc(self, art: Article) -> dict:
        return {
            "t": art.title,
            "u": art.url,
            "c": CATEGORIES[art.category]["title"] if art.category in CATEGORIES else "Page",
            "s": art.summary,
            "a": art.meta.get("aliases", []),
            "h": [t for _, _, t in art.toc],
            "x": art.plain,
        }

    def plain_page(self, art: Article, active: str) -> str:
        root = ""
        r = Renderer(art, self, root)
        body_html = r.render(art.body)
        refs = r.references()
        art.plain = plain_text(body_html)
        art.category = art.category or "page"
        lead, rest = split_lead(body_html)
        content = self.frame(esc(art.title), f"{lead}{self.toc_html(art)}{rest}{refs}")
        doc = self.doc(art)
        doc["c"] = "Help"
        self.search_docs.append(doc)
        return self.layout(title=art.title, root=root, content=content, active=active,
                           tabs='<li class="selected"><a href="">Project page</a></li>', description=art.summary)

    def category_page(self, cat: dict) -> str:
        root = "../"
        arts = self.in_category(cat["id"])
        planned = sorted([p for p in PLANNED.values() if p["category"] == cat["id"]], key=lambda p: p["title"])

        def groups(items):
            by_letter: dict[str, list[str]] = {}
            for letter, html_item in items:
                by_letter.setdefault(letter, []).append(html_item)
            return "".join(
                f'<div class="mw-category-group"><h3>{esc(k)}</h3><ul>{"".join(v)}</ul></div>' for k, v in sorted(by_letter.items())
            )

        members = [
            (a.title[0].upper(), f'<li><a class="wikilink" href="{root}{a.url}" data-preview="{esc(a.url)}">{esc(a.title)}</a>'
                                 f'{" <small>(in progress)</small>" if a.status != "complete" else ""}</li>')
            for a in arts
        ]
        title = cat["title"]
        if arts:
            listing = (f'{heading(2, "pages", f"Pages in category “{esc(title)}”")}'
                       f'<p>The following {plural(len(arts), "page")} {"is" if len(arts) == 1 else "are"} in this category, out of {len(arts)} total.</p>'
                       f'<div class="mw-category">{groups(members)}</div>')
        else:
            listing = ('<p><i>This category currently contains no pages. Articles are added only once their facts have been '
                       'checked against the covered English volumes.</i></p>')
        plan = ""
        if planned:
            reds = [(p["title"][0].upper(), f'<li><span class="redlink" title="Not yet written">{esc(p["title"])}</span></li>') for p in planned]
            plan = (f'{heading(2, "planned", "Planned pages")}'
                    '<p>These pages are linked from existing articles but have not been written yet. They appear in red until published.</p>'
                    f'<div class="mw-category">{groups(reds)}</div>')
        body = f"<p>{esc(cat['blurb'])}</p>{listing}{plan}"
        content = self.frame(
            f'<span class="mw-page-title-namespace">Category</span>:{esc(title)}', body,
            sub=f'<span class="subpages">&lt; <a href="{root}category/index.html">All categories</a></span>',
        )
        self.search_docs.append({"t": title, "u": f"category/{cat['id']}.html", "c": "Category", "s": cat["blurb"], "a": [], "h": [], "x": ""})
        return self.layout(title=f"Category:{title}", root=root, content=content, active=cat["id"],
                           tabs='<li class="selected"><a href="">Category</a></li>', description=cat["blurb"])

    def categories_index(self) -> str:
        root = "../"
        items = "".join(
            f'<li><a href="{root}category/{c["id"]}.html">{esc(c["title"])}</a> ({plural(len(self.in_category(c["id"])), "page")}) – {esc(c["blurb"])}</li>'
            for c in SITE["categories"]
        )
        body = f"<p>Every article belongs to one of these categories.</p><ul>{items}</ul>"
        content = self.frame("All categories", body)
        return self.layout(title="All categories", root=root, content=content, active="categories",
                           tabs='<li class="selected"><a href="">Special page</a></li>')

    def home(self) -> str:
        root = ""
        featured = [self.by_slug[s] for s in SITE["featured"] if s in self.by_slug and self.by_slug[s].status == "complete"]
        n_articles = len([a for a in self.all])
        tfa = "<p><i>No article is featured at the moment.</i></p>"
        for f in featured[:1]:
            ib = f.meta.get("infobox", {})
            facts = "; ".join(
                f"{esc(row['label'])}: {esc(strip_markup(row['value']))}"
                for row in ib.get("rows", []) if row.get("label") in f.meta.get("feature_facts", [])
            )
            tfa = (f'<p><b><a class="wikilink" href="{f.url}" data-preview="{esc(f.url)}">{esc(f.title)}</a></b> — '
                   f'{esc(f.meta.get("feature_text", f.summary))} (<b><a href="{f.url}">Full article…</a></b>)</p>'
                   + (f'<p class="mp-facts">{facts}.</p>' if facts else ""))
        cats = "".join(
            f'<li><a href="category/{c["id"]}.html">{esc(c["title"])}</a> <span class="mp-count">({len(self.in_category(c["id"]))})</span></li>'
            for c in SITE["categories"]
        )
        coverage = "".join(
            f'<li><a class="wikilink" href="wiki/{s["slug"]}.html" data-preview="wiki/{s["slug"]}.html"><i>{esc(s["short"])}</i></a> – Vols. {s["covered"][0]}–{s["covered"][1]}</li>'
            for s in WORKS["series"]
        )
        planned = "".join(
            f'<li><span class="redlink" title="Not yet written">{esc(p["title"])}</span></li>'
            for p in PLANNED.values() if p["category"] in ("characters", "familias", "terminology")
        )
        prep = (
            '<h2 class="mp-h2" id="mp-prep-h2">In preparation</h2>\n'
            f'    <div class="mp-box"><p>Linked from existing articles but not written yet:</p><ul class="mp-inline">{planned}</ul></div>'
        ) if planned else ""
        body = f"""
<div id="mp-topbanner">
  <div id="mp-welcome">Welcome to <a href="index.html">{esc(SITE['name'])}</a>,</div>
  <div id="mp-free">{TAGLINE}, built only from the official English releases.</div>
  <div id="mp-stats">{plural(n_articles, 'article')} · 42 English volumes covered · checked {fmt_date(WORKS['verified_on'])}</div>
</div>
<div id="mp-upper">
  <div id="mp-left" class="mp-col">
    <h2 class="mp-h2" id="mp-tfa-h2">Featured article</h2>
    <div class="mp-box">{tfa}</div>
    <h2 class="mp-h2" id="mp-cats-h2">Browse by category</h2>
    <div class="mp-box"><ul class="mp-cats">{cats}</ul>
      <p class="mp-more"><a href="category/index.html">All categories…</a></p></div>
    <h2 class="mp-h2" id="mp-tl-h2">Timeline</h2>
    <div class="mp-box"><p>The <a href="timeline.html">timeline</a> puts the story's events in order, from the ancient past to the
      latest covered volume, and marks whether each one's timing is stated, relative, inferred or unplaced.
      <b><a href="timeline.html">Browse the timeline…</a></b></p></div>
  </div>
  <div id="mp-right" class="mp-col">
    <h2 class="mp-h2" id="mp-cov-h2">What this wiki covers</h2>
    <div class="mp-box"><ul>{coverage}</ul>
      <p>Checked against Yen Press on {fmt_date(WORKS['verified_on'])}. Later volumes are added only after their English release;
        nothing comes from untranslated volumes, fan translations or memory. <a href="sources.html">Sources &amp; coverage…</a></p></div>
    <h2 class="mp-h2" id="mp-read-h2">How to read an article</h2>
    <div class="mp-box">
      <p>Every factual sentence carries a numbered citation naming the volume and chapter. Anything that is not plain narrated fact is labelled:</p>
      <ul>
        <li><span class="badge badge--statement">Character statement</span> something a character says; the narration does not confirm it.</li>
        <li><span class="badge badge--inference">Editorial inference</span> our reading of the evidence, not stated outright.</li>
        <li><span class="badge badge--unresolved">Unresolved</span> the covered volumes do not settle it.</li>
      </ul>
      <p>Facts that change over time, such as Levels and affiliations, are dated to the volume where they apply.</p>
    </div>
    {prep}
  </div>
</div>"""
        content = self.frame("Main Page", body, title_class="mp-title-hidden")
        return self.layout(title="Main Page", root=root, content=content, active="home",
                           tabs='<li class="selected"><a href="">Main Page</a></li>',
                           doc_title=f"{SITE['name']}, {TAGLINE}")

    def timeline_page(self) -> str:
        """Chronology from content/data/timeline.json (imported from the research timeline)."""
        root = ""
        path = CONTENT / "data" / "timeline.json"
        if not path.exists():
            ERRORS.append("content/data/timeline.json is missing; run py tools/import_timeline.py")
            return ""
        events = json.loads(path.read_text(encoding="utf-8"))["events"]
        ids = {e["id"] for e in events}
        timing_label = {"explicit": "Explicit", "relative": "Relative", "inferred": "Inferred", "unplaced": "Unplaced"}

        def who(names):
            out = []
            for n in names:
                art = self.find(n)
                out.append(f'<a class="wikilink" href="{art.url}" data-preview="{esc(art.url)}">{esc(n)}</a>' if art else esc(n))
            return ", ".join(out)

        def refs(ids_):
            return ", ".join(f'<a href="#{esc(i)}">{esc(i)}</a>' for i in ids_ if i in ids) or ""

        def row(e):
            for v in e["volumes"]:
                if work_info(v)[0] is None:
                    ERRORS.append(f"timeline {e['id']}: evidence from {v}, outside the covered English releases")
            notes = []
            for label, key in (("What changes", "changes"), ("Who learns what", "knowledge"), ("Uncertainty", "uncertainty"), ("Narrative frame", "frame")):
                if e[key]:
                    notes.append(f"<dt>{label}</dt><dd>{esc(readable_codes(e[key]))}</dd>")
            if refs(e["after"]):
                notes.append(f"<dt>After</dt><dd>{refs(e['after'])}</dd>")
            if refs(e["during"]):
                notes.append(f"<dt>During</dt><dd>{refs(e['during'])}</dd>")
            details = f'<details class="tl-notes"><summary>Notes</summary><dl>{"".join(notes)}</dl></details>' if notes else ""
            series = " ".join(sorted({v[:2] for v in e["volumes"]}))
            text = readable_codes(" ".join([e["title"], e["arc"], e["when"], " ".join(e["participants"])])).lower()
            return (
                f'<tr id="{esc(e["id"])}" data-timing="{esc(e["timing"])}" data-series="{series}" data-text="{esc(text)}">'
                f'<td class="tl-id">{esc(e["id"])}</td>'
                f'<td><b>{esc(readable_codes(e["title"]))}</b>'
                f'{f"<div class=tl-who>{who(e['participants'])}</div>" if e["participants"] else ""}{details}</td>'
                f'<td class="tl-arc">{esc(e["arc"])}</td>'
                f'<td>{esc(readable_codes(e["when"]))}</td>'
                f'<td><span class="tl-timing tl-timing--{esc(e["timing"])}">{timing_label.get(e["timing"], e["timing"])}</span></td>'
                f'<td class="tl-src">{esc(works_compact(e["volumes"])) or "—"}</td></tr>'
            )

        def table(rows, tid):
            return (f'<div class="table-wrap"><table class="wikitable tl-table" id="{tid}"><thead><tr><th scope="col">ID</th><th scope="col">Event</th>'
                    f'<th scope="col">Arc</th><th scope="col">When</th><th scope="col">Timing</th><th scope="col">Sources</th></tr></thead>'
                    f'<tbody>{"".join(rows)}</tbody></table></div>')

        history = [e for e in events if e["arc"].startswith("Histor")]
        story = [e for e in events if not e["arc"].startswith("Histor")]
        counts = {k: sum(1 for e in events if e["timing"] == k) for k in timing_label}
        series_boxes = "".join(
            f'<label><input type="checkbox" name="series" value="{s["id"]}" checked> {esc(s["short"])}</label>' for s in WORKS["series"]
        )
        timing_boxes = "".join(
            f'<label><input type="checkbox" name="timing" value="{k}" checked> <span class="tl-timing tl-timing--{k}">{v}</span> ({counts[k]})</label>'
            for k, v in timing_label.items()
        )
        art = Article.__new__(Article)
        art.toc = [(2, "how-to-read", "How to read the timeline"), (2, "history", "Historical background"), (2, "main-story", "Main story and side stories")]
        body = f"""
<p>This timeline orders {len(events)} events from the covered English volumes. Most of the story has no calendar, so the
timeline mainly records <b>order</b>, not dates. Each event is labelled with how firmly it is placed, and cites the volumes it
comes from.</p>
{self.toc_html(art)}
{heading(2, "how-to-read", "How to read the timeline")}
<ul>
  <li><span class="tl-timing tl-timing--explicit">Explicit</span> The text states the timing directly.</li>
  <li><span class="tl-timing tl-timing--relative">Relative</span> Placed relative to another event, such as "the morning after" or "two days before".</li>
  <li><span class="tl-timing tl-timing--inferred">Inferred</span> Placed by a stated line of reasoning from the evidence.</li>
  <li><span class="tl-timing tl-timing--unplaced">Unplaced</span> No finer placement can be defended; the position shown is approximate.</li>
</ul>
<p>None of these labels promises an absolute date. <b>Day 0</b> is the day Aiz rescues Bell from a Minotaur
(<a href="#TL-0001">TL-0001</a>). It is a reference point chosen for this timeline, not a date in the books, and no running
day count is inferred from it. Where a book gives its own local count, such as the six days of Familia Chronicle 3's
Zolingam visit, that count stays local.</p>
<p>The order of rows is a reading aid. Two neighbouring rows did not necessarily happen one after the other, and an event
listed later may have been <i>told</i> later rather than happened later. Each event's notes separate what happens from who
learns about it: nobody is assumed to know something just because they were nearby, and readers often learn things
before the characters do.</p>
<p>Sources name the volumes only; chapter locations for these events have not been recorded yet. The IDs (TL-0001 and
so on) are stable and will not be renumbered.</p>
<form class="tl-filter" id="tl-filter" onsubmit="return false">
  <fieldset><legend>Timing</legend>{timing_boxes}</fieldset>
  <fieldset><legend>Series</legend>{series_boxes}</fieldset>
  <div class="tl-filter__text"><input type="search" id="tl-text" placeholder="Filter by name, arc or event" aria-label="Filter events">
  <span id="tl-count" aria-live="polite">{len(events)} events shown</span></div>
</form>
{heading(2, "history", "Historical background")}
<p>Events before the main story, from the ancient past to the years just before Bell arrives in Orario, many of them told
in flashbacks, side stories and Astrea Record.</p>
{table([row(e) for e in history], "tl-history")}
{heading(2, "main-story", "Main story and side stories")}
<p>From the days before Bell's arrival to the end of the covered volumes, with Sword Oratoria, Familia Chronicle and the
short stories placed alongside the main series where the evidence allows.</p>
{table([row(e) for e in story], "tl-story")}
"""
        content = self.frame("Timeline", body)
        self.search_docs.append({
            "t": "Timeline", "u": "timeline.html", "c": "Help",
            "s": f"Order of {len(events)} events across the covered English volumes, with how firmly each is placed.",
            "a": ["Chronology"], "h": ["How to read the timeline", "Historical background", "Main story and side stories"],
            "x": " ".join(e["title"] for e in events),
        })
        return self.layout(title="Timeline", root=root, content=content, active="timeline",
                           tabs='<li class="selected"><a href="">Project page</a></li>',
                           description="A source-backed DanMachi timeline that separates stated timing from approximate order.")

    def search_page(self) -> str:
        root = ""
        body = f"""
<form class="mw-search-form" role="search" action="search.html">
  <input id="search-page-input" type="search" name="q" placeholder="Search {esc(SITE['name'])}" aria-label="Search {esc(SITE['name'])}">
  <button class="mw-button" type="submit">Search</button>
</form>
<p class="search-page__status" aria-live="polite"></p>
<ul class="search-results" id="search-results"></ul>
<noscript><p>Search needs JavaScript. Browse <a href="category/index.html">all categories</a> instead.</p></noscript>"""
        content = self.frame("Search results", body)
        return self.layout(title="Search results", root=root, content=content, active="search",
                           tabs='<li class="selected"><a href="">Special page</a></li>')

    def not_found(self) -> str:
        body = ('<p>There is no page at this address, or it has not been written yet. Try the <a href="search.html">search</a> '
                'or browse <a href="category/index.html">all categories</a>.</p>')
        return self.layout(title="Page not found", root="", content=self.frame("Page not found", body))


# --------------------------------------------------------------------------- build

LEAK_PATTERNS = [
    (re.compile(r"[぀-ヿ㐀-䶿一-鿿ｦ-ﾟ]"), "Japanese/CJK text"),
    (re.compile(r"\b(?:FM2[1-9]|SO1[5-9]|FC0[4-9]|AR0[4-9]|SS0[3-9])\b"), "identifier of a volume not covered in English"),
    (re.compile(r"\bVol(?:ume|\.)\s*2[1-9]\b"), "main-series volume beyond the English boundary"),
    (re.compile(r"[A-Za-z]:\\\\?Users|/Users/|Desktop\\\\|Danmachi Raw Text|fulltext\.txt", re.I), "local source path"),
]


def leak_check():
    for path in DIST.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(DIST).as_posix()
        if path.suffix.lower() in {".txt", ".epub", ".pdf", ".mobi", ".azw3"}:
            ERRORS.append(f"public output contains a source-format file: {rel}")
            continue
        if path.suffix.lower() not in {".html", ".js", ".css", ".json", ".svg", ".xml"}:
            continue
        text = path.read_text(encoding="utf-8")
        # Japanese is allowed only inside the {{ja|…}} spans (names and chants); everywhere else it still fails.
        if path.suffix.lower() == ".html":
            text = JA_SPAN.sub("", text)
        for pat, what in LEAK_PATTERNS:
            m = pat.search(text)
            if m:
                ERRORS.append(f"leak check: {what} in {rel}: {text[max(0, m.start() - 40):m.end() + 40]!r}")


REDIRECT = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} – {site}</title><meta name="robots" content="noindex">
<meta http-equiv="refresh" content="0; url={target}"><link rel="canonical" href="{target}">
<link rel="stylesheet" href="../assets/classic.css"></head>
<body><p style="margin:2em">“{title}” is now a section of the <a href="{target}">{page}</a> page.</p></body></html>
"""


CATEGORY_REDIRECT = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} – {site}</title><meta name="robots" content="noindex">
<meta http-equiv="refresh" content="0; url={target}"><link rel="canonical" href="{target}">
<link rel="stylesheet" href="../assets/classic.css"></head>
<body><p style="margin:2em">The “{title}” category has a single page, <a href="{target}">{page}</a>.</p></body></html>
"""


def write(rel: str, text: str):
    path = DIST / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build():
    if DIST.exists():
        shutil.rmtree(DIST)
    (DIST / "assets").mkdir(parents=True)
    for f in STATIC.iterdir():
        shutil.copy2(f, DIST / "assets" / f.name)
    if MEDIA_DIR.exists():
        (DIST / "media").mkdir()
        for f in MEDIA_DIR.glob("*.webp"):
            shutil.copy2(f, DIST / "media" / f.name)

    site = Site()
    for art in site.all:
        if art.category not in CATEGORIES:
            ERRORS.append(f"{art.slug}: unknown category {art.category!r}")
            continue
        write(art.url, site.article_page(art))
    for page in site.pages:
        write(page.url, site.plain_page(page, active=page.slug))
    for cat in SITE["categories"]:
        members = site.in_category(cat["id"])
        if len(members) == 1:
            # A category with a single page redirects straight to that page (Magic, Skills, Development Abilities).
            only = members[0]
            write(f"category/{cat['id']}.html", CATEGORY_REDIRECT.format(
                title=esc(cat["title"]), page=esc(only.title), target=esc(f"../{only.url}"), site=esc(SITE["name"])))
            continue
        write(f"category/{cat['id']}.html", site.category_page(cat))
    # Former article URLs that now live as a section of another page ("former_slug" in front-matter sections).
    for art in site.articles:
        for sec in art.meta.get("sections", []):
            old = sec.get("former_slug")
            if not old:
                continue
            if old in site.by_slug:
                ERRORS.append(f"{art.slug}: redirect wiki/{old}.html would overwrite an existing page")
                continue
            target = f"{art.slug}.html#{sec['anchor']}"
            write(f"wiki/{old}.html", REDIRECT.format(title=esc(sec["title"]), page=esc(art.title), target=esc(target), site=esc(SITE["name"])))
    write("category/index.html", site.categories_index())
    write("index.html", site.home())
    write("timeline.html", site.timeline_page())
    write("search.html", site.search_page())
    write("404.html", site.not_found())
    index_js = "window.OL_INDEX=" + json.dumps(site.search_docs, ensure_ascii=False, separators=(",", ":")) + ";\n"
    write("assets/search-index.js", index_js)
    leak_check()

    n_pages = sum(1 for _ in DIST.rglob("*.html"))
    print(f"Built {n_pages} pages into {DIST.relative_to(WIKI)}/ ({len(site.articles)} articles, {len(site.series_pages)} publication pages)")
    for w in WARNINGS:
        print("warning:", w)
    for e in ERRORS:
        print("ERROR:", e)
    return 1 if ERRORS else 0


def serve(port: int = 8770):
    import functools
    import http.server
    import socketserver

    class Handler(http.server.SimpleHTTPRequestHandler):
        def end_headers(self):
            self.send_header("Cache-Control", "no-store")  # always show the latest build
            super().end_headers()

        def log_message(self, fmt, *args):
            pass

    handler = functools.partial(Handler, directory=str(DIST))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("127.0.0.1", port), handler) as httpd:
        print(f"Orario Ledger preview at http://127.0.0.1:{port}/  (Ctrl+C to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    status = build()
    if "--serve" in sys.argv:
        serve()
    sys.exit(status)
