import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import AgentCard from './AgentCard.jsx';

// Fixed pipeline layout: three rows
const ROW1 = ['setup', 'normalizer', 'extracter'];
const ROW2 = ['verifier', 'generator', 'latex'];
const ROW3 = ['evaluation'];
const CONNECTIONS = [
  { id: 'setup-normalizer', from: 'setup', to: 'normalizer', type: 'horizontal' },
  { id: 'normalizer-extracter', from: 'normalizer', to: 'extracter', type: 'horizontal' },
  { id: 'extracter-verifier', from: 'extracter', to: 'verifier', type: 'turn' },
  { id: 'verifier-generator', from: 'verifier', to: 'generator', type: 'horizontal' },
  { id: 'generator-latex', from: 'generator', to: 'latex', type: 'horizontal' },
  { id: 'latex-evaluation', from: 'latex', to: 'evaluation', type: 'turn' },
];

function statusForLine(sourceStatus, previousStatus) {
  // Connector turns green only after the source agent completes.
  if (sourceStatus === 'completed') {
    return 'completed';
  }
  // If an agent fails after completion, keep the connector green.
  if (sourceStatus === 'errored') {
    return previousStatus === 'completed' ? 'completed' : 'inactive';
  }
  return 'inactive';
}

function AgentRow({ keys, agents, onCardClick, onNodeRef }) {
  return (
    <div className="agents-row">
      {keys.map((key) => (
        <div key={key} className="agent-node" ref={(node) => onNodeRef(key, node)}>
          <AgentCard agentKey={key} agent={agents[key]} onClick={onCardClick} />
        </div>
      ))}
    </div>
  );
}

export default function AgentPipeline({ agents, onCardClick }) {
  const gridRef = useRef(null);
  const nodeRefs = useRef({});
  const [connector, setConnector] = useState({ width: 1, height: 1, paths: {} });
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
    <div className="agents-grid" ref={gridRef}>
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
                d={connector.paths[id]}
              />
            )
            : null
        ))}
      </svg>
      <AgentRow keys={ROW1} agents={agents} onCardClick={onCardClick} onNodeRef={setNodeRef} />
      <AgentRow keys={ROW2} agents={agents} onCardClick={onCardClick} onNodeRef={setNodeRef} />
      <AgentRow keys={ROW3} agents={agents} onCardClick={onCardClick} onNodeRef={setNodeRef} />
    </div>
  );
}
