"""HTML for the breakdown page and the landing page."""
from __future__ import annotations

import json
from html import escape
from typing import List, Optional
from urllib.parse import quote

from . import classify, rubric

CSS = """
:root{--bg:#f5f3fa;--card:#fff;--text:#1c1830;--muted:#6a6580;--line:#e6e2f0;--accent:#5b3ba8;--accent-soft:#eee8fb;
--cream:#f6cf98;--green:#2f9a5a;--yellow:#b88a00;--orange:#d0661c;--red:#c43b3b;--grey:#8a8698}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#141122;--card:#1d1930;--text:#eeebf7;
--muted:#a6a0bd;--line:#2f2a47;--accent:#a58bff;--accent-soft:#2a2347;--green:#46b874;--yellow:#e0b232;
--orange:#ef8c48;--red:#ee6a6a;--grey:#8e89a3}}
:root[data-theme="dark"]{--bg:#141122;--card:#1d1930;--text:#eeebf7;--muted:#a6a0bd;--line:#2f2a47;--accent:#a58bff;
--accent-soft:#2a2347;--green:#46b874;--yellow:#e0b232;--orange:#ef8c48;--red:#ee6a6a;--grey:#8e89a3}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
-webkit-font-smoothing:antialiased}
main{max-width:760px;margin:0 auto;padding:20px 16px 56px}
a{color:var(--accent)}
.brand{display:flex;align-items:center;gap:10px;margin-bottom:18px;font-weight:700;color:var(--text);text-decoration:none}
.brand img{width:30px;height:30px;border-radius:8px}
.brand span{font-size:.95rem;letter-spacing:.01em}
h1{font-size:1.6rem;line-height:1.2;margin:0}
h2{font-size:1.15rem;margin:0}
h3{font-size:.8rem;margin:0 0 12px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
.sub{color:var(--muted);margin:4px 0 0;font-size:.95rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:20px;margin-bottom:16px}
.lv-green{background:var(--green)}.lv-yellow{background:var(--yellow)}.lv-orange{background:var(--orange)}
.lv-red{background:var(--red)}.lv-grey{background:var(--grey)}
.chip{flex:none;width:34px;height:34px;border-radius:10px;display:inline-flex;align-items:center;justify-content:center;
font-weight:700;color:#fff;font-size:.95rem}
.pills{display:flex;flex-wrap:wrap;gap:8px}
.pill{border:1px solid var(--line);border-radius:999px;padding:3px 11px;font-size:.85rem;color:var(--muted)}
.pill.accent{background:var(--accent-soft);border-color:transparent;color:var(--accent);font-weight:600}
.scale{display:grid;grid-template-columns:repeat(11,1fr);gap:4px;margin:16px 0 6px}
.scale span{height:8px;border-radius:4px;opacity:.28}
.scale span.on{opacity:1;height:14px;margin-top:-3px;box-shadow:0 0 0 3px var(--card),0 0 0 5px currentColor}
.scale-labels{display:flex;justify-content:space-between;font-size:.75rem;color:var(--muted)}
.bar{display:flex;height:10px;border-radius:5px;overflow:hidden;margin:4px 0 8px;background:var(--line)}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:.85rem;color:var(--muted)}
.legend i{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:5px}
ul.items{list-style:none;margin:0;padding:0}
ul.items li{display:flex;gap:14px;padding:13px 0;border-top:1px solid var(--line)}
ul.items li:first-child{border-top:0}
.group{font-weight:600;font-size:.9rem;margin:18px 0 2px;display:flex;align-items:center;gap:8px}
.group:first-of-type{margin-top:0}
.group small{color:var(--muted);font-weight:400}
.tags{display:flex;gap:6px;margin-top:6px}
.tag{font-size:.72rem;color:var(--muted);border:1px solid var(--line);border-radius:6px;padding:0 7px}
details.spoiler summary{cursor:pointer;color:var(--muted);font-style:italic;list-style:none}
details.spoiler summary::-webkit-details-marker{display:none}
details.ladder summary{cursor:pointer;font-weight:600}
ul.rules{margin:0;padding-left:20px;font-size:.9rem;color:var(--muted)}ul.rules li{margin:6px 0}
table{width:100%;border-collapse:collapse;margin-top:12px;font-size:.92rem}
td{padding:8px 4px;border-top:1px solid var(--line);vertical-align:top}td:first-child{width:44px}
.btn{display:inline-block;background:var(--accent);color:#fff;text-decoration:none;padding:12px 18px;border-radius:12px;
font-weight:600;border:0;font:inherit;font-weight:600;cursor:pointer}
.btn2{display:inline-block;border:1px solid var(--line);color:var(--text);text-decoration:none;padding:11px 16px;
border-radius:12px;font-weight:600;background:transparent;font:inherit;font-weight:600;cursor:pointer}
code{background:var(--accent-soft);padding:2px 6px;border-radius:6px;font-size:.88em;word-break:break-all}
.foot{color:var(--muted);font-size:.82rem;text-align:center;margin-top:28px}
.muted{color:var(--muted);font-size:.9rem}
.lv-warn{background:#b8326f}
.warning{border-color:#b8326f;background:rgba(184,50,111,.10)}.warning h3{color:#d9518f}.warning p{margin:0}
.back{display:inline-block;margin:0 0 14px;padding:8px 14px;border-radius:10px;background:var(--accent-soft);
color:var(--accent);font-weight:600;text-decoration:none;font-size:.92rem}
"""


