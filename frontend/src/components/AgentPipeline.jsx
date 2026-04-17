import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import AgentCard from './AgentCard.jsx';
import { IconBrain } from './Icons.jsx';

// Fixed pipeline layout: three rows
/** Fixed pipeline rows used to lay out the workflow graph. */
const ROW1 = ['setup', 'normalizer', 'extracter'];
const ROW2 = ['verifier', 'generator', 'latex'];
const ROW3 = ['evaluation', 'output'];
const PIPELINE_KEYS = [...ROW1, ...ROW2, ...ROW3];
const PLACEHOLDER_BRAINS = [0, 1, 2, 3, 4, 5];
/** Static connector map used to draw the pipeline SVG paths. */
const CONNECTIONS = [
  { id: 'setup-normalizer', from: 'setup', to: 'normalizer', type: 'horizontal' },
  { id: 'normalizer-extracter', from: 'normalizer', to: 'extracter', type: 'horizontal' },
  { id: 'extracter-verifier', from: 'extracter', to: 'verifier', type: 'turn' },
  { id: 'verifier-generator', from: 'verifier', to: 'generator', type: 'horizontal' },
  { id: 'generator-latex', from: 'generator', to: 'latex', type: 'horizontal' },
  { id: 'latex-evaluation', from: 'latex', to: 'evaluation', type: 'turn' },
  { id: 'evaluation-output', from: 'evaluation', to: 'output', type: 'horizontal' },
];

/**
 * Derives connector status from the source agent state.
 *
 * @param {string} sourceStatus
 * @param {string} previousStatus
 * @returns {string}
 */
function statusForLine(sourceStatus, previousStatus) {
  if (sourceStatus === 'completed') {
    return 'completed';
  }
  if (sourceStatus === 'thinking') {
    return 'thinking';
  }
  if (sourceStatus === 'errored') {
    return previousStatus === 'completed' ? 'completed' : 'errored';
  }
  return 'inactive';
}

/**
 * Returns true once a node should be visible in the progressive reveal.
 *
 * @param {string} status
 * @returns {boolean}
 */
function isRevealedStatus(status) {
  return !['inactive', 'queued'].includes(status || 'inactive');
}

/**
 * Renders one horizontal row of pipeline nodes.
 *
 * @param {{keys: string[], agents: object, revealedNodes: Set<string>, onCardClick: Function, onNodeRef: Function}} props
 */
function AgentRow({ keys, agents, revealedNodes, onCardClick, onNodeRef }) {
  return (
    <div className="agents-row">
      {keys.map((key) => {
        const isRevealed = revealedNodes.has(key);
        return (
          <div
            key={key}
            className={`agent-node${isRevealed ? ' is-revealed' : ''}`}
            ref={(node) => onNodeRef(key, node)}
            aria-hidden={!isRevealed}
          >
            <AgentCard agentKey={key} agent={agents[key]} onClick={onCardClick} />
          </div>
        );
      })}
    </div>
  );
}

/**
 * Placeholder shown before the first workflow node starts.
 *
 * @param {{isHidden: boolean}} props
 */
function BrainPlaceholder({ isHidden }) {
  return (
    <div className={`agent-placeholder${isHidden ? ' is-hidden' : ''}`} aria-hidden={isHidden}>
      <div className="agent-placeholder-brains">
        {PLACEHOLDER_BRAINS.map((item) => (
          <IconBrain key={item} className="agent-placeholder-brain" />
        ))}
      </div>
      <p>Start a workflow to reveal the agent chain.</p>
    </div>
  );
}

/**
 * Renders the workflow graph and SVG connectors between agents.
 *
 * @param {{agents: object, onCardClick: Function}} props
 */
