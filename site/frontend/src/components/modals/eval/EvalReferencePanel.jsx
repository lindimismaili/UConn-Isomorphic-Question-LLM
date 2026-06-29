import { IconDownload, IconFile } from '../../Icons.jsx';
import { Badge, Button } from '../../ui/index.js';
import { formatFileKind } from './evalData.js';

/**
 * One side panel listing related evaluation files.
 *
 * When `onDownload` is provided each row gets a Download button; otherwise the
 * file `kind` is shown as a chip (used for the read-only Run Inputs panel).
 *
 * @param {object} props
 * @param {string} props.title
 * @param {Array<object>} props.files - Normalized file references.
 * @param {string} props.emptyText
 * @param {(name: string) => void} [props.onDownload]
 */
export default function EvalReferencePanel({ title, files, emptyText, onDownload }) {
  return (
    <section className="eval-reference-panel">
      <div className="eval-reference-header">
        <h4>{title}</h4>
        <span>{files.length}</span>
      </div>
      {files.length === 0 ? (
        <div className="eval-reference-empty">{emptyText}</div>
      ) : (
        <div className="eval-reference-list">
          {files.map((file, index) => (
            <div
              className="eval-reference-row"
              key={`${file.displayPath || file.name || file.label || 'file'}-${index}`}
            >
              <div className="eval-reference-meta">
                <div className="eval-reference-title">
                  <IconFile className="eval-reference-icon" />
                  <span>{file.label}</span>
                </div>
                <div className="eval-reference-path">{file.displayPath || file.name}</div>
              </div>
              {onDownload ? (
                <Button
                  variant="secondary"
                  className="eval-reference-download"
                  disabled={!file.name}
                  onClick={() => file.name && onDownload(file.name)}
                  icon={<IconDownload className="btn-icon" />}
                >
                  Download
                </Button>
              ) : (
                file.kind && (
                  <Badge kind="reference-kind" tone={file.kind}>
                    {formatFileKind(file.kind)}
                  </Badge>
                )
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
