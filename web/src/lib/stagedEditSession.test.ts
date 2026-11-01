import { beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import type { Edit } from './edit-ops';
import type { StationTemplate } from './editor';

const { getStationSidecarMock, startPreviewJobMock, watchJobMock, cancelJobMock, applyBatchMock, FakeBatchFailure, FakeConflict } =
  vi.hoisted(() => {
    class FakeConflict extends Error {}
    class FakeBatchFailure extends Error {
      failedAtIdx: number;
      kind: string;
      constructor(message: string, failedAtIdx: number, kind = 'patchBlock') {
        super(message);
        this.failedAtIdx = failedAtIdx;
        this.kind = kind;
      }
    }
    return {
      getStationSidecarMock: vi.fn(),
      startPreviewJobMock: vi.fn(),
      watchJobMock: vi.fn(),
      cancelJobMock: vi.fn(async () => undefined),
      applyBatchMock: vi.fn(),
      FakeBatchFailure,
      FakeConflict,
    };
  });

vi.mock('$lib/api', async () => ({
  ...(await vi.importActual<typeof import('$lib/api')>('$lib/api')),
  getStationSidecar: getStationSidecarMock,
  startPreviewJob: startPreviewJobMock,
  watchJob: watchJobMock,
  cancelJob: cancelJobMock,
  applyBatch: applyBatchMock,
  BlockPatchConflict: FakeConflict,
  SidecarBatchFailure: FakeBatchFailure,
}));

import { createStagedEditSession } from './stagedEditSession';
import { stagedEditsKey, STAGED_EDITS_SCHEMA_VERSION } from './stagedEdits';

function template(): StationTemplate {
  return {
    station: 'ts',
    name: 'Test',
    sources: [{ name: 'A', colour: '#abc', role: 'content' }],
    layout: {
      unit: 'slot',
      unit_labels: [],
      row_start_offsets: [],
      flat_slots: [
        { source: 'A', strategy: 'pick', size: 1 },
        { source: 'A', strategy: 'pick', size: 2 },
      ],
    },
  };
}

const SIDECAR = { schema_version: 1, sections: [], macros: [], layout_order: [] };

function loaded(updatedAt = 'ts-1') {
  return { template: template(), sidecar: structuredClone(SIDECAR), sidecar_error: null, updated_at: updatedAt };
}

/** A trace tagged with the size of slot 1 in the template it previewed,
 *  so tests can tell a saved-week preview from an edited one. */
function traceFor(t: { layout: { flat_slots?: { size: number }[] } }, week: number) {
  return { trace_id: 'x', station: 'ts', week, rng: 'r', seed: null, generated_at: '', slots: [], size1: t.layout.flat_slots![1].size };
}

const sizeEdit: Edit = { kind: 'patchBlock', address: { slot_index: 1 }, fields: { size: 7 } };

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  getStationSidecarMock.mockImplementation(async () => loaded());
  let n = 0;
  startPreviewJobMock.mockImplementation(async (_slug: string, t: StationTemplate, _s: unknown, week: number) => {
    n += 1;
    return { job_id: `job-${n}`, trace: traceFor(t, week) };
  });
  // Each job finishes with the trace computed at start (stashed by job id).
  const traces = new Map<string, unknown>();
  const origStart = startPreviewJobMock.getMockImplementation()!;
  startPreviewJobMock.mockImplementation(async (...args: Parameters<typeof origStart>) => {
    const r = await origStart(...args);
    traces.set(r.job_id, r.trace);
    return { job_id: r.job_id };
  });
  watchJobMock.mockImplementation(async function* (id: string) {
    yield { status: 'done', result: { trace: traces.get(id) } };
  });
});

async function ready(week = 1) {
  const session = createStagedEditSession('ts', { debounceMs: 0, week });
  await session.load();
  await vi.waitFor(() => expect(get(session).previewing).toBe(false));
  await vi.waitFor(() => expect(get(session).stagedTrace).not.toBeNull());
  return session;
}

