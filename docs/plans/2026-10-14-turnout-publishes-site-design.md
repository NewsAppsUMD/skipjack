# Turnout snapshot publishes the site — design

Date: 2026-10-14
Approved approach: **A — explicit workflow_dispatch trigger** from the snapshot workflow after its commit.

## The problem

The daily turnout job (`turnout-daily.yml`) snapshots the press-room files, rebuilds the
database, tests, and commits changed snapshots to main. Both its header comment and the
README say the resulting push deploys the site, because `pages.yml` publishes on every
push to main — but the bot pushes with the repository's own `GITHUB_TOKEN`, and GitHub
Actions deliberately does not run workflows off events a `GITHUB_TOKEN` creates. The run
history confirms the gap: the 2026-10-13 1:37pm ET snapshot pushed its data at 17:47Z and
no publish followed; the site only caught up when someone dispatched *Publish site* by
hand at 21:48Z.

## The change (verified decisions: publish only when data changed; approach A)

One step in `turnout-daily.yml`, after the commit step, gated by the same
`changed` output the commit uses:

```yaml
      - name: Publish the site (the bot push alone does not trigger Pages)
        if: steps.changed.outputs.skip != 'true'
        run: gh workflow run pages.yml --ref main
        env:
          GH_TOKEN: ${{ github.token }}
```

Why this works: `gh` is preinstalled on the runner; `workflow_dispatch` is one of the two
events exempt from the `GITHUB_TOKEN` no-trigger rule, and `pages.yml` already declares
`workflow_dispatch`; the workflow's existing `contents: write` permission is enough to
dispatch it; ordering is guaranteed because the dispatch happens only after the data push
succeeded, and the fresh publish run checks out main with the new data. No new secrets,
no changes to `pages.yml`.

The now-false comments get corrected: the commit step's "(the push deploys the site)" and
the file header both say what actually happens — the snapshot commits the data and
explicitly dispatches the publish; a human push still publishes on its own.

## Failure behavior

Unchanged in spirit. A parse that does not reconcile still fails the run before any data
lands. The trigger is fire-and-forget: `gh workflow run` returns once the dispatch is
accepted; the publish runs under its own `concurrency: group: pages` (queued after any
in-flight deploy). A failing publish leaves the site on yesterday's figures with a red
*Publish site* run to investigate — today's semantics, made self-healing instead of
human-triggered.

## Docs

README line "…is what deploys the updated site" gets the corrected chain (snapshot
commits + dispatches; human pushes still publish).

## Verification

1. Syntax check both workflows (actionlint if available; otherwise the push plus a manual
   dispatch proves GitHub parses them).
2. Dispatch `Turnout snapshot` by hand on a no-change day: no commit, no publish.
3. Run the step's exact command by hand once (`gh workflow run pages.yml --ref main`) and
   confirm a *Publish site* run starts.
4. The loop closes on the next real data change — the next scheduled 1:37pm ET run, or
   the next `make turnout` — which must produce a *Publish site* run with no human step.

## Out of scope

Publishing after no-change runs; moving the build/deploy into the snapshot workflow;
PAT-based bot pushes; any change to what publishes on human pushes to main.