# Just-this-week changes

How one song of a generated week is changed for that week only, and
stays changed when the week is generated again.

## What a change is

A `week_change` row belongs to a week (`playlist`; deleting the week
deletes it) and names one song by its place: `row` and `block` (from 1,
as traces number them; a flat station's row is 1) and which of that
rule's songs it is (`k`, from 0). There are two kinds:

- **swap**: a song chosen by hand (`track_id`, from any of the
  station's pools);
- **rule**: the song a changed rule picks there (`rule`, the whole
  block as changed; its size is always the original's).

Each keeps its rule's `rule_strategy` and `rule_source` as they were
when it was made. A change whose place now holds another kind of rule
isn't put on it.

## Making one

`POST /api/playlists/{id}/changes` works out the song and appends a
version of the week at once, with only that song different and a
`note` saying so ("Just this week: song 7 swapped for …"). It's made
against the version the page shows (`expected_version`; 409 if the
week has changed since) and refused while the station's week is being
generated. `DELETE …/changes/{change_id}` undoes it the same way. The
changed song's trace slot keeps its rule's source, strategy and place,
and carries a `change` note: what kind, the rule's own song (`was`,
all of it, so undoing puts it back exactly) and, for a rule change, the
rule and what it picked.

A rule change's song is worked out by playing the week again with the
changed rule at that one place (`generate(..., override_block=…)`; on a
row station the other rows keep the template's rule), with the same
seed and inputs, and stopping once it has played (`stop_after`).
Everything before it plays the same, so the song is picked in the same
context the rule's own was. Each replay costs part of a week (about
3 s at most on Rebirth Radio's 2,196 songs), so a week has at most 20
rule changes; swaps cost nothing.

## Generating again

Every way a week is generated (`/generate`, its job, the preview job,
`generate_chain`, the CLI) generates it as the rules play it, then makes
its changes (`engine.week_change_store`). The preview job makes the
changes it's sent, so the week page can preview its own, following its
queued edits. Changes that can't be made are reported
(`week_changes_dropped`, with the reason) and deleted.

The station's state follows what its **rules** picked
(`GenerationResult.rule_picks`): mix usage records the rule's song, and
cursors don't depend on picks, so a change doesn't shift later weeks.
The songs the week played, changes included, are what `prev_ref` repeats
next week.

## Following the rules

When the editor's batch save or the structure route moves, adds or
removes rules, every week's changes follow their rule
(`follow_rule_ops`, the twin of the editor's `slotAfter`, checked
against the shared cases in `tests/fixtures/slot_after.json`). A
change whose rule is removed, or whose song a smaller rule no longer
has, is deleted. The station's JSON page replaces the whole template,
so there the rule check at the next generation is what drops a change
on a place that now holds another kind of rule.

Rollback makes the week's changes those of the version rolled back to,
from its songs' `change` notes.