describe('createStagedEditSession', () => {
  it('loads the station and previews the saved week once when nothing is queued', async () => {
    const session = await ready(3);
    const s = get(session);
    expect(s.loading).toBe(false);
    expect(s.template?.station).toBe('ts');
    expect(startPreviewJobMock).toHaveBeenCalledTimes(1); // edited week IS the saved week
    expect(s.savedTrace).toMatchObject({ week: 3, size1: 2 });
    expect(s.stagedTrace).toBe(s.savedTrace);
    session.destroy();
  });

  it('a queued edit previews the edited week beside the saved one', async () => {
    const session = await ready();
    session.stage(sizeEdit);
    await vi.waitFor(() => expect(get(session).stagedTrace).toMatchObject({ size1: 7 }));
    const s = get(session);
    expect(s.opLog).toHaveLength(1);
    expect(s.staged.template?.layout.flat_slots![1].size).toBe(7);
    expect(s.template?.layout.flat_slots![1].size).toBe(2); // saved untouched
    expect(s.savedTrace).toMatchObject({ size1: 2 }); // not re-run for a staged edit
    expect(startPreviewJobMock).toHaveBeenCalledTimes(2);
    session.destroy();
  });

  it('keeps queued edits across a reload of the page', async () => {
    const first = await ready();
    first.stage(sizeEdit);
    first.destroy();
    const stored = JSON.parse(window.localStorage.getItem(stagedEditsKey('ts'))!);
    expect(stored).toMatchObject({ schemaVersion: STAGED_EDITS_SCHEMA_VERSION, updatedAtSnapshot: 'ts-1' });

    const second = await ready();
    expect(get(second).opLog).toEqual([sizeEdit]); // same version: back without asking
    expect(get(second).recovered).toBeNull();
    second.destroy();
  });

  it('asks before bringing back edits queued against an older version', async () => {
    const first = await ready();
    first.stage(sizeEdit);
    first.destroy();
    getStationSidecarMock.mockImplementation(async () => loaded('ts-2'));

    const second = await ready();
    expect(get(second).opLog).toEqual([]);
    expect(get(second).recovered).toEqual({ count: 1 });
    second.acceptRecovered();
    expect(get(second).opLog).toEqual([sizeEdit]);
    expect(JSON.parse(window.localStorage.getItem(stagedEditsKey('ts'))!).updatedAtSnapshot).toBe('ts-2');
    second.destroy();

    const third = await ready();
    expect(get(third).recovered).toBeNull(); // not asked again
    third.discard();
    third.destroy();
  });

  it('discarding recovered edits forgets them', async () => {
    const first = await ready();
    first.stage(sizeEdit);
    first.destroy();
    getStationSidecarMock.mockImplementation(async () => loaded('ts-2'));
    const second = await ready();
    second.discardRecovered();
    expect(get(second).recovered).toBeNull();
    expect(window.localStorage.getItem(stagedEditsKey('ts'))).toBeNull();
    second.destroy();
  });

  it('saves the queue in one batch, then previews the new saved week', async () => {
    const session = await ready();
    session.stage(sizeEdit);
    const saved = template();
    saved.layout.flat_slots![1].size = 7;
    applyBatchMock.mockResolvedValueOnce({ template: saved, sidecar: SIDECAR, updated_at: 'ts-2' });

    expect(await session.save()).toBe(true);
    expect(applyBatchMock).toHaveBeenCalledWith('ts', [sizeEdit], 'ts-1');
    const s = get(session);
    expect(s.opLog).toEqual([]);
    expect(s.updatedAt).toBe('ts-2');
    expect(window.localStorage.getItem(stagedEditsKey('ts'))).toBeNull();
    await vi.waitFor(() => expect(get(session).savedTrace).toMatchObject({ size1: 7 }));
    session.destroy();
  });

  it('a conflict keeps the queue and says to reload', async () => {
    const session = await ready();
    session.stage(sizeEdit);
    applyBatchMock.mockRejectedValueOnce(new FakeConflict('changed'));
    expect(await session.save()).toBe(false);
    expect(get(session)).toMatchObject({ conflict: true, opLog: [sizeEdit], saving: false });
    session.destroy();
  });

  it('a failed edit can be dropped and the rest saved', async () => {
    const session = await ready();
    const other: Edit = { kind: 'patchBlock', address: { slot_index: 0 }, fields: { size: 3 } };
    session.stage(sizeEdit);
    session.stage(other);
    applyBatchMock.mockRejectedValueOnce(new FakeBatchFailure('bad size', 1));
    expect(await session.save()).toBe(false);
    expect(get(session)).toMatchObject({ failedAtIdx: 1, saveError: 'Save failed at edit 1 of 2: bad size' });

    applyBatchMock.mockResolvedValueOnce({ template: template(), sidecar: SIDECAR, updated_at: 'ts-2' });
    expect(await session.dropFailed()).toBe(true);
    expect(applyBatchMock).toHaveBeenLastCalledWith('ts', [other], 'ts-1');
    expect(get(session).opLog).toEqual([]);
    session.destroy();
  });

  it('discard drops the queue and reloads the saved station', async () => {
    const session = await ready();
    session.stage(sizeEdit);
    await session.discard();
    expect(get(session).opLog).toEqual([]);
    expect(getStationSidecarMock).toHaveBeenCalledTimes(2);
    expect(window.localStorage.getItem(stagedEditsKey('ts'))).toBeNull();
    session.destroy();
  });

  it('changing the week previews the new week', async () => {
    const session = await ready(1);
    session.setWeek(4);
    await vi.waitFor(() => expect(get(session).savedTrace).toMatchObject({ week: 4 }));
    expect(get(session).week).toBe(4);
    session.setWeek(Number.NaN); // ignored
    expect(get(session).week).toBe(4);
    session.destroy();
  });

  it('a failed preview is reported in words', async () => {
    watchJobMock.mockImplementation(async function* () {
      yield { status: 'error', error: { message: 'engine.template.TemplateError: no slots' } };
    });
    const session = createStagedEditSession('ts', { debounceMs: 0 });
    await session.load();
    await vi.waitFor(() => expect(get(session).previewError).toBe('no slots'));
    expect(get(session).previewing).toBe(false);
    session.destroy();
  });

  it('a load failure is reported', async () => {
    getStationSidecarMock.mockRejectedValueOnce(new Error('404 not found'));
    const session = createStagedEditSession('ts', { debounceMs: 0 });
    await session.load();
    expect(get(session)).toMatchObject({ loading: false, loadError: '404 not found' });
    session.destroy();
  });

  it("a newer change cancels the edits' preview that was running", async () => {
    const session = await ready(); // job-1: the saved week
    let release: () => void = () => {};
    watchJobMock.mockImplementationOnce(async function* () {
      await new Promise<void>((r) => { release = r; });
      yield { status: 'cancelled' };
    });
    session.stage({ kind: 'patchBlock', address: { slot_index: 1 }, fields: { size: 5 } });
    await vi.waitFor(() => expect(startPreviewJobMock).toHaveBeenCalledTimes(2));
    session.stage(sizeEdit);
    expect(cancelJobMock).toHaveBeenCalledWith('job-2');
    release();
    await vi.waitFor(() => expect(get(session).stagedTrace).toMatchObject({ size1: 7 }));
    session.destroy();
  });

  it("an edit made while the saved week's preview runs waits for it instead of cancelling it", async () => {
    let release: () => void = () => {};
    const original = watchJobMock.getMockImplementation()!;
    watchJobMock.mockImplementationOnce(async function* (id: string) {
      await new Promise<void>((r) => { release = r; });
      yield* original(id);
    });
    const session = createStagedEditSession('ts', { debounceMs: 0, previewOnlyWhenEdited: true });
    await session.load();
    await vi.waitFor(() => expect(startPreviewJobMock).toHaveBeenCalledTimes(1)); // started on load
    session.stage(sizeEdit);
    await new Promise((r) => setTimeout(r, 10));
    expect(cancelJobMock).not.toHaveBeenCalled();
    expect(startPreviewJobMock).toHaveBeenCalledTimes(1); // the edit waits its turn
    release();
    await vi.waitFor(() => expect(get(session).stagedTrace).toMatchObject({ size1: 7 }));
    expect(get(session).savedTrace).toMatchObject({ size1: 2 });
    expect(startPreviewJobMock).toHaveBeenCalledTimes(2);
    session.destroy();
  });

  it("the saved week's preview is dropped when the week changes", async () => {
    let release: () => void = () => {};
    const original = watchJobMock.getMockImplementation()!;
    watchJobMock.mockImplementationOnce(async function* (id: string) {
      await new Promise<void>((r) => { release = r; });
      yield* original(id);
    });
    const session = createStagedEditSession('ts', { debounceMs: 0, previewOnlyWhenEdited: true });
    await session.load();
    await vi.waitFor(() => expect(startPreviewJobMock).toHaveBeenCalledTimes(1));
    session.setWeek(2);
    expect(cancelJobMock).toHaveBeenCalledWith('job-1');
    release();
    await vi.waitFor(() => expect(get(session).savedTrace).toMatchObject({ week: 2 }));
    session.destroy();
  });

  // ---- review fixes ------------------------------------------------------

  it('an edit queued while a save is on its way stays queued', async () => {
    const session = await ready();
    session.stage(sizeEdit);
    let land: (v: unknown) => void = () => {};
    applyBatchMock.mockImplementationOnce(() => new Promise((r) => { land = r; }));
    const saving = session.save();
    const later: Edit = { kind: 'patchBlock', address: { slot_index: 0 }, fields: { size: 3 } };
    session.stage(later);
    const saved = template();
    saved.layout.flat_slots![1].size = 7;
    land({ template: saved, sidecar: SIDECAR, updated_at: 'ts-2' });
    expect(await saving).toBe(true);
    expect(applyBatchMock).toHaveBeenCalledWith('ts', [sizeEdit], 'ts-1');
    expect(get(session).opLog).toEqual([later]);
    expect(JSON.parse(window.localStorage.getItem(stagedEditsKey('ts'))!)).toMatchObject({
      opLog: [later], updatedAtSnapshot: 'ts-2',
    });
    session.destroy();
  });

  it('reloading keeps the queue and stamps it with the new version, without asking', async () => {
    const session = await ready();
    session.stage(sizeEdit);
    getStationSidecarMock.mockImplementation(async () => loaded('ts-2'));
    await session.load();
    expect(get(session).opLog).toEqual([sizeEdit]);
    expect(get(session).recovered).toBeNull();
    expect(JSON.parse(window.localStorage.getItem(stagedEditsKey('ts'))!).updatedAtSnapshot).toBe('ts-2');
    session.destroy();
  });

  it('an edit before the station has loaded is ignored and keeps what an earlier visit left', () => {
    const left = { schemaVersion: STAGED_EDITS_SCHEMA_VERSION, opLog: [sizeEdit], updatedAtSnapshot: 'ts-1' };
    window.localStorage.setItem(stagedEditsKey('ts'), JSON.stringify(left));
    const session = createStagedEditSession('ts', { debounceMs: 0 });
    session.stage({ kind: 'patchBlock', address: { slot_index: 0 }, fields: { size: 3 } });
    expect(get(session).opLog).toEqual([]);
    expect(JSON.parse(window.localStorage.getItem(stagedEditsKey('ts'))!)).toEqual(left);
    session.destroy();
  });

  it('discard stops the preview of the discarded edits', async () => {
    const session = await ready();
    watchJobMock.mockImplementationOnce(async function* () {
      await new Promise(() => {}); // never finishes
    });
    session.stage(sizeEdit);
    await vi.waitFor(() => expect(startPreviewJobMock).toHaveBeenCalledTimes(2));
    await session.discard();
    expect(cancelJobMock).toHaveBeenCalledWith('job-2');
    session.destroy();
  });

  it('previewOnlyWhenEdited: the saved week is worked out on load; edits preview only while queued', async () => {
    const session = createStagedEditSession('ts', { debounceMs: 0, previewOnlyWhenEdited: true });
    await session.load();
    // Ready before the first edit, so that edit needs one job, not two.
    await vi.waitFor(() => expect(get(session).savedTrace).toMatchObject({ size1: 2 }));
    expect(get(session)).toMatchObject({ stagedTrace: null, previewing: false });
    expect(startPreviewJobMock).toHaveBeenCalledTimes(1);

    session.stage(sizeEdit);
    await vi.waitFor(() => expect(get(session).stagedTrace).toMatchObject({ size1: 7 }));
    expect(startPreviewJobMock).toHaveBeenCalledTimes(2);

    session.unstage([0]);
    expect(get(session).stagedTrace).toBeNull();
    // The saved week's preview is still good: the next edit reuses it.
    session.stage(sizeEdit);
    await vi.waitFor(() => expect(get(session).stagedTrace).toMatchObject({ size1: 7 }));
    expect(startPreviewJobMock).toHaveBeenCalledTimes(3);
    session.destroy();
  });
});
