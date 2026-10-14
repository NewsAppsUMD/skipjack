# Turnout snapshot publishes the site — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the daily turnout snapshot workflow start the Pages deploy itself after committing new data, because the bot's `GITHUB_TOKEN` push cannot trigger the Pages workflow on its own.

**Architecture:** One gated step in `.github/workflows/turnout-daily.yml` runs `gh workflow run pages.yml --ref main` (workflow_dispatch is exempt from GitHub's token-trigger rule; `pages.yml` already declares `workflow_dispatch`) inside the same `changed != 'true'` gate the commit step uses — no new secrets, no change to `pages.yml`. The now-false comments in the workflow and the README's Publishing section get the corrected story.

**Tech Stack:** GitHub Actions + `gh` CLI (preinstalled on runners; authenticated locally), YAML, git.

**Design doc:** `docs/plans/2026-10-14-turnout-publishes-site-design.md` (committed).

**Facts you must know:**
- Verified in the real run history: the 2026-10-13 snapshot run pushed `turnout snapshot 2026-10-13` at 17:47Z and no *Publish site* run followed; a human dispatch at 21:48Z did the deploy. The comment in `turnout-daily.yml:5-6` ("The push to main is what deploys the site") and `README.md:180-182` are false for bot pushes.
- The new step must sit **after** the commit step and share its gate: `if: steps.changed.outputs.skip != 'true'` (the approved decision: publish only when data changed).
- `pages.yml` has `workflow_dispatch` (line 16) and its own `concurrency` group, so dispatches queue behind in-flight deploys.
- Local `make turnout` pushes as the user (a human push), so it does NOT need the manual dispatch — the workflow step exists only for the runner's token push.
- The remote is `github.com:NewsAppsUMD/skipjack`; `gh` is authenticated (verified during exploration).

---

### Task 1: The dispatch step and the corrected comments in `turnout-daily.yml`

**Files:**
- Modify: `.github/workflows/turnout-daily.yml`

**Step 1: Edit the file**

Three changes, all in `.github/workflows/turnout-daily.yml`:

1. **Header comment** — replace lines 5-6:

   ```yaml
   # downloads nothing new), parse the new snapshots into data/turnout/, rebuild the
   # database, run the tests, and commit what changed. The push to main is what deploys
   # the site (the Pages workflow runs on it), so this workflow does not deploy itself.
   ```

   with:

   ```yaml
   # downloads nothing new), parse the new snapshots into data/turnout/, rebuild the
   # database, run the tests, and commit what changed. The token push cannot start the
   # Pages workflow (GitHub ignores pushes made by a workflow's own token), so after
   # committing the snapshot dispatches the publish itself; a person's push deploys the
   # ordinary way.
   ```

2. **Commit step** — its name `Commit the new snapshots (the push deploys the site)` becomes `Commit the new snapshots`, keeping the `if:` and body unchanged.

3. **New step** — insert immediately after the commit step (at the end of the `snapshot` job):

   ```yaml
      - name: Publish the site (the bot push alone does not trigger Pages)
        if: steps.changed.outputs.skip != 'true'
        run: gh workflow run pages.yml --ref main
        env:
          GH_TOKEN: ${{ github.token }}
   ```

   Match the file's two-space step indentation exactly (the existing steps are at six
   spaces for `- name:`).

**Step 2: Syntax check**

Run: `uv run --with pyyaml python -c 'import yaml,sys; yaml.safe_load(open(sys.argv[1])); print("ok")' .github/workflows/turnout-daily.yml`
Expected: `ok` (if `actionlint` is installed — `command -v actionlint` — run it too and expect no findings; skip silently if absent).

**Step 3: Commit**

```bash
git add .github/workflows/turnout-daily.yml
git commit -m "the turnout snapshot starts the pages deploy itself

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: Correct the README's Publishing section

**Files:**
- Modify: `README.md:180-182`

**Step 1: Edit**

Replace:

````markdown
During a campaign, `.github/workflows/turnout-daily.yml` snapshots the press-room turnout
files once a day (fetch, parse, build, test), commits what changed, and pushes — which is
what deploys the updated site. `make turnout` does the same by hand.
````

with:

````markdown
During a campaign, `.github/workflows/turnout-daily.yml` snapshots the press-room turnout
files once a day (fetch, parse, build, test), commits what changed, and pushes — then
starts the Pages workflow itself, because GitHub ignores pushes made by a workflow's own
token. `make turnout` does the same by hand, and publishes the ordinary way: your push is
a person's push, which triggers `pages.yml` on its own.
````

(The preceding paragraph at lines 175-178 stays as-is: it remains true for human pushes.)

**Step 2: Commit**

```bash
git add README.md
git commit -m "the README says what deploys the site honestly

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Push and verify against GitHub

No code changes; this task's steps run against the live repository and must be executed
after Tasks 1-2 are committed. Each command is safe to run (a no-change dispatch touches
nothing; a Pages deploy re-publishes the current site).

**Step 1: Push** — `git push origin main`.
Expected: fast-forward (no bot commit since the rebase), and a *Publish site* run appears
from this push (event: `push`).

**Step 2: Syntax proof on the pushed workflow** — the push above is also GitHub's own
parse gate (invalid YAML fails the workflow file at run time).

**Step 3: Dispatch a no-change snapshot** — `gh workflow run turnout-daily.yml --ref main`,
then `gh run watch` (or poll `gh run list --workflow=turnout-daily.yml --limit 1`).
Expected: the run skips the database/test/commit steps (`skip=true` — the 2026-10-13
snapshot is the latest data and the board has not refreshed since), exits success, and — the
assertion that matters — **no new *Publish site* run is created** (check
`gh run list --workflow=pages.yml --limit 2`; the newest entry is still the push from
Step 1).

**Step 4: Prove the trigger with the step's exact command** —
`gh workflow run pages.yml --ref main`.
Expected: a new *Publish site* run appears with event `workflow_dispatch` and goes green
(about 1.5 minutes).

**Step 5: Record the closed-loop test** — the full loop cannot be proven without a real
data change. On the next actual change (tomorrow's 17:37Z scheduled run, or any
`make turnout` run that lands new snapshots), verify a *Publish site* run starts with
event `workflow_dispatch` about a minute after the bot push, with no human dispatch.
The design doc documents this expectation under Verification step 4; confirm it in the
next session's summary.

**No commit needed** for Task 3.