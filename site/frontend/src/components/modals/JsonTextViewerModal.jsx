import Modal from './Modal.jsx';
import { IconDownload } from '../Icons.jsx';
import useFileContent from '../../hooks/useFileContent.js';
import { Button, EmptyState } from '../ui/index.js';

/**
 * Light JSON syntax tokenizer for a single pretty-printed line.
 * Returns an array of {type, text} tokens. Keys are strings immediately
 * followed by a colon; everything else is a plain string value.
 *
 * @param {string} line
 * @returns {Array<{type: string, text: string}>}
 */
function tokenizeJsonLine(line) {
  const tokens = [];
  const re = /("(?:[^"\\]|\\.)*")(\s*:)?|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|\b(true|false|null)\b|([{}[\],:])|(\s+)|([^\s]+)/g;
  let match;
  while ((match = re.exec(line)) !== null) {
    if (match[1] !== undefined) {
      if (match[2] !== undefined) {
        tokens.push({ type: 'key', text: match[1] });
        tokens.push({ type: 'punct', text: match[2] });
      } else {
        tokens.push({ type: 'string', text: match[1] });
      }
    } else if (match[3] !== undefined) {
      tokens.push({ type: 'number', text: match[3] });
    } else if (match[4] !== undefined) {
      tokens.push({ type: match[4] === 'null' ? 'null' : 'boolean', text: match[4] });
    } else if (match[5] !== undefined) {
      tokens.push({ type: 'punct', text: match[5] });
    } else {
      tokens.push({ type: 'text', text: match[6] ?? match[7] ?? '' });
    }
  }
  return tokens;
}

const TOKEN_CLASS = {
  key: 'json-key',
  string: 'json-string',
  number: 'json-number',
  boolean: 'json-bool',
  null: 'json-null',
  punct: 'json-punct',
};

function CodeLine({ tokens, lineNum }) {
  return (
    <div className="tex-line">
      <span className="tex-line-num">{lineNum}</span>
      <span className="tex-line-content">
        {tokens.map((tok, i) => {
          const cls = TOKEN_CLASS[tok.type];
          return cls
            ? <span className={cls} key={i}>{tok.text}</span>
            : <span key={i}>{tok.text}</span>;
        })}
      </span>
    </div>
  );
}

/**
 * Generic viewer for any text file. JSON (and JSONL) content is pretty-printed
 * with syntax coloring; anything that does not parse is shown as plain text.
 *
 * @param {object} props
 */
export default function JsonTextViewerModal({ isOpen, onClose, fileName, url, onDownload }) {
  const { text, loading, error } = useFileContent(url, isOpen);

  let pretty = null;
  let isJson = false;
  if (text !== null && text !== undefined) {
    const trimmed = text.trim();
    if (trimmed) {
      // Try whole-document JSON first, then newline-delimited JSON (JSONL).
      try {
        pretty = JSON.stringify(JSON.parse(trimmed), null, 2);
        isJson = true;
      } catch {
        const lines = trimmed.split(/\r?\n/).filter((l) => l.trim());
        if (lines.length > 1) {
          try {
            const records = lines.map((l) => JSON.parse(l));
            pretty = records
              .map((r) => JSON.stringify(r, null, 2))
              .join('\n');
            isJson = true;
          } catch {
            /* not JSONL */
          }
        }
      }
    }
  }

  const content = isJson ? pretty : text;
  const codeLines = content ? content.split('\n') : [];

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={fileName ? fileName.split('/').pop() : 'File'}
      contentClassName="json-viewer-modal" size="full"
      footer={(
        <div className="modal-footer-actions">
          {onDownload && fileName && (
            <Button variant="secondary" onClick={() => onDownload(fileName)} icon={<IconDownload className="btn-icon" />}>
              Download
            </Button>
          )}
          <Button variant="primary" onClick={onClose}>Close</Button>
        </div>
      )}
    >
      {loading && <EmptyState variant="modal">Loading…</EmptyState>}
      {error && <EmptyState variant="modal" error>{error}</EmptyState>}
      {!loading && !error && codeLines.length > 0 && (
        <div className="tex-pre">
          {isJson
            ? codeLines.map((line, i) => (
              <CodeLine key={i} tokens={tokenizeJsonLine(line)} lineNum={i + 1} />
            ))
            : codeLines.map((line, i) => (
              <div className="tex-line" key={i}>
                <span className="tex-line-num">{i + 1}</span>
                <span className="tex-line-content">{line}</span>
              </div>
            ))}
        </div>
      )}
      {!loading && !error && codeLines.length === 0 && (
        <EmptyState variant="modal">File is empty.</EmptyState>
      )}
    </Modal>
  );
}
