import { useCallback, useEffect, useRef, useState } from 'react';
import AgentCard from './AgentCard.jsx';
import { IconArrowRight } from './Icons.jsx';

// Fixed pipeline layout: two rows of 3
const ROW1 = ['setup', 'normalizer', 'extracter'];
const ROW2 = ['verifier', 'generator', 'latex'];

function ArrowRight() {
  return (
    <div className="agent-arrow">
      <IconArrowRight />
    </div>
  );
}

function AgentRow({ keys, agents, onCardClick, onNodeRef }) {
  return (
    <div className="agents-row">
      {keys.map((key, i) => (
        <div key={key} className="agent-row-item">
          {i > 0 && <ArrowRight />}
          <div className="agent-node" ref={(node) => onNodeRef(key, node)}>
            <AgentCard agentKey={key} agent={agents[key]} onClick={onCardClick} />
          </div>
        </div>
      ))}
    </div>
  );
}

export default function AgentPipeline({ agents, onCardClick }) {
  const gridRef = useRef(null);
  const nodeRefs = useRef({});
  const [connector, setConnector] = useState({ width: 1, height: 1, path: '' });

  const updateConnector = useCallback(() => {
    const grid = gridRef.current;
    const fromNode = nodeRefs.current[ROW1[ROW1.length - 1]];
    const toNode = nodeRefs.current[ROW2[0]];
    if (!grid || !fromNode || !toNode) {
      setConnector((prev) => ({ ...prev, path: '' }));
      return;
    }

    const gridRect = grid.getBoundingClientRect();
    const fromRect = fromNode.getBoundingClientRect();
    const toRect = toNode.getBoundingClientRect();

    const width = Math.max(1, gridRect.width);
    const height = Math.max(1, gridRect.height);

    const startX = fromRect.left + fromRect.width / 2 - gridRect.left;
    const startY = fromRect.bottom - gridRect.top;
    const endX = toRect.left + toRect.width / 2 - gridRect.left;
    const endY = toRect.top - gridRect.top;
    const bendY = startY + (endY - startY) / 2;

    const path = `M ${startX} ${startY} L ${startX} ${bendY} L ${endX} ${bendY} L ${endX} ${endY}`;
    setConnector({ width, height, path });
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
        {connector.path && <path d={connector.path} />}
      </svg>
      <AgentRow keys={ROW1} agents={agents} onCardClick={onCardClick} onNodeRef={setNodeRef} />
      <AgentRow keys={ROW2} agents={agents} onCardClick={onCardClick} onNodeRef={setNodeRef} />
    </div>
  );
}
