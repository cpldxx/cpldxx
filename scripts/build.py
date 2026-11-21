"""Build the profile's SVG cards and README.md from live GitHub data.

Writes assets/hero.svg, assets/stats.svg and one assets/project-<name>.svg
per pinned project, then rewrites the project block in README.md.
Standard library only, so the daily workflow needs no installs.

    GITHUB_TOKEN=... python scripts/build.py
    python scripts/build.py            # falls back to `gh auth token`
"""

import json
import os
import re
import subprocess
import textwrap
import urllib.request
from collections import Counter, OrderedDict
from datetime import date, datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
HISTORY = ROOT / "data" / "contributions.json"
SITE = ROOT / "docs"

PROFILE = {
    "login": "cpldxx",
    "name": "Sangwon Ji",
    "tagline": "I build AI tools that run on your own machine.",
    "tags": ["agents", "MCP servers", "local-first research", "macOS apps"],
}
SKIP_LANGUAGES = {"HTML", "CSS", "Dockerfile", "PLpgSQL", "Makefile"}
PINNED_COUNT = 4

W = 880
PAD = 44
BG, LINE = "#0a0a0a", "#222222"
TEXT, MUTED, DIM = "#fafafa", "#a1a1a1", "#5c5c5c"
RAMP = ["#161616", "#2e2e2e", "#545454", "#9a9a9a", "#f2f2f2"]
LANG_SHADES = ["#f2f2f2", "#a1a1a1", "#6b6b6b", "#454545", "#2e2e2e"]
SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"
MONO_CHAR = 0.6  # monospace advance as a fraction of font size

QUERY = """
query($login: String!) {
  user(login: $login) {
    createdAt
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount weekday } }
      }
    }
    repositories(first: 100, ownerAffiliations: OWNER, isFork: false, privacy: PUBLIC,
                 orderBy: {field: PUSHED_AT, direction: DESC}) {
      nodes {
        name description url pushedAt
        primaryLanguage { name }
        languages(first: 20) { edges { size node { name } } }
      }
    }
  }
}
"""

STYLE = """<style>
.in{animation:in .7s cubic-bezier(.2,.8,.2,1) both}
.cell{animation:cell .5s ease-out both}
.grow{transform-box:fill-box;transform-origin:50% 100%;animation:grow .8s cubic-bezier(.2,.8,.2,1) both}
.growx{transform-box:fill-box;transform-origin:0 50%;animation:growx .9s cubic-bezier(.2,.8,.2,1) both}
@keyframes in{from{opacity:0;transform:translateY(6px)}}
@keyframes cell{from{opacity:0}}
@keyframes grow{from{transform:scaleY(0)}}
@keyframes growx{from{transform:scaleX(0)}}
@media (prefers-reduced-motion:reduce){*{animation:none!important}}
</style>"""


# ---------------------------------------------------------------- data

def token() -> str:
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        return tok
    return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True).stdout.strip()


