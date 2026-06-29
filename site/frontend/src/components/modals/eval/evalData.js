/**
 * Pure data helpers for the Evaluator Results modal: parsing the JSONL export,
 * normalizing criterion/file metadata, and shaping records into table rows.
 * No React here — kept separate so the modal components stay presentational.
 */

/** Strips backend UUID suffixes that are attached to rubric keys. */
const UUID_SUFFIX_RE = /-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
/** Default pass threshold used when the report does not specify one. */
export const DEFAULT_PASSING_THRESHOLD = 70;
/** Shared empty array to keep memoized fallbacks stable. */
export const EMPTY_ITEMS = [];

/**
 * Parses newline-delimited JSON records used by evaluation item exports.
 *
 * @param {string} text
 * @returns {Array<object>}
 */
export function parseJsonl(text) {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => JSON.parse(line));
}

/**
 * Strips UUID suffixes from evaluation criterion keys for display.
 *
 * @param {string} key
 * @returns {string}
 */
function normalizeCriterionName(key) {
  return String(key || '').replace(UUID_SUFFIX_RE, '');
}

/**
 * Resolves a display label for one evaluation criterion.
 *
 * @param {string} key
 * @param {Map<string, object>} rubricByName
 * @returns {string}
 */
function formatCriterionLabel(key, rubricByName) {
  const name = normalizeCriterionName(key);
  const criterion = rubricByName.get(name);
  const label = criterion?.name || name || 'criterion';
  return label
    .replaceAll('_', ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

/**
 * Formats numeric scores for the summary table and per-item chips.
 *
 * @param {number} value
 * @returns {string}
 */
export function formatScore(value) {
  if (typeof value !== 'number' || Number.isNaN(value)) return 'n/a';
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

/**
 * Returns the last path segment for a file-like string.
 *
 * @param {string} value
 * @returns {string}
 */
function baseName(value) {
  if (typeof value !== 'string' || !value) return '';
  return value.split('/').pop() || value;
}

/**
 * Coerces a maybe-object field into a plain object.
 *
 * @param {unknown} value
 * @returns {object}
 */
function asObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
}

/**
 * Normalizes a backend file reference for the modal side panels.
 *
 * @param {unknown} value
 * @param {string} [fallbackLabel]
 * @returns {object | null}
 */
export function normalizeFileReference(value, fallbackLabel = '') {
  if (typeof value === 'string' && value) {
    return {
      name: value,
      label: fallbackLabel || baseName(value),
      kind: '',
      displayPath: value,
    };
  }

  const file = asObject(value);
  const name = typeof file.name === 'string' ? file.name : '';
  const displayPath = typeof file.display_path === 'string' ? file.display_path : name;
  if (!name && !displayPath) return null;

  return {
    name,
    label:
      (typeof file.label === 'string' && file.label) ||
      fallbackLabel ||
      baseName(name) ||
      displayPath ||
      'File',
    kind: typeof file.kind === 'string' ? file.kind : '',
    displayPath,
  };
}

/**
 * Formats a short chip label for file kind metadata.
 *
 * @param {string} kind
 * @returns {string}
 */
export function formatFileKind(kind) {
  if (!kind) return '';
  return kind.replaceAll('_', ' ').replace(/\b\w/g, (char) => char.toUpperCase());
}

/**
 * Normalizes pass/fail fields to booleans when possible.
 *
 * @param {unknown} value
 * @returns {boolean | null}
 */
function coercePassed(value) {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'string') {
    const normalized = value.toLowerCase();
    if (['pass', 'passed', 'true'].includes(normalized)) return true;
    if (['fail', 'failed', 'false'].includes(normalized)) return false;
  }
  return null;
}

/**
 * Pulls score, pass, and raw sample maps from a single eval record payload.
 *
 * @param {object} record
 * @returns {{grades: object, passes: object, samplesByKey: object}}
 */
function extractResultMaps(record) {
  // Canonicalize every key to its UUID-stripped criterion name so the same
  // criterion never appears under two key variants (which would render a
  // duplicate, score-less chip alongside the real one).
  const grades = {};
  const passes = {};
  const samplesByKey = {};

  for (const [key, value] of Object.entries(asObject(record.grades))) {
    grades[normalizeCriterionName(key)] = value;
  }
  for (const [key, value] of Object.entries(asObject(record.passes))) {
    passes[normalizeCriterionName(key)] = value;
  }

  for (const result of Array.isArray(record.results) ? record.results : []) {
    const name = result?.name || result?.criterion || result?.criterion_name;
    if (!name) continue;
    const normalizedName = normalizeCriterionName(name);
    if (grades[normalizedName] === undefined && typeof result.score === 'number') {
      grades[normalizedName] = result.score;
    }
    const passed = coercePassed(
      result.passed ?? result.pass ?? result.status ?? result.result,
    );
    if (passes[normalizedName] === undefined && passed !== null) {
      passes[normalizedName] = passed;
    }
    if (result.sample && !samplesByKey[normalizedName]) {
      samplesByKey[normalizedName] = result.sample;
    }
  }

  return { grades, passes, samplesByKey };
}

/**
 * Parses grader reasoning steps from a sample payload.
 * Sample shape: { output: [{role, content: JSON string}], model, ... }
 *
 * @param {object} sample
 * @returns {Array<{conclusion: string, description: string}>}
 */
export function parseSampleSteps(sample) {
  const outputs = Array.isArray(sample?.output) ? sample.output : [];
  const outputContent = outputs.find((o) => typeof o?.content === 'string')?.content;
  if (!outputContent) return [];

  try {
    const parsed = JSON.parse(outputContent);
    if (Array.isArray(parsed.steps)) {
      return parsed.steps.map((s) => ({
        conclusion: String(s.conclusion || ''),
        description: String(s.description || ''),
      }));
    }
  } catch {
    // non-JSON content — return as single description
    return [{ conclusion: '', description: outputContent }];
  }

  return [];
}

/**
 * Extracts a short grader note from a sampled evaluation payload.
 *
 * @param {object} sample
 * @returns {string}
 */
function extractSampleNote(sample) {
  const steps = parseSampleSteps(sample);
  if (steps.length > 0) {
    return steps
      .map((s) => [s.conclusion, s.description].filter(Boolean).join(': '))
      .join(' ');
  }
  return '';
}

/**
 * Builds a lookup table for rubric entries keyed by criterion name.
 *
 * @param {object} report
 * @returns {Map<string, object>}
 */
export function buildRubricMap(report) {
  const map = new Map();
  for (const criterion of Array.isArray(report?.rubric) ? report.rubric : []) {
    if (criterion?.name) {
      map.set(criterion.name, criterion);
    }
  }
  return map;
}

/**
 * Converts one item from the evaluation export into table-ready data.
 *
 * @param {object} record
 * @param {number} index
 * @param {Map<string, object>} rubricByName
 * @param {number} threshold
 * @returns {object}
 */
export function normalizeEvalRecord(record, index, rubricByName, threshold) {
  const item = asObject(
    record.datasource_item ||
    record.data_source_item ||
    record.item ||
    record.input ||
    record.sample?.input,
  );
  const { grades, passes, samplesByKey } = extractResultMaps(record);
  const keys = [
    ...new Set([
      ...Object.keys(grades),
      ...Object.keys(passes),
      ...Object.keys(samplesByKey),
    ]),
  ];

  const evaluations = keys.map((key) => {
    const normalizedKey = normalizeCriterionName(key);
    const sample = samplesByKey[normalizedKey] || samplesByKey[key];
    const score = typeof grades[key] === 'number' ? grades[key] : null;
    const explicitPassed = coercePassed(passes[key]);
    const passed = explicitPassed ?? (score !== null ? score >= threshold : null);

    return {
      key,
      label: formatCriterionLabel(key, rubricByName),
      score,
      passed,
      note: extractSampleNote(sample),
      steps: parseSampleSteps(sample),
      graderModel: sample?.model || '',
    };
  });

  const knownResults = evaluations.filter((evaluation) => typeof evaluation.passed === 'boolean');
  const failedCount = knownResults.filter((evaluation) => !evaluation.passed).length;
  const passedCount = knownResults.filter((evaluation) => evaluation.passed).length;
  const status = failedCount > 0 ? 'failed' : knownResults.length > 0 ? 'passed' : 'unknown';

  return {
    id: `${item.source_id || record.data_source_idx || index}-${index}`,
    sourceId: String(item.source_id || record.source_id || record.data_source_idx || index + 1),
    input: item.source_question_text || item.input || record.source_question_text || '',
    output: item.generated_question_text || item.output || record.generated_question_text || '',
    status,
    passedCount,
    failedCount,
    evaluations,
  };
}

/**
 * Builds the summary rows shown when only aggregate evaluation data exists.
 *
 * @param {object} report
 * @returns {Array<object>}
 */
export function buildAggregateRows(report) {
  const criterionScores = asObject(report?.criterion_scores);
  return Object.entries(criterionScores).map(([name, scoreData]) => ({
    name: formatCriterionLabel(name, new Map([[name, { name }]])),
    score: scoreData?.score,
    itemsScored: scoreData?.items_scored,
    category: scoreData?.category || '',
  }));
}
