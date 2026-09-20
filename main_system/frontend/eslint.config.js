// The lint gate. Errors are defects (undefined names, broken hook rules,
// unused code); the hook-dependency rule is a warning because the codebase
// predates the linter and its existing exceptions are deliberate and commented.
import js from "@eslint/js";
import globals from "globals";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";

export default [
  { ignores: ["dist/**", "node_modules/**", "test-results/**", "playwright-report/**", "public/**"] },
  js.configs.recommended,
  {
    files: ["**/*.{js,jsx}"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: { ...globals.browser },
    },
    plugins: { react, "react-hooks": reactHooks },
    settings: { react: { version: "detect" } },
    rules: {
      "react/jsx-uses-vars": "error",
      "react/jsx-uses-react": "off",
      "react/jsx-key": "error",
      "react/jsx-no-undef": "error",
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      // Warning, under a ratchet: `npm run lint` fails if the count grows
      // (--max-warnings). The legacy unused imports are removed in P9.
      "no-unused-vars": ["warn", { args: "after-used", argsIgnorePattern: "^_", varsIgnorePattern: "^_",
        caughtErrors: "none", ignoreRestSiblings: true }],
      "no-empty": ["error", { allowEmptyCatch: true }],
    },
  },
  {
    // [label, <node>] tuples read as cell values, never rendered as a list.
    files: ["src/components/report/IncidentReport.jsx"],
    rules: { "react/jsx-key": "off" },
  },
  {
    files: ["tests-unit/**", "tests-e2e/**", "*.config.js"],
    languageOptions: { globals: { ...globals.node, ...globals.browser, vi: true, describe: true, it: true,
      expect: true, beforeEach: true, afterEach: true, beforeAll: true, afterAll: true } },
  },
];
