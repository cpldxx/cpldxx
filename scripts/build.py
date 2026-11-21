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

PROFILE = {
    "login": "cpldxx",
    "name": "Sangwon Ji",
    "banner": "SANGWON JI",
    "whoami": "Sangwon Ji. I build AI tools that run on your own machine.",
    "tags": ["agents", "MCP servers", "local-first research", "macOS apps"],
}
SKIP_LANGUAGES = {"HTML", "CSS", "Dockerfile", "PLpgSQL", "Makefile"}
PINNED_COUNT = 4

W = 880
BG, PANEL, BORDER = "#0d1117", "#161b22", "#30363d"
TEXT, MUTED, DIM = "#e6edf3", "#8b949e", "#484f58"
GREEN = "#39d353"
GREENS = ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"]
LANG_SHADES = ["#39d353", "#2ea043", "#1a7f37", "#56d4dd", "#2f81f7"]
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"
CHAR = 0.6  # monospace advance as a fraction of font size

# 5x7 bitmap glyphs for the banner; a space is 3 columns wide.
FONT = {
    "A": [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "G": [".###.", "#...#", "#....", "#.###", "#...#", "#...#", ".###."],
    "I": [".###.", "..#..", "..#..", "..#..", "..#..", "..#..", ".###."],
    "J": ["..###", "...#.", "...#.", "...#.", "...#.", "#..#.", ".##.."],
    "N": ["#...#", "##..#", "##..#", "#.#.#", "#..##", "#..##", "#...#"],
    "O": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "S": [".###.", "#...#", "#....", ".###.", "....#", "#...#", ".###."],
    "W": ["#...#", "#...#", "#...#", "#.#.#", "#.#.#", "##.##", "#...#"],
    " ": ["...", "...", "...", "...", "...", "...", "..."],
}

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
.pop{animation:pop .45s cubic-bezier(.2,.8,.2,1) both}
.fade{animation:fade .8s ease-out both}
.grow{transform-box:fill-box;transform-origin:50% 100%;animation:grow .8s cubic-bezier(.2,.8,.2,1) both}
.growx{transform-box:fill-box;transform-origin:0 50%;animation:growx .9s cubic-bezier(.2,.8,.2,1) both}
.type{transform-box:fill-box;transform-origin:0 50%}
.blink{animation:blink 1.1s steps(1) infinite}
@keyframes pop{from{opacity:0;transform:scale(.4)}}
@keyframes fade{from{opacity:0}}
@keyframes grow{from{transform:scaleY(0)}}
@keyframes growx{from{transform:scaleX(0)}}
@keyframes type{from{transform:scaleX(0)}}
@keyframes blink{50%{opacity:0}}
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
    return [(name, size / total) for name, size in sizes.most_common(top)]


def projects(user: dict) -> list[dict]:
    return [r for r in user["repositories"]["nodes"] if r["name"] != PROFILE["login"]]


# ---------------------------------------------------------------- svg helpers

def text(x, y, s, size=13, fill=TEXT, weight=400, anchor="start", extra=""):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" font-weight="{weight}" '
            f'text-anchor="{anchor}"{extra}>{escape(str(s))}</text>')


def frame(w: int, h: int, title: str, label: str) -> list[str]:
    """Shared window chrome: background, CRT scanlines, title bar."""
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'font-family="{MONO}" role="img" aria-labelledby="t">',
        f'<title id="t">{escape(label)}</title>',
        STYLE,
        "<defs>"
        '<filter id="glow" x="-50%" y="-50%" width="200%" height="200%">'
        '<feGaussianBlur stdDeviation="2.4" result="b"/>'
        '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>'
        '<radialGradient id="haze" cx="50%" cy="0%" r="80%">'
        '<stop offset="0" stop-color="#0f2e1c"/><stop offset="1" stop-color="#0d1117"/></radialGradient>'
        '<pattern id="scan" width="4" height="3" patternUnits="userSpaceOnUse">'
        '<rect width="4" height="1" fill="#ffffff" opacity=".018"/></pattern>'
        '<linearGradient id="bar" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{GREEN}"/><stop offset="1" stop-color="#0e4429"/></linearGradient>'
        "</defs>",
        f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="12" fill="url(#haze)" stroke="{BORDER}"/>',
        f'<rect x=".5" y=".5" width="{w - 1}" height="{h - 1}" rx="12" fill="url(#scan)"/>',
        f'<path d="M.5 34V12.5a12 12 0 0 1 12-12h{w - 25}a12 12 0 0 1 12 12V34z" fill="{PANEL}" fill-opacity=".85"/>',
        f'<line x1=".5" y1="34" x2="{w - .5}" y2="34" stroke="{BORDER}"/>',
        *(f'<circle cx="{20 + i * 18}" cy="17" r="5.5" fill="{c}"/>'
          for i, c in enumerate(["#ff5f57", "#febc2e", "#28c840"])),
        text(w / 2, 21.5, title, 12, MUTED, anchor="middle"),
    ]


