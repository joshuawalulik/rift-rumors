# Lesson 01 exercise queries

My SQL for the [Lesson 01 exercises](../01-ground-truth.md). Each file is runnable and records what I predicted, what went wrong, what I found, and what it means for the model.

Run any of them from the repo root (`-readonly` means the database can't be modified by accident):

```bash
sqlite3 -readonly -header -box data/ground_truth.db < lessons/01-queries/ex1-turnover-by-team.sql
```

Or browse interactively: `uvx datasette data/ground_truth.db` → http://localhost:8001, and paste a query into the SQL box.

| File | Question | Finding |
|---|---|---|
| [ex1-turnover-by-team.sql](ex1-turnover-by-team.sql) | Which team lost the most players in the 2024 offseason? | NRG and Immortals, the whole roster each: both orgs left the league |
| [ex1b-whole-roster-gone.sql](ex1b-whole-roster-gone.sql) | Follow-up: which teams lost *everyone*? | Found a label bug: missing rebrands (Liiv SANDBOX → BNK FEARX, Kwangdong Freecs → DN Freecs) counted as moves |
| [ex2-turnover-by-role.sql](ex2-turnover-by-role.sql) | Do supports move more than mids? | Supports 60.9% vs mids 52.8%, but within noise at n≈85 per role |
| [ex3-wrong-labels.sql](ex3-wrong-labels.sql) | Find a label you know is wrong | G2 2025 shows 3 of 5 players: player names differ in case across tables (`Hans Sama`/`Hans sama`; 37 players affected), 4 players have no tenures, and renames (`SamD` → `Hans SamD`) split identities. **Fixed:** names resolve via `PlayerRedirects`; 26 players recovered, 14 labels corrected (lesson §8) |
| [ex4-cutoff-sensitivity.sql](ex4-cutoff-sensitivity.sql) | Does the Feb 1 cutoff matter? | Jan 1 flips 33 of 445 labels (mostly January signings → "no_team"). Found 4 labels where a player "moved" to the league itself ("LoL EMEA Championship"). An event-based cutoff (first official match) would be better |
| [ex5-gcd-staleness.sql](ex5-gcd-staleness.sql) | How fresh is Riot's contract sheet? | EMEA tab last updated Sep 9 (2 months stale), KR Nov 3. Mitigations: recency rule, fresher roster changes, snapshot diffs, rumors |
| [ex6-live-board.sql](ex6-live-board.sql) | Which 2026 seats are most open right now? | 69 players expiring with no news; BNK FEARX, Nongshim, Disguised, Shopify Rebellion, GIANTX have all 5 open. My calls: ShowMaker stays, Sharvel and Doran move (v0 says 66% leave for all three) |

## Label issues found by these exercises: all fixed (lesson §8, §8b)
- ~~**League-as-team:**~~ only playing stints count now. That also caught Bible, Spica and Th3Antonio rejoining their teams as streamers ("stayed" → `no_team`).
- ~~**Academy demotions:**~~ still `moved` (seat lost), tagged `move_type = 'demotion'` (7 cases).
- ~~**Event-based cutoff:**~~ `cutoff` = the day the league's next season starts; `outcome_feb1` kept for v0's pre-registered grading.

## SQL lessons learned
- **Restart Datasette after every rebuild.** The rebuild replaces the `.db` file, and a running Datasette keeps showing the old deleted file (it showed 190 rows where the real file had 380).
- **`ATTACH 'other.db' AS x`** lets one query read two database files; `x.table` is the other file's table. Join on the key columns to compare row by row.
- **`COUNT(*)` without `GROUP BY` collapses every row into one.** SQLite then fills other columns from an arbitrary row and gives no error (Postgres would refuse). A plausible-looking wrong answer.
- **Integer division truncates:** `47 / 85` is `0`. Start with `100.0 *` to force decimal math.
- **"Syntax error near X" points at the word *after* the mistake.** Check the line before X, usually a missing comma.
- **`IS NULL`, never `= NULL`:** comparing to NULL with `=` is never true, so it silently matches nothing.
- **`first_move_date` isn't "left the team".** 153 players who stayed have one (e.g. a re-sign logged as a roster change). Use `outcome`.
