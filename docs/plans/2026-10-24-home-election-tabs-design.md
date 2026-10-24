# Homepage election lead: mail-in and early voting, tabbed — design

Date: 2026-10-24

## Context

The election-mode lead (2026-10-17 redesign) showed the mail-in tracking alone. Early
voting opened on 2026-10-22 and its figures now publish on the turnout pages, so the lead
should carry both ways Marylanders are voting before election day.

## Design

Election mode only; the registration lead is unchanged.

* **Header**: the election name and "ballots so far", the as-of day, and a row of large
  unboxed numbers:
  * *already voted*: mail-in ballots received back plus early voters, labelled as that
    sum. Envelopes received can still be rejected in the canvass, and the label says the
    figure counts ballots in hand, not votes counted;
  * that sum as a share of the eligible active voters the early-voting report prints;
  * days until election day.
  The first two appear only when both families have a snapshot.
* **Tabs**: *Mail-in ballots* and *Early voting*. Each panel is the old two-column lead:
  tiles, one sentence, a link, and a county map.
  * Mail-in: the four tiles, the return-rate sentence, the return-rate map (unchanged).
  * Early voting: early voters (change since the previous report), share of eligible
    actives, the latest day's count, the Democratic and Republican shares of early
    voters; a sentence naming the county with the highest share; a gold-ramp map of each
    county's early voters over its eligible actives, linking to the county table.
* **Tabs without JS**: the panels stack. `static/tabs.js` turns a `[data-tabs]` container's
  `[data-tab]` children into an ARIA tablist; it does nothing with fewer than two panels,
  so a campaign without early voting yet shows the mail-in panel alone.
* The masthead's "what is current" line adds "Early voting as of …"; the Explore strip's
  Turnout line mentions early voting.

## Verification

`home_context` tests on the real data (both panels, the summed figure equals the two
families' latest totals), the existing map tests updated for two maps, and a browser check
of the tabs at desktop and phone width.