def typed(x, y, s, size, fill, delay_ms, ms_per_char=28, cid="ty"):
    """Text that appears one character at a time, followed by nothing if CSS is off."""
    width = len(s) * size * CHAR
    dur = len(s) * ms_per_char
    return [
        f'<clipPath id="{cid}"><rect class="type" x="{x}" y="{y - size}" width="{width + 2:.1f}" '
        f'height="{size * 1.5}" style="animation:type {dur}ms steps({len(s)}) {delay_ms}ms both"/></clipPath>',
        f'<g clip-path="url(#{cid})">{text(x, y, s, size, fill)}</g>',
    ]


# ---------------------------------------------------------------- hero

def banner_mask(word: str) -> list[list[bool]]:
    cols: list[list[bool]] = []
    for i, ch in enumerate(word):
        glyph = FONT[ch]
        if i:
            cols.append([False] * 7)
        for c in range(len(glyph[0])):
            cols.append([glyph[r][c] == "#" for r in range(7)])
    return cols


def render_hero(user: dict) -> str:
    cal = user["contributionsCollection"]["contributionCalendar"]
    weeks = cal["weeks"]
    lvl = levels([d for w in weeks for d in w["contributionDays"]])

    mask = banner_mask(PROFILE["banner"])
    ncols = max(len(mask) + 2, len(weeks))
    mask_off = (ncols - len(mask)) // 2
    week_off = ncols - len(weeks)

    h = 330
    o = frame(W, h, f'{PROFILE["login"]}@github: ~', f'{PROFILE["name"]}: {cal["totalContributions"]} contributions in the last year')

    step = (W - 48) / ncols
    cell = step * 0.82
    gx, gy = 24 + (step - cell) / 2, 66

    behind, front = [], []
    for c in range(ncols):
        wk = c - week_off
        days = weeks[wk]["contributionDays"] if 0 <= wk < len(weeks) else []
        by_row = {d["weekday"]: d for d in days}
        for r in range(7):
            x, y = gx + c * step, gy + r * step
            m = c - mask_off
            lit = 0 <= m < len(mask) and mask[m][r]
            d = by_row.get(r)
            level = lvl[d["date"]] if d else 0
            tip = f'<title>{d["date"]}: {d["contributionCount"]}</title>' if d else ""
            if lit:
                fill = GREENS[4]
                front.append(f'<rect class="pop" style="animation-delay:{180 + m * 22}ms;transform-origin:{x + cell / 2:.1f}px {y + cell / 2:.1f}px" '
                             f'x="{x:.1f}" y="{y:.1f}" width="{cell:.1f}" height="{cell:.1f}" rx="2.5" fill="{fill}">{tip}</rect>')
            else:
                op = "" if level == 0 else ' fill-opacity=".24"'
                behind.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{cell:.1f}" height="{cell:.1f}" rx="2.5" '
                              f'fill="{GREENS[level]}"{op}>{tip}</rect>')
    o.append(f'<g class="fade">{"".join(behind)}</g>')
    o.append(f'<g filter="url(#glow)">{"".join(front)}</g>')

    grid_bottom = gy + 7 * step
    o.append(text(24, grid_bottom + 22, f'{cal["totalContributions"]} contributions in the last year, with my name drawn over them',
                  11, DIM))
    o.append(text(W - 24, grid_bottom + 22, f'updated {datetime.now(timezone.utc).date().isoformat()}', 11, DIM, anchor="end"))

    y1 = grid_bottom + 64
    o.append(text(24, y1, "$", 15, GREEN, 700))
    o += typed(24 + 15 * CHAR * 2, y1, "whoami", 15, TEXT, 900, 70, "ty1")
    y2 = y1 + 28
    o += typed(24, y2, PROFILE["whoami"], 15, TEXT, 1500, 22, "ty2")
    cursor_x = 24 + len(PROFILE["whoami"]) * 15 * CHAR + 4
    o.append(f'<rect class="blink" x="{cursor_x:.1f}" y="{y2 - 13}" width="9" height="17" fill="{GREEN}"/>')

    x = 24
    y3 = y2 + 34
    for i, tag in enumerate(PROFILE["tags"]):
        tw = len(tag) * 12 * CHAR + 20
        o.append(f'<g class="fade" style="animation-delay:{2600 + i * 120}ms">'
                 f'<rect x="{x:.1f}" y="{y3 - 15}" width="{tw:.1f}" height="22" rx="11" fill="#0e4429" fill-opacity=".55" stroke="#1a7f37"/>'
                 f'{text(x + 10, y3, tag, 12, "#7ee787")}</g>')
        x += tw + 8

    o.append("</svg>")
    return "\n".join(o) + "\n"


