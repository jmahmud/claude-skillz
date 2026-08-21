# worklog (`wl`)

Tracks the parallel **streams of work** running across Claude Code sessions, and
surfaces them in the personal vault as `wiki/personal/active-streams.md`.

## Why streams, not sessions

A stream is one line of work: a repo + branch, usually a git worktree. Sessions
attach to a stream and come and go — you resume, `/clear`, crash, and start
fresh sessions against the same branch all the time. Keying on session ID gives
you 30+ rows of noise; keying on repo + branch gives you the handful of things
you are actually working on.

## Where the data comes from

Claude Code already writes every session to
`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`, and each user record
carries `cwd`, `gitBranch` and `timestamp`. `wl` reads the head of each
transcript, groups by repo + branch, and uses file mtime for last-activity.

Nothing needs to be instrumented, and it works retroactively over history that
already exists.

## Install

```bash
ln -sf "$PWD/worklog/wl.py" ~/.claude/bin/wl
```

The `claude` shell wrapper in `~/.zshrc` runs `wl sync --no-git --no-prs` in the
background on every launch, so the vault page stays current without any network
or git work. The page is only rewritten when its content actually changed.

## Commands

| Command | What it does |
|---|---|
| `wl list [--all]` | Streams in the terminal; `--all` includes dormant ones |
| `wl scan [--prs]` | Rebuild the registry; `--prs` also looks up PR titles via `gh` |
| `wl sync [--no-git] [--no-prs]` | Render the vault page, then commit and push it |
| `wl title <id> "..." [--goals G1,G3]` | Give a stream a human title and link weekly goals |
| `wl done <id> [--note "..."]` | Close a stream — moves it to history, does not delete it |
| `wl context <path> [--format shell]` | Context id and tab colour for a path |

## Titles

Resolved in order: a title you set with `wl title`, then the PR title for the
branch (via `gh`), then a prettified branch slug. Acronyms and single-letter
prototype designators are cased correctly, so `feature/cissa-prototype-m`
renders as *CISSA prototype M*.

## Forgetting

Streams are never deleted silently. A stream becomes dormant and is listed under
"Dormant — close these?" when any of these hold:

- its PR is **merged or closed** — the one unambiguous "this is finished" signal
- its **worktree is gone**
- it has been **idle 14+ days**, or 5+ days if it only ever had one session
  (a one-off PR review or question, not a line of work)

`wl done <id>` moves it to `~/.claude/worklog/history.json`. A closed stream
stays closed, and only reopens if genuinely new sessions appear on that branch
after the date it was closed.

## State

| File | Contents |
|---|---|
| `~/.claude/worklog/streams.json` | The live registry |
| `~/.claude/worklog/history.json` | Closed streams |
| `~/.claude/worklog/scan-cache.json` | Per-transcript extraction cache, keyed on mtime + size |

State lives outside the vault deliberately: it changes on every session start,
and committing that to an iCloud-synced git repo would be constant churn. The
vault gets a rendered page instead.
