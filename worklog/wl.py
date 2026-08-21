#!/usr/bin/env python3
"""
worklog (wl) - tracks parallel streams of work across Claude Code sessions.

A *stream* is one line of work: a repo + branch (usually a git worktree).
Sessions attach to a stream; a stream outlives them, because you resume,
/clear, crash, and start fresh sessions against the same branch.

Streams are derived from Claude Code's own transcripts in
~/.claude/projects/<encoded-cwd>/<session-id>.jsonl - no instrumentation
needed, works retroactively.

Commands:
    wl scan              rebuild the registry from transcripts
    wl list [--all]      print streams to the terminal
    wl sync [--no-git]   render the vault page (implies scan)
    wl context <path>    print context id + tab colour for a path
    wl title <id> <text> set a human title for a stream
    wl done <id>         close a stream (moves it to history)
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ============================================================================
# Configuration
# ============================================================================

PROJECTS_DIR = Path.home() / ".claude" / "projects"
STATE_DIR = Path.home() / ".claude" / "worklog"
STREAMS_FILE = STATE_DIR / "streams.json"
HISTORY_FILE = STATE_DIR / "history.json"
CACHE_FILE = STATE_DIR / "scan-cache.json"

VAULT = Path(
    "/Users/joshanmahmud/Library/Mobile Documents/iCloud~md~obsidian"
    "/Documents/personal-valut/personal-valut"
)
VAULT_PAGE = VAULT / "wiki" / "personal" / "active-streams.md"

STALE_DAYS = 14
# A stream with a single session was probably a one-off (a PR review, a
# question) rather than a line of work, so it goes dormant sooner.
ONE_SHOT_DAYS = 5

# Branches that are a repo's standing desk rather than a stream of work.
DEFAULT_BRANCHES = {"main", "master", "develop", "HEAD", ""}

# Slug words that should render as acronyms.
ACRONYMS = {
    "cissa", "gisa", "fcr", "bscr", "marf", "bma", "isd", "swa", "mcp", "evm",
    "db", "qa", "bau", "pr", "api", "ui", "ux", "llm", "kyc", "sow", "e2e",
    "cqrs", "ddd", "csv", "pdf", "sql", "ai",
}

# Fields that `scan` must never overwrite - these are human-set.
PRESERVED = ("title", "goals", "note", "closed", "context_override", "pr")

# context id -> (display heading, iTerm RGB)
CONTEXTS = {
    "bma": ("BMA / Line4.ai (JMSquared)", (55, 140, 235)),
    "block-infra": ("Block-Infrastructure", (70, 185, 85)),
    "jmsquared": ("JMSquared Consultancy", (155, 70, 225)),
    "personal": ("Personal / Vault", (135, 135, 145)),
    "other": ("Other", (215, 185, 45)),
}

# Head of a transcript to scan for the first user record, in lines.
HEAD_LINES = 400

# Bump when transcript parsing changes, so cached extractions are redone.
PARSER_VERSION = 3

# ============================================================================
# State helpers
# ============================================================================


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    """Atomic write - several Claude sessions may run this concurrently."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def now():
    return datetime.now(timezone.utc)


def parse_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def fmt_ts(dt):
    if not dt:
        return "unknown"
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def age_days(dt):
    if not dt:
        return 9999
    return (now() - dt).days


# ============================================================================
# Deriving a stream from a working directory
# ============================================================================


def repo_of(cwd):
    """
    Repo name for a working directory.

    Worktrees live at <repo>.worktrees/<folder> per the worktree rule, so a
    path component containing '.worktrees' names the repo directly. This also
    folds sessions started in a subdirectory into the parent repo.
    """
    parts = Path(cwd).parts
    for part in parts:
        if part.endswith(".worktrees"):
            return part[: -len(".worktrees")]

    if Path(cwd).is_dir():
        try:
            root = subprocess.run(
                ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=5,
            )
            if root.returncode == 0 and root.stdout.strip():
                return Path(root.stdout.strip()).name
        except (subprocess.SubprocessError, OSError):
            pass

    return Path(cwd).name


