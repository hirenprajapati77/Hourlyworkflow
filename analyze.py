#!/usr/bin/env python3
"""
Compares the latest FO gainers/losers snapshot against all earlier snapshots
from the same day and generates the report:

  Gainers Analysis / Losers Analysis / Direct High Rank Entries /
  Sector Rotation / Strongest Bullish Theme / Weakest Bearish Theme /
  Final Market Conclusion

Run right after capture.py, same day:
    python3 analyze.py                     # analyzes today, most recent snapshot vs earlier ones
    python3 analyze.py --date 2026-08-19    # analyze a specific day
"""

import argparse
import json
from datetime import date
from pathlib import Path

# Minimal sector map for common F&O names. Extend as needed - anything not
# in here falls into "Other/Unmapped" rather than breaking the script.
SECTOR_MAP = {
    "RELIANCE": "Energy", "ONGC": "Energy", "BPCL": "Energy", "IOC": "Energy",
    "HDFCBANK": "Banking", "ICICIBANK": "Banking", "SBIN": "Banking", "AXISBANK": "Banking",
    "KOTAKBANK": "Banking", "BANKBARODA": "Banking", "PNB": "Banking", "INDUSINDBK": "Banking",
    "TCS": "IT", "INFY": "IT", "WIPRO": "IT", "HCLTECH": "IT", "TECHM": "IT", "LTIM": "IT",
    "SUNPHARMA": "Pharma", "DRREDDY": "Pharma", "CIPLA": "Pharma", "DIVISLAB": "Pharma", "AUROPHARMA": "Pharma",
    "TATAMOTORS": "Auto", "MARUTI": "Auto", "M&M": "Auto", "BAJAJ-AUTO": "Auto", "EICHERMOT": "Auto",
    "HEROMOTOCO": "Auto", "TVSMOTOR": "Auto",
    "TATASTEEL": "Metal", "JSWSTEEL": "Metal", "HINDALCO": "Metal", "VEDL": "Metal", "SAIL": "Metal",
    "NTPC": "Power", "POWERGRID": "Power", "TATAPOWER": "Power", "ADANIPOWER": "Power",
    "DLF": "Realty", "GODREJPROP": "Realty", "OBEROIRLTY": "Realty",
    "ADANIENT": "Adani Group", "ADANIPORTS": "Adani Group", "ADANIGREEN": "Adani Group",
    "ITC": "FMCG", "HINDUNILVR": "FMCG", "NESTLEIND": "FMCG", "BRITANNIA": "FMCG", "TATACONSUM": "FMCG",
    "BAJFINANCE": "NBFC/Finance", "BAJAJFINSV": "NBFC/Finance", "CHOLAFIN": "NBFC/Finance", "SBILIFE": "Finance",
    "PIDILITIND": "Chemicals", "UPL": "Chemicals", "SRF": "Chemicals",
    "ULTRACEMCO": "Cement", "SHREECEM": "Cement", "AMBUJACEM": "Cement", "ACC": "Cement",
}


def sector_of(symbol: str) -> str:
    return SECTOR_MAP.get(symbol, "Other/Unmapped")


def load_snapshots_for_day(root: Path, day: str) -> list[dict]:
    files = sorted((root / "snapshots").glob(f"FO_{day}_*.json"))
    snaps = []
    for f in files:
        snaps.append(json.loads(f.read_text()))
    return snaps


def index_by_symbol(rows: list[dict]) -> dict:
    return {r["symbol"]: r for r in rows}


