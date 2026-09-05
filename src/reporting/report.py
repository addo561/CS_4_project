# =============================================================================
# report.py — generates a PDF analytics report from the SQLite event store.
#
# Usage:  python src/reporting/report.py [--days 30] [--out report.pdf]
# =============================================================================

import os
import sys
import argparse
from datetime import datetime

_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.dirname(_DIR)
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle)

from core import history

INK    = colors.HexColor("#0B1220"); DEEP = colors.HexColor("#065A82")
TEAL   = colors.HexColor("#1C7293"); MINT = colors.HexColor("#2FA36B")
AMBER  = colors.HexColor("#B5641E"); CLOUD = colors.HexColor("#F1F5FB")
BORDER = colors.HexColor("#DBE3EF"); BODY = colors.HexColor("#1E2A3F")
MUT    = colors.HexColor("#5F6E85"); WHITE = colors.white

ss = getSampleStyleSheet()
def S(n, **k): return ParagraphStyle(n, parent=ss["Normal"], **k)
body = S("b",  fontName="Helvetica",      fontSize=10, leading=14.5, textColor=BODY, spaceAfter=6)
h1   = S("h1", fontName="Helvetica-Bold", fontSize=17, leading=21, textColor=INK, spaceAfter=3)
h2   = S("h2", fontName="Helvetica-Bold", fontSize=12.5, leading=16, textColor=DEEP,
         spaceBefore=12, spaceAfter=5)
eye  = S("e",  fontName="Helvetica-Bold", fontSize=8.5, leading=11, textColor=TEAL, spaceAfter=2)
big  = S("bg", fontName="Helvetica-Bold", fontSize=19, leading=21, textColor=DEEP, alignment=TA_CENTER)
lbl  = S("l",  fontName="Helvetica",      fontSize=8, leading=10.5, textColor=MUT, alignment=TA_CENTER)
mono = S("m",  fontName="Courier",        fontSize=8.5, leading=11.5, textColor=colors.HexColor("#CFE0F5"))


def _stat_row(items, width=6.4):
    cells = []
    for v, l in items:
        cw = (width/len(items) - 0.10) * inch
        t = Table([[Paragraph(str(v), big)], [Paragraph(l, lbl)]], colWidths=[cw])
        t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),CLOUD),("BOX",(0,0),(-1,-1),0.8,BORDER),
            ("TOPPADDING",(0,0),(0,0),8),("BOTTOMPADDING",(0,-1),(-1,-1),8),
            ("TOPPADDING",(0,1),(-1,1),0)]))
        cells.append(t)
    r = Table([cells], colWidths=[(width/len(items))*inch]*len(items))
    r.setStyle(TableStyle([("LEFTPADDING",(0,0),(-1,-1),3),("RIGHTPADDING",(0,0),(-1,-1),3)]))
    return r


def _bar_chart(rows, label_key, value_key, width=6.4, bar_h=0.26, color=DEEP, max_rows=10):
    """A horizontal bar chart drawn as a borderless table — no plotting library,
    so nothing extra is bundled into the packaged application."""
    rows = rows[:max_rows]
    if not rows:
        return Paragraph("No data recorded for this period.", body)
    peak = max((r[value_key] or 0) for r in rows) or 1
    data = []
    for r in rows:
        v = r[value_key] or 0
        frac = v / peak
        bar = Table([[""]], colWidths=[max(0.06, frac * (width - 2.7))*inch],
                    rowHeights=[bar_h*inch])
        bar.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),color),
                                 ("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0),
                                 ("TOPPADDING",(0,0),(-1,-1),0),("BOTTOMPADDING",(0,0),(-1,-1),0)]))
        name = str(r[label_key] or "unknown")
        data.append([Paragraph(name[:28], S("n", fontName="Helvetica", fontSize=9,
                                            textColor=BODY, leading=12)),
                     bar,
                     Paragraph(str(v), S("v", fontName="Helvetica-Bold", fontSize=9,
                                         textColor=DEEP, leading=12))])
    t = Table(data, colWidths=[2.0*inch, (width - 2.7)*inch, 0.6*inch])
    t.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3),
        ("LEFTPADDING",(0,0),(-1,-1),2),("RIGHTPADDING",(0,0),(-1,-1),4)]))
    return t


def _table(headers, rows, widths):
    data = [[Paragraph(f"<b>{h}</b>", S("th", fontName="Helvetica-Bold", fontSize=9,
                                        textColor=WHITE, leading=12)) for h in headers]]
    for r in rows:
        data.append([Paragraph(str(c), S("td", fontName="Helvetica", fontSize=9,
                                         textColor=BODY, leading=12)) for c in r])
    t = Table(data, colWidths=widths)
    t.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),INK),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),[WHITE, CLOUD]),
        ("GRID",(0,0),(-1,-1),0.5,BORDER),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ("LEFTPADDING",(0,0),(-1,-1),6),("RIGHTPADDING",(0,0),(-1,-1),6),
        ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4)]))
    return t


