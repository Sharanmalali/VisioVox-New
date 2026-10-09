import { describe, expect, it } from 'vitest';

import { arrangement, participantNumber, susScore } from './study';

describe('arrangement', () => {
  it('balances condition, recording and position over every four participants', () => {
    const seen = new Set<string>();
    for (let n = 5; n <= 8; n++) {
      arrangement(n).forEach((step, position) => {
        seen.add(`${step.condition}-${step.recording}-${String(position)}`);
      });
    }
    expect(seen.size).toBe(8);
  });

  it('always gives each condition one recording, never the same twice', () => {
    for (let n = 1; n <= 12; n++) {
      const [first, second] = arrangement(n);
      expect(first.condition).not.toBe(second.condition);
      expect(first.recording).not.toBe(second.recording);
    }
  });

  it('reads the number out of a participant code', () => {
    expect(participantNumber('P07')).toBe(7);
    expect(participantNumber('pilot')).toBe(1);
    expect(participantNumber('P00')).toBe(1);
  });
});

describe('susScore', () => {
  it('scores the best and worst answers 100 and 0', () => {
    expect(susScore([5, 1, 5, 1, 5, 1, 5, 1, 5, 1])).toBe(100);
    expect(susScore([1, 5, 1, 5, 1, 5, 1, 5, 1, 5])).toBe(0);
  });

  it('scores all-neutral answers 50', () => {
    expect(susScore(Array.from({ length: 10 }, () => 3))).toBe(50);
  });
});