def color(level: Optional[int]) -> str:
    if level is None:
        return "grey"
    return {"🟢": "green", "🟡": "yellow", "🟠": "orange", "🔴": "red"}[rubric.emoji(level)]


def _doc(title: str, body: str, refresh: bool = False, extra_css: str = "") -> str:
    meta = '<meta http-equiv="refresh" content="3">' if refresh else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">{meta}
<link rel="icon" href="/static/logo.png"><title>{escape(title)}</title><style>{CSS}{extra_css}</style></head>
<body><main><a class="brand" href="/configure"><img src="/static/logo.png" alt=""><span>Couch Check</span></a>
{body}</main></body></html>"""


def _ladder_table() -> str:
    rows = "".join(
        f'<tr><td><span class="chip lv-{color(l)}">{l}</span></td><td><b>{escape(n)}</b><br>{escape(d)}</td></tr>'
        for l, n, d in rubric.LADDER
    )
    rules = "".join(f"<li>{escape(r)}</li>" for r in rubric.RULES)
    return (f'<details class="ladder"><summary>What the levels mean</summary><table>{rows}</table>'
            f'<p style="margin:16px 0 4px"><b>How edge cases are scored</b></p><ul class="rules">{rules}</ul></details>')


def _scale(level: Optional[int]) -> str:
    """The 0–10 ladder as a coloured strip, with this title's level highlighted."""
    cells = "".join(
        f'<span class="lv-{color(l)}{" on" if l == level else ""}" style="color:var(--{color(l)})" title="{l} · {escape(n)}"></span>'
        for l, n, _ in rubric.LADDER
    )
    return f'<div class="scale">{cells}</div><div class="scale-labels"><span>0 · None</span><span>10 · Graphic</span></div>'


def imdb_url(imdb_id: str) -> str:
    return f"https://www.imdb.com/title/{imdb_id}/parentalguide/#nudity"


def _imdb_link(imdb_id: str) -> str:
    return f'<a href="{escape(imdb_url(imdb_id))}">IMDb parents guide</a>'


def pending(imdb_id: str) -> str:
    body = f"""<div class="card"><h1>Checking IMDb…</h1><p class="sub">Looking up <code>{escape(imdb_id)}</code>.
This page refreshes on its own.</p></div>"""
    return _doc("Checking IMDb…", body, refresh=True)


def error(imdb_id: str, message: str) -> str:
    body = f"""<div class="card"><h1>Couldn't load this title</h1><p class="sub">{escape(message)}</p>
<p>{_imdb_link(imdb_id)}</p></div>"""
    return _doc("Couldn't load", body)


def missing(imdb_id: str) -> str:
    body = f"""<div class="card"><h1>Not on IMDb</h1>
<p class="sub">IMDb has no parents guide for <code>{escape(imdb_id)}</code>.</p></div>"""
    return _doc("Not on IMDb", body)


