"""Static status.html — rendered at the end of every run, no server involved.

One self-contained HTML file next to state.json. It contains no credentials
and no JavaScript: a snapshot of the last two run periods (the running one
with what happened so-far, the finished one with its final outcome) plus
orphans. Runs are recorded in state.json; a full run opens a new period,
promotion passes accumulate into the running one. If no full run has been
recorded yet (fresh install/upgrade), the page falls back to a live view.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta
from pathlib import Path

from . import state as st
from .config import Config

log = logging.getLogger(__name__)


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


def _expiry_label(c: dict, cfg: Config) -> str:
    """Approximate: a clean run on/after that date removes the track."""
    if cfg.expiry_runs:
        used = int(c.get("auditions", 0))
        return f"run budget {used}/{cfg.expiry_runs}(+{cfg.carry_over_runs})"
    ts = c.get("downloaded_at") or c.get("added_at")
    try:
        t = datetime.fromisoformat(ts)
        d = t.replace(hour=0, minute=0, second=0, microsecond=0) \
             + timedelta(days=cfg.expiry_days)
        return "expires " + d.strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return "expires ?"


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


def _name_list(names: list, empty: str) -> str:
    items = "\n".join(f"<li>{_esc(n)}</li>" for n in names)
    return f"<ul>{items}</ul>" if items else f"<p class=\"k\">{empty}</p>"


def _period_html(title: str, runs: list, cfg: Config, stt: dict,
                 live: bool) -> str:
    """One period: auditioned tracks + promoted names.

    `live=True` (running period) annotates the auditioned tracks with their
    expiry from the real candidates; an ended period lists names only.
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
            rows = "\n".join(
                f"<tr><td>{_esc(a)}</td><td>{_esc(t)}</td>"
                f"<td>{_esc(_expiry_label(by_key.get((a.casefold(), t.casefold()), {}), cfg))}</td></tr>"
                for a, t in downloaded)
            audition = (f"<table><tr><th>Artist</th><th>Title</th>"
                        f"<th>Lifetime</th></tr>{rows}</table>")
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
        _name_list(promoted, "none yet"),
    ]
    if not live:
        parts.append(f"<p class=\"k\">expired at period end: {len(expired)}</p>")
    return "\n".join(parts)


def _live_period_html(cfg: Config, stt: dict) -> str:
    """Fallback until the first full run is recorded: what is auditioning
    right now, straight from the candidate state."""
    audition = [c for c in stt.get("candidates", [])
                if c.get("status") == "downloaded"]
    rows = "\n".join(
        f"<tr><td>{_esc(c['artist'])}</td><td>{_esc(c['title'])}</td>"
        f"<td>{_esc(_expiry_label(c, cfg))}</td></tr>"
        for c in audition
    ) or "<tr><td colspan=\"3\" class=\"k\">nothing auditioning</td></tr>"
    promoted = [Path(m["to"]).name for m in stt.get("moved", [])][::-1][:10]
    return "\n".join([
        "<h2>Current audition (live) <span class=\"k\">"
        "(two-period view starts at the next full run)</span></h2>",
        f"<p><strong>Auditioning ({len(audition)})</strong></p>",
        f"<table><tr><th>Artist</th><th>Title</th><th>Lifetime</th></tr>{rows}</table>",
        f"<p><strong>Promoted so-far (last {len(promoted)})</strong></p>",
        _name_list(promoted, "none yet"),
    ])


_CSS = """
body{background:#111;color:#ddd;font:15px/1.5 system-ui,sans-serif;margin:0 auto;
     max-width:860px;padding:1.5rem}
h1{font-size:1.3rem}h2{font-size:1rem;color:#9cf;margin:1.4rem 0 .4rem}
table{border-collapse:collapse;width:100%}
td,th{padding:.2rem .6rem .2rem 0;border-bottom:1px solid #333;text-align:left;
      font-size:.9rem}
.warn{color:#f8b}ul{margin:.2rem 0;padding-left:1.2rem}
footer{margin-top:2rem;color:#777;font-size:.8rem}
.k{color:#888}
"""


def write(cfg: Config, stt: dict, orphans: list[str]) -> str:
    """Render the snapshot. Returns the file path."""
    counts = {s: 0 for s in ("queued", "downloaded", "promoted", "failed",
                             "missing", "expired", "skipped-owned")}
    for c in stt.get("candidates", []):
        if c.get("status") in counts:
            counts[c["status"]] += 1

    current, prev = _periods(stt.get("runs", []))
    if current is not None:
        body = _period_html("Running period — so far", current, cfg, stt,
                            live=True)
        if prev:
            body += "\n" + _period_html("Previous period — finished", prev,
                                        cfg, stt, live=False)
    else:
        body = _live_period_html(cfg, stt)

    orphan_items = "\n".join(f"<li class=\"warn\">{_esc(o)}</li>" for o in orphans)
    orphan_html = (f"<h2>Orphans ({len(orphans)})</h2><ul>{orphan_items}</ul>"
                   "<p class=\"k\">Files in incoming/ that state does not know "
                   "about — never deleted automatically. Check them by hand or "
                   "run <code>starkeep status</code>.</p>") if orphans else \
                  "<h2>Orphans</h2><p class=\"k\">none</p>"

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>starkeep status</title><style>{_CSS}</style></head><body>
<h1>starkeep</h1>
<p>Last run: <strong>{_date(stt.get('last_run'))}</strong></p>
<p class="k">queued {counts['queued']} · downloaded {counts['downloaded']} ·
promoted {counts['promoted']} · failed {counts['failed']} ·
missing {counts['missing']} · expired {counts['expired']} ·
skipped-owned {counts['skipped-owned']}</p>
{body}
{orphan_html}
<footer>Generated by starkeep — deletes only its own downloads in
incoming_dir; your library is only ever added to. This page is a static
snapshot; runs are driven by cron, not by this page.</footer>
</body></html>"""

    path = report_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    log.info("status snapshot: %s", path)
    return str(path)
