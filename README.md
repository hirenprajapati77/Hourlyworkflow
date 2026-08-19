# NSE F&O Gainers/Losers - Automated 3x-Daily Snapshot & Comparison (GitHub Actions)

## Why GitHub Actions

Two other options were ruled out first:

- **Your Oracle VM**: it's a 1GB box already running PM2 (Next.js), PostgreSQL,
  Redis, and Fyers polling for `cprcalculatorhp`. Adding this job there risks
  OOM-killing your production trading app during market hours - not worth it
  for a nice-to-have report.
- **PythonAnywhere free tier**: as of the Jan 2026 restructure, brand-new free
  accounts have *no* scheduled-task feature at all (it moved to their paid
  Developer tier), and even older free accounts that kept it are limited to
  1 task/day and restricted outbound internet (an allowlist of specific
  domains) - `nseindia.com` almost certainly isn't on it.

GitHub Actions has neither problem: runners are ephemeral (no idle memory
cost, no shared risk with your trading app), have full outbound internet
access, and free-tier minutes comfortably cover three ~1-minute runs/day.

## How it works

1. A scheduled workflow (`.github/workflows/nse-fo-movers.yml`) fires at
   9:30 / 10:00 / 10:30 IST on market days.
2. `capture.py` calls NSE's own JSON API (`live-analysis-variations`)
   directly - the same call the nseindia.com website's JS makes - and saves
   a timestamped snapshot to `snapshots/`.
3. `analyze.py` compares the new snapshot against every earlier snapshot
   from the same day and writes the 7-section report to `reports/`.
4. The workflow commits both back to the repo, so every day's snapshots and
   reports are just... in your git history. No external database, no
   ephemeral-runner data loss.

## Before relying on this

I couldn't test against `nseindia.com` from my sandbox (outside my allowed
network egress), so this is built defensively rather than blind-tested.
**First run should be via the manual trigger** (see below) so you can check
it actually works before trusting the schedule.

If `capture.py` throws a `KeyError` about `FOSec` or a field name, the
`--debug` flag (already on in the workflow) dumps the full raw API response
into `snapshots/raw_debug_*.json` as an uploaded workflow artifact - open
that, find the real key names, and fix them in one place: `FIELD_MAP` and
`FO_BUCKET_KEY` near the top of `capture.py`. Paste me the raw JSON (or just
the top-level keys) if you'd rather I fix the mapping directly.

## Setup

```bash
# from an empty local folder
git init nse-fo-movers && cd nse-fo-movers
# copy in capture.py, analyze.py, requirements.txt, .gitignore,
# .github/workflows/nse-fo-movers.yml from this delivery
git add .
git commit -m "Initial NSE F&O movers automation"
```

Create a new empty GitHub repo (public or private, either works - a private
repo is fine and free) and push:

```bash
git remote add origin https://github.com/<you>/nse-fo-movers.git
git branch -M main
git push -u origin main
```

Then in the repo's Settings -> Actions -> General -> Workflow permissions,
select "Read and write permissions" - this lets the workflow commit
snapshots/reports back to the repo (default is read-only, which would make
the "Commit results back to repo" step fail silently).

No secrets or API keys are needed - the NSE endpoint used doesn't require
authentication.

## Test it manually before trusting the schedule

Go to the repo's Actions tab -> "NSE F&O Gainers/Losers" workflow ->
"Run workflow" button (this works because of the `workflow_dispatch`
trigger in the YAML). This runs the exact same steps the schedule will, any
time you want - use it now to confirm the NSE API call actually succeeds,
and again anytime during market hours to sanity-check a report.

Once one manual run succeeds, the 3 scheduled runs will start firing
automatically on the next market day.

## Schedule notes

- Cron times in the workflow are UTC (4:00, 4:30, 5:00 = 9:30/10:00/10:30
  IST). GitHub Actions doesn't support IST directly in `cron:`.
- GitHub's scheduler is best-effort and can run a few minutes late during
  high load - don't rely on it for second-precision timing.
- No holiday calendar is built in. On an NSE holiday, `capture.py` fails to
  get meaningful data; the workflow step is marked `continue-on-error`, so
  the job doesn't fail red, it just skips the commit for that run.

## Local testing (optional)

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python3 capture.py --debug
python3 analyze.py
```

## About screenshots

`capture.py --screenshot` exists but is off by default in the workflow.
GitHub Actions runners have enough RAM (~7GB) that Playwright is actually
fine here, unlike the VM this was first scoped for - so if you want a visual
archive alongside the JSON data, add `playwright>=1.44.0` to
`requirements.txt`, a `playwright install --with-deps chromium` step to the
workflow before the capture step, and change the capture step to
`python3 capture.py --debug --screenshot`. Say the word if you want me to
wire that in.

## Sector mapping

`analyze.py` has a hand-built `SECTOR_MAP` covering common F&O names
(Banking, IT, Auto, Metal, Pharma, FMCG, etc). Anything not in the map falls
into "Other/Unmapped" rather than crashing. Extend the dict as you notice
gaps in the sector-rotation section of the report.

## Possible next steps (not built yet, say the word if you want these)

- Push the generated report to Telegram at the end of each run (you already
  have bot infrastructure wired up for event-risk suppression in
  `cprcalculatorhp` - reusing that would just need the bot token/chat ID as
  a GitHub Actions secret and a `curl` step in the workflow).
- A 4th end-of-day snapshot (e.g. 3:00 PM) for a full-day rotation summary.
- Persist snapshots into your existing PostgreSQL/Prisma schema instead of
  git-committed JSON files, if you want this queryable alongside your other
  signals.