def context_of(cwd, repo):
    lowered = str(cwd).lower()
    if str(cwd).rstrip("/") == str(Path.home()):
        return "personal"
    if "/code/bma/" in lowered or repo.lower().startswith("softwaredevelopment-bma"):
        return "bma"
    if "block-infrastructure" in lowered or "blocktravel" in lowered or "blockid" in lowered:
        return "block-infra"
    if "personal-valut" in lowered or "obsidian" in lowered or "/github/jmahmud/" in lowered:
        return "personal"
    if "jmsquared" in lowered:
        return "jmsquared"
    return "other"


def stream_id(repo, branch):
    return f"{repo}/{branch}" if branch else repo


def pretty(branch, repo):
    """Fallback title: 'claude/feature/fcr-criteria-set' -> 'FCR criteria set'."""
    if branch in DEFAULT_BRANCHES:
        return repo.replace("SoftwareDevelopment-BMA-", "").replace("-", " ")

    slug = branch.rsplit("/", 1)[-1]
    for prefix in ("feature-", "feat-", "fix-", "task-", "chore-", "refine-", "review-"):
        if slug.startswith(prefix):
            slug = slug[len(prefix):]
            break

    words = slug.replace("-", " ").replace("_", " ").split()
    if not words:
        return branch

    # Single letters are prototype designators here - Prototype M, Prototype E.
    out = [w.upper() if w.lower() in ACRONYMS or len(w) == 1 else w for w in words]
    if out[0].lower() not in ACRONYMS:
        out[0] = out[0][:1].upper() + out[0][1:]
    return " ".join(out)


# ============================================================================
# Transcript reading
# ============================================================================

TAG_BLOCK = re.compile(
    r"<(system-reminder|local-command-caveat|command-name|command-message|"
    r"command-args|local-command-stdout)>.*?</\1>",
    re.DOTALL,
)
ANY_TAG = re.compile(r"<[^>]+>")


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def clean(raw):
    raw = TAG_BLOCK.sub(" ", raw)
    raw = ANY_TAG.sub(" ", raw)
    return " ".join(raw.split())


# Openers that are a skill or persona preamble, not Joshan describing the work.
MACHINE_OPENERS = (
    "base directory for this skill", "you are a", "you are an", "read and apply",
    "caveat", "repo:", "review the", "analyse the diff", "here is your task",
    "another claude session", "your task", "implement the work described",
)


def is_human_ask(text):
    lowered = text.lower()
    if len(text) < 25 or lowered.startswith(MACHINE_OPENERS):
        return False
    # Tool results and agent plumbing are recorded as user records too.
    if "toolu_" in text or "tool_use_id" in text:
        return False
    return "skill.md" not in lowered[:80]


def read_transcript(path):
    """
    Pull (cwd, branch, started, first_ask) from the head of a transcript.

    Returns None if the file holds no usable user record.
    """
    cwd = branch = started = None
    first_ask = ""

    try:
        with open(path, errors="replace") as f:
            for count, line in enumerate(f):
                if count > HEAD_LINES:
                    break
                if '"type":"user"' not in line and '"type": "user"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") != "user":
                    continue

                if cwd is None and record.get("cwd"):
                    cwd = record["cwd"]
                    branch = record.get("gitBranch") or ""
                    started = record.get("timestamp")

                if not first_ask:
                    body = clean(text_of(record.get("message", {}).get("content")))
                    if is_human_ask(body):
                        first_ask = body[:160]

                if cwd and first_ask:
                    break
    except OSError:
        return None

    if not cwd:
        return None
    return {"cwd": cwd, "branch": branch, "started": started, "first_ask": first_ask}


