import { useEffect } from 'react';
import { IconClose } from '../Icons.jsx';

let openModalCount = 0;
let previousBodyOverflow = '';
let previousBodyPaddingRight = '';

function lockBodyScroll() {
  if (openModalCount === 0) {
    previousBodyOverflow = document.body.style.overflow;
    previousBodyPaddingRight = document.body.style.paddingRight;

    const scrollbarWidth = Math.max(
      0,
      window.innerWidth - document.documentElement.clientWidth,
    );

    document.body.classList.add('modal-open');
    document.body.style.overflow = 'hidden';
    if (scrollbarWidth > 0) {
      document.body.style.paddingRight = `${scrollbarWidth}px`;
    }
  }

  openModalCount += 1;
}

function unlockBodyScroll() {
  openModalCount = Math.max(0, openModalCount - 1);
  if (openModalCount > 0) return;

  document.body.classList.remove('modal-open');
  document.body.style.overflow = previousBodyOverflow;
  document.body.style.paddingRight = previousBodyPaddingRight;
  previousBodyOverflow = '';
  previousBodyPaddingRight = '';
}

/**
 * Base modal wrapper. Handles backdrop, animation, keyboard close (Escape),
 * and body scroll lock. Children render inside modal-content.
 */
export default function Modal({
  isOpen,
  onClose,
  title,
  large = false,
  size,
  modalClassName = '',
  bodyClassName = '',
  contentClassName = '',
  modalBodyClassName = '',
  children,
  footer,
}) {
  // Lock body scroll and add modal-open class when open
  useEffect(() => {
    if (!isOpen) return undefined;

    lockBodyScroll();
    if (bodyClassName) {
      document.body.classList.add(bodyClassName);
    }

    return () => {
      if (bodyClassName) {
        document.body.classList.remove(bodyClassName);
      }
      unlockBodyScroll();
    };
  }, [bodyClassName, isOpen]);

  // Close on Escape key
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [isOpen, onClose]);

  const modalSizeClass = size
    ? ` modal-${size}`
    : (large ? ' modal-large' : '');

  return (
    <div
      className={`modal${isOpen ? ' is-visible' : ''}${modalClassName ? ` ${modalClassName}` : ''}`}
      role="dialog"
      aria-modal="true"
    >
      <div className="modal-backdrop" onClick={onClose} />
      <div className={`modal-content${modalSizeClass}${contentClassName ? ` ${contentClassName}` : ''}`}>
        <div className="modal-header">
          <h3>{title}</h3>
          <button className="btn-icon-only" onClick={onClose} aria-label="Close">
            <IconClose />
          </button>
        </div>
        <div className={`modal-body${modalBodyClassName ? ` ${modalBodyClassName}` : ''}`}>
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
