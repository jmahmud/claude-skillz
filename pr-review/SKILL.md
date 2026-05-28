---
name: pr-review
description: "Universal PR review skill. Works with any language or codebase. Accepts an optional PR URL (GitHub or Azure DevOps). Always produces a change summary, clean code assessment, architecture review, comment hygiene, outside-in test coverage check, and pragmatic verdict. After review, offers to post findings as PR comments. Dispatches to language-specific skills for C#, DAML, TypeScript, Terraform. Reviews Azure cloud architecture when relevant. Use when reviewing any PR, diff, or set of changes."
version: 1.2.0
---

# PR Review

Universal pre-landing review. Produces a structured report every time: change summary first, findings by severity, then verdict. After presenting the report, always offer to post findings back to the PR.

---

## Phase 0: Setup

### gh CLI auth fallback

Whenever a `gh` command fails with an authentication error (non-zero exit, stderr contains "authentication required", "401", "Could not resolve to a Repository", or "no credentials"), run:

```bash
gh auth switch
```

Then retry the same command once. If it still fails, report the error to the user and stop.

---

### Step 0.0 — Verify repository context

Before anything else, confirm the current working directory is a git repository for the target repo.

```bash
git rev-parse --show-toplevel 2>/dev/null && git remote get-url origin 2>/dev/null
```

- If this succeeds → set `IN_REPO=true`, record `LOCAL_ROOT` (the toplevel path) and `REMOTE_URL`.
- If this fails (not a git repo, or no remote) → set `IN_REPO=false`.

**If a PR URL was provided**, compare the URL's `{owner}/{repo}` against `REMOTE_URL`. If they do not match, warn the user:

> "The current directory (`<cwd>`) does not appear to be the repository for this PR. File inspection and local git operations will not be available — diff will be fetched via the GitHub API instead."

Set `IN_REPO=false` in this case and proceed in remote-only mode.

---

### Step 0.1 — Detect platform and PR source

Check whether a PR URL was provided in the user's message.

**If a PR URL was provided:**

Determine the platform from the URL:

- URL contains `github.com` → **GitHub PR**
- URL contains `dev.azure.com` or `visualstudio.com` → **Azure DevOps PR**

**GitHub PR:**
```bash
# Fetch PR metadata — apply auth fallback if this fails
gh pr view <PR_NUMBER_OR_URL> --json baseRefName,headRefName,title,body \
  -q '{base: .baseRefName, head: .headRefName, title: .title}'
```

Set `PLATFORM=github`, `PR_NUMBER=<extracted from URL>`, `BASE=<baseRefName>`, `HEAD=<headRefName>`.

**Azure DevOps PR:**

Use the Azure DevOps MCP server to fetch the PR. Extract the organisation, project, repo, and PR ID from the URL pattern:
`https://dev.azure.com/{org}/{project}/_git/{repo}/pullrequest/{id}`

Call the ADO MCP to retrieve:
- PR description and title
- Changed files list
- PR thread comments (existing)

Then get the diff via git if `IN_REPO=true`:
```bash
git fetch origin
git diff origin/<BASE>...origin/<HEAD>
```

Set `PLATFORM=ado`, `PR_ID=<id>`, `BASE`, `HEAD`.

**If no PR URL was provided:**

`IN_REPO` must be `true` here (no URL means we work from the local branch). If `IN_REPO=false`, stop and ask the user to cd into the repository.

Determine platform from `REMOTE_URL` (`github.com` → GitHub, `dev.azure.com` / `visualstudio.com` → ADO).

```bash
gh pr view --json baseRefName -q .baseRefName 2>/dev/null \
  || git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||' \
  || echo "main"
```

Set BASE from result. If on the base branch with no diff: "Nothing to review — on base branch." and stop.

Try to detect if an open PR exists for this branch:
```bash
gh pr view --json number,url 2>/dev/null
```

If found, record `PR_NUMBER` and `PLATFORM=github`. If none found, set `PR_NUMBER=none`.