def scan_sessions():
    """All sessions on disk, using a cache keyed on file mtime+size."""
    cache = load_json(CACHE_FILE, {})
    fresh = {}
    sessions = []

    if not PROJECTS_DIR.is_dir():
        return sessions

    for transcript in PROJECTS_DIR.glob("*/*.jsonl"):
        try:
            stat = transcript.stat()
        except OSError:
            continue

        session = transcript.stem
        stamp = f"v{PARSER_VERSION}:{int(stat.st_mtime)}:{stat.st_size}"
        entry = cache.get(session)

        if not entry or entry.get("stamp") != stamp:
            parsed = read_transcript(transcript)
            if not parsed:
                continue
            entry = {"stamp": stamp, **parsed}

        entry["last_seen"] = datetime.fromtimestamp(
            stat.st_mtime, timezone.utc
        ).isoformat()
        entry["size"] = stat.st_size
        fresh[session] = entry
        sessions.append({"id": session, **entry})

    save_json(CACHE_FILE, fresh)
    return sessions


# ============================================================================
# Registry
# ============================================================================


def build():
    """Rebuild streams.json from transcripts, preserving human-set fields."""
    existing = load_json(STREAMS_FILE, {})
    history = load_json(HISTORY_FILE, {})
    streams = {}

    for session in scan_sessions():
        repo = repo_of(session["cwd"])
        branch = session.get("branch") or ""
        sid = stream_id(repo, branch)

        stream = streams.setdefault(sid, {
            "id": sid,
            "repo": repo,
            "branch": branch,
            "paths": [],
            "sessions": [],
            "first_ask": "",
        })

        if session["cwd"] not in stream["paths"]:
            stream["paths"].append(session["cwd"])
        stream["sessions"].append({
            "id": session["id"],
            "last_seen": session["last_seen"],
            "started": session.get("started"),
            "size": session.get("size", 0),
            "first_ask": session.get("first_ask", ""),
        })

    # A closed stream stays closed. It only comes back if there is genuinely
    # new work on that branch after the date it was closed.
    reopened = []
    for sid, closed_stream in list(history.items()):
        if sid not in streams:
            continue
        latest = max(s["last_seen"] for s in streams[sid]["sessions"])
        if latest > closed_stream.get("closed", ""):
            reopened.append(sid)
            history.pop(sid)
        else:
            del streams[sid]
    if reopened:
        save_json(HISTORY_FILE, history)
        print(f"Reopened (new activity): {', '.join(reopened)}", file=sys.stderr)

    for sid, stream in streams.items():
        # What the work is gets said in the *earliest* session of a branch,
        # so take the opening ask from there, not from whichever file we hit first.
        opening = sorted(
            stream["sessions"], key=lambda s: s.get("started") or s["last_seen"]
        )
        stream["first_ask"] = next(
            (s["first_ask"] for s in opening if s.get("first_ask")), ""
        )

        stream["sessions"].sort(key=lambda s: s["last_seen"], reverse=True)
        stream["last_active"] = stream["sessions"][0]["last_seen"]
        starts = [s["started"] for s in stream["sessions"] if s.get("started")]
        stream["first_active"] = min(starts) if starts else stream["last_active"]

        # The worktree root is the shortest cwd seen for this stream.
        stream["paths"].sort(key=len)
        stream["path"] = stream["paths"][0]

        prior = existing.get(sid, {})
        for field in PRESERVED:
            if field in prior:
                stream[field] = prior[field]

        stream["context"] = stream.get("context_override") or context_of(
            stream["path"], stream["repo"]
        )
        stream["is_default"] = stream["branch"] in DEFAULT_BRANCHES
        stream["status"] = status_of(stream)

    save_json(STREAMS_FILE, streams)
    return streams


def fetch_pr(stream):
    """
    The PR title for a branch, which describes the work far better than the
    branch slug does. Best-effort: no gh, wrong account, or no PR -> nothing.
    """
    if stream["is_default"] or not Path(stream["path"]).is_dir():
        return None
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--head", stream["branch"], "--state", "all",
             "--limit", "1", "--json", "number,title,state,url"],
            cwd=stream["path"], capture_output=True, text=True, timeout=20,
        )
        if result.returncode != 0:
            return None
        found = json.loads(result.stdout or "[]")
        return found[0] if found else None
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError):
        return None


