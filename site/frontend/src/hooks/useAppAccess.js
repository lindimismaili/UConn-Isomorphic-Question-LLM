import { useCallback, useEffect, useState } from 'react';

import {
  APP_ACCESS_TOKEN_HEADER,
  APP_ACCESS_UNAUTHORIZED_EVENT,
  clearAccessToken,
  fetchWithAbort,
  getAccessToken,
  readErrorDetail,
  requestJson,
  setAccessToken,
} from '../utils/api.js';

const INITIAL_STATE = {
  loading: true,
  verifying: false,
  config: null,
  readiness: null,
  accessError: '',
  readinessError: '',
};

function normalizeConfig(payload) {
  return {
    authRequired: Boolean(payload?.authRequired),
    maxInstructionChars: Number(payload?.maxInstructionChars || 20000),
    donationUrl:
      typeof payload?.donationUrl === 'string'
        ? payload.donationUrl.trim()
        : typeof payload?.paypalDonateUrl === 'string'
          ? payload.paypalDonateUrl.trim()
          : '',
    evaluationPolicy: {
      maxItems: Number(payload?.evaluationPolicy?.maxItems || 100),
      graderCount: Number(payload?.evaluationPolicy?.graderCount || 8),
      graderMaxCompletionTokens: Number(
        payload?.evaluationPolicy?.graderMaxCompletionTokens || 4096
      ),
    },
    createDataPolicy: {
      maxEntries: Number(payload?.createDataPolicy?.maxEntries || 5),
    },
  };
}

function normalizeReadiness(payload) {
  if (payload?.authenticated !== true && payload?.authenticated !== false) {
    throw new Error('The server returned an invalid readiness response.');
  }
  return {
    authenticated: payload.authenticated,
    openaiApiKeyConfigured: Boolean(payload?.openaiApiKeyConfigured),
  };
}

async function requestReadiness({ signal, fallbackError, ...options } = {}) {
  const response = await fetchWithAbort('/api/readiness', options, signal);
  if (!response.ok) {
    throw Object.assign(
      new Error(
        await readErrorDetail(
          response,
          fallbackError || `Readiness request failed (${response.status})`,
        ),
      ),
      { status: response.status },
    );
  }
  return normalizeReadiness(await response.json());
}

/**
 * Loads the public browser configuration, verifies the tab-scoped application
 * access token when required, and exposes boolean-only server readiness.
 *
 * The OpenAI API key is never requested or stored by this hook.
 */
export function useAppAccess() {
  const [state, setState] = useState(INITIAL_STATE);

  const load = useCallback(async () => {
    setState((previous) => ({
      ...previous,
      loading: true,
      accessError: '',
      readinessError: '',
    }));

    try {
      const config = normalizeConfig(
        await requestJson('/api/public-config', {
          cache: 'no-store',
          fallbackError: 'Unable to load the server access configuration.',
        }),
      );

      if (config.authRequired && !getAccessToken()) {
        setState({
          ...INITIAL_STATE,
          loading: false,
          config,
          accessError: '',
        });
        return;
      }

      try {
        const readiness = await requestReadiness({
          cache: 'no-store',
          fallbackError: 'Unable to verify server readiness.',
        });
        if (config.authRequired && !readiness.authenticated) {
          clearAccessToken();
          setState({
            ...INITIAL_STATE,
            loading: false,
            config,
            accessError: 'The saved application access token is no longer valid.',
          });
          return;
        }
        setState({
          ...INITIAL_STATE,
          loading: false,
          config,
          readiness,
        });
      } catch (error) {
        const accessRejected = config.authRequired && error?.status === 401;
        if (accessRejected) clearAccessToken();
        setState({
          ...INITIAL_STATE,
          loading: false,
          config,
          accessError: config.authRequired
            ? accessRejected
              ? 'The saved application access token is no longer valid.'
              : 'The saved application access token could not be verified.'
            : '',
          readinessError: config.authRequired ? '' : error.message,
        });
      }
    } catch (error) {
      setState({
        ...INITIAL_STATE,
        loading: false,
        accessError: error.message,
      });
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const handleUnauthorized = () => {
      clearAccessToken();
      setState((previous) => {
        if (!previous.config?.authRequired) return previous;
        return {
          ...previous,
          loading: false,
          verifying: false,
          readiness: null,
          accessError: 'Your application access session expired. Enter the access token again.',
          readinessError: '',
        };
      });
    };
    globalThis.addEventListener?.(APP_ACCESS_UNAUTHORIZED_EVENT, handleUnauthorized);
    return () => {
      globalThis.removeEventListener?.(APP_ACCESS_UNAUTHORIZED_EVENT, handleUnauthorized);
    };
  }, []);

  const verify = useCallback(async (candidateToken) => {
    const token = typeof candidateToken === 'string' ? candidateToken.trim() : '';
    if (!token) {
      setState((previous) => ({
        ...previous,
        accessError: 'Enter the application access token.',
      }));
      return false;
    }

    setState((previous) => ({
      ...previous,
      verifying: true,
      accessError: '',
      readinessError: '',
    }));

    try {
      const readiness = await requestReadiness({
        cache: 'no-store',
        headers: { [APP_ACCESS_TOKEN_HEADER]: token },
        fallbackError: 'The application access token was not accepted.',
      });
      if (!readiness.authenticated) {
        clearAccessToken();
        setState((previous) => ({
          ...previous,
          loading: false,
          verifying: false,
          readiness: null,
          accessError: 'The application access token was not accepted.',
        }));
        return false;
      }
      setAccessToken(token);
      setState((previous) => ({
        ...previous,
        loading: false,
        verifying: false,
        readiness,
        accessError: '',
        readinessError: '',
      }));
      return true;
    } catch (error) {
      if (error?.status === 401) clearAccessToken();
      setState((previous) => ({
        ...previous,
        loading: false,
        verifying: false,
        readiness: null,
        accessError:
          error?.status === 401
            ? 'The application access token was not accepted.'
            : error.message || 'The application access token could not be verified.',
      }));
      return false;
    }
  }, []);

  const accessGranted = Boolean(
    state.config &&
    (
      !state.config.authRequired ||
      state.readiness?.authenticated
    ),
  );
  const workflowReady = Boolean(
    accessGranted &&
    state.readiness?.openaiApiKeyConfigured &&
    !state.readinessError,
  );

  return {
    ...state,
    authRequired: Boolean(state.config?.authRequired),
    hasSavedAccessToken: Boolean(getAccessToken()),
    donationUrl: state.config?.donationUrl || '',
    accessGranted,
    workflowReady,
    verify,
    retry: load,
  };
}