---

### Step 0.2 — Checkout PR branch (GitHub only)

**Skip this step for ADO PRs or if `IN_REPO=false`.**

With `PLATFORM=github` and `IN_REPO=true`, check out the PR branch so that local file inspection tools (Read, Grep, Glob) work against the actual PR content:

```bash
gh pr checkout <PR_NUMBER>
```

Apply the auth fallback if this fails. After checkout, confirm the active branch:

```bash
git branch --show-current
```

If checkout fails for any reason other than auth (e.g. uncommitted local changes), warn the user and continue using the remote diff only — set `IN_REPO=false` for file-read purposes.

---

### Step 0.3 — Detect stack

Get the list of changed files, preferring local git when available:

**If `IN_REPO=true` (local git — preferred):**
```bash
git diff origin/<BASE>...HEAD --name-only 2>/dev/null
```

**If `IN_REPO=false` and `PLATFORM=github` (remote fallback only):**
```bash
gh pr diff <PR_NUMBER> --name-only 2>/dev/null
```

From the changed file extensions, mark each that applies:

| Marker | Signal |
|--------|--------|
| `HAS_CSHARP` | any `.cs` or `.csproj` in diff |
| `HAS_DAML` | any `.daml` in diff |
| `HAS_TYPESCRIPT` | any `.ts` or `.tsx` in diff |
| `HAS_PYTHON` | any `.py` in diff |
| `HAS_SQL` | any `.sql` or file matching `*migration*` |
| `HAS_TERRAFORM` | any `.tf` or `.tfvars` in diff |
| `HAS_BICEP` | any `.bicep` in diff |
| `HAS_INFRA` | `HAS_TERRAFORM` OR `HAS_BICEP` OR any `.yaml` under `.github/` or `infrastructure/` |
| `HAS_CLOUD_ARCH` | `HAS_TERRAFORM` OR `HAS_BICEP` OR any `.tf`, `.bicep`, ARM/Pulumi files |

---

### Step 0.4 — Get the full diff

**If `IN_REPO=true` (preferred for all platforms):**
```bash
git fetch origin <BASE> --quiet
git diff origin/<BASE>...HEAD
git log origin/<BASE>..HEAD --oneline
```

**If `IN_REPO=false` and `PLATFORM=github` (remote fallback only):**
```bash
gh pr diff <PR_NUMBER>
gh pr view <PR_NUMBER> --json commits -q '.commits[].messageHeadline'
```

Read the diff in full before producing any output.

---

## Phase 1: Change Summary (ALWAYS output this first)

Before any findings, produce a plain-English summary:

```
## PR Summary

**What changed:** <2-4 sentences describing the user-facing or system-facing effect — not the file names.>

**Files touched:** <N files — list the most significant 3-5, grouped by concern>

**Commit trail:**
  <oneline log>

**Scope check:**
  Intent: <what was this PR trying to accomplish?>
  Delivered: <what does the diff actually do?>
  Verdict: CLEAN | SCOPE CREEP (<what extra>) | MISSING (<what's absent>)
```

Scope creep and missing requirements are **informational** — they do not block review. Flag clearly and move on.

---

## Phase 2: Universal Review

Apply these dimensions to every PR regardless of language.

### 2.1 — Code Quality

- **Naming**: Names reveal intent. No `data`, `temp`, `manager`, `utils`, `helper` without specificity. Searchable and pronounceable.
- **Function length and complexity**: Functions doing more than one thing. Long conditionals that could be extracted. Nested loops obscuring intent.
- **Duplication**: Same logic in multiple places that should be a shared abstraction.
- **Over-abstraction**: Abstractions with one implementation and no clear reuse case.
- **Dead code**: Commented-out code, unused imports, unreachable branches.

### 2.2 — Architecture