HERO_CSS = """
.hero{position:relative;overflow:hidden;padding:0}
.hero .bgimg{position:absolute;inset:0;background-size:cover;background-position:center 25%}
.hero .bgimg::after{content:"";position:absolute;inset:0;background:linear-gradient(180deg,rgba(12,9,26,.45),rgba(12,9,26,.92))}
.hero .inner{position:relative;padding:22px}
.hero.art .inner{color:#fff;padding-top:120px}
.hero.art .sub,.hero.art .scale-labels{color:rgba(255,255,255,.75)}
.hero.art a{color:#fff}
.hero.art .pill{color:rgba(255,255,255,.85);border-color:rgba(255,255,255,.25)}
.hero.art .pill.accent{background:rgba(165,139,255,.25);color:#fff}
.hero.art .scale span.on{box-shadow:0 0 0 3px #1a1530,0 0 0 5px currentColor}
.score{display:flex;gap:16px;align-items:center;margin:16px 0 4px}
.badge{flex:none;width:76px;height:76px;border-radius:20px;display:flex;align-items:center;justify-content:center;
font-size:2.2rem;font-weight:800;color:#fff}
.badge small{font-size:.9rem;font-weight:600;opacity:.8;margin-left:2px;margin-top:14px}
.score p{margin:2px 0 0;font-size:.95rem;opacity:.85}
.summary p{margin:0;font-size:1.05rem;line-height:1.6}
.summary p.src{margin-top:10px;font-size:.78rem;color:var(--muted)}
"""


