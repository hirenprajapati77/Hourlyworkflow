#!/usr/bin/env python3
"""
NSE F&O Gainers/Losers snapshot capture.

Runs on a GitHub Actions scheduled workflow (see .github/workflows/nse-fo-movers.yml)
rather than a self-hosted server - so there's no RAM budget to worry about and no
internet allowlist restriction (unlike PythonAnywhere free tier, which was ruled out
for both reasons). Talks to NSE's own JSON API directly via requests - the same
endpoint the nseindia.com website's JS calls to render the "Top Gainers & Losers" ->
"F&O Securities" table. No browser needed for the data itself.

Defensive parsing: on first run (or with --debug), dumps the FULL raw JSON response
next to the snapshot so field names can be verified/adjusted in FIELD_MAP below
without guessing blind.

Usage:
    python3 capture.py                # normal run, writes snapshot
    python3 capture.py --debug        # also dumps raw API response
    python3 capture.py --screenshot   # also grab a Playwright screenshot -
                                       # viable here since Actions runners have
                                       # ~7GB RAM, unlike the VM this was
                                       # originally scoped for. Off by default
                                       # to keep the workflow fast; enable if
                                       # you want a visual record too.
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

IST = ZoneInfo("Asia/Kolkata")

BASE = "https://www.nseindia.com"
GAINERS_URL = f"{BASE}/api/live-analysis-variations?index=gainers"
LOSERS_URL = f"{BASE}/api/live-analysis-variations?index=loosers"  # NSE's own (mis)spelling

# The FO securities sub-bucket key inside the API response.
# Confirmed via NSE's public docs mirrors as of Aug 2026: response is
# keyed by security-size / index legend buckets, one of which is FOSec.
FO_BUCKET_KEY = "FOSec"

# ---- Field mapping -----------------------------------------------------
# If NSE tweaks field names, adjust ONLY this dict - nothing else in the
# script needs to change. Run with --debug once to see the real raw keys
# in snapshots/raw_debug_*.json and fix these if a KeyError shows up.
FIELD_MAP = {
    "symbol": "symbol",
    "series": "series",
    "ltp": "ltp",
    "pct_change": "netPrice",       # % change field
    "prev_close": "previousPrice",
    "volume": "tradedQuantity",
    "value": "turnoverInLakhs",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.nseindia.com/market-data/top-gainers-losers",
    "Connection": "keep-alive",
}

MAX_RETRIES = 3
RETRY_DELAY_SEC = 4


def build_session() -> requests.Session:
    """Warm up cookies the way a real browser would before hitting the API."""
    s = requests.Session()
    s.headers.update(HEADERS)
    # NSE issues bot-protection cookies on the homepage; the API call fails
    # (401/403) without them. Two warm-up hits mirrors normal navigation.
    s.get(BASE, timeout=10)
    s.get(f"{BASE}/market-data/top-gainers-losers", timeout=10)
    return s


def fetch_json(session: requests.Session, url: str) -> dict:
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=12)
            if resp.status_code == 200:
                try:
                    return resp.json()
                except json.JSONDecodeError:
                    # NSE returned 200 but not JSON - almost always a block/challenge
                    # page (HTML) rather than the API payload. Surface the actual
                    # body so we can see what NSE is sending instead of guessing.
                    snippet = resp.text[:500].replace("\n", " ")
                    last_err = (
                        f"HTTP 200 but non-JSON body (likely a bot-block page). "
                        f"Content-Type: {resp.headers.get('Content-Type')}. "
                        f"First 500 chars: {snippet!r}"
                    )
            else:
                snippet = resp.text[:500].replace("\n", " ")
                last_err = f"HTTP {resp.status_code}. First 500 chars: {snippet!r}"
        except requests.RequestException as e:
            last_err = f"Request exception: {e}"
        print(f"[attempt {attempt}/{MAX_RETRIES}] {url} -> {last_err}", file=sys.stderr)
        # bot-check cookies can expire mid-retry; rebuild session before next try
        time.sleep(RETRY_DELAY_SEC)
        session = build_session()
    raise RuntimeError(f"Failed to fetch {url} after {MAX_RETRIES} attempts: {last_err}")


def extract_fo_rows(raw: dict) -> list[dict]:
    bucket = raw.get(FO_BUCKET_KEY)
    if bucket is None:
        # Structure changed - fail loudly rather than silently returning nothing.
        raise KeyError(
            f"'{FO_BUCKET_KEY}' not found in API response. Top-level keys were: "
            f"{list(raw.keys())}. Run with --debug and check the raw dump."
        )
    # Some NSE endpoints nest one level deeper (e.g. {"data": [...]}))
    if isinstance(bucket, dict) and "data" in bucket:
        bucket = bucket["data"]
    rows = []
    for item in bucket:
        row = {}
        for out_key, src_key in FIELD_MAP.items():
            row[out_key] = item.get(src_key)
        rows.append(row)
    return rows


def rank_and_clean(rows: list[dict]) -> list[dict]:
    """Sort by % change descending (gainers) - callers reverse for losers input."""
    cleaned = [r for r in rows if r.get("symbol") and r.get("pct_change") is not None]
    cleaned.sort(key=lambda r: float(r["pct_change"]), reverse=True)
    for i, r in enumerate(cleaned, start=1):
        r["rank"] = i
    return cleaned


def take_screenshot(out_path: Path) -> bool:
    """Optional, off by default. Launches Chromium briefly and exits immediately.
    Only enable via --screenshot if you've confirmed VM headroom (see README)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[screenshot] playwright not installed, skipping.", file=sys.stderr)
        return False

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-gpu", "--single-process", "--no-zygote"],
            )
            page = browser.new_page(viewport={"width": 1400, "height": 1000})
            page.goto(
                "https://www.nseindia.com/market-data/top-gainers-losers",
                timeout=30000,
                wait_until="networkidle",
            )
            # Select "Securities in F&O" tab/filter
            try:
                page.get_by_text("Securities in F&O", exact=False).first.click(timeout=5000)
                page.wait_for_timeout(2000)
            except Exception:
                pass  # tab label may differ; screenshot still captured for manual check
            locator = page.locator("text=Top Gainers").first
            locator.scroll_into_view_if_needed(timeout=5000)
            page.screenshot(path=str(out_path))
            browser.close()
        return True
    except Exception as e:
        print(f"[screenshot] failed: {e}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="dump raw API JSON")
    parser.add_argument("--screenshot", action="store_true", help="also capture a screenshot")
    args = parser.parse_args()

    root = Path(__file__).parent
    now = datetime.now(IST)
    stamp = now.strftime("%Y-%m-%d_%H%M")

    session = build_session()

    raw_gainers = fetch_json(session, GAINERS_URL)
    raw_losers = fetch_json(session, LOSERS_URL)

    if args.debug:
        (root / "snapshots" / f"raw_debug_gainers_{stamp}.json").write_text(
            json.dumps(raw_gainers, indent=2)
        )
        (root / "snapshots" / f"raw_debug_losers_{stamp}.json").write_text(
            json.dumps(raw_losers, indent=2)
        )

    gainers = rank_and_clean(extract_fo_rows(raw_gainers))
    losers = rank_and_clean(extract_fo_rows(raw_losers))
    losers.sort(key=lambda r: float(r["pct_change"]))  # most negative first
    for i, r in enumerate(losers, start=1):
        r["rank"] = i

    snapshot = {
        "captured_at_ist": now.isoformat(),
        "label": stamp,
        "gainers": gainers,
        "losers": losers,
    }

    out_path = root / "snapshots" / f"FO_{stamp}.json"
    out_path.write_text(json.dumps(snapshot, indent=2))
    print(f"Saved snapshot: {out_path} ({len(gainers)} gainers, {len(losers)} losers)")

    if args.screenshot:
        shot_path = root / "screenshots" / f"FO_{stamp}.png"
        ok = take_screenshot(shot_path)
        print(f"Screenshot: {'saved to ' + str(shot_path) if ok else 'skipped/failed'}")


if __name__ == "__main__":
    main()
