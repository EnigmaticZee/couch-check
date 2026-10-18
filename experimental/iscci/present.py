"""How ISCCI scores look in Stremio (chip, source row, in-app breakdown) and on the web page."""
from __future__ import annotations

import textwrap
from html import escape
from typing import List, Optional

from pg import pages
from pg import rubric as base_rubric

from . import rubric
from .service import is_ai

ROW_WIDTH = 28
IN_APP_PREFIX = "si:"  # the in-app ISCCI breakdown is a virtual movie with this ID prefix


def source(entry: dict) -> str:
    return "AI" if is_ai(entry) else ""  # keyword scores carry no label


def chip(result: rubric.Result, entry: dict, label: str = "") -> str:
    """e.g. "🔴 7/10 · Topless ●●○ · AI"; label (e.g. "ISCCI") goes first when both rubrics show."""
    lead = f"{label} " if label else ""
    if result.level is None:
        return f"{lead}❔ No scene details"
    text = f"{lead}{rubric.emoji(result.level)} {result.shown} · {result.tag} {result.pips}" + (
        f" · {source(entry)}" if source(entry) else "")
    return text + (" · ⚠️" if result.warnings else "")


def short(result: rubric.Result) -> str:
    """e.g. "🟢 1/10 · Flirting", for episode lines."""
    if result.level is None:
        return ""
    return f"{rubric.emoji(result.level)} {result.shown} · {result.tag}"


def stream_row(result: rubric.Result, entry: dict, url: str, scope: str, label: str = "") -> dict:
    """scope: "Show-wide", "This episode" or "" for movies."""
    if result.level is None:
        return {"name": f"❔ {label or 'Couch Check'}".strip(), "description": "No scene details on IMDb",
                "externalUrl": url}
    lines = [result.tag + (" · AI score" if is_ai(entry) else ""),
             f"{rubric.band_name(result.level)} · {result.presence} {result.pips}"]
    if result.scenes:
        lines.append(f"{result.scenes} scene{'s' if result.scenes != 1 else ''} at 4+")
    if result.star:
        lines.append("* non-sexual nudity")
    if result.warnings:
        lines.append(f"⚠️ {base_rubric.WARNINGS[result.warnings[0]]}")
    extras = [x for x in (scope, "Low confidence" if result.low_confidence else "") if x]
    if extras:
        lines.append(" · ".join(extras))
    if label:
        lines.append(f"{label} rubric")
    lines.append("Tap: full list in browser ↗")
    return {"name": f"{rubric.emoji(result.level)} {result.shown}", "description": "\n".join(lines), "externalUrl": url}


def breakdown_meta(imdb_id: str, entry: Optional[dict], result: Optional[rubric.Result], items: list, art: dict,
                   title: str, is_series: bool, web: str, show_text: bool) -> dict:
    meta = {
        "id": f"{IN_APP_PREFIX}{imdb_id}", "type": "movie", "name": f"{title} · ISCCI",
        "poster": art.get("poster"), "background": art.get("background"), "logo": art.get("logo"),
        "behaviorHints": {"defaultVideoId": f"{IN_APP_PREFIX}{imdb_id}"},
    }
    links = [{"name": "Open full page", "category": "Couch Check", "url": web}]
    if entry is None:
        meta["description"] = "Scoring… back out and reopen in a few seconds."
    elif entry["guide"].get("missing"):
        meta["description"] = "IMDb has no parents guide for this title."
    elif result.level is None:
        meta["releaseInfo"] = "❔ No scene details"
        meta["description"] = "IMDb has no flagged Sex & Nudity items for this title yet."
    else:
        meta["releaseInfo"] = (f"{rubric.emoji(result.level)} {result.shown} · {rubric.band_name(result.level)} · "
                               f"{rubric.name(result.level)} · {source(entry)}")
        summary = entry.get("summary") if is_ai(entry) else None
        facts = [f"worst sexual scene: {result.peak_tag.lower()} ({result.peak})",
                 f"{result.presence.lower()} {result.pips}" + (f", +{result.extra}" if result.extra else "")]
        if result.star:
            facts.append(f"highest level is non-sexual ({result.tag.lower()})*")
        if is_series:
            facts.append("show-wide")
        facts.append("scored by AI" if is_ai(entry) else "keyword estimate")
        meta["description"] = (f"{summary or rubric.auto_summary(result)} "
                               f"{'; '.join(facts).capitalize()}. "
                               + ("Every flagged scene is listed on the right. " if show_text
                                  else "Scene counts are on the right; the descriptions are on IMDb. ")
                               + rubric.NOTICE)
    links.append({"name": f"Back to {title}", "category": "Couch Check",
                  "url": f"stremio:///detail/{'series' if is_series else 'movie'}/{imdb_id}"})
    meta["links"] = links
    return meta