def breakdown(imdb_id: str, entry: dict, result: rubric.Result, items: List[rubric.ScoredItem], is_series: bool,
              background: Optional[str] = None, show_text: bool = True) -> str:
    """show_text=False lists scene counts per level and links to IMDb instead of showing its descriptions."""
    guide = entry["guide"]
    year = f" ({guide['year']})" if guide.get("year") else ""
    lvl = result.level
    if lvl is None:
        hero_name, hero_desc = "No scene details", "IMDb has no flagged Sex & Nudity items for this title yet."
    else:
        hero_name, hero_desc = rubric.name(lvl), rubric.meaning(lvl)

    pills = ['<span class="pill accent">' + ("AI score" if classify.is_ai(entry["classifier"]) else "Keyword estimate") + "</span>"]
    if lvl is not None:
        pills.append(f'<span class="pill">{result.scenes} scene{"s" if result.scenes != 1 else ""} at level {rubric.SCENE_THRESHOLD}+</span>')
        if result.prolonged:
            pills.append(f'<span class="pill">{result.prolonged} prolonged</span>')
    if is_series:
        pills.append('<span class="pill">Show-wide</span>')
    if result.low_confidence:
        pills.append('<span class="pill">Low confidence</span>')

    art = f'<div class="bgimg" style="background-image:url(\'{escape(background)}\')"></div>' if background else ""
    hero = f"""<div class="card hero{" art" if background else ""}">{art}<div class="inner">
<h1>{escape(guide["title"])}{escape(year)}</h1><p class="sub">Sex &amp; nudity · {_imdb_link(imdb_id)}</p>
<div class="score"><div class="badge lv-{color(lvl)}">{lvl if lvl is not None else "?"}<small>/10</small></div>
<div><h2>{escape(hero_name)}</h2><p>{escape(hero_desc)}</p></div></div>
{_scale(lvl) if lvl is not None else ""}<div class="pills" style="margin-top:16px">{"".join(pills)}</div></div></div>"""

    def item_html(i: rubric.ScoredItem) -> str:
        tags = [t for t, on in (("prolonged", i.prolonged), ("overview", not i.is_scene), ("spoiler", i.is_spoiler)) if on]
        tag_html = f'<div class="tags">{"".join(f"<span class=tag>{t}</span>" for t in tags)}</div>' if tags else ""
        text = escape(i.text)
        if i.is_spoiler:
            text = f'<details class="spoiler"><summary>Spoiler: tap to show</summary>{text}</details>'
        if i.warning:
            chip = f'<span class="chip lv-warn" title="{escape(rubric.WARNINGS[i.warning])}">⚠️</span>'
        else:
            chip = f'<span class="chip lv-{color(i.level)}" title="{escape(rubric.name(i.level))}">{i.level}</span>'
        return f"<li>{chip}<div>{text}{tag_html}</div></li>"

    warned = [i for i in items if i.warning]
    scored = [i for i in items if not i.warning]

    if items and not show_text:
        lines = []
        if warned:
            lines.append(f'<li><span class="chip lv-warn">⚠️</span><div><b>Warning</b><br><span class="muted">'
                         f'{escape(rubric.warning_text(result))} · {len(warned)} item{"s" if len(warned) != 1 else ""}</span></div></li>')
        for level in sorted({i.level for i in scored}, reverse=True):
            in_level = [i for i in scored if i.level == level]
            scenes = sum(1 for i in in_level if i.is_scene)
            prolonged = sum(1 for i in in_level if i.prolonged)
            count = f"{scenes} scene{'s' if scenes != 1 else ''}" if scenes else "general comments"
            extra = f" · {prolonged} prolonged" if prolonged else ""
            lines.append(f'<li><span class="chip lv-{color(level)}">{level}</span><div><b>{escape(rubric.name(level))}</b>'
                         f'<br><span class="muted">{count}{extra}</span></div></li>')
        items_html = (f'<div class="card"><h3>Flagged content · {len(items)}</h3><ul class="items">{"".join(lines)}</ul>'
                      f'<p class="muted" style="margin:14px 0 0">The scene descriptions are written by IMDb\'s contributors. '
                      f'<a href="{escape(imdb_url(imdb_id))}">Read them on IMDb ↗</a></p></div>')
    elif items:
        groups = []
        if warned:
            groups.append(f'<div class="group"><span class="chip lv-warn" style="width:22px;height:22px;border-radius:6px;'
                          f'font-size:.7rem">⚠️</span>Warning <small>· {len(warned)}</small></div>'
                          f'<ul class="items">{"".join(item_html(i) for i in warned)}</ul>')
        for level in sorted({i.level for i in scored}, reverse=True):
            in_level = sorted((i for i in scored if i.level == level), key=lambda i: not i.is_scene)
            groups.append(f'<div class="group"><span class="chip lv-{color(level)}" style="width:22px;height:22px;'
                          f'border-radius:6px;font-size:.75rem">{level}</span>{escape(rubric.name(level))}'
                          f' <small>· {len(in_level)}</small></div><ul class="items">{"".join(item_html(i) for i in in_level)}</ul>')
        items_html = f'<div class="card"><h3>Flagged content · {len(items)}</h3>{"".join(groups)}</div>'
    else:
        items_html = ""

    votes = guide.get("votes") or {}
    total = sum(votes.values())
    vote_colors = [("NONE", "None", "green"), ("MILD", "Mild", "yellow"), ("MODERATE", "Moderate", "orange"), ("SEVERE", "Severe", "red")]
    if total:
        segs = "".join(f'<span class="lv-{c}" style="width:{votes.get(k, 0) / total * 100:.1f}%"></span>' for k, _, c in vote_colors)
        legend = "".join(f'<span><i class="lv-{c}"></i>{label} {votes.get(k, 0) / total * 100:.0f}%</span>' for k, label, c in vote_colors)
        vote_html = f"""<div class="card"><h3>IMDb community votes · {total:,}</h3><div class="bar">{segs}</div>
<div class="legend">{legend}</div></div>"""
    else:
        vote_html = ""

    ai = classify.is_ai(entry["classifier"])
    summary = (entry.get("summary") if ai else None) or rubric.auto_summary(result, items)
    source = "Summary by AI" if ai and entry.get("summary") else "Summary from the scores"
    summary_html = f"""<div class="card summary"><h3>In short</h3><p>{escape(summary)}</p>
<p class="src">{source}</p></div>"""
    if result.warnings:
        summary_html = f"""<div class="card warning"><h3>⚠️ Content warning</h3><p><b>{escape(rubric.warning_text(result))}</b>.
It's part of the story but not shown, so it isn't scored. The items are listed first below.</p></div>""" + summary_html
    title_link = f"stremio:///detail/{'series' if is_series else 'movie'}/{imdb_id}"
    back = f"""<a class="back" id="back" href="{escape(title_link)}">← Back to Stremio</a>
<script>
// Inside Stremio this page opens in the app's own window, which has no back button. Stremio shows its
// "leaving Stremio" notice first, which reports itself only as https://www.stremio.com/ (or nothing) as the referrer.
document.getElementById("back").addEventListener("click", function (e) {{
  var viaNotice = !document.referrer || /stremio\\.com/.test(document.referrer);
  if (viaNotice) {{
    // History is [Stremio screen, notice, this page]: go back past the notice. With only [notice, this page],
    // the link itself opens the title in Stremio.
    if (history.length > 2) {{ e.preventDefault(); history.go(-2); }}
  }} else if (history.length > 1) {{
    e.preventDefault();
    history.back();
  }}
}});
</script>"""
    body = f"""{back}{hero}{summary_html}{items_html}{vote_html}<div class="card">{_ladder_table()}</div>
<p class="foot">{escape(rubric.BETA_NOTICE)}<br>Scene descriptions from IMDb's parents guide. Levels by Couch Check.</p>"""
    return _doc(f"{guide['title']} · Couch Check", body, extra_css=HERO_CSS)