def fetch(login: str) -> dict:
    body = json.dumps({"query": QUERY, "variables": {"login": login}}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={"Authorization": f"bearer {token()}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    if payload.get("errors"):
        raise SystemExit(f"GitHub API error: {payload['errors']}")
    return payload["data"]["user"]


def streaks(days: list[dict]) -> tuple[int, int]:
    longest = run = 0
    for d in days:
        run = run + 1 if d["contributionCount"] else 0
        longest = max(longest, run)
    current = 0
    # Today not having a contribution yet shouldn't break the streak.
    tail = days[:-1] if days and not days[-1]["contributionCount"] else days
    for d in reversed(tail):
        if not d["contributionCount"]:
            break
        current += 1
    return current, longest


def levels(days: list[dict]) -> dict[str, int]:
    counts = sorted(d["contributionCount"] for d in days if d["contributionCount"])
    if not counts:
        return {d["date"]: 0 for d in days}
    q = [counts[int(len(counts) * f)] for f in (0.25, 0.5, 0.75)]
    return {d["date"]: 0 if not d["contributionCount"] else 1 + sum(d["contributionCount"] > t for t in q)
            for d in days}


def monthly(days: list[dict]) -> "OrderedDict[str, int]":
    months: OrderedDict[str, int] = OrderedDict()
    for d in days:
        months[d["date"][:7]] = months.get(d["date"][:7], 0) + d["contributionCount"]
    return OrderedDict(list(months.items())[-12:])


def language_shares(repos: list[dict], top: int = 5) -> list[tuple[str, float]]:
    sizes: Counter[str] = Counter()
    for r in repos:
        for e in r["languages"]["edges"]:
            if e["node"]["name"] not in SKIP_LANGUAGES:
                sizes[e["node"]["name"]] += e["size"]
    total = sum(sizes.values()) or 1
    return [(name, size / total) for name, size in sizes.most_common(top) if size / total >= 0.01]


def keep_history(user: dict) -> None:
    """Never let a day's count go down.

    Every run's daily counts are merged into data/contributions.json, keeping
    the highest count seen for each date, and the calendar is drawn from that
    record. Work in private repos stays on the dashboard even if GitHub stops
    counting it later, for example after leaving the organization.
    """
    saved = json.loads(HISTORY.read_text()) if HISTORY.exists() else {}
    cal = user["contributionsCollection"]["contributionCalendar"]
    for wk in cal["weeks"]:
        for d in wk["contributionDays"]:
            d["contributionCount"] = max(d["contributionCount"], saved.get(d["date"], 0))
            if d["contributionCount"]:
                saved[d["date"]] = d["contributionCount"]
    cal["totalContributions"] = sum(d["contributionCount"] for wk in cal["weeks"] for d in wk["contributionDays"])
    HISTORY.parent.mkdir(exist_ok=True)
    HISTORY.write_text(json.dumps(dict(sorted(saved.items())), indent=0) + "\n")


def projects(user: dict) -> list[dict]:
    return [r for r in user["repositories"]["nodes"] if r["name"] != PROFILE["login"]]


# ---------------------------------------------------------------- svg helpers

def text(x, y, s, size=13, fill=TEXT, font=SANS, weight=400, anchor="start", extra=""):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{font}" font-size="{size}" fill="{fill}" '
            f'font-weight="{weight}" text-anchor="{anchor}"{extra}>{escape(str(s))}</text>')


def label(x, y, s, fill=MUTED, anchor="start"):
    return text(x, y, s, 12, fill, MONO, anchor=anchor)


def card(w: int, h: int, title: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'role="img" aria-labelledby="t">',
        f'<title id="t">{escape(title)}</title>',
        STYLE,
        f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="14" fill="{BG}" stroke="{LINE}"/>',
    ]


def fade(delay_ms: int, *parts: str) -> str:
    return f'<g class="in" style="animation-delay:{delay_ms}ms">{"".join(parts)}</g>'


# ---------------------------------------------------------------- hero

def render_hero(user: dict, repos: list[dict]) -> str:
    cal = user["contributionsCollection"]["contributionCalendar"]
    weeks = cal["weeks"]
    days = [d for w in weeks for d in w["contributionDays"]]
    lvl = levels(days)
    _, longest = streaks(days)
    active = sum(1 for d in days if d["contributionCount"])
    langs = language_shares(repos)

    gy = 236
    step = (W - 2 * PAD) / len(weeks)
    gb = gy + 7 * step
    sy = gb + 52
    h = round(sy + 116)
    o = card(W, h, f'{PROFILE["name"]}: {PROFILE["tagline"]}')
    o.append(fade(0, label(PAD, 62, f'@{PROFILE["login"]}')))
    o.append(fade(80, text(PAD, 128, PROFILE["name"], 64, TEXT, SANS, 600, extra=' letter-spacing="-2"')))
    o.append(fade(160, text(PAD, 166, PROFILE["tagline"], 19, MUTED)))
    o.append(fade(240, label(PAD, 198, "  ·  ".join(PROFILE["tags"]), DIM)))

    cell = step - 3
    cells = []
    for wi, wk in enumerate(weeks):
        for d in wk["contributionDays"]:
            cells.append(f'<rect class="cell" style="animation-delay:{300 + wi * 12}ms" '
                         f'x="{PAD + wi * step:.1f}" y="{gy + d["weekday"] * step:.1f}" width="{cell:.1f}" '
                         f'height="{cell:.1f}" rx="2" fill="{RAMP[lvl[d["date"]]]}">'
                         f'<title>{d["date"]}: {d["contributionCount"]}</title></rect>')
    o.append("".join(cells))
    o.append(label(PAD, gb + 22, f'{cal["totalContributions"]} contributions in the last year', DIM))
    o.append(label(W - PAD, gb + 22, f'updated {datetime.now(timezone.utc).date().isoformat()}', DIM, "end"))

    o.append(f'<line x1="{PAD}" y1="{sy}" x2="{W - PAD}" y2="{sy}" stroke="{LINE}"/>')
    stats = [
        (str(cal["totalContributions"]), "contributions"),
        (str(active), "active days"),
        (f"{longest}d", "longest streak"),
        (langs[0][0] if langs else "—", f"top language · {langs[0][1] * 100:.0f}%" if langs else "top language"),
    ]
    cw = (W - 2 * PAD) / 4
    for i, (value, key) in enumerate(stats):
        x = PAD + i * cw
        if i:
            o.append(f'<line x1="{x:.1f}" y1="{sy + 18}" x2="{x:.1f}" y2="{sy + 78}" stroke="{LINE}"/>')
        px = x + (20 if i else 0)
        o.append(fade(900 + i * 90,
                      text(px, sy + 56, value, 34, TEXT, SANS, 600, extra=' letter-spacing="-1"'),
                      label(px, sy + 80, key)))

    o.append("</svg>")
    return "\n".join(o) + "\n"


# ---------------------------------------------------------------- activity + languages

def render_stats(user: dict, repos: list[dict]) -> str:
    cal = user["contributionsCollection"]["contributionCalendar"]
    days = [d for w in cal["weeks"] for d in w["contributionDays"]]
    months = monthly(days)
    current, _ = streaks(days)
    best = max(days, key=lambda d: d["contributionCount"])
    langs = language_shares(repos)

    h = 300
    o = card(W, h, f'{PROFILE["name"]}: monthly activity and languages')

    # monthly bars
    lw = 452
    o.append(text(PAD, 60, "Activity", 17, TEXT, SANS, 600))
    o.append(label(PAD + lw, 60, "contributions per month", DIM, "end"))
    peak = max(months.values()) or 1
    slot = lw / max(len(months), 1)
    base, top = 206, 92
    for i, (m, v) in enumerate(months.items()):
        bh = max(3, (base - top) * v / peak)
        x = PAD + i * slot + 5
        bw = slot - 10
        fill = TEXT if v == peak else "#3a3a3a"
        o.append(f'<rect class="grow" style="animation-delay:{150 + i * 40}ms" x="{x:.1f}" y="{base - bh:.1f}" '
                 f'width="{bw:.1f}" height="{bh:.1f}" rx="3" fill="{fill}"><title>{m}: {v}</title></rect>')
        if v == peak:
            o.append(fade(700, label(x + bw / 2, base - bh - 8, v, TEXT, "middle")))
        o.append(label(x + bw / 2, base + 22, date.fromisoformat(m + "-01").strftime("%b")[0], DIM, "middle"))
    o.append(f'<line x1="{PAD}" y1="{base + .5}" x2="{PAD + lw}" y2="{base + .5}" stroke="{LINE}"/>')

    # divider
    dx = PAD + lw + 40
    o.append(f'<line x1="{dx}" y1="44" x2="{dx}" y2="{h - 76}" stroke="{LINE}"/>')

    # languages
    rx = dx + 40
    rw = W - PAD - rx
    o.append(text(rx, 60, "Languages", 17, TEXT, SANS, 600))
    for i, (name, share) in enumerate(langs[:5]):
        y = 96 + i * 30
        o.append(text(rx, y, name, 13.5, TEXT if i == 0 else MUTED))
        o.append(label(rx + rw, y, f"{share * 100:.1f}%", DIM, "end"))
        o.append(f'<rect x="{rx}" y="{y + 8}" width="{rw}" height="3" rx="1.5" fill="#1c1c1c"/>')
        o.append(f'<rect class="growx" style="animation-delay:{300 + i * 80}ms" x="{rx}" y="{y + 8}" '
                 f'width="{max(rw * share, 3):.1f}" height="3" rx="1.5" fill="{TEXT if i == 0 else "#6b6b6b"}"/>')

    o.append(f'<line x1="{PAD}" y1="{h - 52}" x2="{W - PAD}" y2="{h - 52}" stroke="{LINE}"/>')
    o.append(label(PAD, h - 24, f'best day {best["contributionCount"]} · {date.fromisoformat(best["date"]).strftime("%b %-d")}'
                   f'   ·   current streak {current}d', DIM))
    o.append("</svg>")
    return "\n".join(o) + "\n"


# ---------------------------------------------------------------- project cards

def render_card(repo: dict) -> str:
    w, h = 430, 176
    o = card(w, h, f'{repo["name"]}: {repo["description"]}')
    lang = repo["primaryLanguage"]["name"] if repo["primaryLanguage"] else ""
    o.append(text(24, 46, repo["name"], 18, TEXT, SANS, 600, extra=' letter-spacing="-.3"'))
    o.append(label(w - 24, 46, lang, MUTED, "end"))

    lines = textwrap.wrap(repo["description"], 54)
    if len(lines) > 3:
        lines = lines[:3]
        lines[-1] = lines[-1][:51].rstrip(" ,.") + "…"
    for i, line in enumerate(lines):
        o.append(text(24, 76 + i * 20, line, 13.5, MUTED))

    langs = language_shares([repo], top=4)
    by = h - 34
    x, bw = 24, w - 48
    for i, (name, share) in enumerate(langs):
        sw = bw * share
        o.append(f'<rect class="growx" style="animation-delay:{200 + i * 80}ms" x="{x:.1f}" y="{by}" '
                 f'width="{max(sw - 2, 2):.1f}" height="3" rx="1.5" fill="{LANG_SHADES[i]}"/>')
        x += sw
    split = " · ".join(f"{n} {s * 100:.0f}%" for n, s in langs[:2])
    o.append(label(24, by + 20, split, DIM))
    pushed = date.fromisoformat(repo["pushedAt"][:10]).strftime("%b %-d")
    o.append(label(w - 24, by + 20, f"updated {pushed}", DIM, "end"))
    o.append("</svg>")
    return "\n".join(o) + "\n"


def site_data(user: dict, repos: list[dict], pinned: list[dict]) -> dict:
    """Everything the interactive page in docs/ draws, in one JSON file."""
    cal = user["contributionsCollection"]["contributionCalendar"]
    days = [d for w in cal["weeks"] for d in w["contributionDays"]]
    lvl = levels(days)
    current, longest = streaks(days)
    best = max(days, key=lambda d: d["contributionCount"])
    return {
        "profile": PROFILE,
        "updated": datetime.now(timezone.utc).date().isoformat(),
        "total": cal["totalContributions"],
        "active": sum(1 for d in days if d["contributionCount"]),
        "current": current,
        "longest": longest,
        "best": {"date": best["date"], "count": best["contributionCount"]},
        "weeks": [[[d["date"], d["contributionCount"], d["weekday"], lvl[d["date"]]]
                   for d in w["contributionDays"]] for w in cal["weeks"]],
        "months": list(monthly(days).items()),
        "languages": language_shares(repos),
        "projects": [{
            "name": r["name"],
            "description": r["description"],
            "url": r["url"],
            "language": r["primaryLanguage"]["name"] if r["primaryLanguage"] else "",
            "languages": language_shares([r], top=4),
            "pushed": r["pushedAt"][:10],
        } for r in pinned],
    }


def readme_projects(repos: list[dict]) -> str:
    links = [f'<a href="{r["url"]}"><img src="./assets/project-{r["name"]}.svg" width="49%" '
             f'alt="{escape(r["name"])}: {escape(r["description"])}"></a>' for r in repos]
    return '<p align="center">\n' + "\n".join(links) + "\n</p>"


def main() -> None:
    user = fetch(PROFILE["login"])
    keep_history(user)
    repos = projects(user)
    pinned = [r for r in repos if r["description"]][:PINNED_COUNT]

    ASSETS.mkdir(exist_ok=True)
    for old in ASSETS.glob("project-*.svg"):
        old.unlink()
    (ASSETS / "hero.svg").write_text(render_hero(user, repos))
    (ASSETS / "stats.svg").write_text(render_stats(user, repos))
    for r in pinned:
        (ASSETS / f'project-{r["name"]}.svg').write_text(render_card(r))

    SITE.mkdir(exist_ok=True)
    (SITE / "data.json").write_text(json.dumps(site_data(user, repos, pinned), separators=(",", ":")) + "\n")

    readme = ROOT / "README.md"
    readme.write_text(re.sub(
        r"(<!-- projects:start -->).*?(<!-- projects:end -->)",
        lambda m: f"{m.group(1)}\n{readme_projects(pinned)}\n{m.group(2)}",
        readme.read_text(),
        flags=re.S,
    ))
    print(f"built hero, stats and {len(pinned)} project cards")


if __name__ == "__main__":
    main()
