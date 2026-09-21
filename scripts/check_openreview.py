"""Cross-check publications.html against OpenReview.

Pulls every OpenReview note authored by the group faculty (one request per
profile), matches them to the papers on the page by title, and reports where
the venue on the page disagrees with OpenReview, or where OpenReview shows the
paper as still under review / rejected. Papers with no OpenReview record are
listed separately (ACL-family, CVPR/ICCV, security venues do not use it).

Usage:
    source ~/.openreview_env   # exports OPENREVIEW_USERNAME / OPENREVIEW_PASSWORD
    python3 scripts/check_openreview.py [--years 2024 2025 2026] [--mismatches-only]
"""
import argparse, difflib, html, os, re, sys
import openreview

PUBS = os.path.join(os.path.dirname(__file__), "..", "publications.html")
FACULTY = ["~William_Yang_Wang1", "~Xin_Eric_Wang2", "~Xin_Eric_Wang1", "~Shiyu_Chang2",
           "~Shiyu_Chang1", "~Xifeng_Yan1", "~Wenbo_Guo1", "~Yuheng_Bu1"]
TAG = re.compile(r"<[^>]+>")


def norm(t):
    return re.sub(r"[^a-z0-9]", "", t.lower())


def val(content, key):
    v = content.get(key)
    return v.get("value", "") if isinstance(v, dict) else (v or "")


def parse_pubs(years):
    s = open(PUBS, encoding="utf-8").read()
    out, cur = [], None
    for tok in re.finditer(r'<h4>(\d{4})</h4>|<li class="li-paper">(.*?)</li>', s, flags=re.S):
        if tok.group(1):
            cur = int(tok.group(1)); continue
        if cur not in years:
            continue
        spans = re.findall(r"<span[^>]*>(.*?)</span>", tok.group(2), flags=re.S)
        title = html.unescape(TAG.sub("", spans[0])).strip()
        venue_html = spans[2] if len(spans) > 2 else ""
        b = re.search(r"<b>(.*?)</b>", venue_html, flags=re.S)
        tag = html.unescape(TAG.sub("", b.group(1) if b else venue_html)).strip()
        out.append(dict(year=cur, title=title, tag=tag))
    return out


def fetch_notes(client):
    """All notes by the faculty, keyed by normalized title. Prefer accepted records."""
    notes = {}
    for pid in FACULTY:
        try:
            got = client.get_all_notes(content={"authorids": pid})
        except Exception as e:
            print(f"  could not fetch {pid}: {e}", file=sys.stderr); continue
        for n in got:
            t = norm(val(n.content, "title"))
            if not t:
                continue
            st = status_of(n)
            if t not in notes or (st == "accepted" and status_of(notes[t]) != "accepted"):
                notes[t] = n
    return notes


def status_of(note):
    low = (val(note.content, "venue") + " " + val(note.content, "venueid")).lower()
    if "reject" in low or "withdrawn" in low or "desk" in low:
        return "rejected/withdrawn"
    if "submitted" in low or "under review" in low or "/submission" in low or not low.strip():
        return "under review"
    return "accepted"


def match(paper, notes, keys):
    want = norm(paper["title"])
    if want in notes:
        return notes[want]
    close = difflib.get_close_matches(want, keys, n=1, cutoff=0.9)
    return notes[close[0]] if close else None


def agrees(tag, venue):
    """Same venue if the abbreviation+year tokens match, ignoring order and filler words."""
    stop = {"of", "the", "by", "accepted", "conference", "poster", "spotlight", "oral", "regular", "track", "1"}
    toks = lambda t: {w for w in re.findall(r"[a-z0-9]+", t.lower()) if w not in stop}
    a, b = toks(tag), toks(venue)
    if not a or not b:
        return False
    return a <= b or b <= a or difflib.SequenceMatcher(None, norm(tag), norm(venue)).ratio() > 0.8


def make_client():
    """Log in, reusing a cached token: OpenReview allows only a few logins per minute."""
    base = "https://api2.openreview.net"
    cache = os.path.expanduser("~/.openreview_token")
    if os.path.exists(cache):
        try:
            c = openreview.api.OpenReviewClient(baseurl=base, token=open(cache).read().strip())
            c.get_note("oHR862dpMC")  # cheap request to validate the token
            return c
        except Exception:
            pass
    c = openreview.api.OpenReviewClient(baseurl=base,
        username=os.environ["OPENREVIEW_USERNAME"], password=os.environ["OPENREVIEW_PASSWORD"])
    with open(cache, "w") as fh:
        fh.write(c.token)
    os.chmod(cache, 0o600)
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", nargs="+", type=int, default=[2024, 2025, 2026])
    ap.add_argument("--mismatches-only", action="store_true")
    args = ap.parse_args()

    client = make_client()

    papers = parse_pubs(set(args.years))
    notes = fetch_notes(client)
    keys = list(notes)
    print(f"{len(notes)} OpenReview records for group faculty; checking {len(papers)} papers on the page")

    rows = {"ok": [], "mismatch": [], "not_accepted": [], "not_found": []}
    for p in papers:
        n = match(p, notes, keys)
        if n is None:
            rows["not_found"].append((p, None, None)); continue
        venue, st = val(n.content, "venue"), status_of(n)
        if st != "accepted":
            rows["not_accepted"].append((p, venue, st))
        elif agrees(p["tag"], venue):
            rows["ok"].append((p, venue, st))
        else:
            rows["mismatch"].append((p, venue, st))

    def show(key, header):
        if not rows[key]:
            return
        print(f"\n== {header} ({len(rows[key])}) ==")
        for p, venue, st in rows[key]:
            line = f"- {p['year']} {p['title'][:80]}\n    page: [{p['tag']}]"
            if venue is not None:
                line += f"  openreview: [{venue}]"
            if st and st != "accepted":
                line += f"  status: {st}"
            print(line)

    show("mismatch", "Venue on page differs from OpenReview")
    show("not_accepted", "OpenReview record is not an accepted paper")
    if not args.mismatches_only:
        show("not_found", "No OpenReview record found (venue may not use OpenReview)")
        show("ok", "Agrees with OpenReview")
    print(f"\nchecked {len(papers)} papers: {len(rows['ok'])} agree, {len(rows['mismatch'])} mismatch, "
          f"{len(rows['not_accepted'])} not accepted, {len(rows['not_found'])} not on OpenReview")


if __name__ == "__main__":
    main()
