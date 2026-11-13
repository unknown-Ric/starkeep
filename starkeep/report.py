"""Static status.html — rendered at the end of every run, no server involved.

One self-contained HTML file next to state.json. It contains no credentials:
a snapshot of the last two run periods (the running one with what happened
so-far, the finished one with its final outcome) plus orphans. Runs are
recorded in state.json; a full run opens a new period, promotion passes
accumulate into the running one. If no full run has been recorded yet (fresh
install/upgrade), the page falls back to a live view.

Content is readable with JavaScript off (every date is in the HTML); a few
lines of JS only *add* relative wording ("2h ago", "in 3 days") and flag a
last run older than STALE_AFTER_HOURS, which catches a silently dead timer.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from . import state as st
from .config import Config

log = logging.getLogger(__name__)

STALE_AFTER_HOURS = 36  # daily cron + margin; older last-run gets .warn


def report_path(cfg: Config) -> Path:
    # fixed name, not derived from state.json's name (docs say status.html)
    return Path(cfg.state_file).expanduser().parent / "status.html"


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def _date(iso: str | None) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return "?"


def _day(iso: str | None) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return "?"


def _expiry_info(c: dict, cfg: Config) -> tuple[datetime | None, str, str]:
    """(expiry date or None, fallback group label, cell text) for one row.

    Day mode returns the day on which the next cleaning run removes the
    track; run-budget mode has no calendar date and groups under one label.
    """
    if cfg.expiry_runs:
        used = int(c.get("auditions", 0))
        label = f"run budget {used}/{cfg.expiry_runs}(+{cfg.carry_over_runs})"
        return None, "Lifetime: run budget", label
    ts = c.get("downloaded_at") or c.get("added_at")
    try:
        t = datetime.fromisoformat(ts)
        d = t.replace(hour=0, minute=0, second=0, microsecond=0) \
             + timedelta(days=cfg.expiry_days)
        return d, "Unknown expiry", d.strftime("%m-%d")
    except (TypeError, ValueError):
        return None, "Unknown expiry", "expires ?"


def _group_label(n: int) -> str:
    if n < 0:  # past expiry: removed at the next cleaning run
        return "Past expiry — cleans at next full run"
    if n == 0:
        return "Expires today"
    if n == 1:
        return "Expires tomorrow"
    return f"Expires in {n} days"


def _norm_key(item) -> tuple:
    return tuple(item) if isinstance(item, (list, tuple)) else (str(item),)


def _merge(entries: list, key: str) -> list:
    """Concatenate a field across run records, deduped, order kept."""
    out, seen = [], set()
    for e in entries:
        for item in e.get(key) or []:
            k = _norm_key(item)
            if k not in seen:
                seen.add(k)
                out.append(item)
    return out


def _periods(runs: list) -> tuple[list | None, list | None]:
    """Split run records into (current, previous) periods.

    A full run opens a new period (it downloads the new audition batch), so
    the current period starts AT the last full run; the previous one spans
    from the full run before it up to (not incl.) the last full run.
    (None, None) = no full run recorded yet -> caller uses the live fallback.
    """
    full = [i for i, r in enumerate(runs) if r.get("full")]
    if not full:
        return None, None
    last = full[-1]
    current = runs[last:]
    prev = runs[full[-2]:last] if len(full) >= 2 else []
    return current, prev


def _td_seed(c: dict) -> str:
    seed = c.get("seed")
    return f"<td class=\"seed\">{_esc(seed)}</td>" if seed else "<td class=\"seed\"></td>"


def _td_expiry(cell: str) -> str:
    # plain date on purpose: no data-ts, the JS must not append "in X days"
    return f"<td>{_esc(cell)}</td>"


def _audition_groups_html(cands: list[dict], cfg: Config) -> str:
    """Audition table grouped by expiry, soonest first.

    Date-mode rows carry no per-row expiry cell — every row in a group shares
    the date, which lives in the group heading instead (exception: rows with
    an unknown date keep their per-row cell). Groups are <details> so the big
    "in N days" batches start collapsed; today/tomorrow/past stay open.
    """
    now = st.utcnow_dt()
    head = "Lifetime" if cfg.expiry_runs else "Expires"
    buckets: dict[tuple, tuple[str, str, list, bool, bool]] = {}
    order: list[tuple] = []
    for c in cands:
        d, fallback, cell = _expiry_info(c, cfg)
        if d is not None:
            n = (d.date() - now.date()).days
            label = _group_label(n)
            # past-expiry spans dates: the heading can't show one
            date_part = "" if n < 0 else f" · {d.strftime('%a %m-%d')}"
            gk = (-1, 0) if n < 0 else (1, d)
            sort = (d, str(c.get("artist", "")).casefold())
            keep_cell, open_ = False, n < 2
        else:
            gk, label = (2, fallback), fallback
            date_part = ""
            sort = (str(c.get("artist", "")).casefold(),
                    str(c.get("title", "")).casefold())
            keep_cell, open_ = True, True
        b = buckets.get(gk)
        if b is None:
            buckets[gk] = b = (label, date_part, [], keep_cell, open_)
            order.append(gk)
        b[2].append((sort, c, cell))
    order.sort()  # past-expiry, then future dates (soonest first), then no-date
    parts = []
    for gk in order:
        label, date_part, rows, keep_cell, open_ = buckets[gk]
        rows.sort(key=lambda r: r[0])
        trs = "\n".join(
            f"<tr><td class=\"artist\">{_esc(c['artist'])}</td>"
            f"<td class=\"title\">{_esc(c['title'])}</td>{_td_seed(c)}"
            f"{_td_expiry(cell) if keep_cell else ''}</tr>"
            for _, c, cell in rows)
        thead = ("<tr><th>Artist</th><th>Title</th>"
                 + (f"<th>{head}</th>" if keep_cell else "") + "</tr>")
        parts.append(
            f"<details{' open' if open_ else ''}><summary>"
            f"{_esc(label)} ({len(rows)}){_esc(date_part)}</summary>"
            f"<table>{thead}{trs}</table></details>")
    return "\n".join(parts) or "<p class=\"k\">nothing auditioning</p>"


def _promoted_rows(entries: list) -> list[tuple[str, str, str | None]]:
    """New records are [artist, title, date]; old ones are bare filenames."""
    out = []
    for e in entries:
        if isinstance(e, (list, tuple)) and len(e) == 3:
            out.append((str(e[0]), str(e[1]),
                        str(e[2])[:10] if e[2] else None))
        else:  # legacy: "01. Artist - Title.m4a"
            stem = re.sub(r"^\d+\.\s*", "", Path(str(e)).stem)
            a, sep, t = stem.partition(" - ")
            out.append((a if sep else "?", t if sep else stem, None))
    return out


def _promoted_html(entries: list, empty: str = "none yet") -> str:
    rows = _promoted_rows(entries)
    if not rows:
        return f"<p class=\"k\">{empty}</p>"
    trs = "\n".join(
        f"<tr><td class=\"artist\">{_esc(a)}</td>"
        f"<td class=\"title\">{_esc(t)}</td></tr>" for a, t, _ in rows)
    return f"<table><tr><th>Artist</th><th>Title</th></tr>{trs}</table>"


def _starred_html(cands: list[dict]) -> str:
    rows = [c for c in cands if c.get("status") == "downloaded"
            and c.get("starred_at")]
    if not rows:
        return ""
    trs = "\n".join(f"<tr><td class=\"artist\">{_esc(c['artist'])}</td>"
                    f"<td class=\"title\">{_esc(c['title'])}</td></tr>"
                    for c in rows)
    return (f"<h2>Starred — will be promoted at the next run ({len(rows)})</h2>"
            f"<table><tr><th>Artist</th><th>Title</th></tr>{trs}</table>")


def _short_error(text: object) -> str:
    t = str(text or "?")
    return t if len(t) <= 100 else "…" + t[-99:]


def _failed_html(cands: list[dict], cfg: Config) -> str:
    rows = [c for c in cands if c.get("status") == "failed"]
    if not rows:
        return ""
    trs = "\n".join(
        f"<tr><td class=\"artist\">{_esc(c['artist'])}</td>"
        f"<td class=\"title\">{_esc(c['title'])}</td>"
        f"<td class=\"meta\">{int(c.get('strikes', 0))}/{cfg.max_strikes}"
        f"{' — skipped' if int(c.get('strikes', 0)) >= cfg.max_strikes else ''}</td>"
        f"<td class=\"meta warn\" title=\"{_esc(c.get('last_error') or '')}\">"
        f"{_esc(_short_error(c.get('last_error')))}</td></tr>"
        for c in rows)
    return (f"<h2>Failed downloads ({len(rows)})</h2>"
            f"<table><tr><th>Artist</th><th>Title</th><th>Strikes</th>"
            f"<th>Error</th></tr>{trs}</table>")


def _period_html(title: str, runs: list, cfg: Config, stt: dict,
                 live: bool) -> str:
    """One period: auditioned tracks + promoted names.

    `live=True` (running period) annotates the auditioned tracks with their
    expiry and seed from the real candidates; an ended period lists names only.
    """
    start = runs[0].get("at") if runs else None
    end = runs[-1].get("at") if runs else None
    span = f"{_day(start)}" if live else f"{_day(start)} → {_day(end)}"

    downloaded = _merge(runs, "downloaded")
    promoted = _merge(runs, "promoted")
    expired = _merge(runs, "expired")

    if downloaded:
        if live:
            by_key = {(c["artist"].casefold(), c["title"].casefold()): c
                      for c in stt.get("candidates", [])}
            rows = [by_key.get((a.casefold(), t.casefold()),
                               {"artist": a, "title": t})
                    for a, t in downloaded]
            audition = _audition_groups_html(rows, cfg)
        else:
            items = "\n".join(f"<li>{_esc(a)} — {_esc(t)}</li>"
                              for a, t in downloaded)
            audition = f"<ul>{items}</ul>"
        head = f"Auditioned ({len(downloaded)})"
    else:
        audition = "<p class=\"k\">no downloads this period</p>"
        head = "Auditioned"

    parts = [
        f"<h2>{_esc(title)} <span class=\"k\">({span})</span></h2>",
        f"<p><strong>{head}</strong></p>", audition,
        f"<p><strong>Promoted ({len(promoted)})</strong></p>",
        _promoted_html(promoted),
    ]
    if not live:
        parts.append(f"<p class=\"k\">expired at period end: {len(expired)}</p>")
    return "\n".join(parts)


def _live_period_html(cfg: Config, stt: dict) -> str:
    """Fallback until the first full run is recorded: what is auditioning
    right now, straight from the candidate state."""
    audition = [c for c in stt.get("candidates", [])
                if c.get("status") == "downloaded"]
    promoted = [[m["artist"], m["title"], m.get("at")] if m.get("artist")
                else Path(m["to"]).name  # legacy: parse "Artist - Title.ext"
                for m in stt.get("moved", [])[-10:][::-1]]
    return "\n".join([
        "<h2>What's auditioning right now <span class=\"k\">"
        "(the history view fills in after the next full run)</span></h2>",
        f"<p><strong>Auditioning ({len(audition)})</strong></p>",
        _audition_groups_html(audition, cfg),
        f"<p><strong>Promoted so-far (last {len(promoted)})</strong></p>",
        _promoted_html(promoted),
    ])


_CSS = """
body{background:#111;color:#ddd;font:15px/1.5 system-ui,sans-serif;margin:0 auto;
     max-width:860px;padding:1.5rem}