- **Layer violations**: Business logic in controllers, infrastructure concerns in domain code, presentation bleeding into services.
- **Coupling**: Does a change in one module force unrelated changes elsewhere? Dependencies pointing the wrong direction?
- **Responsibility creep**: A class or function acquiring responsibilities that belong elsewhere.
- **SOLID violations**: Flag obvious violations only — don't nitpick correct trade-offs.
- **New abstractions**: Does it earn its complexity? Can it be explained in one sentence?

### 2.3 — Comments

**Flag missing comments when:**
- A non-obvious constraint or invariant (why an order matters, why a value is hard-coded)
- A deliberate workaround or known limitation
- A subtle edge case handled without explanation
- A public API or interface with no documented contract or error behaviour

**Flag redundant or harmful comments when:**
- Comments restate what the code says (`// increment i`)
- Comments describe WHAT without WHY
- Outdated comments contradicting current code
- Large blocks of commented-out old code

### 2.4 — Test Coverage (Outside-In)

The outside-in principle: **tests should start at the highest level that meaningfully verifies behaviour.**

1. Is there a **component or integration test** that exercises the full path (controller → service → persistence or equivalent)?
2. If yes: do unit tests supplement it for complex isolated logic?
3. If only unit tests exist: are they testing behaviour or implementation? Tests mirroring internal call sequences are fragile — flag them.
4. Are edge cases covered? (null inputs, empty collections, error paths)
5. Are tests **asserting on behaviour** (output, state, events) rather than method calls?

Output:
```
Test Coverage:
  Level: component | integration | unit-only | none
  Gaps: <specific missing scenarios>
  Fragility: <tests mocking internals or testing implementation>
  Verdict: ADEQUATE | GAPS PRESENT | INSUFFICIENT
```

### 2.5 — Security (Universal)

- Hardcoded secrets, tokens, or connection strings
- Unsanitised inputs used in queries, shell commands, or file paths
- Missing authentication or authorisation on new endpoints
- Sensitive data written to logs without masking
- New dependencies added — note them for supply chain awareness

---

## Phase 3: Language-Specific Review

### If HAS_CSHARP

Read and apply the full checklist from:
```
~/.claude/skills/dotnet-platform-reviewer/SKILL.md
```
Apply every dimension. Report findings under a **C# .NET** heading.

### If HAS_DAML

- Signatories and observers correctly specified on every template
- Choice controllers are justified (who can exercise and why)
- Fetch/exercise patterns don't violate privacy model
- No business logic bypasses the choice/signatory model
- Daml Script tests cover the primary workflow paths

### If HAS_TYPESCRIPT

- `any` used without justification (each use should explain why)
- `as` casts that bypass the type system without a safety check
- Async/await correctness: missing awaits, unhandled promise rejections
- No direct `process.env` access outside the config/bootstrap layer
- React (if present): no side effects in render, correct dependency arrays in hooks

### If HAS_SQL

- Migrations are reversible (down migration present) or explicitly noted as irreversible
- No raw string interpolation into queries
- New indexes justified by query patterns
- Large table alterations flagged (may need zero-downtime strategy)

### If HAS_TERRAFORM

- All resources are tagged (environment, owner, cost-centre at minimum)
- No secrets or credentials in `.tf` or `.tfvars` files — use variable references or vault references
- Destructive changes (`-/+` replace) explicitly called out — these need human sign-off
- Remote state backend configured (not local state)
- `terraform plan` output would be clean — no unintended drift
- Variables have descriptions and type constraints
- `count` or `for_each` used correctly — no accidental resource multiplication
- Provider version constraints pinned (`~> X.Y`, not `>= X`)
- Module versions pinned where modules are used
- Output values defined for anything a consumer needs to reference

### If HAS_BICEP

- Parameters use `@secure()` for secrets, not plain string types
- No inline secrets or connection strings
- Deployment scope is explicit (`resourceGroup`, `subscription`, `tenant`)
- Destructive changes flagged (resource replacement, deletion of existing resources)
- `existing` keyword used correctly — not re-deploying already-managed resources

---

## Phase 4: Cloud Architecture Review

