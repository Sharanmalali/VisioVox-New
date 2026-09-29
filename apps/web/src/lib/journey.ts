/**
 * The customer's view of a job: one list of steps that the activity feed, the
 * checklist and the progress bar all read from, so the three can never
 * disagree about where a video is.
 *
 * The pipeline's own stage names (S0_ingest, S5_extract, ...) are an
 * engineering vocabulary. A person uploading a recording wants to know that
 * it arrived, that work has started, what is happening now, and when it is
 * ready - in that order, in plain words. Every message below corresponds to
 * an event that actually happened; nothing here advances on a timer.
 */

export type StepId = 'upload' | 'prepare' | 'separate' | 'captions' | 'ready';
export type StepState = 'pending' | 'active' | 'done' | 'failed';

export interface Step {
  readonly id: StepId;
  /** Checklist label. */
  readonly title: string;
  /** Progress-bar label while this step is under way, when it should differ. */
  readonly activeTitle?: string;
}

const STEP: Readonly<Record<StepId, Step>> = {
  upload: { id: 'upload', title: 'Upload' },
  prepare: { id: 'prepare', title: 'Prepare video' },
  separate: { id: 'separate', title: 'Separate speakers' },
  captions: { id: 'captions', title: 'Add captions' },
  ready: { id: 'ready', title: 'Ready to watch', activeTitle: 'Finishing up' },
};

/** In the order a video passes through them. */
export const STEPS: readonly Step[] = [
  STEP.upload,
  STEP.prepare,
  STEP.separate,
  STEP.captions,
  STEP.ready,
];

/** Which customer-facing step a pipeline stage belongs to. */
const STAGE_STEP: Record<string, StepId> = {
  S0_ingest: 'prepare',
  S5_extract: 'separate',
  S3_fuse: 'separate',
  S7_transcribe: 'captions',
  // Packaging is the tail of the job; to a viewer it is "almost ready".
  S9_package: 'ready',
};

/** What the feed says as each stage starts. */
export const STAGE_MESSAGE: Record<string, string> = {
  S0_ingest: 'Preparing your video — checking the file and extracting the audio.',
  S5_extract:
    'Separating the speakers — following each face and isolating its voice. This is the longest step.',
  S3_fuse: 'Matching each voice to the right person.',
  S7_transcribe: 'Adding captions — transcribing what each person says.',
  S9_package: 'Finishing up — preparing your video for playback.',
};

const SUCCEEDED = new Set(['succeeded', 'partial']);
const TERMINAL = new Set(['succeeded', 'partial', 'failed', 'cancelled']);

export interface JourneyInput {
  /** The uploader's phase, or null before an upload has started. */
  uploadPhase: string | null;
  /** 0-1 share of bytes sent. */
  uploadFraction: number;
  /** The job as last reported, or null before the server has one. */
  job: { status: string; progress: number; stage: string | null } | null;
}

export interface Journey {
  readonly states: Readonly<Record<StepId, StepState>>;
  /** The step the video is on. */
  readonly current: Step;
  /** What the progress bar says: the step, as it reads while under way. */
  readonly label: string;
  /** 0-100 across the whole journey, upload included. */
  readonly percent: number;
  readonly finished: boolean;
  readonly failed: boolean;
}

// The upload is a tenth of the bar. For most files it is seconds, and giving
// it more would make the bar race and then crawl.
const UPLOAD_SHARE = 10;

export function journey({ uploadPhase, uploadFraction, job }: JourneyInput): Journey {
  const states: Record<StepId, StepState> = {
    upload: 'pending',
    prepare: 'pending',
    separate: 'pending',
    captions: 'pending',
    ready: 'pending',
  };

  const uploadFailed = uploadPhase === 'error' || uploadPhase === 'aborted';
  const uploaded = uploadPhase === 'done';
  if (uploadPhase !== null) states.upload = uploaded ? 'done' : uploadFailed ? 'failed' : 'active';

  let percent = uploadPhase === null ? 0 : Math.round(uploadFraction * UPLOAD_SHARE);
  let current: StepId = 'upload';
  const finished = job !== null && TERMINAL.has(job.status);
  const failed = uploadFailed || (finished && !SUCCEEDED.has(job.status));

  if (uploaded) {
    percent = UPLOAD_SHARE;
    current = 'prepare';
  }

  if (job !== null) {
    percent = UPLOAD_SHARE + Math.round((Math.min(100, job.progress) * (100 - UPLOAD_SHARE)) / 100);
    const order = STEPS.map((s) => s.id);
    const at: StepId = finished
      ? 'ready'
      : job.stage !== null
        ? (STAGE_STEP[job.stage] ?? 'prepare')
        : 'prepare';
    const reached = order.indexOf(at);
    for (const [i, id] of order.entries()) {
      if (id === 'upload') continue;
      if (i < reached) states[id] = 'done';
      else if (i === reached) states[id] = 'active';
    }
    current = at;

    if (finished) {
      if (SUCCEEDED.has(job.status)) {
        states.ready = 'done';
        percent = 100;
      } else {
        // Mark the step it stopped on, so the checklist says where.
        const stoppedAt = job.stage !== null ? (STAGE_STEP[job.stage] ?? 'prepare') : 'prepare';
        for (const id of order) {
          if (id === 'upload') continue;
          if (order.indexOf(id) < order.indexOf(stoppedAt)) states[id] = 'done';
          else if (id === stoppedAt) states[id] = 'failed';
          else states[id] = 'pending';
        }
        current = stoppedAt;
      }
    }
  }

  const step = STEP[current];
  const label = finished || failed ? step.title : (step.activeTitle ?? step.title);
  return { states, current: step, label, percent, finished, failed };
}

/** "1 min 12 s", "48 s" - for "ready in ..." lines. */
export function formatElapsed(ms: number): string {
  const s = Math.max(1, Math.round(ms / 1000));
  const m = Math.floor(s / 60);
  return m > 0 ? `${String(m)} min ${String(s % 60)} s` : `${String(s)} s`;
}