export default function AgentPipeline({ agents, onCardClick }) {
  const gridRef = useRef(null);
  const nodeRefs = useRef({});
  const [connector, setConnector] = useState({ width: 1, height: 1, paths: {} });
  const revealedNodes = useMemo(() => new Set(
    PIPELINE_KEYS.filter((key) => isRevealedStatus(agents?.[key]?.status))
  ), [agents]);
  const isWorkflowVisible = revealedNodes.size > 0;
  const lineStatuses = useMemo(() => (
    Object.fromEntries(
      CONNECTIONS.map((connection) => {
        const sourceStatus = agents?.[connection.from]?.status || 'inactive';
        return [connection.id, statusForLine(sourceStatus, 'inactive')];
      })
    )
  ), [agents]);

  const updateConnector = useCallback(() => {
    const grid = gridRef.current;
    if (!grid) {
      setConnector({ width: 1, height: 1, paths: {} });
      return;
    }

    const gridRect = grid.getBoundingClientRect();
    const width = Math.max(1, gridRect.width);
    const height = Math.max(1, gridRect.height);
    const paths = {};

    for (const connection of CONNECTIONS) {
      const fromNode = nodeRefs.current[connection.from];
      const toNode = nodeRefs.current[connection.to];
      if (!fromNode || !toNode) continue;

      const fromRect = fromNode.getBoundingClientRect();
      const toRect = toNode.getBoundingClientRect();

      if (connection.type === 'horizontal') {
        const startX = fromRect.right - gridRect.left;
        const startY = fromRect.top + fromRect.height / 2 - gridRect.top;
        const endX = toRect.left - gridRect.left;
        const endY = toRect.top + toRect.height / 2 - gridRect.top;
        paths[connection.id] = `M ${startX} ${startY} L ${endX} ${endY}`;
        continue;
      }

      const startX = fromRect.left + fromRect.width / 2 - gridRect.left;
      const startY = fromRect.bottom - gridRect.top;
      const endX = toRect.left + toRect.width / 2 - gridRect.left;
      const endY = toRect.top - gridRect.top;
      const bendY = startY + (endY - startY) / 2;
      paths[connection.id] = `M ${startX} ${startY} L ${startX} ${bendY} L ${endX} ${bendY} L ${endX} ${endY}`;
    }

    setConnector({ width, height, paths });
  }, []);

  const setNodeRef = useCallback((key, node) => {
    if (node) nodeRefs.current[key] = node;
    else delete nodeRefs.current[key];
  }, []);

  useEffect(() => {
    const frame = requestAnimationFrame(updateConnector);
    const onResize = () => updateConnector();

    window.addEventListener('resize', onResize);

    let observer = null;
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(() => updateConnector());
      if (gridRef.current) observer.observe(gridRef.current);
      Object.values(nodeRefs.current).forEach((node) => node && observer.observe(node));
    }

    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('resize', onResize);
      if (observer) observer.disconnect();
    };
  }, [updateConnector, agents]);

  return (
    <div className={`agents-grid${isWorkflowVisible ? ' is-active' : ''}`} ref={gridRef}>
      <BrainPlaceholder isHidden={isWorkflowVisible} />
      <svg
        className="agent-turn-connector"
        aria-hidden="true"
        viewBox={`0 0 ${connector.width} ${connector.height}`}
        preserveAspectRatio="none"
      >
        {CONNECTIONS.map(({ id }) => (
          connector.paths[id]
            ? (
              <path
                key={id}
                className="agent-connector-path"
                data-status={lineStatuses[id] || 'inactive'}
                data-visible={lineStatuses[id] !== 'inactive' ? 'true' : 'false'}
                d={connector.paths[id]}
              />
            )
            : null
        ))}
      </svg>
      <AgentRow keys={ROW1} agents={agents} revealedNodes={revealedNodes} onCardClick={onCardClick} onNodeRef={setNodeRef} />
      <AgentRow keys={ROW2} agents={agents} revealedNodes={revealedNodes} onCardClick={onCardClick} onNodeRef={setNodeRef} />
      <AgentRow keys={ROW3} agents={agents} revealedNodes={revealedNodes} onCardClick={onCardClick} onNodeRef={setNodeRef} />
    </div>
  );
}