CONFIGURE_CSS = """
.intro{display:flex;gap:18px;align-items:center;margin:6px 0 22px}
.intro img{width:84px;height:84px;border-radius:22px;flex:none;box-shadow:0 10px 30px rgba(74,47,143,.35)}
.intro p{margin:6px 0 0;color:var(--muted)}
.brand{display:none}
.preview{display:grid;gap:12px;grid-template-columns:1fr 1fr}
@media (max-width:620px){.preview{grid-template-columns:1fr}.intro{flex-direction:column;align-items:flex-start}}
.mock{background:#120f1f;color:#ecebf5;border-radius:14px;padding:16px;font-size:.92rem}
.mock .label{font-size:.68rem;letter-spacing:.09em;text-transform:uppercase;color:#8d87a6;margin-bottom:8px}
.mock .chipx{display:inline-block;background:#2a2540;border-radius:999px;padding:7px 14px}
.mock .row{display:flex;gap:16px;align-items:flex-start}
.mock .row b{white-space:nowrap;font-weight:500}
.mock .row div{color:#bdb8d2;white-space:pre-line;line-height:1.45}
.mock .cap{color:#8d87a6;font-size:.78rem;margin-top:10px}
.actions{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:14px}
ol.steps{margin:0;padding-left:20px}ol.steps li{margin:6px 0}
.warn{border-color:var(--orange)}.warn h3{color:var(--orange)}
.levels{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:6px}
.beta{font-size:.7rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;vertical-align:middle;
background:var(--accent-soft);color:var(--accent);border-radius:999px;padding:3px 9px;margin-left:6px}
.notice{border-color:var(--accent);background:var(--accent-soft);font-size:.92rem}
.toggle{display:flex;gap:12px;align-items:flex-start;margin:0 0 16px;cursor:pointer}
.toggle input{position:absolute;opacity:0;pointer-events:none}
.toggle small{color:var(--muted)}
.switch{flex:none;width:44px;height:26px;border-radius:13px;background:var(--line);position:relative;transition:background .15s;margin-top:2px}
.switch::after{content:"";position:absolute;top:3px;left:3px;width:20px;height:20px;border-radius:50%;background:#fff;
transition:transform .15s;box-shadow:0 1px 3px rgba(0,0,0,.3)}
.toggle input:checked+.switch{background:var(--accent)}
.toggle input:checked+.switch::after{transform:translateX(18px)}
.toggle input:focus-visible+.switch{outline:2px solid var(--accent);outline-offset:2px}
"""


