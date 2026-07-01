/* @vitest-environment jsdom */

import { act, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useTokenStream } from './useTokenStream.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let root;
let container;
let instances;

/** Minimal EventSource stand-in that records listeners and lets tests emit. */
class MockEventSource {
  constructor(url) {
    this.url = url;
    this.readyState = 0;
    this.listeners = {};
    this.closed = false;
    instances.push(this);
  }

  addEventListener(type, cb) {
    (this.listeners[type] ||= []).push(cb);
  }

  emit(type, data) {
    (this.listeners[type] || []).forEach((cb) => cb({ data }));
  }

  open() {
    this.readyState = 1;
    this.emit('open', '');
  }

  close() {
    this.closed = true;
    this.readyState = 2;
  }
}

/** Harness that exposes the latest hook result to the test via a sinkRef object. */
function Harness({ workflowId, status, sinkRef }) {
  const result = useTokenStream(workflowId, { status });
  useEffect(() => {
    sinkRef.current = result;
  });
  return null;
}

function render(element) {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root.render(element);
  });
}

beforeEach(() => {
  instances = [];
  vi.stubGlobal('EventSource', MockEventSource);
});

afterEach(() => {
  if (root) act(() => root.unmount());
  container?.remove();
  root = null;
  container = null;
  vi.unstubAllGlobals();
});

describe('useTokenStream', () => {
  it('opens an EventSource on the workflow stream when a workflow id is set', () => {
    const sinkRef = { current: null };
    render(<Harness workflowId="wf-1" status="thinking" sinkRef={sinkRef} />);
    expect(instances).toHaveLength(1);
    expect(instances[0].url).toBe('/api/workflow/wf-1/stream');
  });

  it('does not open a stream when there is no workflow id', () => {
    const sinkRef = { current: null };
    render(<Harness workflowId={null} status={undefined} sinkRef={sinkRef} />);
    expect(instances).toHaveLength(0);
    expect(sinkRef.current.getStream('normalizer')).toBe('');
  });

  it('accumulates token deltas into per-agent streamed text', () => {
    const sinkRef = { current: null };
    render(<Harness workflowId="wf-1" status="thinking" sinkRef={sinkRef} />);
    const es = instances[0];
    act(() => es.open());
    act(() => es.emit('token', JSON.stringify({ agent: 'generator', delta: 'Hello ' })));
    act(() => es.emit('token', JSON.stringify({ agent: 'generator', delta: 'world' })));
    expect(sinkRef.current.getStream('generator')).toBe('Hello world');
    expect(sinkRef.current.activeAgent).toBe('generator');
    expect(sinkRef.current.isStreaming).toBe(true);
  });

  it('ignores malformed token payloads and empty deltas', () => {
    const sinkRef = { current: null };
    render(<Harness workflowId="wf-1" status="thinking" sinkRef={sinkRef} />);
    const es = instances[0];
    act(() => es.emit('token', 'not-json'));
    act(() => es.emit('token', JSON.stringify({ agent: 'generator', delta: '' })));
    expect(sinkRef.current.getStream('generator')).toBe('');
  });

  it('closes the stream on the done event', () => {
    const sinkRef = { current: null };
    render(<Harness workflowId="wf-1" status="thinking" sinkRef={sinkRef} />);
    const es = instances[0];
    act(() => es.open());
    act(() => es.emit('done', '{}'));
    expect(es.closed).toBe(true);
    expect(sinkRef.current.isStreaming).toBe(false);
  });

  it('closes when polling reports a terminal status', () => {
    const sinkRef = { current: null };
    render(<Harness workflowId="wf-1" status="thinking" sinkRef={sinkRef} />);
    const es = instances[0];
    act(() => es.open());
    act(() => root.render(<Harness workflowId="wf-1" status="blocked" sinkRef={sinkRef} />));
    expect(es.closed).toBe(true);
  });

  it('does not open a stream if the run is already terminal', () => {
    const sinkRef = { current: null };
    render(<Harness workflowId="wf-1" status="completed" sinkRef={sinkRef} />);
    expect(instances).toHaveLength(0);
  });

  it('degrades gracefully when a connection error closes the source', () => {
    const sinkRef = { current: null };
    render(<Harness workflowId="wf-1" status="thinking" sinkRef={sinkRef} />);
    const es = instances[0];
    es.readyState = 2; // CLOSED
    act(() => es.emit('error', ''));
    expect(sinkRef.current.isStreaming).toBe(false);
  });
});