# ---------------------------------------------------------------- stats

def render_stats(user: dict, repos: list[dict]) -> str:
    cal = user["contributionsCollection"]["contributionCalendar"]
    days = [d for w in cal["weeks"] for d in w["contributionDays"]]
    months = monthly(days)
    current, longest = streaks(days)
    best = max(days, key=lambda d: d["contributionCount"])
    active = sum(1 for d in days if d["contributionCount"])
    langs = language_shares(repos)
    joined = datetime.fromisoformat(user["createdAt"].replace("Z", "+00:00"))

    h = 318
    o = frame(W, h, f'{PROFILE["login"]}@github: ~ $ neofetch', f'{PROFILE["name"]} GitHub stats')

    # neofetch-style info column
    lx = 32
    o.append(text(lx, 70, f'sangwon@{PROFILE["login"]}', 16, GREEN, 700, extra=' filter="url(#glow)"'))
    o.append(text(lx, 88, "─" * 22, 13, DIM))
    info = [
        ("contributions", f'{cal["totalContributions"]} this year'),
        ("active days", f"{active} / {len(days)}"),
        ("best day", f'{best["contributionCount"]} on {date.fromisoformat(best["date"]).strftime("%b %-d")}'),
        ("streak", f"{current}d now · {longest}d best"),
        ("languages", ", ".join(n for n, _ in langs[:3])),
        ("public repos", str(len(repos))),
        ("on github since", joined.strftime("%b %Y")),
    ]
    for i, (k, v) in enumerate(info):
        y = 114 + i * 23
        o.append(f'<g class="fade" style="animation-delay:{150 + i * 90}ms">'
                 f'{text(lx, y, k, 13.5, GREEN, 700)}{text(lx + 17 * 13.5 * CHAR, y, v, 13.5, TEXT)}</g>')
    py = 114 + len(info) * 23 - 2
    for i, c in enumerate(GREENS[1:] + ["#56d4dd", "#2f81f7", "#a371f7", "#e6edf3"]):
        o.append(f'<rect x="{lx + i * 26}" y="{py}" width="22" height="12" rx="2" fill="{c}"/>')

    # monthly bars
    rx, rw = 488, 360
    o.append(text(rx, 70, "activity", 13, TEXT, 700))
    o.append(text(rx + rw, 70, "per month", 11, DIM, anchor="end"))
    peak = max(months.values()) or 1
    slot = rw / max(len(months), 1)
    base, top = 200, 92
    for i, (m, v) in enumerate(months.items()):
        bh = max(3, (base - top) * v / peak)
        x = rx + i * slot + 4
        is_peak = v == peak
        attrs = ' filter="url(#glow)"' if is_peak else ""
        o.append(f'<rect class="grow" style="animation-delay:{250 + i * 45}ms" x="{x:.1f}" y="{base - bh:.1f}" '
                 f'width="{slot - 8:.1f}" height="{bh:.1f}" rx="3" fill="url(#bar)"{attrs}><title>{m}: {v}</title></rect>')
        if is_peak:
            o.append(text(x + (slot - 8) / 2, base - bh - 7, v, 11, GREEN, 700, "middle"))
        label = date.fromisoformat(m + "-01").strftime("%b")[0]
        o.append(text(x + (slot - 8) / 2, base + 16, label, 10.5, DIM, anchor="middle"))
    o.append(f'<line x1="{rx}" y1="{base + .5}" x2="{rx + rw}" y2="{base + .5}" stroke="{BORDER}"/>')

    # language split
    ly = 248
    o.append(text(rx, ly, "languages", 13, TEXT, 700))
    x = rx
    for i, (name, share) in enumerate(langs):
        sw = rw * share
        o.append(f'<rect class="growx" style="animation-delay:{500 + i * 90}ms" x="{x:.1f}" y="{ly + 10}" '
                 f'width="{max(sw - 2, 2):.1f}" height="10" rx="2" fill="{LANG_SHADES[i]}"/>')
        x += sw
    o += legend(rx, ly + 41, langs, rw)

    o.append("</svg>")
    return "\n".join(o) + "\n"


