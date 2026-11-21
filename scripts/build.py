"""Build dashboard.svg and the project list in README.md from live GitHub data.

Standard library only, so the daily workflow needs no installs.

    GITHUB_TOKEN=... python scripts/build.py
    python scripts/build.py            # falls back to `gh auth token`
"""

import json
import os
import re
import subprocess
import urllib.request
from collections import Counter, OrderedDict
from datetime import date, datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent

PROFILE = {
    "login": "cpldxx",
    "name": "Sangwon Ji",
    "bio": ["Builds AI tools that run on", "your own machine."],
    "focus": ["agents · MCP servers", "local-first research", "macOS apps"],
}
SKIP_LANGUAGES = {"HTML", "CSS", "Dockerfile", "PLpgSQL", "Makefile"}
PINNED_COUNT = 4
PORTRAIT_COLS = 46  # keep in sync with scripts/make_ascii.py

W, H = 880, 512
BG, PANEL, BORDER = "#0d1117", "#161b22", "#30363d"
TEXT, MUTED, DIM = "#e6edf3", "#7d8590", "#484f58"
GREENS = ["#1c2128", "#0e4429", "#006d32", "#26a641", "#39d353"]
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"

QUERY = """
query($login: String!) {
  user(login: $login) {
    createdAt
    followers { totalCount }
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount weekday } }
      }
    }
    repositories(first: 100, ownerAffiliations: OWNER, isFork: false, privacy: PUBLIC,
                 orderBy: {field: PUSHED_AT, direction: DESC}) {
      nodes {
        name description url stargazerCount
        primaryLanguage { name }
        languages(first: 20) { edges { size node { name } } }
      }
    }
  }
}
"""


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
    out = {}
    for d in days:
        c = d["contributionCount"]
        out[d["date"]] = 0 if c == 0 else 1 + sum(c > t for t in q)
    return out


def monthly(days: list[dict]) -> "OrderedDict[str, int]":
    months: OrderedDict[str, int] = OrderedDict()
    for d in days:
        key = d["date"][:7]
        months[key] = months.get(key, 0) + d["contributionCount"]
    return OrderedDict(list(months.items())[-12:])


def languages(repos: list[dict]) -> list[tuple[str, float]]:
    sizes: Counter[str] = Counter()
    for r in repos:
        for e in r["languages"]["edges"]:
            if e["node"]["name"] not in SKIP_LANGUAGES:
                sizes[e["node"]["name"]] += e["size"]
    total = sum(sizes.values()) or 1
    return [(name, size / total) for name, size in sizes.most_common(5)]


def projects(user: dict) -> list[dict]:
    return [r for r in user["repositories"]["nodes"] if r["name"] != PROFILE["login"]]


def text(x, y, s, size=12, fill=TEXT, weight=400, anchor="start", cls=""):
    c = f' class="{cls}"' if cls else ""
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" font-weight="{weight}" '
            f'text-anchor="{anchor}"{c}>{escape(str(s))}</text>')


