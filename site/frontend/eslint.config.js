import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'

/**
 * Keeps linting scoped to frontend source and excludes build output.
 */
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
    // primitives themselves (components/ui/**) legitimately emit these classes,
    // and AgentCard/AgentPipeline are the documented .agent-card exception (§9).
    files: ['src/components/**/*.{js,jsx}'],
    ignores: [
      'src/components/ui/**',
      'src/components/AgentCard.jsx',
      'src/components/AgentPipeline.jsx',
    ],
    rules: {
      'no-restricted-syntax': [
        'error',
        {
          selector:
            "JSXAttribute[name.name='className'] > Literal[value=/(^|\\s)(btn|btn-primary|btn-secondary|btn-ghost|btn-danger|btn-icon-only|card|developer-toggle|prompt-input|prompt-dropdown-select)(\\s|$)/]",
          message:
            'Do not hardcode ui/ primitive class strings (btn*/card/developer-toggle/prompt-input/prompt-dropdown-select) in feature components. Compose from src/components/ui/ primitives instead (DESIGN.md §14). Premade sub-variant classes may be passed via a primitive\'s className prop.',
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
