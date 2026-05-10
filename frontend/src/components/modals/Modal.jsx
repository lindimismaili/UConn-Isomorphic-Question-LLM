import { useEffect, useId, useRef } from 'react';
import { createPortal } from 'react-dom';
import { IconClose } from '../Icons.jsx';

let openModalCount = 0;
let previousBodyOverflow = '';
let previousBodyPaddingRight = '';

/**
 * Prevents the background page from scrolling while a modal is open.
 */
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

/**
 * Restores page scrolling when the final modal closes.
 */
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
 * Base modal wrapper for all in-app dialogs.
 *
 * Handles backdrop clicks, Escape-to-close, scroll locking, and portal
 * rendering into `document.body`.
 *
 * @param {object} props
 * @returns {JSX.Element}
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
  const titleId = useId();
  const contentRef = useRef(null);
  const previousFocusRef = useRef(null);

  // Lock body scroll and add modal-open class when open
  useEffect(() => {
    if (!isOpen) return undefined;

    previousFocusRef.current = document.activeElement;
    lockBodyScroll();
    if (bodyClassName) {
      document.body.classList.add(bodyClassName);
    }

    return () => {
      if (bodyClassName) {
        document.body.classList.remove(bodyClassName);
      }
      unlockBodyScroll();
      if (previousFocusRef.current && typeof previousFocusRef.current.focus === 'function') {
        previousFocusRef.current.focus();
      }
    };
  }, [bodyClassName, isOpen]);

  // Close on Escape key
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e) => {
      if (e.key === 'Escape') {
        onClose();
        return;
      }
      if (e.key !== 'Tab' || !contentRef.current) return;
      const focusable = Array.from(
        contentRef.current.querySelectorAll(
          'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((element) => !element.hasAttribute('hidden'));
      if (focusable.length === 0) {
        e.preventDefault();
        contentRef.current.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [isOpen, onClose]);

  useEffect(() => {
    if (!isOpen) return;
    const target = contentRef.current?.querySelector(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    );
    window.requestAnimationFrame(() => {
      (target || contentRef.current)?.focus();
    });
  }, [isOpen]);

  const modalSizeClass = size
    ? ` modal-${size}`
    : (large ? ' modal-large' : '');

  const modal = (
    <div
      className={`modal${isOpen ? ' is-visible' : ' is-closing'}${modalClassName ? ` ${modalClassName}` : ''}`}
      role={isOpen ? 'dialog' : undefined}
      aria-modal={isOpen ? 'true' : undefined}
      aria-hidden={isOpen ? undefined : 'true'}
      aria-labelledby={titleId}
    >
      <div className="modal-backdrop" onClick={isOpen ? onClose : undefined} />
      <div
        className={`modal-content${modalSizeClass}${contentClassName ? ` ${contentClassName}` : ''}`}
        ref={contentRef}
        tabIndex={-1}
      >
        <div className="modal-header">
          <h3 id={titleId}>{title}</h3>
          <button className="btn-icon-only" onClick={onClose} aria-label="Close" type="button">
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

  return createPortal(modal, document.body);
}
