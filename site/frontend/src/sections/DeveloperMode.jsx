/**
 * Protected developer workspace.
 *
 * This is the former public Dev mode: a complete chat workspace next to the
 * existing Developer Tools panel. It is mounted only inside the protected
 * admin dashboard, so development controls never appear on the consumer site.
 */

import { useReducer, useState } from 'react';

import ChatConversation from './ChatConversation.jsx';
import DeveloperToolsPanel from '../components/DeveloperToolsPanel.jsx';
import FilesModal from '../components/modals/FilesModal.jsx';
import TestFilesModal from '../components/modals/TestFilesModal.jsx';
import CreateDataModal from '../components/modals/CreateDataModal.jsx';
import AgentDetailModal from '../components/modals/AgentDetailModal.jsx';
import SetupModal from '../components/modals/SetupModal.jsx';
import OutputModal from '../components/modals/OutputModal.jsx';
import HelpModal from '../components/modals/HelpModal.jsx';
import InputToAgentModal from '../components/modals/InputToAgentModal.jsx';
import BatchEvalModal from '../components/modals/BatchEvalModal.jsx';
import { getDerivedRegistry } from '../hooks/useAgentRegistry.js';
import { useAgents } from '../hooks/useAgents.js';
import { useBatchEval } from '../hooks/useBatchEval.js';
import { useCreateData } from '../hooks/useCreateData.js';
import { useWorkflow } from '../hooks/useWorkflow.js';
import { INITIAL_STATE, reducer } from '../state/reducer.js';

/**
 * The complete former Dev mode, now accessible only under `/admin`.
 *
 * @param {{serviceInactive?: boolean, maintenanceCode?: string}} props
 */
export default function DeveloperMode({ serviceInactive = false, maintenanceCode = '' }) {
  const [state, dispatch] = useReducer(reducer, INITIAL_STATE);
  const [prompt, setPrompt] = useState('');
  const [runEvaluations, setRunEvaluations] = useState(false);
  const {
    run: runWorkflow,
    runFromAgentInput,
    abort: abortWorkflow,
  } = useWorkflow(dispatch);
  const {
    state: createDataState,
    start: startCreateData,
    stop: stopCreateData,
  } = useCreateData();
  const {
    state: batchEvalState,
    start: startBatchEval,
    stop: stopBatchEval,
    loadRuns: loadBatchEvalRuns,
  } = useBatchEval();
  useAgents(dispatch);

  const startRun = (promptText, files, theme = '') => {
    if (serviceInactive) {
      dispatch({
        type: 'LOG',
        message: `${maintenanceCode || 'Service maintenance'}; workflow start blocked.`,
        level: 'error',
      });
      return;
    }
    runWorkflow(promptText, files, { runEvaluations, theme });
  };

  const handleClearInputs = () => {
    abortWorkflow();
    dispatch({ type: 'CLEAR_INPUTS' });
    dispatch({ type: 'CLEAR_AGENTS' });
    setPrompt('');
  };

  const handleComposerSend = (payload = {}) => {
    if (payload.stop) {
      handleClearInputs();
      dispatch({ type: 'CLEAR_LOGS' });
      return;
    }
    startRun(payload.prompt ?? prompt, payload.files ?? state.files, payload.theme ?? '');
  };

  const handleNodeClick = (agentKey) => {
    const kind = state.agents[agentKey]?.kind ?? getDerivedRegistry()?.registry?.[agentKey]?.kind;
    dispatch({ type: 'OPEN_MODAL', modal: kind === 'runtime' ? agentKey : 'agent', agentKey });
  };

  const handleInputToAgentRun = (agentKey, inputFile, callbacks = {}) => {
    if (serviceInactive) {
      const message = `${maintenanceCode || 'Service maintenance'}; agent-input workflow start blocked.`;
      callbacks.onError?.(message);
      dispatch({ type: 'LOG', message, level: 'error' });
      return;
    }
    runFromAgentInput(agentKey, inputFile, prompt, callbacks);
  };

  const closeModal = () => dispatch({ type: 'CLOSE_MODAL' });

  return (
    <>
      <div className="workspace">
        <div className="workspace-main">
          <ChatConversation
            state={state}
            dispatch={dispatch}
            prompt={prompt}
            onPromptChange={setPrompt}
            isRunning={state.isRunning}
            serviceInactive={serviceInactive}
            onSend={handleComposerSend}
            onNodeClick={handleNodeClick}
            onShowHelp={() => dispatch({ type: 'OPEN_MODAL', modal: 'help' })}
            mode="dev"
          />
        </div>
        <aside className="workspace-side">
          <DeveloperToolsPanel
            isRunning={state.isRunning}
            serviceInactive={serviceInactive}
            isCreateDataRunning={createDataState.isRunning || createDataState.isStarting}
            onCreateData={() => dispatch({ type: 'OPEN_MODAL', modal: 'create-data' })}
            onBatchEval={() => dispatch({ type: 'OPEN_MODAL', modal: 'batch-eval' })}
            isBatchEvalRunning={batchEvalState.isRunning || batchEvalState.isStarting}
            onInputToAgent={() => dispatch({ type: 'OPEN_MODAL', modal: 'input-to-agent' })}
            onShowTestFiles={() => dispatch({ type: 'OPEN_MODAL', modal: 'test-files' })}
            runEvaluations={runEvaluations}
            onRunEvaluationsChange={setRunEvaluations}
            logs={state.logs}
          />
        </aside>
      </div>

      <FilesModal
        isOpen={state.activeModal === 'files'}
        onClose={closeModal}
        files={state.files}
        onRemove={(id) => dispatch({ type: 'REMOVE_FILE', fileId: id })}
      />
      <TestFilesModal
        isOpen={state.activeModal === 'test-files'}
        onClose={closeModal}
        files={state.files}
        dispatch={dispatch}
        isRunning={state.isRunning}
      />
      <CreateDataModal
        isOpen={state.activeModal === 'create-data'}
        onClose={closeModal}
        createData={createDataState}
        onRun={() => startCreateData(prompt)}
        onStop={stopCreateData}
      />
      <InputToAgentModal
        isOpen={state.activeModal === 'input-to-agent'}
        onClose={closeModal}
        agents={state.agents}
        isRunning={state.isRunning}
        serviceInactive={serviceInactive}
        onRun={handleInputToAgentRun}
      />
      <AgentDetailModal
        isOpen={state.activeModal === 'agent'}
        onClose={closeModal}
        agentKey={state.activeAgentKey}
        agent={state.activeAgentKey ? state.agents[state.activeAgentKey] : null}
      />
      <SetupModal
        isOpen={state.activeModal === 'setup'}
        onClose={closeModal}
        workflowId={state.workflowId}
        runInputs={state.runInputs}
      />
      <OutputModal
        isOpen={state.activeModal === 'output'}
        onClose={closeModal}
        result={state.result}
        runInputs={state.runInputs}
      />
      <HelpModal isOpen={state.activeModal === 'help'} onClose={closeModal} />
      <BatchEvalModal
        isOpen={state.activeModal === 'batch-eval'}
        onClose={closeModal}
        batchEval={{ ...batchEvalState, start: startBatchEval, stop: stopBatchEval }}
        loadRuns={loadBatchEvalRuns}
      />
    </>
  );
}