def enrich_prs(streams):
    """Attach PR metadata to every stream worth asking about."""
    targets = [
        s for s in streams.values()
        if not s["is_default"] and s["status"] in ("active", "stale")
    ]
    for stream in targets:
        pr = fetch_pr(stream)
        if pr:
            stream["pr"] = pr
            print(f"  {stream['id']} -> PR #{pr['number']} {pr['state']}")
    save_json(STREAMS_FILE, streams)
    return streams


def status_of(stream):
    if stream.get("closed"):
        return "closed"
    # A merged PR is the one unambiguous "this work is finished" signal.
    if stream.get("pr", {}).get("state") in ("MERGED", "CLOSED"):
        return "merged"
    if not Path(stream["path"]).exists():
        return "gone"

    idle = age_days(parse_ts(stream["last_active"]))
    if idle > STALE_DAYS:
        return "stale"
    if len(stream["sessions"]) == 1 and idle > ONE_SHOT_DAYS:
        return "stale"
    return "active"


# ============================================================================
# Rendering
# ============================================================================


def title_of(stream):
    """A title Joshan set beats the PR title, which beats the branch slug."""
    if stream.get("title"):
        return stream["title"]
    pr_title = stream.get("pr", {}).get("title")
    if pr_title:
        # Drop a conventional-commit prefix: 'docs(prototype-e): X' -> 'X'.
        return re.sub(r"^[a-z]+(\([^)]*\))?:\s*", "", pr_title)
    return pretty(stream["branch"], stream["repo"])


def resume_cmd(stream):
    return f'claude --resume {stream["sessions"][0]["id"]}'


def short_path(path):
    home = str(Path.home())
    return str(path).replace(home + "/Documents/Code/", "").replace(home, "~")


def render_stream(stream):
    goals = stream.get("goals") or []
    tag = " " + " ".join(f"(→{g})" for g in goals) if goals else ""
    branch = f" · `{stream['branch']}`" if stream["branch"] else ""
    count = len(stream["sessions"])
    plural = "session" if count == 1 else "sessions"

    pr = stream.get("pr")
    pr_note = ""
    if pr:
        pr_note = f" · [PR #{pr['number']} {pr['state'].lower()}]({pr['url']})"

    lines = [
        f"- **{title_of(stream)}**{tag}{branch}{pr_note} · last active "
        f"{fmt_ts(parse_ts(stream['last_active']))} · {count} {plural}",
        f"    - `cd \"{stream['path']}\" && {resume_cmd(stream)}`",
    ]
    if stream.get("note"):
        lines.append(f"    - {stream['note']}")
    elif stream.get("first_ask") and not pr and count <= 3:
        # On a long-lived branch the opening message is a mid-thought
        # continuation, not a description - only worth showing on young streams.
        lines.append(f"    - _opened with:_ \"{stream['first_ask'][:120]}\"")
    return lines


