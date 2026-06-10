/**
 * Shared helpers for workflow output files: names, types, and download URLs.
 */

/**
 * Returns the last path segment of a workflow file name.
 *
 * @param {string} fileName
 * @returns {string}
 */
export function baseName(fileName) {
  return typeof fileName === 'string' ? fileName.split('/').pop() || fileName : '';
}

/**
 * @param {string} fileName
 * @returns {boolean}
 */
export function isPdfFile(fileName) {
  return typeof fileName === 'string' && fileName.toLowerCase().endsWith('.pdf');
}

/**
 * @param {string} fileName
 * @returns {boolean}
 */
export function isTexFile(fileName) {
  return typeof fileName === 'string' && fileName.toLowerCase().endsWith('.tex');
}

/**
 * Returns the viewer type for a given file. Every file is viewable: PDFs get
 * the PDF preview, CSVs the table viewer, .tex the source viewer, and
 * everything else the JSON/plain-text viewer.
 *
 * @param {string} fileName
 * @returns {'pdf' | 'csv' | 'tex' | 'json-text' | null}
 */
export function getViewerType(fileName) {
  if (!fileName) return null;
  if (isPdfFile(fileName)) return 'pdf';
  if (fileName.toLowerCase().endsWith('.csv')) return 'csv';
  if (isTexFile(fileName)) return 'tex';
  return 'json-text';
}

/**
 * Builds the download/view URL for a workflow output file.
 *
 * @param {string} workflowId
 * @param {string} fileName
 * @param {boolean} [inline]
 * @returns {string}
 */
export function buildWorkflowFileUrl(workflowId, fileName, inline = false) {
  if (!workflowId || !fileName) return '';
  const encodedFileName = fileName.split('/').map(encodeURIComponent).join('/');
  return `/api/download/${workflowId}/${encodedFileName}${inline ? '?inline=1' : ''}`;
}

/**
 * Merges the workflow file list with evaluation and artifact files.
 *
 * @param {object} result - Final workflow result payload.
 * @returns {Array<object>} Deduplicated download entries.
 */
export function collectResultFiles(result) {
  const mergedFiles = [];
  const seen = new Set();

  const addFile = (file) => {
    const fileName = typeof file === 'string' ? file : file?.name || file;
    if (!fileName || seen.has(fileName)) return;
    seen.add(fileName);
    mergedFiles.push(typeof file === 'string' ? { name: fileName } : { ...file, name: fileName });
  };

  (result.files || []).forEach(addFile);

  const workflowResult = result.workflowResult || {};
  const evaluation = workflowResult.evaluation || {};
  const hasEvaluationReport = Boolean(workflowResult.evaluation);
  [
    workflowResult.latex_file,
    workflowResult.pdf_file,
    evaluation.output_items_file,
    evaluation.dataset_file,
    evaluation.spreadsheet_file,
    evaluation.rubric_file,
    hasEvaluationReport ? 'evaluation_outputs/evaluation_report.json' : '',
  ].forEach(addFile);

  return mergedFiles;
}
