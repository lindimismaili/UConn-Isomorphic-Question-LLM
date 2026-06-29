import Modal from './Modal.jsx';
import { IconDownload } from '../Icons.jsx';
import useFileContent from '../../hooks/useFileContent.js';
import { Button, EmptyState } from '../ui/index.js';

/** Very light LaTeX syntax tokenizer — returns an array of {type, text} tokens. */
function tokenizeTex(source) {
  const tokens = [];
  const re = /(%[^\n]*)|(\\[a-zA-Z@]+\*?|\\[{}%\\])|(\{|\})|([^\\{}\n%]+)|(\n)/g;
  let match;
  while ((match = re.exec(source)) !== null) {
    if (match[1]) tokens.push({ type: 'comment', text: match[1] });
    else if (match[2]) tokens.push({ type: 'command', text: match[2] });
    else if (match[3]) tokens.push({ type: 'brace', text: match[3] });
    else if (match[4]) tokens.push({ type: 'text', text: match[4] });
    else if (match[5]) tokens.push({ type: 'newline', text: '\n' });
  }
  return tokens;
}

function TexLine({ tokens, lineNum }) {
  return (
    <div className="tex-line">
      <span className="tex-line-num">{lineNum}</span>
      <span className="tex-line-content">
        {tokens.map((tok, i) => {
          if (tok.type === 'comment') return <span className="tex-comment" key={i}>{tok.text}</span>;
          if (tok.type === 'command') return <span className="tex-command" key={i}>{tok.text}</span>;
          if (tok.type === 'brace') return <span className="tex-brace" key={i}>{tok.text}</span>;
          return <span key={i}>{tok.text}</span>;
        })}
      </span>
    </div>
  );
}

export default function TexViewerModal({ isOpen, onClose, fileName, url, onDownload }) {
  const { text, loading, error } = useFileContent(url, isOpen);

  const lines = text
    ? text.split('\n').map((line) => tokenizeTex(line))
    : [];

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={fileName ? fileName.split('/').pop() : 'LaTeX Source'}
      contentClassName="tex-viewer-modal" size="full"
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
      {!loading && !error && lines.length > 0 && (
        <div className="tex-pre">
          {lines.map((lineTokens, i) => (
            <TexLine key={i} tokens={lineTokens} lineNum={i + 1} />
          ))}
        </div>
      )}
    </Modal>
  );
}
