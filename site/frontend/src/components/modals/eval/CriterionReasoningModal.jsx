import Modal from '../Modal.jsx';
import { EmptyState, ScoreChip } from '../../ui/index.js';

/**
 * Drill-down modal showing a grader's internal reasoning steps for one criterion.
 *
 * @param {{ isOpen: boolean, criterion: object|null, onClose: Function }} props
 */
export default function CriterionReasoningModal({ isOpen, criterion, onClose }) {
  const label = criterion?.label || 'Criterion';
  const passed = criterion?.passed;
  const score = criterion?.score;
  const steps = Array.isArray(criterion?.steps) ? criterion.steps : [];
  const graderModel = criterion?.graderModel || '';

  const statusTone = passed === true ? 'pass' : passed === false ? 'fail' : 'neutral';
  const statusLabel = passed === true ? 'Pass' : passed === false ? 'Fail' : 'N/A';

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={label}
      contentClassName="criterion-reasoning-modal" size="full"
    >
      <div className="reasoning-header-row">
        <ScoreChip tone={statusTone} className="reasoning-status-chip">
          <span>{statusLabel}</span>
          {score !== null && score !== undefined && (
            <strong>{typeof score === 'number' ? score.toFixed(1) : score}</strong>
          )}
        </ScoreChip>
        {graderModel && (
          <span className="reasoning-grader-model">{graderModel}</span>
        )}
      </div>

      {steps.length === 0 ? (
        <EmptyState variant="modal">No reasoning available for this criterion.</EmptyState>
      ) : (
        <div className="reasoning-steps">
          {steps.map((step, i) => {
            const conclusion = step.conclusion?.toLowerCase();
            const conclusionClass =
              conclusion === 'pass'
                ? 'is-pass'
                : conclusion === 'fail'
                  ? 'is-fail'
                  : 'is-neutral';
            return (
              <div className="reasoning-step" key={i}>
                {step.conclusion && (
                  <span className={`reasoning-conclusion ${conclusionClass}`}>
                    {step.conclusion}
                  </span>
                )}
                {step.description && (
                  <p className="reasoning-description">{step.description}</p>
                )}
              </div>
            );
          })}
        </div>
      )}
    </Modal>
  );
}
