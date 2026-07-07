/**
 * Chat / conversational primitives (DESIGN.md §15). The chat-first surface is
 * composed only from these primitives — feature code never hand-rolls a bubble,
 * transcript, or composer. Each is a thin wrapper over documented classes and
 * introduces no new CSS beyond the `.chat-*` / `.composer-*` families in
 * app.css. Academic/physics styling; readable on the bright navy canvas.
 */

import { useEffect, useRef } from 'react';
import { Button, IconButton } from './Button.jsx';
import { TextArea } from './Field.jsx';
import { Dropdown } from './Dropdown.jsx';
import { IconRobot, IconUser, IconComputer, IconShield, IconSend, IconPaperclip } from '../Icons.jsx';

/** Maps a chat role to the default identity icon shown in its avatar. */
const AVATAR_ICONS = {
  user: IconUser,
  assistant: IconRobot,
  agent: IconRobot,
  runtime: IconComputer,
  guardrail: IconShield,
};

/**
 * Avatar — a round identity chip (`.chat-avatar` + `.chat-avatar-{role}`). Shows
 * a custom `icon`, else the role's default icon, else an initial from `label`.
 *
 * @param {object} props
 * @param {'user'|'assistant'|'agent'|'runtime'|'guardrail'} [props.role]
 * @param {import('react').ReactNode} [props.icon] - Override the default role icon.
 * @param {string} [props.label] - Accessible name; also seeds the initial fallback.
 * @param {string} [props.className]
 */
export function Avatar({ role = 'assistant', icon, label = '', className = '', ...rest }) {
  const classes = ['chat-avatar', `chat-avatar-${role}`, className].filter(Boolean).join(' ');
  const Fallback = AVATAR_ICONS[role];
  let content;
  if (icon) {
    content = icon;
  } else if (Fallback) {
    content = <Fallback className="chat-avatar-icon" />;
  } else if (label) {
    content = <span className="chat-avatar-initial">{label.trim().charAt(0).toUpperCase()}</span>;
  } else {
    content = <IconRobot className="chat-avatar-icon" />;
  }
  return (
    <span className={classes} role="img" aria-label={label || role} {...rest}>
      {content}
    </span>
  );
}

/**
 * ChatMessage — one conversation turn (`.chat-message` + `.chat-message-{variant}`).
 * `user` bubbles align right; `assistant`/`agent` align left. Renders an avatar
 * slot, a header (author name + timestamp), the message content, and an optional
 * actions row. `grouped` collapses the avatar/header for a run of same-author
 * turns; `tone` tints the bubble for status turns (success/error/blocked).
 *
 * @param {object} props
 * @param {'user'|'assistant'|'agent'} [props.variant]
 * @param {import('react').ReactNode} [props.avatar] - Avatar node (usually `<Avatar/>`).
 * @param {import('react').ReactNode} [props.author] - Author label in the header.
 * @param {import('react').ReactNode} [props.timestamp] - Time label in the header.
 * @param {import('react').ReactNode} [props.actions] - Trailing actions row.
 * @param {'success'|'error'|'blocked'|'thinking'} [props.tone] - Bubble status tint.
 * @param {boolean} [props.grouped] - Part of a same-author run (hide avatar/header).
 * @param {string} [props.className]
 * @param {import('react').ReactNode} props.children - Message content.
 */
export function ChatMessage({
  variant = 'assistant',
  avatar,
  author,
  timestamp,
  actions,
  tone,
  grouped = false,
  className = '',
  children,
  ...rest
}) {
  const classes = [
    'chat-message',
    `chat-message-${variant}`,
    tone ? `chat-message-${tone}` : '',
    grouped ? 'is-grouped' : '',
    className,
  ]
    .filter(Boolean)
    .join(' ');
  return (
    <div className={classes} {...rest}>
      <div className="chat-message-avatar" aria-hidden={grouped ? 'true' : undefined}>
        {grouped ? null : avatar}
      </div>
      <div className="chat-message-body">
        {!grouped && (author != null || timestamp != null) ? (
          <div className="chat-message-header">
            {author != null ? <span className="chat-message-author">{author}</span> : null}
            {timestamp != null ? <span className="chat-message-time">{timestamp}</span> : null}
          </div>
        ) : null}
        <div className="chat-message-content">{children}</div>
        {actions != null ? <div className="chat-message-actions">{actions}</div> : null}
      </div>
    </div>
  );
}

/**
 * ChatTranscript — the scrollable conversation surface (`.chat-transcript`).
 * Auto-scrolls to the newest message when `messages` grows (unless the user has
 * scrolled up), and renders an `emptyState` slot when there is nothing yet.
 *
 * @param {object} props
 * @param {import('react').ReactNode} props.children - The rendered `ChatMessage`s.
 * @param {number} [props.count] - Message count; auto-scroll fires when it grows.
 * @param {import('react').ReactNode} [props.emptyState] - Shown when `count` is 0.
 * @param {string} [props.className]
 * @param {string} [props['aria-label']]
 */