def breakdown_rows(imdb_id: str, entry: dict, result: rubric.Result, items: list, web: str, show_text: bool) -> list:
    summary = (entry.get("summary") if is_ai(entry) else None) or rubric.auto_summary(result)
    rows = [{"name": "📝\nIn short", "description": "\n".join(textwrap.wrap(summary, ROW_WIDTH)), "externalUrl": web}]
    if not show_text:
        for level in sorted({i.level for i in items if not i.warning}, reverse=True):
            group = [i for i in items if i.level == level and not i.warning]
            rows.append({"name": f"{rubric.emoji(level)} {level}\n{rubric.name(level)}",
                         "description": f"{len(group)} item{'s' if len(group) != 1 else ''}\nRead them on IMDb ↗",
                         "externalUrl": pages.imdb_url(imdb_id)})
        return rows
    for i in sorted(items, key=lambda i: (not i.warning, -i.level, not i.is_scene)):
        if i.warning:
            name = "⚠️\nWarning"
        else:
            name = f"{rubric.emoji(i.level)} {i.level}{'*' if i.nonsexual else ''}\n{i.tag}"
        tags = [t for t, on in (("Non-sexual: " + rubric.NONSEXUAL.get(i.nonsexual, ""), bool(i.nonsexual)),
                                ("Prolonged", i.prolonged), ("Momentary", i.momentary),
                                ("Several times", i.amount == "several"), ("Throughout", i.amount == "throughout"),
                                ("Overview", not i.is_scene), ("Spoiler", i.is_spoiler)) if on]
        rows.append({"name": name, "externalUrl": web,
                     "description": "\n".join(textwrap.wrap(i.text, ROW_WIDTH) + ([" · ".join(tags)] if tags else []))})
    return rows


PAGE_CSS = """
.lv-purple{background:#8b3fb8}
.iscci-hero .badge{width:84px}
.band{display:inline-block;font-size:.75rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;border-radius:999px;
padding:3px 10px;color:#fff;margin-right:6px}
.grid{display:grid;grid-template-columns:62px repeat(5,minmax(0,1fr));gap:4px;font-size:.78rem;margin-top:8px}
.grid div{height:28px;border-radius:6px;display:flex;align-items:center;justify-content:center;background:var(--line);color:var(--muted)}
.grid .h{background:none}.grid .on{color:#fff;font-weight:800}
.grid .on.lv-green{background:var(--green)}.grid .on.lv-yellow{background:var(--yellow)}.grid .on.lv-orange{background:var(--orange)}
.grid .on.lv-red{background:var(--red)}.grid .on.lv-purple{background:#8b3fb8}
.nsbox{background:rgba(47,127,184,.12);color:#2f7fb8;border-radius:10px;padding:8px 10px;font-size:.88rem;margin-top:6px}
@media (prefers-color-scheme:dark){.nsbox{color:#6fb3e6}}
.exp{border-color:#8b3fb8}
@media (max-width:520px){.grid{grid-template-columns:44px repeat(5,minmax(0,1fr));font-size:.6rem}}
"""