def render(user: dict, portrait: list[str]) -> str:
    cc = user["contributionsCollection"]
    cal = cc["contributionCalendar"]
    weeks = cal["weeks"]
    days = [d for w in weeks for d in w["contributionDays"]]
    lvl = levels(days)
    current, longest = streaks(days)
    months = monthly(days)
    repos = projects(user)
    langs = languages(repos)
    today = datetime.now(timezone.utc).date()
    since = user["createdAt"][:7]

    o = []
    o.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
             f'font-family="{MONO}" role="img" aria-labelledby="t">')
    o.append(f'<title id="t">{escape(PROFILE["login"])} — {cal["totalContributions"]} contributions in the last year</title>')
    o.append("""<style>
.c{animation:pop .5s cubic-bezier(.2,.8,.2,1) both}
.b{transform-box:fill-box;transform-origin:50% 100%;animation:grow .9s cubic-bezier(.2,.8,.2,1) both}
.l{transform-box:fill-box;transform-origin:0 50%;animation:growx .9s cubic-bezier(.2,.8,.2,1) both}
.p{animation:fade 1.2s ease-out both}
.k{animation:blink 1.1s steps(1) infinite}
@keyframes pop{from{opacity:0;transform:translateY(3px)}}
@keyframes grow{from{transform:scaleY(0)}}
@keyframes growx{from{transform:scaleX(0)}}
@keyframes fade{from{opacity:0}}
@keyframes blink{50%{opacity:0}}
@media (prefers-reduced-motion:reduce){*{animation:none!important}}
</style>""")
    o.append(f'<rect x="0.5" y="0.5" width="{W-1}" height="{H-1}" rx="12" fill="{BG}" stroke="{BORDER}"/>')

    # title bar
    o.append(f'<path d="M0.5 36V12.5a12 12 0 0 1 12-12h{W-25}a12 12 0 0 1 12 12V36z" fill="{PANEL}"/>')
    o.append(f'<line x1="0.5" y1="36" x2="{W-0.5}" y2="36" stroke="{BORDER}"/>')
    for i, col in enumerate(["#ff5f57", "#febc2e", "#28c840"]):
        o.append(f'<circle cx="{20 + i * 18}" cy="18" r="5.5" fill="{col}"/>')
    o.append(text(W / 2, 22, f'{PROFILE["login"]}@github: ~/dashboard', 12, MUTED, anchor="middle"))
    o.append(text(W - 20, 22, today.isoformat(), 11, DIM, anchor="end"))

    # left column: portrait + identity
    lx = 24
    o.append(f'<rect x="{lx}" y="52" width="252" height="232" rx="8" fill="{PANEL}" stroke="{BORDER}"/>')
    rows = (portrait + [""] * 28)[:28]
    o.append('<g class="p" font-size="8" fill="#3fb950">')
    for i, line in enumerate(rows):
        if line.strip():
            # Stretch every row to the same width so the art fills the box.
            padded = escape(line.ljust(PORTRAIT_COLS)).replace(" ", "\u00a0")
            o.append(f'<text x="{lx + 12}" y="{66 + i * 7.6:.1f}" textLength="228" '
                     f'lengthAdjust="spacing">{padded}</text>')
    o.append("</g>")

    o.append(text(lx, 314, PROFILE["name"], 20, TEXT, 600))
    o.append(text(lx, 334, f'@{PROFILE["login"]}', 12, "#3fb950"))
    for i, line in enumerate(PROFILE["bio"]):
        o.append(text(lx, 362 + i * 18, line, 12, TEXT))
    for i, line in enumerate(PROFILE["focus"]):
        y = 412 + i * 18
        o.append(text(lx, y, "▸", 11, "#3fb950"))
        o.append(text(lx + 14, y, line, 11.5, MUTED))

    # right column
    rx, rw = 300, 556

    # contribution heatmap
    o.append(text(rx, 62, "contributions", 12, TEXT, 600))
    o.append(text(rx + 104, 62, f'— {cal["totalContributions"]} in the last year', 12, MUTED))
    cell, gap = 8.5, 1.9
    n = len(weeks)
    gx = rx + rw - n * (cell + gap) + gap
    month_seen = set()
    for wi, wk in enumerate(weeks):
        for d in wk["contributionDays"]:
            x = gx + wi * (cell + gap)
            y = 74 + d["weekday"] * (cell + gap)
            o.append(f'<rect class="c" style="animation-delay:{wi * 14}ms" x="{x:.1f}" y="{y:.1f}" '
                     f'width="{cell}" height="{cell}" rx="2" fill="{GREENS[lvl[d["date"]]]}">'
                     f'<title>{d["date"]}: {d["contributionCount"]}</title></rect>')
        first = wk["contributionDays"][0]["date"]
        m = first[:7]
        if m not in month_seen and wi < n - 1:
            month_seen.add(m)
            if wi > 0 or int(first[8:]) <= 7:
                label = date.fromisoformat(first).strftime("%b").lower()
                o.append(text(gx + wi * (cell + gap), 156, label, 10, DIM))
    lx2 = rx + rw
    o.append(text(lx2 - 5 * 12 - 32, 62, "less", 10, DIM, anchor="end"))
    for i, g in enumerate(GREENS):
        o.append(f'<rect x="{lx2 - (5 - i) * 12 - 28}" y="54" width="9" height="9" rx="2" fill="{g}"/>')
    o.append(text(lx2, 62, "more", 10, DIM, anchor="end"))

    # stat tiles
    # Derived from the calendar, which counts private work too; the
    # commit/PR totals would only see public repos with the Actions token.
    best = max(days, key=lambda d: d["contributionCount"])
    stats = [
        ("active days", sum(1 for d in days if d["contributionCount"])),
        ("best day", best["contributionCount"]),
        ("this month", list(months.values())[-1]),
        ("public repos", len(repos)),
        ("current streak", f"{current}d"),
        ("longest streak", f"{longest}d"),
    ]
    tw, th, tg = (rw - 2 * 10) / 3, 46, 10
    for i, (label, value) in enumerate(stats):
        x = rx + (i % 3) * (tw + tg)
        y = 174 + (i // 3) * (th + tg)
        o.append(f'<rect x="{x:.1f}" y="{y}" width="{tw:.1f}" height="{th}" rx="8" fill="{PANEL}" stroke="{BORDER}"/>')
        o.append(text(x + 14, y + 29, value, 20, "#3fb950", 600))
        o.append(text(x + tw - 14, y + 28, label, 11, MUTED, anchor="end"))

    # monthly activity bars
    by = 306
    o.append(text(rx, by, "activity", 12, TEXT, 600))
    o.append(text(rx + 70, by, "— per month", 12, MUTED))
    bw_total = 268
    peak = max(months.values()) or 1
    slot = bw_total / max(len(months), 1)
    base = by + 148
    for i, (m, v) in enumerate(months.items()):
        h = max(2, 116 * v / peak)
        x = rx + i * slot
        shade = GREENS[4] if v == peak else GREENS[3]
        o.append(f'<rect class="b" style="animation-delay:{300 + i * 50}ms" x="{x + 3:.1f}" y="{base - h:.1f}" '
                 f'width="{slot - 6:.1f}" height="{h:.1f}" rx="2" fill="{shade}"><title>{m}: {v}</title></rect>')
        if v == peak:
            o.append(text(x + slot / 2, base - h - 6, v, 10, TEXT, anchor="middle"))
        o.append(text(x + slot / 2, base + 14, date.fromisoformat(m + "-01").strftime("%b")[0].lower(), 10, DIM, anchor="middle"))
    o.append(f'<line x1="{rx}" y1="{base + 0.5}" x2="{rx + bw_total}" y2="{base + 0.5}" stroke="{BORDER}"/>')

    # languages
    lx3 = rx + 300
    lw = rw - 300
    o.append(text(lx3, by, "languages", 12, TEXT, 600))
    for i, (name, share) in enumerate(langs):
        y = by + 26 + i * 25
        o.append(text(lx3, y, name, 11.5, TEXT))
        o.append(text(lx3 + lw, y, f"{share * 100:.1f}%", 11, MUTED, anchor="end"))
        o.append(f'<rect x="{lx3}" y="{y + 6}" width="{lw}" height="5" rx="2.5" fill="{GREENS[0]}"/>')
        o.append(f'<rect class="l" style="animation-delay:{400 + i * 80}ms" x="{lx3}" y="{y + 6}" '
                 f'width="{max(3, lw * share):.1f}" height="5" rx="2.5" fill="{GREENS[4 - min(i, 3)]}"/>')

    # prompt line
    o.append(f'<line x1="24" y1="{H - 42}" x2="{W - 24}" y2="{H - 42}" stroke="{BORDER}"/>')
    prompt = f'{PROFILE["login"]}@github ~ $ '
    o.append(text(24, H - 18, prompt, 12, "#3fb950"))
    cmd = f"echo \"since {since} · {user['followers']['totalCount']} followers · auto-updated daily\""
    o.append(text(24 + len(prompt) * 7.22, H - 18, cmd, 12, MUTED))
    o.append(f'<rect class="k" x="{24 + (len(prompt) + len(cmd)) * 7.22 + 4:.1f}" y="{H - 29}" width="7" height="14" fill="#3fb950"/>')

    o.append("</svg>")
    return "\n".join(o) + "\n"


def readme_projects(repos: list[dict]) -> str:
    rows = ["| project | what it does |", "| --- | --- |"]
    for r in [r for r in repos if r["description"]][:PINNED_COUNT]:
        lang = r["primaryLanguage"]["name"] if r["primaryLanguage"] else ""
        desc = r["description"].replace("|", "\\|")
        rows.append(f'| [**{r["name"]}**]({r["url"]})<br><sub>{lang}</sub> | {desc} |')
    return "\n".join(rows)


def main() -> None:
    user = fetch(PROFILE["login"])
    portrait = (ROOT / "assets" / "portrait.txt").read_text().splitlines()
    (ROOT / "dashboard.svg").write_text(render(user, portrait))

    readme = ROOT / "README.md"
    block = readme_projects(projects(user))
    readme.write_text(re.sub(
        r"(<!-- projects:start -->).*?(<!-- projects:end -->)",
        lambda m: f"{m.group(1)}\n{block}\n{m.group(2)}",
        readme.read_text(),
        flags=re.S,
    ))
    print("dashboard.svg and README.md updated")


if __name__ == "__main__":
    main()
