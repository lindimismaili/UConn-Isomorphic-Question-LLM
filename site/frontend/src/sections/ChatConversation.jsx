/**
 * ChatConversation — the chat-first primary surface. Composes the conversation
 * from ui/ primitives only: a Stepper process-rail above a ChatTranscript of
 * ChatMessages, with the Composer pinned at the bottom. It projects the
 * presentation-agnostic reducer state (`state.agents` / `state.logs` /
 * `state.result` / `state.blocked`) into messages via `deriveTranscript` — the
 * reducer is untouched.
 *
 * The poller (`useWorkflow`) is the status source; the Stepper cycles a status
 * word above the thinking node and auto-scrolls it into view as the run moves.
 */

import { useEffect, useRef, useState } from 'react';
import { useFileUpload } from '../hooks/useFileUpload.js';
import { useAgentRegistry } from '../hooks/useAgentRegistry.js';
import {
  Avatar,
  Button,
  ChatMessage,
  ChatTranscript,
  Composer,
  EmptyState,
  Stepper,
  TypingIndicator,
} from '../components/ui/index.js';
import { IconInfo, IconBrain } from '../components/Icons.jsx';
import ResultsSection from '../components/ResultsSection.jsx';
import FailureSection from '../components/FailureSection.jsx';
import { deriveTranscript, deriveStepperNodes, activeAgentKey } from '../state/transcript.js';

/** Normalizes the `/api/dropdown` payload (label + prompt; value is derived). */
function normalizeDropdownItems(raw) {
  const source = Array.isArray(raw) ? raw : raw?.items;
  if (!Array.isArray(source)) return [];
  return source
    .map((item) => {
      const label = String(item?.label || '').trim();
      return {
        value: label.toLowerCase().replace(/\s+/g, '_'),
        label,
        prompt: String(item?.prompt || '').trim(),
      };
    })
    .filter((item) => item.value && item.label && item.prompt);
}

/** "Label: Prompt" line sent to the backend as the run's `theme` field. */
function themeLine(item) {
  return item ? `${item.label}: ${item.prompt}` : '';
}

/** Per-mode hint shown in the empty conversation state. */
const EMPTY_HINTS = {
  user: 'Try uploading or sending questions you want new variations of to study and practice with!',
  teacher: 'Try uploading exams or quizzes you want to create new or alternate versions of.',
  dev: 'Start a conversation — describe the questions you want, attach source files, and send.',
};

/** Which chat variant a derived message maps to. */
function messageVariant(message) {
  if (message.variant === 'user') return 'user';
  return message.kind === 'result' || message.kind === 'error' || message.kind === 'guardrail'
    ? 'assistant'
    : 'agent';
}

/** Avatar role for a message. */
function avatarRole(message) {
  if (message.variant === 'user') return 'user';
  if (message.kind === 'guardrail') return 'guardrail';
  if (message.kind === 'runtime') return 'runtime';
  return 'assistant';
}

