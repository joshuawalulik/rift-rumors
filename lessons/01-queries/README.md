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

## SQL lessons learned
- **`COUNT(*)` without `GROUP BY` collapses every row into one.** SQLite then fills other columns from an arbitrary row and gives no error (Postgres would refuse). A plausible-looking wrong answer.
- **Integer division truncates:** `47 / 85` is `0`. Start with `100.0 *` to force decimal math.
- **"Syntax error near X" points at the word *after* the mistake.** Check the line before X, usually a missing comma.
- **`first_move_date` isn't "left the team".** 153 players who stayed have one (e.g. a re-sign logged as a roster change). Use `outcome`.
