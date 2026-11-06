"""Just-this-week changes in the database (engine.models.WeekChange) and
making them in a generation (engine.week_changes).

Every way a week is generated (the API's generate and its job, the
preview job, `generate_chain`, the CLI) generates it as the rules play
it, then calls `apply_changes` with the same inputs, then, once it's
saved, `forget_dropped` for the changes that couldn't be made.

A change is passed around as a plain dict (`Item`): the database row's
fields, or what a preview was sent.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Iterator, Mapping

from sqlalchemy.orm import Session

from engine.generator import GenerationResult, ProgressEvent, generate
from engine.models import Playlist, Track, WeekChange
from engine.template import REFERENCE_SAMPLING_STRATEGIES, Template, TemplateError
from engine.week_changes import (
    Applied, Change, Dropped, apply_week_changes, apply_week_changes_stream,
    changed_rule, rule_change_count,
)

Item = Mapping[str, Any]

#: The fields of a change, as stored and as the API takes them.
ITEM_FIELDS = (
    "row", "block", "k", "kind", "track_id", "rule", "rule_strategy", "rule_source",
)


def item_of(row: WeekChange) -> dict[str, Any]:
    d: dict[str, Any] = {f: getattr(row, f) for f in ITEM_FIELDS}
    d["id"] = row.id
    return d


def week_change_rows(db: Session, *, station_id: int, week: int) -> list[WeekChange]:
    """The week's changes, oldest first (the order they're made in)."""
    return (
        db.query(WeekChange)
        .join(Playlist, Playlist.id == WeekChange.playlist_id)
        .filter(Playlist.station_id == station_id, Playlist.week == week)
        .order_by(WeekChange.id)
        .all()
    )


def saved_items(db: Session, *, station_id: int, week: int) -> list[dict[str, Any]]:
    return [item_of(r) for r in week_change_rows(db, station_id=station_id, week=week)]


def _extra_pools(db: Session, spec, pools: Mapping[str, list]) -> dict[str, list]:
    """The pools `spec` reads that `pools` doesn't have (a reference pool
    only a changed rule uses). Raises as `load_pools` does."""
    from engine.persistence import load_pools  # persistence imports this module

    content: list[str] = []
    reference: list[str] = []
    if spec.source not in pools:
        (reference if spec.strategy in REFERENCE_SAMPLING_STRATEGIES else content).append(spec.source)
    allow = [n for n in (spec.allowlist_sources or []) if n not in pools and n not in reference]
    if not (content or reference or allow):
        return {}
    return load_pools(db, SimpleNamespace(
        content_source_names=content,
        reference_sampling_source_names=reference,
        allowlist_source_names=allow,
    ))


def build_changes(
    db: Session,
    items: list[Item],
    *,
    template_raw: dict,
    pools: Mapping[str, list],
) -> tuple[list[Change], dict[str, list]]:
    """The engine's changes for `items`, and the pools to work out rule
    changes with (`pools`, plus any a changed rule needs that the station
    doesn't load)."""
    track_ids = {it["track_id"] for it in items if it.get("kind") == "swap" and it.get("track_id")}
    tracks = (
        {t.id: t for t in db.query(Track).filter(Track.id.in_(track_ids)).all()}
        if track_ids else {}
    )
    run_pools = dict(pools)
    out: list[Change] = []
    for it in items:
        kind = it.get("kind")
        common = dict(
            row=int(it["row"]), block=int(it["block"]), k=int(it["k"]), kind=kind,
            rule_strategy=it["rule_strategy"], rule_source=it["rule_source"],
            id=it.get("id"),
        )
        if kind == "swap":
            out.append(Change(**common, track=tracks.get(it.get("track_id"))))
            continue
        if kind != "rule":
            out.append(Change(**common))
            continue
        rule = it.get("rule") if isinstance(it.get("rule"), dict) else None
        spec = changed_rule(template_raw, common["block"], rule) if rule is not None else None
        invalid = None
        if spec is not None:
            try:
                run_pools.update(_extra_pools(db, spec, run_pools))
            except (ValueError, TemplateError) as e:
                invalid = f"the changed rule can't be played: {e}"
        out.append(Change(
            **common, spec=spec, invalid=invalid, rule=rule,
            fields=_fields(template_raw, common["block"], rule) if rule else None,
        ))
    return out, run_pools


def _fields(template_raw: dict, block: int, rule: dict) -> dict:
    """What `rule` changes of the station's rule number `block`."""
    layout = template_raw.get("layout") or {}
    blocks = layout.get("flat_slots") or layout.get("row_template") or []
    orig = blocks[block - 1] if 1 <= block <= len(blocks) else {}
    return {
        k: v for k, v in rule.items()
        if k != "size" and orig.get(k) != v
    } | {k: None for k in orig if k not in rule and k != "size"}


@dataclass
class EngineInputs:
    """What a week was generated with, past the template and pools: the
    rule changes are worked out with the same."""
    week: int
    seed: int | None = None
    excluded_mix_ids: Any = None
    alias_map: Any = None
    previous_playlist: Any = None
    weighted_window_default: Any = None
    named_cursor_positions: Any = None
    excluded_track_pids: Any = None

    def kwargs(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class Prepared:
    """A week's changes, loaded and ready to make (`build_changes`); no
    database needed from here, so a job's worker can make them."""
    changes: list[Change]
    pools: dict[str, list]

    def __bool__(self) -> bool:
        return bool(self.changes)


def prepare_changes(
    db: Session, items: list[Item], *, template_raw: dict, pools: Mapping[str, list],
) -> Prepared:
    if not items:
        return Prepared(changes=[], pools=dict(pools))
    changes, run_pools = build_changes(db, items, template_raw=template_raw, pools=pools)
    return Prepared(changes=changes, pools=run_pools)


def _runner(template: Template, prepared: Prepared, inputs: EngineInputs):
    kw = inputs.kwargs()

    def run(spec, at):
        return generate(
            template, prepared.pools, **kw,
            stop_after=at, override_block=(at[0], at[1], spec),
        )
    return run


def make_prepared(
    result: GenerationResult, prepared: Prepared, *,
    template: Template, inputs: EngineInputs,
) -> Applied:
    """`result` (the week as the rules play it) with the changes made."""
    return apply_week_changes(result, prepared.changes, run=_runner(template, prepared, inputs))


def apply_changes(
    db: Session,
    result: GenerationResult,
    items: list[Item],
    *,
    template: Template,
    template_raw: dict,
    pools: Mapping[str, list],
    inputs: EngineInputs,
) -> Applied:
    """`result` (the week as the rules play it) with `items` made."""
    prepared = prepare_changes(db, items, template_raw=template_raw, pools=pools)
    return make_prepared(result, prepared, template=template, inputs=inputs)


def stream_with_changes(
    stream: Iterator[ProgressEvent | GenerationResult],
    prepared: Prepared,
    *,
    template: Template,
    inputs: EngineInputs,
    holder: dict[str, Applied],
) -> Iterator[ProgressEvent | GenerationResult]:
    """A generation's event stream (`generate_stream`) with the changes
    made to its result, for a job: a progress event before each rule
    change (the job can be cancelled between them), then the changed
    result. The `Applied` is left in `holder["applied"]` for on_result.
    """
    for event in stream:
        if not isinstance(event, GenerationResult):
            yield event
            continue
        if not prepared:
            holder["applied"] = Applied(result=event)
            yield event
            return
        yield ProgressEvent(
            phase="week_changes", current=0, total=rule_change_count(prepared.changes),
            label="Making this week's changes",
        )
        for step in apply_week_changes_stream(
            event, prepared.changes, run=_runner(template, prepared, inputs),
        ):
            if isinstance(step, Applied):
                holder["applied"] = step
                yield step.result
                return
            done, total = step
            yield ProgressEvent(
                phase="week_changes", current=done, total=total,
                label=f"Making this week's changes ({done + 1} of {total})",
            )


def dropped_report(dropped: list[Dropped]) -> list[dict[str, Any]]:
    """The changes that couldn't be made, for an API response."""
    return [
        {
            "id": d.change.id,
            "row": d.change.row, "block": d.change.block, "k": d.change.k,
            "kind": d.change.kind,
            "reason": d.reason,
        }
        for d in dropped
    ]


def forget_dropped(db: Session, dropped: list[Dropped]) -> None:
    """Delete the saved changes that couldn't be made: they'd only be
    dropped again. The caller commits."""
    ids = [d.change.id for d in dropped if d.change.id is not None]
    if ids:
        db.query(WeekChange).filter(WeekChange.id.in_(ids)).delete(
            synchronize_session=False,
        )


def items_from_slots(slots: list[dict]) -> list[dict[str, Any]]:
    """The just-this-week changes a version's songs carry (their
    `change` notes), as items."""
    out: list[dict[str, Any]] = []
    seen: dict[tuple[int, int], int] = {}
    for s in slots:
        key = (s["row"], s["block"])
        k = seen.get(key, 0)
        seen[key] = k + 1
        note = s.get("change")
        if not note:
            continue
        kind = note.get("kind")
        if kind == "swap" and s.get("track_id") is not None:
            out.append({"kind": "swap", "track_id": s["track_id"], "rule": None})
        elif kind == "rule" and isinstance(note.get("rule"), dict):
            out.append({"kind": "rule", "track_id": None, "rule": dict(note["rule"])})
        else:  # pragma: no cover — every note written has these
            continue
        out[-1].update(
            row=s["row"], block=s["block"], k=k,
            rule_strategy=s["strategy"], rule_source=s["source"],
        )
    return out


def restore_from_slots(db: Session, playlist: Playlist, slots: list[dict]) -> None:
    """Make the week's changes those `slots` (a version's) carry. The
    caller commits."""
    db.query(WeekChange).filter_by(playlist_id=playlist.id).delete(
        synchronize_session=False,
    )
    for it in items_from_slots(slots):
        db.add(WeekChange(playlist_id=playlist.id, **it))


# ---------- following the station's rules -----------------------------
#
# A change is addressed by its rule's place (`block`). When the station's
# rules are moved, added or removed (the editor's batch save, the
# structure route), every week's changes follow their rule, as the
# editor's queue does (web/src/lib/edit-ops.ts `slotAfter`; shared cases
# in tests/fixtures/slot_after.json). A change whose rule is removed,
# or whose song a smaller rule no longer has, is deleted.
#
# The station's JSON page (PUT /api/stations/{slug}) replaces the whole
# template, so its changes can't be followed: there the rule check at
# the next generation (`rule_strategy` / `rule_source`) drops a change
# whose place now holds another kind of rule.


@dataclass(frozen=True)
class RuleOp:
    """A change to the station's rules' places, as `slotAfter` takes
    them. Places are from 0 (flat_slots / row_template indices).

      * ("add", at)              — a rule added at `at`
      * ("remove", at)           — the rule at `at` removed
      * ("size", at, new_size)   — the rule at `at` resized
      * ("move", from_, count, to) — moveRules / moveSection
    """
    op: str
    args: tuple[int, ...]


def slot_after(op: RuleOp, p: int) -> int | None:
    """Where the rule at place `p` is after `op`; None if `op` removed it.
    The Python twin of edit-ops.ts `slotAfter`."""
    from engine.rule_moves import new_position

    if op.op == "add":
        (at,) = op.args
        return p + 1 if at <= p else p
    if op.op == "remove":
        (at,) = op.args
        if at == p:
            return None
        return p - 1 if at < p else p
    if op.op == "move":
        from_, count, to = op.args
        return new_position(p, from_, count, to)
    return p


def follow_rule_ops(db: Session, station_id: int, ops: list[RuleOp]) -> None:
    """Make every week of the station's changes follow `ops` (made in
    order). The caller commits, with the edit."""
    if not ops:
        return
    rows = (
        db.query(WeekChange)
        .join(Playlist, Playlist.id == WeekChange.playlist_id)
        .filter(Playlist.station_id == station_id)
        .all()
    )
    if not rows:
        return
    keep: list[tuple[WeekChange, int]] = []
    for r in rows:
        p: int | None = r.block - 1
        for op in ops:
            if op.op == "size" and op.args[0] == p and r.k >= op.args[1]:
                p = None
            else:
                p = slot_after(op, p)
            if p is None:
                break
        if p is None:
            db.delete(r)
        elif p != r.block - 1:
            keep.append((r, p + 1))
    if not keep:
        return
    # Two-step so a change moving into another's old place never clashes
    # with the (playlist, row, block, k) unique constraint mid-update.
    for r, _ in keep:
        r.block = -r.block
    db.flush()
    for r, block in keep:
        r.block = block
    db.flush()
