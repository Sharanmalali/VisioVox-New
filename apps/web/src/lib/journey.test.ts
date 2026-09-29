import { describe, expect, it } from 'vitest';

import { formatElapsed, journey } from './journey';

const job = (status: string, progress: number, stage: string | null = null) => ({
  status,
  progress,
  stage,
});

describe('journey', () => {
  it('is all pending before anything starts', () => {
    const j = journey({ uploadPhase: null, uploadFraction: 0, job: null });
    expect(Object.values(j.states).every((s) => s === 'pending')).toBe(true);
    expect(j.percent).toBe(0);
  });

  it('gives the upload the first tenth of the bar', () => {
    const j = journey({ uploadPhase: 'uploading', uploadFraction: 0.5, job: null });
    expect(j.states.upload).toBe('active');
    expect(j.current.title).toBe('Upload');
    expect(j.percent).toBe(5);
  });

  it('moves on to preparing once the upload lands, before the server reports', () => {
    const j = journey({ uploadPhase: 'done', uploadFraction: 1, job: null });
    expect(j.states.upload).toBe('done');
    expect(j.current.title).toBe('Prepare video');
    expect(j.percent).toBe(10);
  });

  it('names the step the server is on, and the bar agrees with it', () => {
    const j = journey({
      uploadPhase: 'done',
      uploadFraction: 1,
      job: job('running', 6, 'S5_extract'),
    });
    expect(j.states).toEqual({
      upload: 'done',
      prepare: 'done',
      separate: 'active',
      captions: 'pending',
      ready: 'pending',
    });
    expect(j.current.title).toBe('Separate speakers');
    expect(j.percent).toBe(15);
  });

  it('shows captions as their own step', () => {
    const j = journey({
      uploadPhase: 'done',
      uploadFraction: 1,
      job: job('running', 78, 'S7_transcribe'),
    });
    expect(j.states.separate).toBe('done');
    expect(j.states.captions).toBe('active');
  });

  it('never reads 100% until the video is actually ready', () => {
    const packing = journey({
      uploadPhase: 'done',
      uploadFraction: 1,
      job: job('running', 93, 'S9_package'),
    });
    expect(packing.percent).toBeLessThan(100);
    expect(packing.finished).toBe(false);
    expect(packing.label).toBe('Finishing up');

    const done = journey({
      uploadPhase: 'done',
      uploadFraction: 1,
      job: job('succeeded', 100, 'S9_package'),
    });
    expect(done.percent).toBe(100);
    expect(done.label).toBe('Ready to watch');
    expect(done.finished).toBe(true);
    expect(Object.values(done.states).every((s) => s === 'done')).toBe(true);
  });

  it('marks the step a failed job stopped on, not the ones after it', () => {
    const j = journey({
      uploadPhase: 'done',
      uploadFraction: 1,
      job: job('failed', 6, 'S5_extract'),
    });
    expect(j.failed).toBe(true);
    expect(j.states.prepare).toBe('done');
    expect(j.states.separate).toBe('failed');
    expect(j.states.captions).toBe('pending');
    expect(j.current.title).toBe('Separate speakers');
  });

  it('marks a failed upload', () => {
    const j = journey({ uploadPhase: 'error', uploadFraction: 0.3, job: null });
    expect(j.failed).toBe(true);
    expect(j.states.upload).toBe('failed');
  });
});

describe('formatElapsed', () => {
  it('reads like a person would say it', () => {
    expect(formatElapsed(48_000)).toBe('48 s');
    expect(formatElapsed(72_000)).toBe('1 min 12 s');
  });
});