**Run this phase if `HAS_CLOUD_ARCH` is true**, or if the diff touches services, networking, identity, or data storage configuration even without explicit IaC files.

Assume **Azure** unless another cloud is evident from provider configuration.

### 4.1 — Identity and Access

- Are managed identities used instead of service principal secrets where possible?
- Are RBAC assignments scoped to the minimum required scope (resource group, not subscription)?
- Are new role assignments using built-in roles rather than custom ones (unless justified)?
- No wildcard permissions (`*`) on sensitive resources (Key Vault, Storage, Cosmos DB)

### 4.2 — Networking

- Are new services deployed into a VNet or behind a Private Endpoint where they handle sensitive data?
- Is public network access disabled on storage accounts, databases, Key Vault where not needed?
- Are NSG rules as restrictive as possible — no `0.0.0.0/0` inbound on ports other than 443?
- DNS resolution: private DNS zones configured for private endpoints?

### 4.3 — Secrets and Configuration

- Secrets stored in Azure Key Vault, not in app config or environment variables directly
- Applications reading secrets via managed identity + Key Vault reference, not inline credentials
- No connection strings in ARM/Bicep/Terraform outputs (they appear in state files)
- Certificate management: automated renewal configured, not manual upload

### 4.4 — Data and Storage

- Storage accounts: minimum TLS 1.2, `allowBlobPublicAccess = false` unless intentional CDN use
- Cosmos DB / SQL: geo-redundancy and backup retention appropriate to the environment
- Diagnostic settings / audit logs enabled on databases and Key Vault
- Data at rest: encryption with customer-managed keys where compliance requires it

### 4.5 — Cost and Reliability

- Auto-scaling configured for compute resources (App Service, AKS, Container Apps)
- Appropriate SKU for the environment — no Production SKUs in dev/test, no dev SKUs in prod
- Availability Zones enabled for production workloads where the service supports it
- Alert rules or budget alerts configured for new resources

Output findings under a **Cloud Architecture (Azure)** heading using the same severity format.

---

## Phase 5: Report

Output the complete review. Do not omit sections even if empty.

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PR REVIEW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## Summary
[Phase 1 output]

## Findings

### CRITICAL
[Must fix before merge. Security holes, data loss risks, broken contracts, destructive infra changes without sign-off.]
Format: ❌ [CATEGORY] file.ext:line — description. Fix: <concrete action.>

### HIGH
[Architectural violations, missing required test coverage, significant clean code failures, cloud security gaps.]
Format: ⚠️  [CATEGORY] file.ext:line — description. Fix: <concrete action.>

### MEDIUM
[Code quality issues, missing comments on non-obvious logic, fragile tests, IaC best practice gaps.]
Format: 🔶 [CATEGORY] file.ext:line — description. Fix: <concrete action.>

