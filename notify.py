#!/usr/bin/env python3
"""Email the sale/auction alerts research.py wrote (via Resend, the same service as the trading bot).

    python3 notify.py alerts.json             # send, if RESEND_API_KEY, EMAIL_FROM and EMAIL_TO are set
    python3 notify.py alerts.json --dry-run   # print the email instead
"""
import html
import json
import os
import sys
import urllib.request
from pathlib import Path

APP = "https://blewend50-del.github.io/car-bucket-list/"


def build(alerts):
    cars = {}
    for a in alerts:
        cars.setdefault(a["car_id"], []).append(a)
    n = len(cars)
    subject = (f"For sale / auction news: {alerts[0]['car']}" if n == 1
               else f"For sale / auction news on {n} cars")
    parts, text = [], []
    for car_id, items in cars.items():
        name = items[0]["car"] + (f" ({items[0]['model']})" if items[0]["model"] else "")
        parts.append(f'<h3 style="margin:18px 0 6px"><a href="{APP}#{html.escape(car_id)}">{html.escape(name)}</a></h3><ul>')
        text.append(name)
        for a in items:
            parts.append(f'<li><a href="{html.escape(a["url"])}">{html.escape(a["title"])}</a>'
                         f' <span style="color:#777">{html.escape(a["source"])} · {html.escape(a["date"] or "")}</span></li>')
            text.append(f"  - {a['title']} ({a['source']}, {a['date'] or ''})\n    {a['url']}")
        parts.append("</ul>")
    body = ('<div style="font:15px/1.45 -apple-system,Segoe UI,sans-serif">'
            '<p>New sale or auction mentions from today\'s car research:</p>' + "".join(parts) +
            f'<p style="color:#777;font-size:13px">Open the <a href="{APP}">Car Bucket List</a> for everything else it found.</p></div>')
    return subject, body, "New sale or auction mentions from today's car research:\n\n" + "\n".join(text) + f"\n\n{APP}"


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("alerts.json")
    alerts = json.loads(path.read_text()) if path.exists() else []
    if not alerts:
        print("No sale alerts.")
        return
    subject, body, text = build(alerts)
    if "--dry-run" in sys.argv:
        print(subject + "\n\n" + text)
        return
    key, frm, to = (os.environ.get(k) for k in ("RESEND_API_KEY", "EMAIL_FROM", "EMAIL_TO"))
    if not (key and frm and to):
        print("Email skipped: add RESEND_API_KEY, EMAIL_FROM and EMAIL_TO as repository secrets.")
        return
    req = urllib.request.Request("https://api.resend.com/emails", method="POST",
                                 data=json.dumps({"from": frm, "to": [to], "subject": subject, "html": body, "text": text}).encode(),
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                                          "User-Agent": "car-bucket-list/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"Emailed {len(alerts)} alert(s) ({r.status}).")
    except Exception as e:  # never fail the run over email
        print(f"Email failed: {e}")


if __name__ == "__main__":
    main()