def analyze_side(side: str, current: list[dict], previous_snaps: list[dict]) -> dict:
    """side = 'gainers' or 'losers'. Returns structured findings."""
    cur_idx = index_by_symbol(current)
    prev_all_symbols = set()
    prev_idx_by_time = []
    for snap in previous_snaps:
        rows = snap[side]
        prev_idx_by_time.append((snap["label"], index_by_symbol(rows)))
        prev_all_symbols.update(cur_idx.keys() & index_by_symbol(rows).keys() | set())
    prev_symbols_ever_seen = set()
    for _, idx in prev_idx_by_time:
        prev_symbols_ever_seen.update(idx.keys())

    new_entrants = [s for s in cur_idx if s not in prev_symbols_ever_seen]

    # direct high-rank entries: new to top-N in current snapshot, absent in all prior
    direct_high_rank = []
    for s in new_entrants:
        rank = cur_idx[s]["rank"]
        if rank <= 10:
            direct_high_rank.append({"symbol": s, "rank": rank, "pct_change": cur_idx[s]["pct_change"]})

    # rank movers: compare current rank vs immediately-previous snapshot rank
    sharp_up, sharp_down, repeat_leaders = [], [], []
    if prev_idx_by_time:
        last_label, last_idx = prev_idx_by_time[-1]
        for s, row in cur_idx.items():
            if s in last_idx:
                delta = last_idx[s]["rank"] - row["rank"]  # positive = moved up (lower rank number)
                if delta >= 5:
                    sharp_up.append({"symbol": s, "from_rank": last_idx[s]["rank"], "to_rank": row["rank"], "delta": delta})
                elif delta <= -5:
                    sharp_down.append({"symbol": s, "from_rank": last_idx[s]["rank"], "to_rank": row["rank"], "delta": delta})
        # repeat leaders: in top-5 in EVERY snapshot including current
        for s, row in cur_idx.items():
            if row["rank"] <= 5:
                in_all_top5 = all(
                    s in idx and idx[s]["rank"] <= 5 for _, idx in prev_idx_by_time
                )
                if in_all_top5 and prev_idx_by_time:
                    repeat_leaders.append(s)

    sharp_up.sort(key=lambda x: -x["delta"])
    sharp_down.sort(key=lambda x: x["delta"])

    return {
        "new_entrants": new_entrants,
        "direct_high_rank": sorted(direct_high_rank, key=lambda x: x["rank"]),
        "sharp_up": sharp_up,
        "sharp_down": sharp_down,
        "repeat_leaders": repeat_leaders,
        "top10": current[:10],
    }


def sector_rotation(gainers: list[dict], losers: list[dict]) -> dict:
    g_sectors, l_sectors = {}, {}
    for r in gainers[:15]:
        sec = sector_of(r["symbol"])
        g_sectors.setdefault(sec, []).append(r["symbol"])
    for r in losers[:15]:
        sec = sector_of(r["symbol"])
        l_sectors.setdefault(sec, []).append(r["symbol"])
    return {"gainer_sectors": g_sectors, "loser_sectors": l_sectors}