def page(imdb_id: str, entry: dict, result: rubric.Result, items: List[rubric.ScoredItem], is_series: bool,
         background: Optional[str], show_text: bool) -> str:
    guide = entry["guide"]
    year = f" ({guide['year']})" if guide.get("year") else ""
    lvl = result.level
    col = rubric.colour(lvl)
    head = (f'<span class="band lv-{col}">{escape(rubric.band_name(lvl))}</span>' if lvl is not None else "")
    if lvl is None:
        hero_name, hero_desc = "No scene details", "IMDb has no flagged Sex & Nudity items for this title yet."
    else:
        hero_name = f"{rubric.name(lvl)} · {result.tag}"
        hero_desc = (f"Worst sexual scene: {result.peak_tag} ({result.peak}) · {result.presence} {result.pips}"
                     + (f" · +{result.extra}" if result.extra else ""))
    pills = ['<span class="pill accent">' + ("AI score" if is_ai(entry) else "Keyword estimate") + "</span>",
             '<span class="pill">ISCCI · experimental</span>']
    if result.scenes:
        pills.append(f'<span class="pill">{result.scenes} sexual scene{"s" if result.scenes != 1 else ""} at 4+</span>')
    if result.star:
        pills.append('<span class="pill">* non-sexual nudity</span>')
    if is_series:
        pills.append('<span class="pill">Show-wide</span>')
    if result.low_confidence:
        pills.append('<span class="pill">Low confidence</span>')
    art = f'<div class="bgimg" style="background-image:url(\'{escape(background)}\')"></div>' if background else ""
    hero = f"""<div class="card hero iscci-hero{" art" if background else ""}">{art}<div class="inner">
<h1>{escape(guide["title"])}{escape(year)}</h1><p class="sub">Sex &amp; nudity · ISCCI rubric · <a href="{escape(pages.imdb_url(imdb_id))}">IMDb parents guide</a></p>
<div class="score"><div class="badge lv-{col}">{escape(result.shown.split("/")[0])}<small>/10</small></div>
<div>{head}<h2 style="margin-top:6px">{escape(hero_name)}</h2><p>{escape(hero_desc)}</p></div></div>
<div class="pills" style="margin-top:16px">{"".join(pills)}</div></div></div>"""

    summary = (entry.get("summary") if is_ai(entry) else None) or rubric.auto_summary(result)
    summary_html = (f'<div class="card summary"><h3>In short</h3><p>{escape(summary)}</p><p class="src">'
                    f'{"Summary by AI" if is_ai(entry) and entry.get("summary") else "Summary from the scores"}</p></div>')

    grid = ""
    if lvl is not None and result.peak:
        cols = ["Once", "A few", "Frequent", "Prolonged", "Pervasive"]
        active = "Pervasive" if result.pervasive else "Prolonged" if result.prolonged else "Frequent" if result.frequent else "Once"
        cells = '<div class="h"></div>' + "".join(f'<div class="h">{c}</div>' for c in cols)
        for p in range(min(10, result.peak + 1), max(-1, result.peak - 3), -1):
            cells += f'<div class="h">Peak {p}</div>'
            for c in cols:
                on = p == result.peak and c == active
                cells += (f'<div class="on lv-{rubric.colour(result.peak + result.extra)}">{result.peak + result.extra}</div>'
                          if on else "<div></div>")
        grid = (f'<div class="card"><h3>Worst scene against how much</h3><div class="grid">{cells}</div>'
                f'<p class="muted" style="margin:10px 0 0">Peak {result.peak} + {result.extra} extra = '
                f'{result.peak + result.extra}. Extra points: frequent +1, prolonged +1, pervasive +2, at most +2.</p></div>')

    def item_html(i: rubric.ScoredItem) -> str:
        if i.warning:
            chip_html = f'<span class="chip lv-warn">⚠️</span>'
        else:
            chip_html = f'<span class="chip lv-{rubric.colour(i.level)}">{i.level}{"*" if i.nonsexual else ""}</span>'
        tags = [t for t, on in (("prolonged", i.prolonged), ("momentary", i.momentary),
                                ("several times", i.amount == "several"), ("throughout", i.amount == "throughout"),
                                ("overview", not i.is_scene), ("spoiler", i.is_spoiler)) if on]
        tag_html = f'<div class="tags">{"".join(f"<span class=tag>{t}</span>" for t in tags)}</div>' if tags else ""
        text = escape(i.text) if show_text else ""
        if show_text and i.is_spoiler:
            text = f'<details class="spoiler"><summary>Spoiler: tap to show</summary>{text}</details>'
        ns = (f'<div class="nsbox">ℹ️ <b>Non-sexual · {escape(rubric.NONSEXUAL.get(i.nonsexual, ""))}.</b> '
              f'Counted at its body-part floor and marked *. Adds no extra points.</div>') if i.nonsexual else ""
        return f"<li>{chip_html}<div><b>{escape(i.tag)}</b>{'<br>' + text if text else ''}{ns}{tag_html}</div></li>"

    ordered = sorted(items, key=lambda i: (not i.warning, -i.level, not i.is_scene))
    items_html = ""
    if items:
        note = "" if show_text else (f'<p class="muted" style="margin:14px 0 0">The scene descriptions are on '
                                     f'<a href="{escape(pages.imdb_url(imdb_id))}">IMDb ↗</a>.</p>')
        items_html = (f'<div class="card"><h3>Flagged content · {len(items)}</h3><ul class="items">'
                      f'{"".join(item_html(i) for i in ordered)}</ul>{note}</div>')

    ladder = "".join(f'<tr><td><span class="chip lv-{rubric.colour(l)}">{l}</span></td><td><b>{escape(n)}</b><br>{escape(d)}</td></tr>'
                     for l, n, d in rubric.LADDER)
    rules = "".join(f"<li>{escape(r)}</li>" for r in rubric.RULES)
    ladder_html = (f'<div class="card"><details class="ladder"><summary>What the ISCCI levels mean</summary><table>{ladder}</table>'
                   f'<p style="margin:16px 0 4px"><b>Rules</b></p><ul class="rules">{rules}</ul></details></div>')
    v51 = f"/guide/{imdb_id}" + ("?series=1" if is_series else "")
    body = (f'{hero}{summary_html}{grid}{items_html}{ladder_html}'
            f'<div class="card exp"><p style="margin:0">{escape(rubric.NOTICE)} <a href="{escape(v51)}">See the main score</a>.</p></div>'
            f'<p class="foot">Scene descriptions from IMDb\'s parents guide. Levels by Couch Check (ISCCI).</p>')
    return pages._doc(f"{guide['title']} · ISCCI · Couch Check", body, extra_css=pages.HERO_CSS + PAGE_CSS)
