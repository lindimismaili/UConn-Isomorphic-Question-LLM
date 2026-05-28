import { useEffect, useId, useRef } from 'react';
import { createPortal } from 'react-dom';
import { IconClose } from '../Icons.jsx';

let openModalCount = 0;
let lockedScrollX = 0;
let lockedScrollY = 0;

const SCROLL_KEYS = new Set([
  'ArrowUp',
  'ArrowDown',
  'ArrowLeft',
  'ArrowRight',
  'PageUp',
  'PageDown',
  'Home',
  'End',
  ' ',
  'Spacebar',
]);

/**
 * Returns true if the event target lives inside a scrollable element that
 * the user should still be able to scroll while a modal is open (e.g. the
 * modal body when its content overflows).
 *
 * @param {EventTarget | null} target
 * @returns {boolean}
 */
function isInsideScrollableModalArea(target) {
  let el = target instanceof Element ? target : null;
  while (el && el !== document.body) {
    const style = window.getComputedStyle(el);
    const overflowY = style.overflowY;
    if ((overflowY === 'auto' || overflowY === 'scroll') && el.scrollHeight > el.clientHeight) {
      return true;
    }
    el = el.parentElement;
  }
  return false;
}

function handleScroll() {
  if (window.scrollX !== lockedScrollX || window.scrollY !== lockedScrollY) {
    window.scrollTo(lockedScrollX, lockedScrollY);
  }
}

function handleWheelOrTouch(event) {
  if (isInsideScrollableModalArea(event.target)) return;
  if (event.cancelable) event.preventDefault();
}

function handleKeyDown(event) {
  if (!SCROLL_KEYS.has(event.key)) return;
  const target = event.target;
  if (
    target instanceof HTMLElement &&
    (target.tagName === 'INPUT' ||
      target.tagName === 'TEXTAREA' ||
      target.tagName === 'SELECT' ||
      target.isContentEditable)
  ) {
    return;
  }
  if (isInsideScrollableModalArea(target)) return;
  if (event.cancelable) event.preventDefault();
}

/**
 * Prevents the background page from scrolling while a modal is open without
 * hiding the page scrollbar. Page scroll position is pinned via event
 * interception; scrolling inside an overflowing modal region is still allowed.
 */
function lockBodyScroll() {
  if (openModalCount === 0) {
    lockedScrollX = window.scrollX;
    lockedScrollY = window.scrollY;
    document.body.classList.add('modal-open');
    window.addEventListener('scroll', handleScroll, { passive: true });
    window.addEventListener('wheel', handleWheelOrTouch, { passive: false });
    window.addEventListener('touchmove', handleWheelOrTouch, { passive: false });
    window.addEventListener('keydown', handleKeyDown);
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
  window.removeEventListener('scroll', handleScroll);
  window.removeEventListener('wheel', handleWheelOrTouch);
  window.removeEventListener('touchmove', handleWheelOrTouch);
  window.removeEventListener('keydown', handleKeyDown);
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