### LOW
[Minor naming, style, redundant comments — flag but don't gate merge on these.]
Format: 💬 [CATEGORY] file.ext:line — description.

(If a severity level has no findings: "None.")

## Test Coverage
[Phase 2.4 output]

## Language-Specific: [C# .NET | DAML | TypeScript | none detected]
[Phase 3 output, or "No language-specific checks applicable."]

## Cloud Architecture: [Azure | none detected]
[Phase 4 output, or "No cloud architecture changes detected."]

## Verdict
[PASS | PASS WITH CONCERNS | FAIL]

<Pragmatic paragraph: mergeable as-is? What must change before landing? What can follow-up? A CRITICAL finding = FAIL. Multiple HIGH = PASS WITH CONCERNS unless trivially fixed. Never gate on LOW alone.>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Phase 6: Post Review Comments

After presenting the report, **always ask the user**:

> "Would you like me to post these findings as comments on the PR?"

If the user says **yes** (or equivalent), proceed based on platform:

---

### GitHub — Post via gh CLI

Post an overall review with a summary body, then individual inline comments for each finding that has a file and line reference.

**Overall review comment:**
```bash
gh pr review <PR_NUMBER> \
  --body "## PR Review\n\n[VERDICT: PASS|PASS WITH CONCERNS|FAIL]\n\n### Summary\n<summary text>\n\n### Key Findings\n<CRITICAL and HIGH findings as a bullet list>" \
  --comment
```

**Inline comments for CRITICAL and HIGH findings** (where file and line are known):
```bash
gh api repos/{owner}/{repo}/pulls/<PR_NUMBER>/comments \
  --method POST \
  --field body="<finding description and fix>" \
  --field commit_id="$(gh pr view <PR_NUMBER> --json headRefOid -q .headRefOid)" \
  --field path="<file path>" \
  --field line=<line number> \
  --field side="RIGHT"
```

Post MEDIUM and LOW findings as a single grouped comment on the PR (not inline) to avoid noise:
```bash
gh pr review <PR_NUMBER> \
  --body "### Medium / Low findings\n\n<grouped list>" \
  --comment
```

Extract `{owner}` and `{repo}` from:
```bash
gh repo view --json owner,name -q '"\(.owner.login)/\(.name)"'
```

If the line number is not available for a finding, post it as a PR-level comment rather than inline.

---

### Azure DevOps — Post via ADO MCP

Use the Azure DevOps MCP server to post review threads.

For each CRITICAL and HIGH finding with a known file path:
- Create a new PR thread on the specific file (and line if available)
- Set the thread status to `active`
- Comment body: `[SEVERITY] <category> — <description>\n\nFix: <concrete action>`

For the overall verdict and summary:
- Post a single PR-level thread with the full summary and verdict

For MEDIUM and LOW findings:
- Group into a single PR-level thread titled "Medium / Low findings" to avoid noise

Use the ADO MCP tools available in this session. If the MCP is not available or the PR ID cannot be determined, fall back to printing the formatted comments for the user to post manually.

---

### If platform is unknown or no PR exists

Print the formatted comments for the user to copy:

```
## Ready to post — copy these comments manually:

### Overall comment:
[formatted summary]

### Inline findings:
File: <path> Line: <line>
> <finding and fix>
```

---

## Pragmatic Review Principles

1. **Flag what matters.** A finding that can't plausibly cause a bug, security issue, or maintenance problem is not worth mentioning.
2. **Respect existing conventions.** Don't flag consistent patterns unless this PR introduces a NEW inconsistency.
3. **CRITICAL and HIGH are gates. MEDIUM and LOW are suggestions.** Never block a merge on LOW alone.
4. **No speculative bugs.** If a finding needs multiple unlikely conditions to cause harm, it's at most MEDIUM.
5. **One fix per finding.** Pick the best fix. Don't offer three alternatives.
6. **Don't pad.** A clean review with no CRITICAL/HIGH findings is a good outcome.

---

## Mandatory Review Checklist

Before outputting the Phase 5 report:

1. [ ] Repository context verified (`IN_REPO` set, cwd matched against PR URL if provided)
2. [ ] PR branch checked out locally (GitHub, `IN_REPO=true`) — or remote-only mode recorded if not
3. [ ] Platform detected and diff sourced correctly (local git preferred; gh API only if `IN_REPO=false`)
4. [ ] Change summary written — scope check included (Phase 1)
5. [ ] Naming and function quality assessed (2.1)
6. [ ] Architecture and layer violations checked (2.2)
7. [ ] Comment presence and quality assessed (2.3)
8. [ ] Test coverage assessed outside-in first (2.4)
9. [ ] Universal security checks applied (2.5)
10. [ ] Language-specific skill applied if stack detected (Phase 3)
11. [ ] Cloud architecture reviewed if HAS_CLOUD_ARCH (Phase 4)
12. [ ] All four severity levels reported (even if empty)
13. [ ] Verdict is explicit: PASS / PASS WITH CONCERNS / FAIL
14. [ ] Verdict paragraph states what must change vs. what can wait
15. [ ] User asked whether to post findings back to the PR (Phase 6)

Do not output the report until checks 1–14 are done. Do not skip check 15.