# ---------------------------------------------------------------- project cards

def legend(x, y, langs, max_w, size=11) -> list[str]:
    """Dot + name + share for each language that fits in max_w."""
    out, cx = [], x
    for i, (name, share) in enumerate(langs):
        label = f"{name} {share * 100:.0f}%"
        w = len(label) * size * CHAR + 12
        if cx + w > x + max_w:
            break
        out.append(f'<circle cx="{cx + 4}" cy="{y - 4}" r="4" fill="{LANG_SHADES[i]}"/>')
        out.append(text(cx + 12, y, label, size, MUTED))
        cx += w + 12
    return out


def render_card(repo: dict) -> str:
    w, h = 430, 206
    o = frame(w, h, f'~/{repo["name"]}', f'{repo["name"]}: {repo["description"]}')
    o.append(text(20, 66, repo["name"], 18, GREEN, 700, extra=' filter="url(#glow)"'))
    lines = textwrap.wrap(repo["description"], 50)
    if len(lines) > 4:
        lines = lines[:4]
        lines[-1] = lines[-1][:47].rstrip() + "..."
    for i, line in enumerate(lines):
        o.append(text(20, 92 + i * 18, line, 12.5, MUTED))

    langs = [(n, sh) for n, sh in language_shares([repo], top=4) if sh >= 0.01]
    by = h - 38
    x = 20
    bw = w - 40
    for i, (name, share) in enumerate(langs):
        sw = bw * share
        o.append(f'<rect class="growx" style="animation-delay:{200 + i * 80}ms" x="{x:.1f}" y="{by}" '
                 f'width="{max(sw - 2, 2):.1f}" height="6" rx="2" fill="{LANG_SHADES[i]}"/>')
        x += sw
    o += legend(20, by + 25, langs, bw - 70)
    pushed = date.fromisoformat(repo["pushedAt"][:10]).strftime("%b %-d")
    o.append(text(w - 20, by + 25, f"↻ {pushed}", 11, DIM, anchor="end"))
    o.append("</svg>")
    return "\n".join(o) + "\n"


def readme_projects(repos: list[dict]) -> str:
    links = [f'<a href="{r["url"]}"><img src="./assets/project-{r["name"]}.svg" width="49%" '
             f'alt="{escape(r["name"])}: {escape(r["description"])}"></a>' for r in repos]
    return '<p align="center">\n' + "\n".join(links) + "\n</p>"


def main() -> None:
    user = fetch(PROFILE["login"])
    repos = projects(user)
    pinned = [r for r in repos if r["description"]][:PINNED_COUNT]

    ASSETS.mkdir(exist_ok=True)
    for old in ASSETS.glob("project-*.svg"):
        old.unlink()
    (ASSETS / "hero.svg").write_text(render_hero(user))
    (ASSETS / "stats.svg").write_text(render_stats(user, repos))
    for r in pinned:
        (ASSETS / f'project-{r["name"]}.svg').write_text(render_card(r))

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