h1{font-size:1.3rem}h2{font-size:1rem;color:#9cf;margin:1.4rem 0 .4rem}
table{border-collapse:collapse;width:100%}
td,th{padding:.2rem .6rem .2rem 0;border-bottom:1px solid #333;text-align:left;
      font-size:.9rem}
.warn{color:#f8b}ul{margin:.2rem 0;padding-left:1.2rem}
li.warn{word-break:break-all}  /* orphan paths must wrap, not push width */
footer{margin-top:2rem;color:#8a8a8a;font-size:.8rem}
.k{color:#888}
.counters .zero{color:#888}  /* zero counters recede, non-zero stand out */
.seed,.meta{color:#888}      /* "similar to ..." is self-describing: no header */
summary{margin:.8rem 0 .3rem;color:#9cf;cursor:pointer}
/* phones: tighter padding; rows stack (artist — title, seed underneath) */
@media (max-width:560px){
  body{padding:.75rem;font-size:14px}
  table,tbody,tr{display:block}
  th{display:none}
  tr{padding:.4rem 0;border-bottom:1px solid #333}
  td{display:block;border:0;padding:.1rem 0}  /* every table stacks by cell */
  td.artist,td.title{display:inline}
  td.artist{font-weight:600}
  td.artist::after{content:" — ";font-weight:400;color:#888}
  td.seed,td.meta{font-size:.8rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  td.seed:empty{display:none}  /* no seed yet -> no blank line */
}
"""

_JS = ("var STALE_H = %d;\n" % STALE_AFTER_HOURS) + """
(function(){
  var now = Date.now();
  document.querySelectorAll('[data-ts]').forEach(function(el){
    var t = Date.parse(el.getAttribute('data-ts'));
    if (isNaN(t)) return;
    var s = (now - t) / 1000, txt;
    if (s >= 0) {
      txt = s < 3600 ? Math.max(1, Math.round(s / 60)) + 'm ago'
          : s < 86400 ? Math.round(s / 3600) + 'h ago'
          : Math.round(s / 86400) + 'd ago';
    } else {
      var d = Math.ceil(-s / 86400);
      txt = d <= 1 ? 'tomorrow' : 'in ' + d + ' days';
    }
    el.insertAdjacentText('beforeend', ' (' + txt + ')');
  });
  var lr = document.querySelector('#lastrun [data-ts]');
  if (lr) {
    var age = now - Date.parse(lr.getAttribute('data-ts'));
    if (!isNaN(age) && age > STALE_H * 3600 * 1000)
      document.getElementById('lastrun').className = 'warn';
  }
})();
"""


def _next_harvest(now: datetime) -> str:
    """Next weekly harvest occurrence. shortcut: hardcodes the schedule of
    deploy/starkeep.timer (OnCalendar=Mon 04:17, server runs UTC); update both
    if the timer changes."""
    days_ahead = (0 - now.weekday()) % 7  # Monday = weekday 0
    fire = now.replace(hour=4, minute=17, second=0, microsecond=0)
    if days_ahead == 0 and fire <= now:
        days_ahead = 7
    return (fire + timedelta(days=days_ahead)).isoformat(timespec="seconds")


def _schedule_header(runs: list, now: datetime | None = None) -> str:
    """The three scheduler lines: last promotion, last and next harvest.

    Promotion passes record full=False (every-4h timer), harvest runs
    full=True (weekly Monday timer).
    """
    now = now or st.utcnow_dt()
    harvests = [r["at"] for r in runs if r.get("full") and r.get("at")]
    promos = [r["at"] for r in runs if not r.get("full") and r.get("at")]
    last_harvest = harvests[-1] if harvests else None
    # a full run promotes too; with no promote pass ever recorded, it is the
    # most recent promotion that happened
    last_promo = promos[-1] if promos else (last_harvest or None)
    return "\n".join([
        f"<p id=\"lastrun\">Last promotion run: "
        f"<strong data-ts=\"{_esc(last_promo or '')}\">{_date(last_promo)}</strong></p>",
        f"<p>Last harvest run: "
        f"<strong data-ts=\"{_esc(last_harvest or '')}\">{_date(last_harvest)}</strong></p>",
        f"<p>Next harvest run: "
        f"<strong data-ts=\"{_esc(_next_harvest(now))}\">{_date(_next_harvest(now))}</strong></p>",
    ])


def write(cfg: Config, stt: dict, orphans: list[str]) -> str:
    """Render the snapshot. Returns the file path."""
    counts = {s: 0 for s in ("queued", "downloaded", "promoted", "failed",
                             "missing", "expired", "skipped-owned")}
    for c in stt.get("candidates", []):
        if c.get("status") in counts:
            counts[c["status"]] += 1
    counters = " · ".join(
        f"<span class=\"{'' if counts[n] else 'zero'}\">{n} {counts[n]}</span>"
        for n in counts)

    current, prev = _periods(stt.get("runs", []))
    if current is not None:
        body = _period_html("Running period — so far", current, cfg, stt,
                            live=True)
        if prev:
            body += "\n" + _period_html("Previous period — finished", prev,
                                        cfg, stt, live=False)
    else:
        body = _live_period_html(cfg, stt)

    cands = stt.get("candidates", [])

    orphan_items = "\n".join(f"<li class=\"warn\">{_esc(o)}</li>" for o in orphans)
    orphan_html = (f"<h2>Orphans ({len(orphans)})</h2><ul>{orphan_items}</ul>"
                   "<p class=\"k\">Files in incoming/ that state does not know "
                   "about — never deleted automatically. Check them by hand or "
                   "run <code>starkeep status</code>.</p>") if orphans else \
                  "<h2>Orphans</h2><p class=\"k\">none</p>"

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>starkeep status</title><style>{_CSS}</style></head><body>
<h1>starkeep</h1>
{_schedule_header(stt.get('runs', []))}
<p class="counters">{counters}</p>
{_starred_html(cands)}
{body}
{_failed_html(cands, cfg)}
{orphan_html}
<footer>Generated by starkeep — deletes only its own downloads in
incoming_dir; your library is only ever added to. This page is a static
snapshot; runs are driven by cron, not by this page.</footer>
<script>{_JS}</script>
</body></html>"""

    path = report_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    log.info("status snapshot: %s", path)
    return str(path)
