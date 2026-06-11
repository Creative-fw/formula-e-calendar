#!/usr/bin/env python3
"""
Formula E -> auto-updating ICS feed. EVERGREEN.

Source: the official Pulselive API behind fiaformulae.com.
Auto-detects whichever championship season is flagged "Present",
so the feed rolls over to the next season with zero maintenance.

Sessions arrive with explicit start/finish times and a per-session
GMT offset -- no timezone repairs needed. The nine qualifying
micro-sessions (Group A/B, duels, final) are merged into a single
"Qualifying" block (first start -> last finish), matching how the
session is actually broadcast.
"""
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.formula-e.pulselive.com/formula-e/v1"


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (fe-ics-sync)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def parse_offset(s):
    m = re.match(r"(-?)(\d{1,2}):(\d{2})", s or "00:00")
    if not m:
        return timezone.utc
    sign = -1 if m.group(1) == "-" else 1
    return timezone(sign * timedelta(hours=int(m.group(2)), minutes=int(m.group(3))))


def session_dt(s, field_date, field_time):
    tz = parse_offset(s.get("offsetGMT", "00:00"))
    dt = datetime.fromisoformat(f"{s[field_date]}T{s[field_time]}:00").replace(tzinfo=tz)
    return dt.astimezone(timezone.utc)


def esc(x):
    return x.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;")


def main(out_path):
    champs = fetch_json(f"{API}/championships").get("championships", [])
    current = next((c for c in champs if c.get("status") == "Present"), None)
    if not current:
        print("ABORT: no 'Present' championship found.", file=sys.stderr)
        sys.exit(1)
    season = current["name"].title().replace("Season ", "")  # e.g. 2025-2026

    races = fetch_json(f"{API}/races?championshipId={current['id']}").get("races", [])
    events = []
    for r in races:
        city = r.get("city", "").strip() or r.get("name", "E-Prix")
        gp_name = f"{city} E-Prix"
        rnd = r.get("sequence", 0)
        try:
            sessions = fetch_json(f"{API}/races/{r['id']}/sessions").get("sessions", [])
        except Exception as e:
            print(f"WARN: sessions for '{gp_name}' R{rnd} failed: {e}", file=sys.stderr)
            continue

        quals, others = [], []
        for s in sessions:
            if not (s.get("sessionDate") and s.get("startTime") and s.get("finishTime")):
                continue
            (quals if s["sessionName"].lower().startswith("qual") else others).append(s)

        for s in others:
            name = s["sessionName"].strip()
            start = session_dt(s, "sessionDate", "startTime")
            end = session_dt(s, "sessionDate", "finishTime")
            if end <= start:
                end = start + timedelta(hours=1)
            is_race = name.lower().startswith("race")
            events.append((start, end, gp_name, name, rnd, is_race))

        if quals:
            starts = [session_dt(s, "sessionDate", "startTime") for s in quals]
            ends = [session_dt(s, "sessionDate", "finishTime") for s in quals]
            events.append((min(starts), max(ends), gp_name, "Qualifying", rnd, False))

    if len(events) < 40:
        print(f"ABORT: only {len(events)} events - refusing to overwrite feed.", file=sys.stderr)
        sys.exit(1)

    events.sort(key=lambda e: (e[0], e[4]))
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0",
             "PRODID:-//LGS//Formula E Auto-Sync//EN",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             "X-WR-CALNAME:Formula E",
             f"X-WR-CALDESC:ABB FIA Formula E World Championship (season {esc(season)}) - "
             "auto-synced daily from the official Formula E API\\, auto-rolls to new seasons",
             "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
             "X-PUBLISHED-TTL:PT12H"]

    for start, end, gp_name, session, rnd, is_race in events:
        flag = "\U0001F3C1 " if is_race else ""
        uid = re.sub(r"[^a-z0-9]+", "-", f"{season}-r{rnd}-{gp_name}-{session}".lower()).strip("-")
        lines += ["BEGIN:VEVENT",
                  f"UID:{uid}@lgs-formula-e",
                  f"DTSTAMP:{now}",
                  f"DTSTART:{start.strftime('%Y%m%dT%H%M%S')}Z",
                  f"DTEND:{end.strftime('%Y%m%dT%H%M%S')}Z",
                  f"SUMMARY:{esc(flag + 'FE ' + gp_name + ' - ' + session)}",
                  f"LOCATION:{esc(gp_name.replace(' E-Prix',''))}",
                  f"DESCRIPTION:Round {rnd}\\, season {esc(season)}. Times auto-convert "
                  "to your timezone. Auto-synced daily."]
        if is_race:
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY",
                      f"DESCRIPTION:{esc(gp_name)} race starts in 1 hour",
                      "TRIGGER:-PT1H", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")

    with open(out_path, "w", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")
    print(f"OK: wrote {len(events)} events (season {season}) -> {out_path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "FormulaE.ics")