export default function ChatConversation({
  state,
  dispatch,
  prompt,
  onPromptChange,
  isRunning,
  serviceInactive,
  onSend,
  onNodeClick,
  onShowHelp,
  mode = 'user',
}) {
  const registry = useAgentRegistry();
  const fileInputRef = useRef(null);
  const [dropdownItems, setDropdownItems] = useState([]);
  const [selectedDropdownValue, setSelectedDropdownValue] = useState('');
  const [uploadWarning, setUploadWarning] = useState(false);
  const uploadWarningTimer = useRef(null);

  const { handleFileSelect, limitReached } = useFileUpload(state.files, dispatch);

  useEffect(() => () => clearTimeout(uploadWarningTimer.current), []);

  useEffect(() => {
    let cancelled = false;
    fetch('/api/dropdown', { cache: 'no-store' })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (!cancelled) setDropdownItems(normalizeDropdownItems(data));
      })
      .catch(() => {
        if (!cancelled) setDropdownItems([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const messages = deriveTranscript(state, registry);
  const stepperNodes = deriveStepperNodes(state, registry);
  const thinkingKey = activeAgentKey(state, registry);

  // Selecting a theme never touches the composer text — the selection is sent
  // as the run's `theme` field, and the select itself glows while active.
  const handleDropdownChange = (event) => {
    setSelectedDropdownValue(event.target.value);
  };

  const handleSend = () => {
    if (isRunning) {
      onSend({ stop: true });
      return;
    }
    if (serviceInactive) {
      alert('OpenAI servers are down. This site is currently inactive.');
      return;
    }
    if (!prompt.trim() && state.files.length === 0) {
      alert('Please enter a prompt or upload files.');
      return;
    }
    const selectedItem = dropdownItems.find((item) => item.value === selectedDropdownValue);
    onSend({ prompt, files: state.files, theme: themeLine(selectedItem) });
  };

  const handleAttach = () => {
    if (limitReached) {
      setUploadWarning(true);
      clearTimeout(uploadWarningTimer.current);
      uploadWarningTimer.current = setTimeout(() => setUploadWarning(false), 1600);
      dispatch({ type: 'UPLOAD_WARNING' });
      return;
    }
    fileInputRef.current?.click();
  };

  const attachLabel = uploadWarning
    ? 'Unsupported or too many files!'
    : state.files.length > 0
      ? `Attach - ${state.files.length}`
      : 'Attach';

  const renderMessageContent = (message) => {
    if (message.kind === 'result') {
      // Nest the full ResultsSection (owns its file viewers + Results/Eval/Agents
      // modals) inside the assistant bubble — every existing feature preserved.
      return <ResultsSection result={message.result} />;
    }
    if (message.kind === 'error') {
      return <FailureSection error={message.error} outputFolder={message.errorFolder} />;
    }
    if (message.kind === 'guardrail') {
      const blocked = message.blocked || {};
      return (
        <div className="chat-blocked">
          <p className="chat-blocked-title">Request blocked by a guardrail.</p>
          <p className="chat-blocked-reason">{blocked.reason}</p>
          {blocked.category ? (
            <p className="chat-blocked-meta">Category: {blocked.category}</p>
          ) : null}
          {blocked.stage ? (
            <p className="chat-blocked-meta">Stage: {blocked.stage}</p>
          ) : null}
        </div>
      );
    }
    // Agent activity bubble.
    return (
      <div className="chat-agent-body">
        {message.description ? <p className="chat-agent-desc">{message.description}</p> : null}
        <p className="chat-agent-status">{message.text}</p>
        {message.status === 'thinking' ? (
          <TypingIndicator label={`${message.author} is working`} />
        ) : null}
      </div>
    );
  };

  const embedded = (message) => message.kind === 'result' || message.kind === 'error';

  return (
    <div className="chat-shell">
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept=".txt,.pdf,image/*,.md,.markdown,.tex,.latex,.docx,.doc"
        hidden
        onChange={handleFileSelect}
      />

      <div className="chat-rail">
        <Stepper
          nodes={stepperNodes}
          onNodeClick={onNodeClick}
          activeKey={thinkingKey || undefined}
        />
      </div>

      <ChatTranscript
        count={messages.length}
        aria-label="Conversation"
        emptyState={
          <EmptyState>
            <div className="chat-empty">
              <IconBrain className="chat-empty-icon" />
              <p>{EMPTY_HINTS[mode] || EMPTY_HINTS.user}</p>
              <Button variant="secondary" onClick={onShowHelp} icon={<IconInfo className="btn-icon" />}>
                <span>How it works</span>
              </Button>
            </div>
          </EmptyState>
        }
      >
        {messages.map((message) => (
          <ChatMessage
            key={message.id}
            variant={messageVariant(message)}
            tone={embedded(message) ? undefined : message.tone}
            className={embedded(message) ? 'chat-message-embed' : ''}
            avatar={<Avatar role={avatarRole(message)} label={message.author} />}
            author={message.author}
            timestamp={message.timestamp}
          >
            {message.variant === 'user' ? (
              <div className="chat-user-body">
                <p>{message.text}</p>
                {message.files?.length ? (
                  <p className="chat-user-files">Attached: {message.files.join(', ')}</p>
                ) : null}
              </div>
            ) : (
              renderMessageContent(message)
            )}
          </ChatMessage>
        ))}
      </ChatTranscript>

      <Composer
        value={prompt}
        onChange={onPromptChange}
        onSend={handleSend}
        onAttach={handleAttach}
        attachLabel={attachLabel}
        attachFeedback={uploadWarning ? 'error' : undefined}
        disabled={serviceInactive && !isRunning}
        sendDisabled={!isRunning && serviceInactive}
        isRunning={isRunning}
        placeholder="Describe the questions to generate… (Ctrl+Enter to send)"
        themeSelect={{
          value: selectedDropdownValue,
          onChange: handleDropdownChange,
          options: dropdownItems,
          placeholder: 'Select a theme… (none)',
          disabled: isRunning || serviceInactive || dropdownItems.length === 0,
        }}
      />
    </div>
  );
}