def render(streams):
    today = now().astimezone().strftime("%Y-%m-%d %H:%M")
    active = [s for s in streams.values() if s["status"] == "active"]
    dormant = [s for s in streams.values() if s["status"] in ("stale", "gone", "merged")]

    out = [
        "# Active Work Streams",
        "",
        f"> Generated by `wl sync` · Last updated: {today}",
        "> A **stream** is one line of work (a repo + branch, usually a worktree).",
        "> Sessions attach to a stream and come and go; the stream is the unit that matters.",
        "> Do not edit by hand — set titles with `wl title <id> \"...\"`, close with `wl done <id>`.",
        "> Tasks live in [[current-tasks]]; goals in [[weekly-goals]].",
        "",
        "---",
        "",
    ]

    if not active:
        out += ["_No active streams._", ""]

    for key, (heading, _rgb) in CONTEXTS.items():
        group = sorted(
            [s for s in active if s["context"] == key],
            key=lambda s: s["last_active"], reverse=True,
        )
        if not group:
            continue

        branches = [s for s in group if not s["is_default"]]
        desks = [s for s in group if s["is_default"]]

        count = len(branches)
        label = "1 stream" if count == 1 else f"{count} streams"
        out.append(f"## {heading} — {label}")
        out.append("")
        for stream in branches:
            out += render_stream(stream)
        if branches:
            out.append("")

        if desks:
            out += ["**Main lines** (work done straight on the default branch)", ""]
            for stream in desks:
                out.append(
                    f"- `{stream['repo']}` · {stream['branch'] or 'no branch'} · "
                    f"{fmt_ts(parse_ts(stream['last_active']))} · "
                    f"`{resume_cmd(stream)}`"
                )
            out.append("")

    if dormant:
        dormant.sort(key=lambda s: s["last_active"], reverse=True)
        shown, hidden = dormant[:12], dormant[12:]
        out += [
            "---",
            "",
            f"## Dormant — close these? ({len(dormant)})",
            "",
            f"> Idle {STALE_DAYS}+ days (or {ONE_SHOT_DAYS}+ for a single-session one-off), "
            "or the worktree is gone. Close with `wl done <id>` — it moves to history, "
            "it is not deleted.",
            "",
        ]
        for stream in shown:
            if stream["status"] == "merged":
                pr = stream["pr"]
                reason = f"**PR #{pr['number']} {pr['state'].lower()}**"
            elif stream["status"] == "gone":
                reason = "worktree gone"
            else:
                reason = f"idle {age_days(parse_ts(stream['last_active']))}d"
            out.append(
                f"- `{stream['id']}` — {title_of(stream)} · {reason} · "
                f"last active {fmt_ts(parse_ts(stream['last_active']))}"
            )
        if hidden:
            out.append(f"- _…and {len(hidden)} older — see `wl list --all`._")
        out.append("")

    out += ["## References", "", "- [[current-tasks]]", "- [[weekly-goals]]", ""]
    return "\n".join(out)


# ============================================================================
# Commands
# ============================================================================


def cmd_scan(args):
    streams = build()
    if getattr(args, "prs", False):
        print("Looking up pull requests...")
        enrich_prs(streams)
    counts = {}
    for stream in streams.values():
        counts[stream["status"]] = counts.get(stream["status"], 0) + 1
    summary = ", ".join(f"{v} {k}" for k, v in sorted(counts.items()))
    print(f"Scanned {len(streams)} streams: {summary}")
    return streams


def cmd_list(args):
    streams = build()
    wanted = ("active",) if not args.all else \
        ("active", "stale", "gone", "merged", "closed")
    rows = sorted(
        [s for s in streams.values() if s["status"] in wanted],
        key=lambda s: (s["context"], s["last_active"]), reverse=True,
    )
    if not rows:
        print("No streams.")
        return

    for key, (heading, _rgb) in CONTEXTS.items():
        group = [s for s in rows if s["context"] == key]
        if not group:
            continue
        print(f"\n{heading}")
        for stream in group:
            flag = "" if stream["status"] == "active" else f" [{stream['status']}]"
            print(f"  {title_of(stream)}{flag}")
            print(f"    {stream['id']}")
            print(f"    {fmt_ts(parse_ts(stream['last_active']))} · {resume_cmd(stream)}")
    print()


