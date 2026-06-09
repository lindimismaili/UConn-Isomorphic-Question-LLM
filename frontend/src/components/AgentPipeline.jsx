import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import AgentCard from './AgentCard.jsx';
import { IconBrain } from './Icons.jsx';
import { useAgentRegistry } from '../hooks/useAgentRegistry';

const PLACEHOLDER_BRAINS = [0, 1, 2, 3, 4, 5];

const CARD_WIDTH = 220;
const CARD_GAP = 72;
const SCROLL_PADDING_X = 88; // 44px per side

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
 * Renders one horizontal row of pipeline nodes plus ghost brain placeholders.
 *
 * @param {{keys: string[], agents: object, onCardClick: Function, onNodeRef: Function, ghostBrainCount: number}} props
 */
function AgentRow({ keys, agents, onCardClick, onNodeRef, ghostBrainCount }) {
  return (
    <div className="agents-row">
      {keys.map((key) => (
        <div
          key={key}
          className="agent-node is-revealed"
          ref={(node) => onNodeRef(key, node)}
        >
          <AgentCard agentKey={key} agent={agents[key]} onClick={onCardClick} />
        </div>
      ))}
      {Array.from({ length: ghostBrainCount }, (_, i) => (
        <div key={`ghost-${i}`} className="agent-node-ghost">
          <IconBrain
            className="agent-ghost-brain-icon"
            style={{ animationDelay: `${i * 200}ms` }}
          />
        </div>
      ))}
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
 * @param {{agents: object, onCardClick: Function, isRunning: boolean}} props
 */
export default function AgentPipeline({ agents, onCardClick, isRunning }) {
  const scrollRef = useRef(null);
  const trackRef = useRef(null);
  const nodeRefs = useRef({});
  const [connector, setConnector] = useState({ width: 1, height: 1, paths: {} });
  const [scrollContainerWidth, setScrollContainerWidth] = useState(0);

  const registryData = useAgentRegistry();
  const pipelineKeys = registryData?.pipelineKeys ?? [];
  const connections = useMemo(
    () => pipelineKeys.slice(0, -1).map((from, index) => ({
      id: `${from}-${pipelineKeys[index + 1]}`, from, to: pipelineKeys[index + 1],
    })),
    [pipelineKeys],
  );

  const visibleKeys = useMemo(() => (
    pipelineKeys.filter((key) => isRevealedStatus(agents?.[key]?.status))
  ), [agents, pipelineKeys]);
  const revealedNodes = useMemo(() => new Set(visibleKeys), [visibleKeys]);
  const isWorkflowVisible = revealedNodes.size > 0;
  const visibleConnections = useMemo(() => (
    Object.fromEntries(
      connections.map((connection) => {
        const sourceStatus = agents?.[connection.from]?.status || 'inactive';
        return [connection.id, isConnectorVisible(sourceStatus, revealedNodes.has(connection.to))];
      })
    )
  ), [agents, connections, revealedNodes]);

  // Calculate how many agent slots fit before horizontal scrolling begins.
  const slotsBeforeScroll = scrollContainerWidth > 0
    ? Math.max(1, Math.floor((Math.max(0, scrollContainerWidth - SCROLL_PADDING_X) + CARD_GAP) / (CARD_WIDTH + CARD_GAP)))
    : 0;

  // Show ghost brain placeholders while running if there is room in the viewport.
  const workflowIsActive = isRunning && visibleKeys.length > 0;
  const remainingPipelineSlots = pipelineKeys.length - visibleKeys.length;
  const ghostBrainCount = workflowIsActive
    ? Math.max(0, Math.min(slotsBeforeScroll - visibleKeys.length, remainingPipelineSlots))
    : 0;

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

    for (const connection of connections) {
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

  // Track scroll container width to compute ghost brain count.
  useEffect(() => {
    const scrollNode = scrollRef.current;
    if (!scrollNode) return;
    const update = () => setScrollContainerWidth(scrollNode.offsetWidth);
    update();
    if (typeof ResizeObserver === 'undefined') return;
    const obs = new ResizeObserver(update);
    obs.observe(scrollNode);
    return () => obs.disconnect();
  }, []);

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
            {connections.map(({ id }) => (
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
          <AgentRow
            keys={visibleKeys}
            agents={agents}
            onCardClick={onCardClick}
            onNodeRef={setNodeRef}
            ghostBrainCount={ghostBrainCount}
          />
        </div>
      </div>
    </div>
  );
}
