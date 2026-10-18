# UAE Job Copilot

A daily pipeline that searches UAE job boards for AI/ML roles, scores each listing
against your candidate profile with an LLM, preps cover letters + resume bullets for
the best matches, and hands you a digest you can actually act on.

```
search (LinkedIn/Indeed/Bayt/GulfTalent)
   → fetch job descriptions
   → LLM score 0–100 + must_apply flag
   → prep cover letter + resume bullets (score ≥ 70)
   → markdown digest + delivery (Telegram / email / Slack)
   → track ready → applied in SQLite
   → review in the local web UI
```

The daily cycle stops at digest by default. Add `--auto-apply` (or `AUTO_APPLY=1`)
and it submits the top ready applications too — see **Auto-apply** below.

## Setup

```bash
pip3 install -r requirements.txt
# optional, for --playwright scraping of sites that block plain requests:
python3 -m playwright install chromium

cp .env.example .env   # then fill in your keys
cp config/profile.example.py config/profile.py   # then fill in your details
```

`.env` and `config/profile.py` are both gitignored — your keys and personal
details never leave this machine.

`.env` is fully optional per feature:

| Keys | Enables |
|---|---|
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `OLLAMA_*` | AI scoring & writing. Fallback chain: Claude → OpenAI → local Ollama. |
| `QUERIES`, `JOB_LOCATION`, `SOURCES`, `MIN_SCORE`, `PREP_LIMIT` | Search/threshold tuning |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Digest → Telegram |
| `SMTP_HOST` (+ `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_TO`) | Digest → email |
| `SLACK_WEBHOOK_URL` | Digest → Slack |
| `COPILOT_FLAGS` | Extra flags for the scheduled run (e.g. `--playwright`) |

Also put your profile in `config/profile.py` — that's what the LLM scores against.

## Usage

```bash
python3 main.py run          # full daily cycle: search → score → prep → digest
python3 main.py run --auto-apply   # same, but also submit ready applications
python3 main.py search       # search + score only
python3 main.py prep         # generate cover letters for top matches
python3 main.py autoapply    # submit ready applications now (no search/prep)
python3 main.py digest       # build today's digest (and deliver it)
python3 main.py stats        # tracker stats
python3 main.py ready        # print applications ready to submit
python3 main.py applied <job_id>   # mark one as applied
python3 main.py serve        # review UI at http://127.0.0.1:8787
python3 main.py linkedin-login   # one-time: save LinkedIn session for Easy Apply
python3 main.py indeed-login     # one-time: save Indeed session for Easy Apply
```

Useful flags (`python3 main.py <cmd> --help` for all):

```bash
python3 main.py search --sources linkedin,bayt --queries "LLM Engineer,Agentic AI"
python3 main.py search --desc-limit 0        # skip description fetching
python3 main.py search --playwright          # browser scrape too (slow)
python3 main.py prep --min-score 60 --limit 10
python3 main.py prep --force                 # regenerate existing cover letters
python3 main.py autoapply --apply-limit 5 --apply-min-score 80
python3 main.py autoapply --apply-mode email # only jobs with an HR email in the description
python3 main.py digest --no-notify
```

Environment defaults are in `config/settings.py`; flags override them.

## Auto-apply

Submits prepped applications instead of just queueing them. The review UI has
a one-click **▶ Start Automation** button that runs the whole cycle
(search → score → prep → auto-apply → digest email) and shows live status:

```bash
python3 main.py serve                          # open http://127.0.0.1:8787, click the button
python3 main.py autoapply                      # submit now (up to 30, score >= 60)
python3 main.py run --auto-apply               # include it in the daily cycle
AUTO_APPLY=1 python3 main.py run               # same, via .env / environment
COPILOT_FLAGS=--auto-apply                     # for the launchd schedule (set in .env)
```

The digest now opens with an **Applied Today** section — every company the
copilot applied to in the last 24h, with score + link. Configure SMTP in `.env`
(`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_TO`) and that
digest lands in your inbox after every run.

Safety rails:

- Only jobs with a prepped application in `ready` status are touched — never
  re-applies anything already marked `applied`.
- Capped per run (`--apply-limit`, default 3; `AUTO_APPLY_LIMIT`) and gated on
  score (`--apply-min-score`, default `MIN_SCORE`; `AUTO_APPLY_MIN_SCORE`).
- `--apply-mode fill` (default) drives the browser: autofills, clicks Submit,
  and marks applied only on a detected confirmation. Submit clicked but no
  confirmation → marked applied with a "verify manually" note (a retry would
  send a duplicate).
- Failures (CAPTCHA, drifted selectors, network) stay `ready` and retry on the
  next run; a failure screenshot lands in `logs/`.
