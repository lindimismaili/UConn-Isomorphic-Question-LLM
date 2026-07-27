import { useEffect, useMemo, useState } from 'react';
import Modal from './Modal.jsx';
import { Badge, Button, EmptyState } from '../ui/index.js';
import { fetchWithAbort } from '../../utils/api.js';

/** Load rubric JSON through the authenticated backend API. */
async function fetchRubric() {
  const res = await fetchWithAbort('/api/rubric', { cache: 'no-store' });
  if (!res.ok) throw new Error(`Server responded with ${res.status}`);
  return res.json();
}

function formatTitle(value) {
  return String(value || 'Criterion')
    .replaceAll('_', ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

/**
 * Displays the evaluation rubric in a structured, readable view.
 *
 * @param {object} props
 */
export default function RubricViewerModal({ isOpen, onClose }) {
  const [rubric, setRubric] = useState(null);
  const [error, setError] = useState('');
  const criteria = useMemo(
    () => (Array.isArray(rubric?.criteria) ? rubric.criteria : []),
    [rubric],
  );

  useEffect(() => {
    if (!isOpen) return;

    let cancelled = false;

    const load = async () => {
      try {
        const payload = await fetchRubric();
        if (!cancelled) {
          setRubric(payload);
          setError('');
        }
      } catch (err) {
        if (!cancelled) {
          setRubric(null);
          setError(err.message);
        }
      }
    };

    load();

    return () => {
      cancelled = true;
    };
  }, [isOpen]);

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Evaluator Rubric"
      large
      modalBodyClassName="modal-body-flush"
      footer={<Button variant="primary" onClick={onClose}>Close</Button>}
    >
      <div className="rubric-viewer-body">
        {error && (
          <EmptyState variant="modal" error>
            Error loading rubric: {error}
          </EmptyState>
        )}

        {!error && !rubric && (
          <EmptyState variant="modal">Loading rubric...</EmptyState>
        )}

        {!error && rubric && (
          <>
            <div className="rubric-summary">
              <div>
                <span>Model</span>
                <strong>{rubric.model || 'Unavailable'}</strong>
              </div>
              <div>
                <span>Criteria</span>
                <strong>{criteria.length}</strong>
              </div>
            </div>

            <div className="rubric-criteria-list">
              {criteria.map((criterion, index) => (
                <section className="rubric-criterion" key={criterion.name || index}>
                  <div className="rubric-criterion-header">
                    <div>
                      <Badge kind="rubric-index">{String(index + 1).padStart(2, '0')}</Badge>
                      <h4>{formatTitle(criterion.name)}</h4>
                    </div>
                    {criterion.category && (
                      <Badge kind="rubric-category">{criterion.category}</Badge>
                    )}
                  </div>

                  {criterion.question && (
                    <p className="rubric-question">{criterion.question}</p>
                  )}

                  {Array.isArray(criterion.guidance) && criterion.guidance.length > 0 && (
                    <div className="rubric-section">
                      <h5>Guidance</h5>
                      <ul>
                        {criterion.guidance.map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {Array.isArray(criterion.scoring_guide) && criterion.scoring_guide.length > 0 && (
                    <div className="rubric-section">
                      <h5>Scoring Guide</h5>
                      <ul>
                        {criterion.scoring_guide.map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </section>
              ))}
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