def render_report(day: str, snap_label: str, g: dict, l: dict, rot: dict, current: dict) -> str:
    lines = []
    lines.append(f"# NSE F&O Gainers/Losers - {day} {snap_label.split('_')[-1]} IST\n")

    lines.append("## 📈 Gainers Analysis")
    lines.append("**Current Top 10:**")
    for r in g["top10"]:
        lines.append(f"- #{r['rank']} {r['symbol']} ({r['pct_change']:+.2f}%)")
    if g["new_entrants"]:
        lines.append(f"\n**New entrants this session:** {', '.join(g['new_entrants'])}")
    if g["sharp_up"]:
        lines.append("\n**Sharp rank climbs:**")
        for m in g["sharp_up"][:8]:
            lines.append(f"- {m['symbol']}: rank {m['from_rank']} → {m['to_rank']} (+{m['delta']})")
    if g["sharp_down"]:
        lines.append("\n**Fading gainers (dropping ranks):**")
        for m in g["sharp_down"][:8]:
            lines.append(f"- {m['symbol']}: rank {m['from_rank']} → {m['to_rank']} ({m['delta']})")
    if g["repeat_leaders"]:
        lines.append(f"\n**Consistent top-5 leaders across all snapshots:** {', '.join(g['repeat_leaders'])}")

    lines.append("\n## 📉 Losers Analysis")
    lines.append("**Current Top 10:**")
    for r in l["top10"]:
        lines.append(f"- #{r['rank']} {r['symbol']} ({r['pct_change']:+.2f}%)")
    if l["new_entrants"]:
        lines.append(f"\n**New weak entrants this session:** {', '.join(l['new_entrants'])}")
    if l["sharp_up"]:
        lines.append("\n**Sharp downside acceleration (worsening rank):**")
        for m in l["sharp_up"][:8]:
            lines.append(f"- {m['symbol']}: rank {m['from_rank']} → {m['to_rank']}")
    if l["sharp_down"]:
        lines.append("\n**Recovering losers (climbing out of bottom ranks):**")
        for m in l["sharp_down"][:8]:
            lines.append(f"- {m['symbol']}: rank {m['from_rank']} → {m['to_rank']}")
    if l["repeat_leaders"]:
        lines.append(f"\n**Consistent bottom-5 laggards across all snapshots:** {', '.join(l['repeat_leaders'])}")

    lines.append("\n## 🚀 Direct High Rank Entries")
    all_direct = g["direct_high_rank"] + l["direct_high_rank"]
    if all_direct:
        for d in all_direct:
            side = "Gainer" if d in g["direct_high_rank"] else "Loser"
            lines.append(f"- {d['symbol']}: entered directly at rank #{d['rank']} ({side}, {d['pct_change']:+.2f}%) - no earlier presence")
    else:
        lines.append("- None this session - all current top ranks were already present in earlier snapshots.")

    lines.append("\n## 🔄 Sector Rotation")
    lines.append("**Gainer-side sector concentration (top 15):**")
    for sec, syms in sorted(rot["gainer_sectors"].items(), key=lambda kv: -len(kv[1])):
        lines.append(f"- {sec}: {', '.join(syms)}")
    lines.append("\n**Loser-side sector concentration (top 15):**")
    for sec, syms in sorted(rot["loser_sectors"].items(), key=lambda kv: -len(kv[1])):
        lines.append(f"- {sec}: {', '.join(syms)}")

    bull_sector = max(rot["gainer_sectors"].items(), key=lambda kv: len(kv[1]), default=(None, []))
    bear_sector = max(rot["loser_sectors"].items(), key=lambda kv: len(kv[1]), default=(None, []))

    lines.append("\n## 🟢 Strongest Bullish Theme")
    if bull_sector[0]:
        lines.append(f"**{bull_sector[0]}** leads gainers with {len(bull_sector[1])} names in the top 15: {', '.join(bull_sector[1])}")
    else:
        lines.append("No clear sector concentration yet.")

    lines.append("\n## 🔴 Weakest Bearish Theme")
    if bear_sector[0]:
        lines.append(f"**{bear_sector[0]}** leads losers with {len(bear_sector[1])} names in the top 15: {', '.join(bear_sector[1])}")
    else:
        lines.append("No clear sector concentration yet.")

    lines.append("\n## 📊 Final Market Conclusion")
    conclusion_bits = []
    if g["repeat_leaders"]:
        conclusion_bits.append(f"sustained strength in {', '.join(g['repeat_leaders'][:3])}")
    if g["direct_high_rank"]:
        conclusion_bits.append(f"fresh momentum bursts in {', '.join(d['symbol'] for d in g['direct_high_rank'][:3])}")
    if bull_sector[0]:
        conclusion_bits.append(f"{bull_sector[0]} sector rotation on the buy side")
    if bear_sector[0]:
        conclusion_bits.append(f"pressure building in {bear_sector[0]}")
    if conclusion_bits:
        lines.append("Session theme: " + "; ".join(conclusion_bits) + ".")
    else:
        lines.append("Not enough snapshots yet to draw a confident conclusion - re-check after the next capture.")
    lines.append(
        "\n*Note: This is a mechanical read of rank/price movement across F&O gainers/losers snapshots, "
        "not a trade recommendation. Cross-check against your existing CPR/BTST signals before acting.*"
    )

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="YYYY-MM-DD, defaults to today")
    args = parser.parse_args()

    root = Path(__file__).parent
    day = args.date or date.today().isoformat()

    snaps = load_snapshots_for_day(root, day)
    if not snaps:
        print(f"No snapshots found for {day}. Run capture.py first.")
        return
    if len(snaps) < 2:
        print(f"Only 1 snapshot found for {day} - need at least 2 to compare. "
              f"Report will still generate but with limited findings.")

    current = snaps[-1]
    previous = snaps[:-1]

    g = analyze_side("gainers", current["gainers"], previous)
    l = analyze_side("losers", current["losers"], previous)
    rot = sector_rotation(current["gainers"], current["losers"])

    report = render_report(day, current["label"], g, l, rot, current)

    out_path = root / "reports" / f"report_{current['label']}.md"
    out_path.write_text(report)
    print(report)
    print(f"\n\n[Saved to {out_path}]")


if __name__ == "__main__":
    main()