def cmd_sync(args):
    streams = build()
    if not args.no_prs:
        streams = enrich_prs(streams)
    page = render(streams)

    VAULT_PAGE.parent.mkdir(parents=True, exist_ok=True)
    previous = VAULT_PAGE.read_text() if VAULT_PAGE.exists() else ""

    # Ignore the timestamp line when deciding whether anything really changed,
    # so a no-op sync does not churn the vault's git history.
    def body(text):
        return "\n".join(l for l in text.splitlines() if not l.startswith("> Generated by"))

    if body(previous) == body(page):
        print(f"No change — {VAULT_PAGE.name} left alone.")
        return

    VAULT_PAGE.write_text(page)
    print(f"Wrote {VAULT_PAGE}")

    if args.no_git:
        return

    try:
        subprocess.run(["git", "-C", str(VAULT), "add", str(VAULT_PAGE)], check=True)
        staged = subprocess.run(
            ["git", "-C", str(VAULT), "diff", "--cached", "--quiet"]
        )
        if staged.returncode != 0:
            subprocess.run(
                ["git", "-C", str(VAULT), "commit", "-m", "Update active work streams"],
                check=True, capture_output=True,
            )
            subprocess.run(["git", "-C", str(VAULT), "push", "origin", "main"], check=True)
            print("Committed and pushed to the vault.")
    except subprocess.CalledProcessError as exc:
        print(f"Vault git step failed: {exc}", file=sys.stderr)


def cmd_context(args):
    path = args.path or os.getcwd()
    repo = repo_of(path)
    key = context_of(path, repo)

    streams = load_json(STREAMS_FILE, {})
    title = ""
    for stream in streams.values():
        if stream.get("path") == str(path):
            title = stream.get("title", "")
            key = stream.get("context", key)
            break

    heading, (r, g, b) = CONTEXTS[key]
    if args.format == "shell":
        print(f"WL_CONTEXT={key}")
        print(f"WL_LABEL={heading.split(' /')[0].split(' —')[0]}")
        print(f"WL_TITLE={title or pretty('', repo)}")
        print(f"WL_R={r}")
        print(f"WL_G={g}")
        print(f"WL_B={b}")
    else:
        print(key)


def cmd_title(args):
    streams = load_json(STREAMS_FILE, {})
    if args.id not in streams:
        print(f"No stream '{args.id}'. Run `wl list --all` to see ids.", file=sys.stderr)
        sys.exit(1)
    streams[args.id]["title"] = args.text
    if args.goals:
        streams[args.id]["goals"] = args.goals.split(",")
    save_json(STREAMS_FILE, streams)
    print(f"{args.id} -> {args.text}")


def cmd_done(args):
    streams = load_json(STREAMS_FILE, {})
    if args.id not in streams:
        print(f"No stream '{args.id}'.", file=sys.stderr)
        sys.exit(1)

    stream = streams.pop(args.id)
    stream["closed"] = now().isoformat()
    stream["closing_note"] = args.note or ""

    history = load_json(HISTORY_FILE, {})
    history[args.id] = stream
    save_json(HISTORY_FILE, history)
    save_json(STREAMS_FILE, streams)
    print(f"Closed {args.id} — moved to history.")


def main():
    parser = argparse.ArgumentParser(prog="wl", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan")
    scan.add_argument("--prs", action="store_true", help="also look up PR titles via gh")
    scan.set_defaults(func=cmd_scan)

    listing = subparsers.add_parser("list")
    listing.add_argument("--all", action="store_true", help="include dormant streams")
    listing.set_defaults(func=cmd_list)

    sync = subparsers.add_parser("sync")
    sync.add_argument("--no-git", action="store_true", help="write the page, do not commit")
    sync.add_argument("--no-prs", action="store_true", help="skip the gh PR lookup")
    sync.set_defaults(func=cmd_sync)

    context = subparsers.add_parser("context")
    context.add_argument("path", nargs="?")
    context.add_argument("--format", choices=["id", "shell"], default="id")
    context.set_defaults(func=cmd_context)

    title = subparsers.add_parser("title")
    title.add_argument("id")
    title.add_argument("text")
    title.add_argument("--goals", help="comma-separated goal ids, e.g. G1,G3")
    title.set_defaults(func=cmd_title)

    done = subparsers.add_parser("done")
    done.add_argument("id")
    done.add_argument("--note", help="closing summary")
    done.set_defaults(func=cmd_done)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
