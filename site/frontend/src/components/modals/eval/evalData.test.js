import { describe, expect, it } from 'vitest';
import { normalizeEvalRecord } from './evalData.js';

describe('normalizeEvalRecord', () => {
  it('produces exactly one evaluation per criterion when grades/passes carry UUID suffixes', () => {
    // Backend keys grades/passes by a UUID-suffixed criterion name, while the
    // sampled result uses the bare name. Both must collapse to one criterion,
    // otherwise the table renders a duplicate, score-less (grey) chip.
    const uuidKey = 'distractor_logic-12345678-1234-1234-1234-123456789abc';
    const record = {
      datasource_item: {
        source_id: 'q1',
        source_question_text: 'in',
        generated_question_text: 'out',
      },
      grades: { [uuidKey]: 90 },
      passes: { [uuidKey]: true },
      results: [
        {
          name: 'distractor_logic',
          score: 90,
          passed: true,
          sample: { model: 'gpt', output: [{ role: 'assistant', content: '{"steps":[]}' }] },
        },
      ],
    };

    const row = normalizeEvalRecord(record, 0, new Map(), 70);

    expect(row.evaluations).toHaveLength(1);
    expect(row.evaluations[0].label).toBe('Distractor Logic');
    expect(row.evaluations[0].score).toBe(90);
    expect(row.evaluations[0].passed).toBe(true);
  });
});