def build(out_path, days=30):
    story = []
    band = Table([[Paragraph(
        '<font size=16 color="#FFFFFF"><b>System Resource Optimizer</b></font><br/>'
        f'<font size=10 color="#CADCFC">Mitigation analytics report  ·  last {days} days  ·  '
        f'generated {datetime.now():%d %B %Y, %H:%M}</font>', body)]], colWidths=[6.4*inch])
    band.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),INK),
        ("LEFTPADDING",(0,0),(-1,-1),16),("RIGHTPADDING",(0,0),(-1,-1),16),
        ("TOPPADDING",(0,0),(-1,-1),14),("BOTTOMPADDING",(0,0),(-1,-1),14)]))
    story += [band, Spacer(1, 14)]

    s = history.summary(days) or {}
    n     = s.get("n") or 0
    apps  = s.get("apps") or 0
    dayn  = s.get("days") or 0
    conf  = s.get("conf")
    mem   = s.get("mem") or 0
    story.append(Paragraph("Summary", h2))
    story.append(_stat_row([
        (f"{n}", "mitigations applied"),
        (f"{apps}", "distinct applications"),
        (f"{dayn}", "active days"),
        (f"{conf*100:.0f}%" if conf else "n/a", "mean confidence"),
        (f"{mem/1024:.1f} GB", "memory relieved"),
    ]))
    story.append(Spacer(1, 6))
    conf_txt = (f" at a mean forecast confidence of {conf*100:.0f}%" if conf
                else " (manual interventions carry no forecast confidence)")
    story.append(Paragraph(
        f"Over the reporting period the optimizer intervened {n} times across {apps} distinct "
        f"applications{conf_txt}. Every intervention was reversible and no application was "
        f"terminated.", body))

    story.append(Paragraph("Applications most often causing contention", h2))
    top = history.top_offenders(days, limit=10)
    story.append(_bar_chart(top, "process_name", "events", color=DEEP))
    if top:
        story.append(Spacer(1, 8))
        story.append(_table(
            ["Application", "Times mitigated", "Avg. memory (MB)", "Avg. confidence", "Last seen"],
            [[r["process_name"], r["events"], r["avg_mb"] or "—",
              (f'{r["avg_conf"]:.0f}%' if r["avg_conf"] else '—'),
              datetime.fromtimestamp(r["last_seen"]).strftime("%d %b %H:%M")] for r in top],
            widths=[2.0*inch, 1.05*inch, 1.15*inch, 1.1*inch, 1.1*inch]))

    story.append(Paragraph("Activity by day", h2))
    story.append(_bar_chart(history.by_day(days), "day", "events", color=TEAL, max_rows=14))

    story.append(Paragraph("Monthly history", h2))
    months = history.by_month(12)
    if months:
        story.append(_table(["Month", "Mitigations", "Distinct applications"],
                            [[m["month"], m["events"], m["apps"]] for m in months],
                            widths=[2.0*inch, 2.2*inch, 2.2*inch]))
    else:
        story.append(Paragraph("No monthly history recorded yet.", body))

    story.append(Paragraph("Action breakdown", h2))
    acts = history.by_action(days)
    if acts:
        story.append(_table(["Action", "Count"],
                            [[a["action"], a["n"]] for a in acts],
                            widths=[3.2*inch, 3.2*inch]))
    else:
        story.append(Paragraph("No actions recorded yet.", body))

    story.append(Paragraph("Most recent events", h2))
    rec = history.recent(15)
    if rec:
        story.append(_table(["When", "Action", "Trigger", "Application", "Memory (MB)"],
                            [[datetime.fromtimestamp(r["ts"]).strftime("%d %b %H:%M:%S"),
                              r["action"], r["trigger"] or "—",
                              r["process_name"] or "—",
                              f'{r["memory_mb"]:.1f}' if r["memory_mb"] else "—"] for r in rec],
                            widths=[1.4*inch, 0.9*inch, 1.0*inch, 2.0*inch, 1.1*inch]))
    else:
        story.append(Paragraph("No events recorded yet.", body))

    story.append(Spacer(1, 10))
    story.append(Paragraph(
        "Source: SQLite event store (sro_history.db) maintained by the background service. "
        "High-rate telemetry is written to flat files; mitigation events are recorded "
        "relationally so that they can be queried and reported.", body))

    def footer(c, doc):
        c.saveState(); c.setFont("Helvetica", 8); c.setFillColor(MUT)
        c.drawString(0.85*inch, 0.5*inch, "System Resource Optimizer — mitigation analytics")
        c.drawRightString(A4[0]-0.85*inch, 0.5*inch, "Page %d" % doc.page)
        c.restoreState()

    doc = SimpleDocTemplate(out_path, pagesize=A4,
                            leftMargin=0.85*inch, rightMargin=0.85*inch,
                            topMargin=0.7*inch, bottomMargin=0.7*inch,
                            title="SRO Mitigation Analytics Report")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Generate the SRO analytics report")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--out", default=os.path.join(os.path.expanduser("~"), "Desktop",
                                                  "SRO_Analytics_Report.pdf"))
    a = ap.parse_args()
    print("WROTE", build(a.out, a.days))
