import { useEffect } from 'react';
import { IconClose } from '../Icons.jsx';

/**
 * Base modal wrapper. Handles backdrop, animation, keyboard close (Escape),
 * and body scroll lock. Children render inside modal-content.
 */
export default function Modal({ isOpen, onClose, title, large = false, children, footer }) {
  // Lock body scroll and add modal-open class when open
  useEffect(() => {
    if (isOpen) {
      document.body.classList.add('modal-open');
      document.body.style.overflow = 'hidden';
    } else {
      document.body.classList.remove('modal-open');
      document.body.style.overflow = '';
    }
    return () => {
      document.body.classList.remove('modal-open');
      document.body.style.overflow = '';
    };
  }, [isOpen]);

  // Close on Escape key
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [isOpen, onClose]);

  return (
    <div className={`modal${isOpen ? ' is-visible' : ''}`} role="dialog" aria-modal="true">
      <div className="modal-backdrop" onClick={onClose} />
      <div className={`modal-content${large ? ' modal-large' : ''}`}>
        <div className="modal-header">
          <h3>{title}</h3>
          <button className="btn-icon-only" onClick={onClose} aria-label="Close">
            <IconClose />
          </button>
        </div>
        <div className="modal-body">
          {children}
        </div>
        {footer && (
          <div className="modal-footer">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}
