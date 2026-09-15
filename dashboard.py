#!/usr/bin/env python3
"""Generate a self-contained HTML dashboard from the decision log."""
from __future__ import annotations
import json, sqlite3, sys
from datetime import datetime
from html import escape
from pathlib import Path

CSS = """
*{box-sizing:border-box}
.viz-root{color-scheme:light;--surface-1:#fcfcfb;--plane:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;
--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--series-1:#2a78d6;--good:#0ca30c;--crit:#d03b3b;
--warn:#fab219;--border:rgba(11,11,11,.10);--f1:#86b6ef;--f2:#5598e7;--f3:#2a78d6;--f4:#1c5cab}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme=light])) .viz-root{color-scheme:dark;
--surface-1:#1a1a19;--plane:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;
--axis:#383835;--series-1:#3987e5;--border:rgba(255,255,255,.10);
--f1:#9ec5f4;--f2:#6da7ec;--f3:#3987e5;--f4:#256abf}}
:root[data-theme=dark] .viz-root{color-scheme:dark;--surface-1:#1a1a19;--plane:#0d0d0d;--ink:#fff;
--ink2:#c3c2b7;--grid:#2c2c2a;--axis:#383835;--series-1:#3987e5;--border:rgba(255,255,255,.10);
--f1:#9ec5f4;--f2:#6da7ec;--f3:#3987e5;--f4:#256abf}
body{margin:0;background:var(--plane);color:var(--ink);
font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:28px 16px 64px}
h1{font-size:21px;margin:0 0 2px;letter-spacing:-.01em}
.sub{color:var(--ink2);font-size:13px;margin:0 0 22px}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:12px;
padding:18px;margin-bottom:16px}
.card h2{font-size:13px;font-weight:600;margin:0 0 3px;letter-spacing:.01em}
.card .cap{color:var(--muted);font-size:12px;margin:0 0 16px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:16px}
.tile{background:var(--surface-1);border:1px solid var(--border);border-radius:12px;padding:14px 16px}
.tile .k{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px}
.tile .v{font-size:25px;font-weight:600;letter-spacing:-.02em;line-height:1.1}
.tile .n{color:var(--ink2);font-size:12px;margin-top:4px}
.pos{color:var(--good)}.neg{color:var(--crit)}
table{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}
th{text-align:left;color:var(--muted);font-weight:500;font-size:11px;text-transform:uppercase;
letter-spacing:.05em;padding:0 10px 8px 0;border-bottom:1px solid var(--grid)}
td{padding:9px 10px 9px 0;border-bottom:1px solid var(--grid)}
tr:last-child td{border-bottom:none}
.r{text-align:right}
.empty{color:var(--muted);text-align:center;padding:30px 12px;font-size:13px;
border:1px dashed var(--grid);border-radius:8px}
.fr{display:flex;align-items:center;gap:10px;margin-bottom:9px}
.fl{width:120px;font-size:12px;color:var(--ink2);flex-shrink:0}
.ft{flex:1;height:22px;border-radius:0 4px 4px 0;min-width:2px;position:relative}
.fv{width:92px;text-align:right;font-size:12px;font-variant-numeric:tabular-nums;color:var(--ink2)}
.bar-row{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.bar-lab{width:104px;font-size:12px;color:var(--ink2);flex-shrink:0}
.bar-tr{flex:1;height:26px;background:var(--grid);border-radius:4px;overflow:hidden;display:flex}
.bar-fill{height:100%;border-radius:4px}
.bar-val{width:96px;text-align:right;font-size:13px;font-variant-numeric:tabular-nums;font-weight:500}
.note{background:var(--surface-1);border:1px solid var(--border);border-left:3px solid var(--warn);
border-radius:8px;padding:13px 15px;font-size:13px;color:var(--ink2);margin-bottom:16px}
.note b{color:var(--ink)}
svg{display:block;width:100%;overflow:visible}
.tt{position:fixed;pointer-events:none;background:var(--surface-1);border:1px solid var(--border);
border-radius:7px;padding:7px 10px;font-size:12px;box-shadow:0 4px 14px rgba(0,0,0,.14);
opacity:0;transition:opacity .1s;z-index:9;font-variant-numeric:tabular-nums;white-space:nowrap}
@media(max-width:560px){.fl,.bar-lab{width:86px}.fv,.bar-val{width:72px}.tile .v{font-size:21px}
.scroll{overflow-x:auto}}
"""