def configure(base_url: str, version: str, ai_on: bool = True) -> str:
    manifest = f"{base_url}{'' if ai_on else '/est'}/manifest.json"
    secure = base_url.startswith("https://")
    # stremio:// links are always opened as https:// on the default port, so they only work for https installs.
    stremio = "stremio://" + manifest.split("://", 1)[1]
    web = "https://web.stremio.com/#/addons?addon=" + quote(manifest, safe="")
    warn = "" if secure else """<div class="card warn"><h3>Stremio can't install this address</h3>
<p style="margin:0">Stremio only installs addons served over <b>https</b>. This server is on plain http, so start it with
<code>./start.sh</code> instead and open the configure page at the https address it prints.</p></div>"""
    levels = "".join(f'<span class="chip lv-{color(l)}" title="{escape(n)}">{l}</span>' for l, n, _ in rubric.LADDER)
    body = f"""<div class="intro"><img src="/static/logo.png" alt="Couch Check logo"><div>
<h1>Couch Check <span class="beta">Beta</span></h1><p>Know what to expect before you press play with family. Every movie and show rated 0–10 for
sex and nudity, with every flagged scene listed.</p></div></div>
{warn}
<div class="card notice"><b>Beta.</b> The scoring rubric is still being tuned, so a score can occasionally be off.
Every score links to the scene-by-scene list it came from, so you can always check.</div>
<div class="card"><h3>How it looks in Stremio</h3><div class="preview">
<div class="mock"><div class="label">Couch Check</div><span class="chipx">🟡 4/10 · Implied · 5 scenes · AI</span>
<div class="cap">On the title's main screen. Tap it for the scene-by-scene breakdown.</div></div>
<div class="mock"><div class="row"><b>🟡 4/10</b><div>Implied · AI score
5 scenes
Tap: full list in browser ↗</div></div><div class="cap">In every episode's and movie's stream list.</div></div>
</div></div>
<div class="card"><h3>Install</h3>
<label class="toggle"><input type="checkbox" id="ai"{" checked" if ai_on else ""}><span class="switch"></span>
<span><b>Use AI scores</b><br><small>An AI reads every scene description and places it on the scale.
Turn off to see only the quick keyword estimate.</small></span></label>
<div class="actions">
<a class="btn" id="install" href="{escape(stremio)}">Install in Stremio</a>
<a class="btn2" id="installweb" href="{escape(web)}" target="_blank" rel="noopener">Install on Stremio Web</a>
<button class="btn2" id="copy" type="button">Copy link</button></div>
<code id="manifest">{escape(manifest)}</code>
<h3 style="margin-top:20px">For the main-screen chip</h3>
<ol class="steps"><li>Open a community addon manager (search "Stremio Addon Manager").</li>
<li>Drag <b>Couch Check</b> above <b>Cinemeta</b> and sync.</li>
<li>Restart Stremio. The stream-list row works without this step.</li></ol></div>
<div class="card"><h3>The scale</h3><div class="levels">{levels}</div>{_ladder_table()}</div>
<div class="card"><h3>Where scores come from</h3><p style="margin:0">Scene descriptions come from IMDb's parents guide.
An AI reads each one and places it on the scale (<b>AI</b>). Until a title has been read, it shows a quick keyword
<b>Estimate</b>. Only sex and nudity are scored: violence, language and the rest are ignored.</p></div>
<p class="foot">Couch Check v{escape(version)}</p>
<script>
var BASE = {json.dumps(base_url)};
document.getElementById("ai").addEventListener("change", function () {{
  var manifest = BASE + (this.checked ? "" : "/est") + "/manifest.json";
  document.getElementById("manifest").textContent = manifest;
  document.getElementById("install").href = "stremio://" + manifest.split("://")[1];
  document.getElementById("installweb").href = "https://web.stremio.com/#/addons?addon=" + encodeURIComponent(manifest);
}});
document.getElementById("copy").addEventListener("click", function () {{
  var url = document.getElementById("manifest").textContent, btn = this;
  function done() {{ btn.textContent = "Copied"; setTimeout(function () {{ btn.textContent = "Copy link"; }}, 1500); }}
  if (navigator.clipboard) {{ navigator.clipboard.writeText(url).then(done, select); }} else {{ select(); }}
  function select() {{ var r = document.createRange(); r.selectNodeContents(document.getElementById("manifest"));
    var s = getSelection(); s.removeAllRanges(); s.addRange(r); }}
}});
</script>"""
    return _doc("Couch Check", body, extra_css=CONFIGURE_CSS)
