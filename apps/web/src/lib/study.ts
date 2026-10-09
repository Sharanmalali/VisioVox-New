/**
 * The user study's counterbalancing and scoring (docs/user-study).
 */

export type Condition = 'visiovox' | 'baseline';
export type RecordingKey = 'A' | 'B';

export interface Step {
  condition: Condition;
  recording: RecordingKey;
}

const PLANS: readonly (readonly [Step, Step])[] = [
  [
    { condition: 'visiovox', recording: 'A' },
    { condition: 'baseline', recording: 'B' },
  ],
  [
    { condition: 'baseline', recording: 'A' },
    { condition: 'visiovox', recording: 'B' },
  ],
  [
    { condition: 'visiovox', recording: 'B' },
    { condition: 'baseline', recording: 'A' },
  ],
  [
    { condition: 'baseline', recording: 'B' },
    { condition: 'visiovox', recording: 'A' },
  ],
];

/** P01 -> 1. Codes without a number all take the first arrangement. */
export function participantNumber(code: string): number {
  const m = /(\d+)/.exec(code);
  return m === null ? 1 : Math.max(1, Number(m[1]));
}

/**
 * The 4-way Latin square: which condition comes first and which recording it
 * gets. Over every four consecutive participants each condition meets each
 * recording in each position exactly once.
 */
export function arrangement(n: number): readonly [Step, Step] {
  const plan = PLANS[(Math.max(1, n) - 1) % PLANS.length];
  if (plan === undefined) throw new Error('unreachable');
  return plan;
}

/** SUS score, 0-100 (Brooke, 1996): odd items score r-1, even items 5-r, sum x 2.5. */
export function susScore(answers: readonly number[]): number {
  return answers.reduce((sum, r, i) => sum + (i % 2 === 0 ? r - 1 : 5 - r), 0) * 2.5;
}