export function ChatTranscript({ children, count = 0, emptyState, className = '', ...rest }) {
  const frameRef = useRef(null);
  const pinnedRef = useRef(true);

  const handleScroll = () => {
    const el = frameRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    pinnedRef.current = distanceFromBottom < 80;
  };

  useEffect(() => {
    const el = frameRef.current;
    if (!el || !pinnedRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [count]);

  const classes = ['chat-transcript', className].filter(Boolean).join(' ');
  return (
    <div
      className={classes}
      role="log"
      aria-live="polite"
      ref={frameRef}
      onScroll={handleScroll}
      {...rest}
    >
      {count === 0 && emptyState != null ? emptyState : children}
    </div>
  );
}

/**
 * TypingIndicator — the animated "agent is working" dots (`.chat-typing`).
 * Three staggered dots; motion degrades gracefully under reduced-motion.
 *
 * @param {object} props
 * @param {import('react').ReactNode} [props.label] - Optional trailing label.
 * @param {string} [props.className]
 */
export function TypingIndicator({ label, className = '', ...rest }) {
  const classes = ['chat-typing', className].filter(Boolean).join(' ');
  return (
    <span className={classes} aria-live="polite" {...rest}>
      <span className="chat-typing-dots" aria-hidden="true">
        <span className="chat-typing-dot" />
        <span className="chat-typing-dot" />
        <span className="chat-typing-dot" />
      </span>
      {label != null ? <span className="chat-typing-label">{label}</span> : null}
    </span>
  );
}

/**
 * Composer — the pinned chat input (`.composer`). Supersedes the old
 * `.prompt-actions-row`: a multiline `TextArea` with an attach control, a theme
 * `Select`, and a primary send button. Composed from the Button/Field
 * primitives so it stays inside the templates-first contract.
 *
 * @param {object} props
 * @param {string} props.value - Textarea value.
 * @param {(value: string) => void} props.onChange
 * @param {() => void} props.onSend - Fired by the send button / Ctrl+Enter.
 * @param {() => void} [props.onAttach] - Fired by the attach button.
 * @param {string} [props.placeholder]
 * @param {boolean} [props.disabled] - Disables the textarea + controls.
 * @param {boolean} [props.sendDisabled] - Disables only the send button.
 * @param {boolean} [props.isRunning] - Renders the send button as a Stop action.
 * @param {import('react').ReactNode} [props.sendIcon] - Override the send icon.
 * @param {import('react').ReactNode} [props.sendLabel] - Override the send label.
 * @param {'primary'|'danger'} [props.sendVariant]
 * @param {'success'|'error'} [props.sendFeedback] - Transient send-button lock.
 * @param {'success'|'error'} [props.attachFeedback] - Transient attach-button lock.
 * @param {import('react').ReactNode} [props.attachLabel] - Attach button label.
 * @param {object} [props.themeSelect] - `{value, onChange(value), options, placeholder, disabled}`;
 *   `onChange` receives the selected value (not a DOM event); the trigger gets
 *   `.is-active` (primary glow) while a theme is selected.
 * @param {import('react').ReactNode} [props.children] - Extra leading controls.
 * @param {string} [props.className]
 */
export function Composer({
  value,
  onChange,
  onSend,
  onAttach,
  placeholder,
  disabled = false,
  sendDisabled = false,
  isRunning = false,
  sendIcon,
  sendLabel,
  sendVariant,
  sendFeedback,
  attachFeedback,
  attachLabel,
  themeSelect,
  children,
  className = '',
  ...rest
}) {
  const classes = ['composer', className].filter(Boolean).join(' ');

  const handleKeyDown = (event) => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      onSend?.();
    }
  };

  const resolvedSendVariant = sendVariant ?? (isRunning ? 'danger' : 'primary');
  const resolvedSendLabel = sendLabel ?? (isRunning ? 'Stop' : 'Send');

  return (
    <div className={classes} {...rest}>
      <TextArea
        className="composer-input"
        value={value}
        onChange={(event) => onChange?.(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
        disabled={disabled}
        rows={2}
      />
      <div className="composer-controls">
        <div className="composer-controls-left">
          {children}
          {themeSelect ? (
            <Dropdown
              className={`composer-theme-select${themeSelect.value ? ' is-active' : ''}`}
              size="sm"
              aria-label="Theme prompt"
              value={themeSelect.value}
              onChange={themeSelect.onChange}
              disabled={themeSelect.disabled}
              placeholder={themeSelect.placeholder}
              options={themeSelect.options}
              title={themeSelect.value ? 'Theme prompt active — sent with your run' : undefined}
            />
          ) : null}
        </div>
        <div className="composer-controls-right">
          {onAttach ? (
            attachLabel ? (
              <Button
                variant="secondary"
                className="composer-attach"
                onClick={onAttach}
                disabled={disabled}
                feedbackState={attachFeedback}
                icon={<IconPaperclip className="btn-icon" />}
              >
                <span className="btn-label">{attachLabel}</span>
              </Button>
            ) : (
              <IconButton
                className="composer-attach"
                aria-label="Attach files"
                onClick={onAttach}
                disabled={disabled}
                icon={<IconPaperclip />}
              />
            )
          ) : null}
          <Button
            variant={resolvedSendVariant}
            className="composer-send"
            onClick={onSend}
            disabled={sendDisabled}
            feedbackState={sendFeedback}
            icon={sendIcon ?? <IconSend className="btn-icon" />}
          >
            <span>{resolvedSendLabel}</span>
          </Button>
        </div>
      </div>
    </div>
  );
}
