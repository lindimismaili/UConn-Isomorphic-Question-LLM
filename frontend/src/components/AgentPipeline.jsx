import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import AgentCard from './AgentCard.jsx';
import { IconBrain } from './Icons.jsx';

/** Fixed stage order used to lay out the workflow graph. */
const PIPELINE_KEYS = ['setup', 'normalizer', 'extracter', 'verifier', 'generator', 'latex', 'evaluation', 'output'];
const PLACEHOLDER_BRAINS = [0, 1, 2, 3, 4, 5];
/** Static connector map used to draw the pipeline SVG paths. */
const CONNECTIONS = PIPELINE_KEYS.slice(0, -1).map((from, index) => {
  const to = PIPELINE_KEYS[index + 1];
  return { id: `${from}-${to}`, from, to };
});

/**
 * Returns true once the connector should be visible.
 *
 * @param {string} sourceStatus
 * @param {boolean} destinationRevealed
 * @returns {boolean}
 */
function isConnectorVisible(sourceStatus, destinationRevealed) {
  return sourceStatus === 'completed' && destinationRevealed;
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
 * @param {{keys: string[], agents: object, onCardClick: Function, onNodeRef: Function}} props
 */
function AgentRow({ keys, agents, onCardClick, onNodeRef }) {
  return (
    <div className="agents-row">
      {keys.map((key) => {
        return (
          <div
            key={key}
            className="agent-node is-revealed"
            ref={(node) => onNodeRef(key, node)}
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
  const scrollRef = useRef(null);
  const trackRef = useRef(null);
  const nodeRefs = useRef({});
  const [connector, setConnector] = useState({ width: 1, height: 1, paths: {} });
  const visibleKeys = useMemo(() => (
    PIPELINE_KEYS.filter((key) => isRevealedStatus(agents?.[key]?.status))
  ), [agents]);
  const revealedNodes = useMemo(() => new Set(visibleKeys), [visibleKeys]);
  const isWorkflowVisible = revealedNodes.size > 0;
  const visibleConnections = useMemo(() => (
    Object.fromEntries(
      CONNECTIONS.map((connection) => {
        const sourceStatus = agents?.[connection.from]?.status || 'inactive';
        return [connection.id, isConnectorVisible(sourceStatus, revealedNodes.has(connection.to))];
      })
    )
  ), [agents, revealedNodes]);

  const updateConnector = useCallback(() => {
    const track = trackRef.current;
    if (!track) {
      setConnector({ width: 1, height: 1, paths: {} });
      return;
    }

    const trackRect = track.getBoundingClientRect();
    const width = Math.max(1, track.scrollWidth, trackRect.width);
    const height = Math.max(1, track.scrollHeight, trackRect.height);
    const paths = {};

    for (const connection of CONNECTIONS) {
      if (!visibleConnections[connection.id]) continue;
      const fromNode = nodeRefs.current[connection.from];
      const toNode = nodeRefs.current[connection.to];
      if (!fromNode || !toNode) continue;

      const fromRect = fromNode.getBoundingClientRect();
      const toRect = toNode.getBoundingClientRect();
      const startX = fromRect.right - trackRect.left;
      const centerY = fromRect.top + fromRect.height / 2 - trackRect.top;
      const endX = toRect.left - trackRect.left;
      paths[connection.id] = `M ${startX} ${centerY} L ${endX} ${centerY}`;
    }

    setConnector({ width, height, paths });
  }, [visibleConnections]);

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
      if (trackRef.current) observer.observe(trackRef.current);
      Object.values(nodeRefs.current).forEach((node) => node && observer.observe(node));
    }

    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('resize', onResize);
      if (observer) observer.disconnect();
    };
  }, [updateConnector, agents, visibleKeys]);

  useEffect(() => {
    const scrollNode = scrollRef.current;
    if (!scrollNode || visibleKeys.length === 0) return;

    window.requestAnimationFrame(() => {
      if (typeof scrollNode.scrollTo === 'function') {
        scrollNode.scrollTo({
          left: scrollNode.scrollWidth,
          behavior: 'smooth',
        });
      } else {
        scrollNode.scrollLeft = scrollNode.scrollWidth;
      }
    });
  }, [visibleKeys.length]);

  return (
    <div className={`agents-grid${isWorkflowVisible ? ' is-active' : ''}`}>
      <BrainPlaceholder isHidden={isWorkflowVisible} />
      <div className="agents-scroll" ref={scrollRef} aria-label="Agent pipeline row">
        <div className="agents-track" ref={trackRef}>
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
                    data-status="completed"
                    data-visible="true"
                    d={connector.paths[id]}
                  />
                )
                : null
            ))}
          </svg>
          <AgentRow keys={visibleKeys} agents={agents} onCardClick={onCardClick} onNodeRef={setNodeRef} />
        </div>
      </div>
    </div>
  );
}
