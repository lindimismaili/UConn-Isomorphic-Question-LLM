import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'

/**
 * Keeps linting scoped to frontend source and excludes build output.
 */

// Every ui/ primitive base class (DESIGN.md §14). Feature components must
// compose from src/components/ui/ instead of emitting these directly. Layout
// wrappers (`*-shell`, `viewer-tab-content`, `viewer-pill*`, chat-shell/
// chat-rail/chat-empty) are intentionally not banned.
const BANNED_PRIMITIVE_CLASSES =
  '(^|\\s)(btn|btn-primary|btn-secondary|btn-ghost|btn-danger|btn-icon-only|card|' +
  'developer-toggle|prompt-input|prompt-dropdown-select|eval-status-chip|eval-score-chip|' +
  'create-data-pill|create-data-step-chip|eval-source-id|eval-reference-kind|rubric-index|' +
  'rubric-category|chip|status-indicator|log-item|log-list|log-empty-state|modal-empty-state|' +
  'viewer-tab|viewer-tab-bar|eval-table|file-viewer-table|viewer-kv-table|chat-avatar|' +
  'chat-message|chat-transcript|chat-typing|composer|stepper)(\\s|$)'

const PRIMITIVE_CLASS_MESSAGE =
  'Do not hardcode ui/ primitive class strings in feature components. Compose from ' +
  'src/components/ui/ primitives instead (DESIGN.md §14). Premade sub-variant classes ' +
  "may be passed via a primitive's className prop."

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{js,jsx}'],
    extends: [
      js.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    rules: {
      'no-unused-vars': ['error', { varsIgnorePattern: '^[A-Z_]' }],
    },
  },
  {
    // Templates-first enforcement (DESIGN.md §14): feature components must compose
    // from the ui/ primitives, not raw control/card/button class strings. The
    // primitives themselves (components/ui/**) legitimately emit these classes.
    files: ['src/components/**/*.{js,jsx}'],
    ignores: ['src/components/ui/**'],
    rules: {
      'no-restricted-syntax': [
        'error',
        {
          // Static + conditional/&&/?: string classNames.
          selector: `JSXAttribute[name.name='className'] Literal[value=/${BANNED_PRIMITIVE_CLASSES}/]`,
          message: PRIMITIVE_CLASS_MESSAGE,
        },
        {
          // Template-literal classNames (catches `eval-score-chip ${…}`).
          selector: `JSXAttribute[name.name='className'] TemplateElement[value.cooked=/${BANNED_PRIMITIVE_CLASSES}/]`,
          message: PRIMITIVE_CLASS_MESSAGE,
        },
      ],
    },
  },
  {
    files: ['vite.config.js'],
    languageOptions: {
      globals: globals.node,
    },
  },
])
