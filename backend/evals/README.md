# Extraction eval

Measures how well `extract()` in `app/llm/extract.py` turns emails into tasks, using emails from your own inbox labeled by hand. Run it before and after changing the prompt or `EXTRACT_MODEL` to see whether the change helped.

All commands run from `backend/`.

## Two sets: tuning and test

- **Tuning set** (`emails.jsonl`, ~60 emails): for improving the prompt. Run it as often as you like, read its mistakes, and change the prompt to fix them.
- **Test set** (`test_emails.jsonl`, 25 emails): held out. Label it once, and run it only on a final prompt version. Never use it to tune: don't read its mistakes to decide what to change in the prompt, and don't relabel it after seeing a run. Once you tune on it, it stops measuring how the prompt does on emails it hasn't seen.

The two sets never share an email: each export leaves out every email in the other set. If the tuning set beats the test set by a lot, the prompt has been fitted to the tuning emails.

## Files

| File | What it is | In git? |
| --- | --- | --- |
| `emails.jsonl` | Tuning set: ~60 of your real emails plus your labels | **No** (gitignored) |
| `test_emails.jsonl` | Test set: 25 other real emails plus your labels | **No** (gitignored) |
| `sample.jsonl` | 5 made-up, already labeled emails, to try the runner | Yes |
| `results/*.md` | One report per run | Yes |
| `export.py`, `run.py`, `scoring.py`, `dataset.py` | The code | Yes |

## 1. Export a sample

```sh
uv run python -m evals.export              # tuning set: 60 emails to evals/emails.jsonl
uv run python -m evals.export --set test   # test set: 25 emails to evals/test_emails.jsonl
```

Export the tuning set first; the test set leaves out every email in it.

The **tuning set** picks processed emails It tags each email (had tasks, no tasks, university, internship, newsletter, notification, action words, relative dates) and takes turns drawing from each tag, so no one kind of email fills the set. Emails that produced tasks are drawn twice per turn because they are rare. Sensitive and noise emails are left out. It prints counts per tag, never subjects.

The **test set** is picked without any model output: there is no "had tasks" tag, only the email's own patterns (action words, relative dates, internship, and sender type: university, newsletter, notification or `other_sender`). That keeps it from leaning toward emails the model already handles well. It has to come from emails the tuning set didn't take, so if a kind of email is rare in your inbox, sync more mail first (see below). The export's tag counts show what you got.

To get more emails to choose from, `uv run python -m app.sync.backfill --max-cost 0.60` imports Gmail emails 30 to 60 days old once (same filters as the sync, stored historyId untouched) and extracts them, stopping before the estimated cost passes the cap.

Options: `--n` (default 60 for tuning, 25 for test), `--seed 8` (a different seed gives a different sample), `--force` to replace an existing file (labels of emails that stay in the sample are kept).

## 2. Label

Start the backend and frontend as usual, sign in, and open <http://localhost:5173/#/label> for the tuning set or <http://localhost:5173/#/label?set=test> for the test set. The header shows which set you're labeling and links to the other.

For each email decide:

- **Actionable?** Does it ask you to do, submit, attend, reply to or pay for something?
- **Tasks**: one per distinct action, with a short title. Wording doesn't need to match the model's, since titles are matched by shared words. Use the words you'd expect in the title (course code, "submit", "register", ...).
- **Due date**: the deadline, or the start of a meeting or exam, in Istanbul time. Count "this Friday" or "tomorrow" from the **sent** date shown above the email. Leave the time empty when the email gives only a day.

Keyboard: `Y` actionable (starts a task), `N` not actionable and next, `T` add a task, `Enter` save and go to the next unlabeled email (works inside fields too), `Esc` leave a field, `J`/`K` next/previous, `U` next unlabeled. Labels are saved to the set's file right away.

The page never shows what the model extracted, so your labels aren't nudged by it. It uses dev-only endpoints (`/evals/...`) that answer only from localhost; set `DEV_ENDPOINTS_ENABLED=false` to remove them.

## 3. Run

```sh
uv run python -m evals.run                          # tuning set
uv run python -m evals.run --set test               # test set, final prompt versions only
uv run python -m evals.run --file evals/sample.jsonl  # the made-up set
```

`--name baseline` adds a note to the report file name.

Each labeled email goes through `extract()` with its sent time as "today" and an empty open-task list, so the input is the same on every run. Like the app, it skips emails that are now sensitive or noise, and `extract()` masks CNIC, card, IBAN and phone numbers before sending. Nothing is written to the database. 60 emails with Haiku cost roughly $0.20.

The report in `results/` is named `<date>_<prompt version>_<model>_<set>.md` and has the set name in its title. It has the date, model, prompt version (`PROMPT_VERSION` in `extract.py`, plus a hash of the prompt in case you forgot to bump it), cost and these metrics:

| Metric | Meaning |
| --- | --- |
| Actionable accuracy | Share of emails where yes/no matches your label. Also split into precision and recall. |
| Task recall | Your tasks that the model also found. |
| Task precision | Model tasks that match one of yours. Tasks on emails you marked not actionable count as wrong. |
| Due date, same day | For matched tasks: same Istanbul day, or both have no date. |
| Due time within 1 hour | For matched tasks where you gave a time. |

**Matching titles.** Each title becomes a set of lowercase words without stopwords ("the", "to", "for", ...) and with plural "s" removed. Similarity is word-overlap F1: `2 × shared words / (words in A + words in B)`. Pairs with similarity ≥ 0.5 match, best pairs first, each task used once. "Submit CS204 homework 2" vs "Submit CS204 HW 2" scores 0.75. No LLM judge, so scoring is free and gives the same answer every time.

The mistakes list shows the subject and what went wrong (missed or extra tasks, wrong day, wrong time), never the body.

**Limits.** The model samples with its default temperature, so two runs can differ by an email or two. Compare runs on the same eval set, and look at the mistakes, not just the totals. Word overlap can miss a correct task with very different wording; check the mistakes list for those.
