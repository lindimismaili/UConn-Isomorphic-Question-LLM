import { describe, expect, it } from 'vitest';

import {
  BATCH_EVAL_TERMINAL_STATUSES,
  buildBatchEvalState,
} from './useBatchEval.js';
import {
  CREATE_DATA_TERMINAL_STATUSES,
  buildCreateDataState,
} from './useCreateData.js';

describe('batch terminal state normalization', () => {
  it('stops Create Data polling when guardrails block an entry', () => {
    const state = buildCreateDataState({
      status: 'blocked',
      completedFiles: 1,
      blockedFiles: 2,
    });

    expect(CREATE_DATA_TERMINAL_STATUSES.has(state.status)).toBe(true);
    expect(state.isRunning).toBe(false);
    expect(state.blockedFiles).toBe(2);
    expect(state.doneFiles).toBe(3);
  });

  it('stops Eval Batch polling when PDF guardrails block an entry', () => {
    const state = buildBatchEvalState({
      status: 'blocked',
      completedFiles: 0,
      blockedFiles: 1,
    });

    expect(BATCH_EVAL_TERMINAL_STATUSES.has(state.status)).toBe(true);
    expect(state.isRunning).toBe(false);
    expect(state.blockedFiles).toBe(1);
    expect(state.doneFiles).toBe(1);
  });
});