def money(v):
    s = f"${abs(v):,.2f}"
    return f"-{s}" if v < 0 else s


def cls(v):
    return "pos" if v > 0 else ("neg" if v < 0 else "")


def build(db_path="paper.db", out="dashboard.html"):
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    q = lambda s, a=(): db.execute(s, a).fetchall()

    trades = q("SELECT * FROM trades ORDER BY closed_at DESC")
    curve = q("SELECT * FROM equity_curve ORDER BY id")
    scans = q("SELECT * FROM scans WHERE universe IS NOT NULL ORDER BY id DESC")
    rej = q("SELECT reason,COUNT(*) c FROM rejections WHERE stage='safety'"
            " GROUP BY substr(reason,1,instr(reason||'(','(')-1) ORDER BY c DESC LIMIT 10")
    blocks = q("SELECT risk_reason,COUNT(*) c FROM decisions WHERE action='REJECTED_BY_RISK'"
               " GROUP BY risk_reason ORDER BY c DESC LIMIT 6")

    start = curve[0]["equity"] if curve else 1000.0
    eq = curve[-1]["equity"] if curve else start
    try:
        cfg = json.loads(Path("config.yaml").read_text()) if False else None
    except Exception:
        cfg = None
    n = len(trades)
    wins = [t for t in trades if t["net_pnl_usd"] > 0]
    gross = sum(t["gross_pnl_usd"] for t in trades)
    costs = sum(t["total_costs_usd"] for t in trades)
    net = sum(t["net_pnl_usd"] for t in trades)
    ret = (eq / start - 1) * 100 if start else 0

    H = [f'<!doctype html><html><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">',
         '<title>Paper Trading — Decision Log</title>',
         f'<style>{CSS}</style></head><body class="viz-root"><div class="wrap">',
         '<h1>Paper Trading — Decision Log</h1>',
         f'<p class="sub">Solana · simulated capital · generated {datetime.now():%Y-%m-%d %H:%M} UTC</p>']

    # ---- honesty banner ---------------------------------------------
    H.append('<div class="note"><b>Every cost parameter in this run is an assumption, '
             'not a measurement.</b> Fees, priority cost, and the adverse-move term have not '
             'been calibrated against real fills yet. Treat these results as directionally '
             'useful and absolutely not as expected returns.</div>')

    # ---- tiles -------------------------------------------------------
    drag = (costs / abs(gross) * 100) if gross else None
    H.append('<div class="tiles">')
    for k, v, note, c in [
        ("Equity", money(eq), f"from {money(start)}", cls(eq - start)),
        ("Return", f"{ret:+.2f}%", "net of all costs", cls(ret)),
        ("Closed trades", str(n), f"{len(wins)}W / {n-len(wins)}L" if n else "none yet", ""),
        ("Win rate", f"{len(wins)/n*100:.0f}%" if n else "—", "realised", ""),
        ("Costs paid", money(costs) if n else "—",
         f"{drag:.0f}% of gross P&L" if drag is not None else "no trades", "neg" if costs else ""),
    ]:
        H.append(f'<div class="tile"><div class="k">{k}</div>'
                 f'<div class="v {c}">{v}</div><div class="n">{escape(note)}</div></div>')
    H.append('</div>')

    # ---- equity curve -------------------------------------------------
    H.append('<div class="card"><h2>Equity curve</h2>'
             '<p class="cap">Simulated account value after every cost. Hover for detail.</p>')
    if len(curve) >= 2:
        W, Hh, P = 1000, 230, 34
        vals = [r["equity"] for r in curve]
        lo, hi = min(vals), max(vals)
        if hi - lo < 1e-9:
            lo, hi = lo - 1, hi + 1
        pad = (hi - lo) * .12
        lo, hi = lo - pad, hi + pad
        X = lambda i: P + i * (W - P - 14) / max(len(vals) - 1, 1)
        Y = lambda v: P / 2 + (hi - v) / (hi - lo) * (Hh - P)
        pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vals))
        g = "".join(f'<line x1="{P}" y1="{Y(lo+(hi-lo)*f):.1f}" x2="{W-14}" '
                    f'y2="{Y(lo+(hi-lo)*f):.1f}" stroke="var(--grid)" stroke-width="1"/>'
                    f'<text x="{P-7}" y="{Y(lo+(hi-lo)*f)+4:.1f}" text-anchor="end" '
                    f'font-size="11" fill="var(--muted)">${lo+(hi-lo)*f:,.0f}</text>'
                    for f in (0, .5, 1))
        dots = "".join(f'<circle class="pt" cx="{X(i):.1f}" cy="{Y(v):.1f}" r="11" fill="transparent" '
                       f'data-v="{v:.2f}" data-t="{escape(curve[i]["ts"][:16].replace("T"," "))}"/>'
                       for i, v in enumerate(vals))
        H.append(f'<svg viewBox="0 0 {W} {Hh}" preserveAspectRatio="none" style="height:230px">{g}'
                 f'<polyline points="{pts}" fill="none" stroke="var(--series-1)" stroke-width="2" '
                 f'stroke-linejoin="round" stroke-linecap="round"/>{dots}</svg>')
    else:
        H.append('<div class="empty">Not enough data points yet — the curve appears '
                 'after a few scan cycles.</div>')
    H.append('</div>')

    # ---- cost attribution (the money chart) ---------------------------
    H.append('<div class="card"><h2>Where the P&amp;L went</h2>'
             '<p class="cap">Gross move captured, minus friction, equals what you actually keep.</p>')
    if n and gross != 0:
        mx = max(abs(gross), abs(costs), abs(net), 1e-9)
        for lab, val, col in [("Gross P&L", gross, "var(--series-1)"),
                              ("Costs paid", -abs(costs), "var(--crit)"),
                              ("Net P&L", net, "var(--good)" if net > 0 else "var(--crit)")]:
            H.append(f'<div class="bar-row"><div class="bar-lab">{lab}</div>'
                     f'<div class="bar-tr"><div class="bar-fill" style="width:'
                     f'{abs(val)/mx*100:.1f}%;background:{col}"></div></div>'
                     f'<div class="bar-val {cls(val)}">{money(val)}</div></div>')
        if drag:
            H.append(f'<p class="cap" style="margin:12px 0 0">Friction consumed '
                     f'<b style="color:var(--ink)">{drag:.0f}%</b> of gross profit.</p>')
    else:
        H.append('<div class="empty">No closed trades yet.</div>')
    H.append('</div>')

    # ---- funnel --------------------------------------------------------
    H.append('<div class="card"><h2>Latest scan funnel</h2>'
             '<p class="cap">How many candidates survive each stage. Most scans should end in zero.</p>')
    if scans:
        s = scans[0]
        stages = [("Universe", s["universe"] or 0, "var(--f1)"),
                  ("Passed safety", s["passed_safety"] or 0, "var(--f2)"),
                  ("Scored", s["scored"] or 0, "var(--f3)"),
                  ("Entered", s["entries"] or 0, "var(--f4)")]
        mx = max(v for _, v, _ in stages) or 1
        for lab, v, c in stages:
            H.append(f'<div class="fr"><div class="fl">{lab}</div>'
                     f'<div class="ft" style="width:{max(v/mx*100,.4):.1f}%;background:{c}"></div>'
                     f'<div class="fv">{v}</div></div>')
    else:
        H.append('<div class="empty">No completed scans yet.</div>')
    H.append('</div>')

    # ---- trades table ---------------------------------------------------
    H.append('<div class="card"><h2>Closed trades</h2>'
             '<p class="cap">Quoted move vs what was actually realised after friction.</p><div class="scroll">')
    if trades:
        H.append('<table><thead><tr><th>Token</th><th class="r">Size</th><th class="r">Quoted</th>'
                 '<th class="r">Net</th><th class="r">Costs</th><th>Exit</th><th class="r">Held</th>'
                 '</tr></thead><tbody>')
        for t in trades[:40]:
            qm = (t["exit_quoted"] / t["entry_quoted"] - 1) * 100 if t["entry_quoted"] else 0
            H.append(f'<tr><td>{escape(t["symbol"])}</td><td class="r">${t["size_usd"]:,.0f}</td>'
                     f'<td class="r {cls(qm)}">{qm:+.1f}%</td>'
                     f'<td class="r {cls(t["net_pnl_usd"])}">{t["net_pnl_pct"]:+.2f}%</td>'
                     f'<td class="r">{money(t["total_costs_usd"])}</td>'
                     f'<td>{escape(t["exit_reason"].split("(")[0])}</td>'
                     f'<td class="r">{t["hold_hours"]:.1f}h</td></tr>')
        H.append('</tbody></table>')
    else:
        H.append('<div class="empty">No closed trades yet. The engine only enters when safety, '
                 'scoring and risk all approve — long stretches of nothing are normal.</div>')
    H.append('</div></div>')

    # ---- why things were rejected ----------------------------------------
    H.append('<div class="card"><h2>Why candidates were rejected</h2>'
             '<p class="cap">The filter is the product. This is what it caught.</p><div class="scroll">')
    if rej:
        mxr = max(r["c"] for r in rej)
        H.append('<table><thead><tr><th>Reason</th><th class="r">Count</th></tr></thead><tbody>')
        for r in rej:
            reason = r["reason"].split("(")[0].replace("_", " ")
            H.append(f'<tr><td>{escape(reason)}</td><td class="r">{r["c"]:,}</td></tr>')
        H.append('</tbody></table>')
    else:
        H.append('<div class="empty">No rejections logged yet.</div>')
    if blocks:
        H.append('<p class="cap" style="margin-top:18px"><b style="color:var(--ink)">'
                 'Blocked by the risk engine after scoring well:</b></p>'
                 '<table><tbody>')
        for b in blocks:
            H.append(f'<tr><td>{escape(b["risk_reason"][:90])}</td>'
                     f'<td class="r">{b["c"]}</td></tr>')
        H.append('</tbody></table>')
    H.append('</div></div>')

    H.append('<div class="tt" id="tt"></div>')
    H.append("""<script>
const tt=document.getElementById('tt');
document.querySelectorAll('.pt').forEach(p=>{
 p.addEventListener('mouseenter',e=>{const r=e.target.getBoundingClientRect();
  tt.innerHTML='<b>$'+(+e.target.dataset.v).toLocaleString(undefined,{minimumFractionDigits:2})+
   '</b><br>'+e.target.dataset.t;
  tt.style.opacity=1;tt.style.left=Math.min(r.left+12,innerWidth-170)+'px';
  tt.style.top=Math.max(r.top-46,8)+'px';});
 p.addEventListener('mouseleave',()=>tt.style.opacity=0);});
</script></div></body></html>""")

    Path(out).write_text("".join(H))
    return out, {"trades": n, "equity": eq, "gross": gross, "costs": costs, "net": net}


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else "paper.db"
    out, st = build(db, sys.argv[2] if len(sys.argv) > 2 else "dashboard.html")
    print(f"wrote {out}  {st}")
