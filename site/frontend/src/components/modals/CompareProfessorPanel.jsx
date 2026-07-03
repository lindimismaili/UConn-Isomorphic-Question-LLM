import { useState } from 'react';
import { Button, Banner, KeyValueTable } from '../ui/index.js';
import { requestJson } from '../../utils/api.js';

/**
 * Formats a 0..1 ratio for display, tolerating nulls from the backend.
 *
 * @param {number|null|undefined} value
 * @returns {string}
 */
function formatRatio(value) {
  if (typeof value !== 'number' || Number.isNaN(value)) return 'n/a';
  return value.toFixed(3);
}

/**
 * One-shot judge-vs-professor agreement panel for a terminal batch run.
 *
 * POSTs `/api/outputs/{label}/compare-professor` on demand and renders the
 * summary (mean Cohen's kappa, raw agreement, poor-kappa criteria). Serves both
 * create-data and eval-batch output folders — the backend route is generic.
 *
 * @param {object} props
 * @param {string} props.outputFolder - Batch output-folder label under outputs/.
 * @param {string} [props.sourceStem] - Optional source-PDF stem for category averages.
 */
export default function CompareProfessorPanel({ outputFolder, sourceStem = '' }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);

  if (!outputFolder) return null;

  const handleCompare = async () => {
    setLoading(true);
    setError('');
    try {
      const body = new FormData();
      if (sourceStem) body.append('sourceStem', sourceStem);
      const label = outputFolder.split('/').map(encodeURIComponent).join('/');
      const data = await requestJson(`/api/outputs/${label}/compare-professor`, {
        method: 'POST',
        body,
        fallbackError: 'Professor comparison failed',
      });
      setResult(data);
    } catch (err) {
      setError(err.message);
      setResult(null);
    } finally {
      setLoading(false);
    }
  };

  const summary = result?.summary || null;
  const poorKappa = summary?.criteria_with_poor_kappa || [];

  return (
    <div>
      <Button variant="secondary" onClick={handleCompare} disabled={loading}>
        {loading ? 'Comparing…' : 'Compare to Professor'}
      </Button>
      {error && <Banner detail={error}>Professor comparison failed</Banner>}
      {summary && (
        <KeyValueTable
          rows={[
            { key: 'Mean Cohen κ', value: formatRatio(summary.mean_kappa) },
            { key: 'Mean raw agreement', value: formatRatio(summary.mean_raw_agreement) },
            {
              key: 'Criteria with κ < 0.4',
              value: poorKappa.length > 0 ? poorKappa.join(', ') : '(none)',
            },
            {
              key: 'Sheets compared',
              value: (result.pdfsCompared || []).length,
            },
            { key: 'Workbook', value: result.xlsxPath || '' },
          ]}
        />
      )}
    </div>
  );
}