- `--apply-mode email` sends to the HR address found in the description
  instead — fully headless-friendly, jobs without an HR email are skipped.
- `--headless` hides the browser window (default: headed, so you can watch;
  `AUTO_APPLY_HEADLESS=1` for the scheduled run).

Review what happened: `python3 main.py stats`, or the notes column in the
review UI.

### LinkedIn Easy Apply (one-time setup)

LinkedIn listings only auto-apply with a saved login session:

```bash
python3 main.py linkedin-login    # browser opens — log in there yourself (~1 min)
```

The session is stored in `data/linkedin_state.json` (gitignored, chmod 600)
and reused for Easy Apply: contact fields + generated resume get filled, common
questions (visa, notice period, relocation…) answered from `config/profile.py`,
then Next → Review → Submit. A question it can't answer safely aborts that one
application (`needs_review`) and leaves it queued — never a wrong answer. Re-run
`linkedin-login` whenever the session expires.

Credential autofill (typing a stored password into the login form) is **opt-in
only** — set `ALLOW_STORED_CREDENTIALS=1` in `.env` if you really want it.
The default path never stores your password on disk.

Generated resumes are one-page, dense and ATS-oriented (keyword-mirrored
skills, metric bullets) — content lives in `config/profile.py`.

### Indeed Easy Apply (one-time setup)

Indeed listings auto-apply with a saved login session (same pattern as LinkedIn):

```bash
python3 main.py indeed-login     # browser opens — log in there yourself (~1 min)
```

The session is stored in `data/indeed_state.json` (gitignored, chmod 600) and
reused for Easy Apply: contact fields + generated resume get filled, common
questions answered from `config/profile.py`, then Continue → Review → Submit.
Jobs without Indeed Easy Apply (external company-site links) fall through to
the generic ATS autofill.

**Note:** Indeed blocks bots aggressively. If scraping gets 403s, use
`--playwright` for search. Easy Apply may still hit CAPTCHA — the browser
stays open for you to solve it, then the automation continues.

## Review UI

```bash
python3 main.py serve          # or: python3 apps/review.py --port 8787
```

Filter by score/source/status, read the description and match reasoning, **Prep**
a cover letter on the spot, then **Mark applied**. Filters survive redirects.

## Scheduling (macOS launchd)

`~/Library/LaunchAgents/com.sanjana.uaejobcopilot.plist` runs `scripts/daily.sh`
daily at 09:00. The script resolves a dependency-bearing `python3` itself (launchd's
default PATH has none) and logs to `logs/daily-YYYYMMDD.log`.

```bash
launchctl unload ~/Library/LaunchAgents/com.sanjana.uaejobcopilot.plist
launchctl load   ~/Library/LaunchAgents/com.sanjana.uaejobcopilot.plist
launchctl print gui/$(id -u)/com.sanjana.uaejobcopilot   # check state
```

Set `COPILOT_FLAGS=--playwright` in the environment (or edit the plist with
`EnvironmentVariables`) to enable the browser scrapers on the scheduled run.

## Layout

```
main.py             CLI orchestrator (argparse subcommands)
config/settings.py  .env loading + search/threshold config
config/profile.py   candidate profile the LLM scores against
scraper/jobs.py     requests+BS4 scrapers (4 sources) + description fetcher
scraper/pw_jobs.py  Playwright scrapers; cached to data/pw_jobs.json (auto-merged)
ai/assistant.py     LLM scoring / cover letters / resume bullets (3-provider chain)
tracker/db.py       SQLite jobs + applications
notify/digest.py    markdown digest builder
notify/channels.py  digest delivery: Telegram / email / Slack
apps/review.py      local Flask review UI
scripts/daily.sh    launchd daily runner
data/               applications.db, digests/, pw_jobs.json
logs/               copilot.log, daily-*.log, launchd.*
```

## Notes

- LinkedIn public pages rate-limit hard (429s); the scraper backs off and retries,
  but expect flaky LinkedIn days — Indeed/Bayt/GulfTalent cover the gaps.
- Description fetching is capped per run (`--desc-limit`, default 50) so a big
  backlog doesn't stall the cycle; already-fetched jobs are never refetched.
- Scraper selectors drift when sites redesign — `scraper/jobs.py` is the file to fix.
- All state is local: SQLite + markdown files, no external services required.
- Security: session files (`data/*_state.json`), generated resumes, failure
  screenshots, and application reports are gitignored. Session files are written
  with `chmod 600`. The review UI rejects cross-origin POSTs via CSRF tokens.
  Stored-password autofill is opt-in (`ALLOW_STORED_CREDENTIALS=1`) — the
  default login path never writes your password to disk.
