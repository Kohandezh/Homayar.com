# React + TypeScript + Vite

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## HeroUI

The console uses HeroUI v3 (`@heroui/react` and `@heroui/styles`). HeroUI v3 needs no provider component. Its styles load in `src/index.css`, right after Tailwind.

HeroUI v3 is new, so old HeroUI examples (v2) are often wrong here. Read the v3 docs before you write HeroUI code:

- **Skill:** `heroui-react` (in `.claude/skills/heroui-react/` at the repo root). It has scripts to fetch component docs, source, and styles.
- **MCP server:** `heroui-react`, set up in the root `.mcp.json`. It runs `npx -y @heroui/react-mcp@latest`.
- **Local docs:** `AGENTS.md` in this folder holds an index of the docs in `.heroui-docs/react/`. That folder is git-ignored. To download it or refresh it, run this here:

  ```bash
  npx heroui-cli@latest agents-md --react --output AGENTS.md
  ```

### Theme

`src/theme.css` maps the Homayar brand colors to HeroUI's theme variables (`--accent`, `--background`, `--success`, and the others), in oklch. Change colors there, not in `index.css`. Set each variable for both light and dark.

The app always renders light. Dark is used only when code sets `class="dark"` or `data-theme="dark"` on `<html>`. The system dark setting does nothing.

### Direction and locale

The console language decides direction: `fa` (the default) is RTL, `en` is LTR. `I18nProvider` in `src/lib/i18n.tsx` sets `dir` and `lang` on `<html>`, and also gives HeroUI the locale (`fa-IR` or `en-US`). React Aria reads the locale from this provider, not from `dir`. It uses the locale for keyboard arrows, overlay placement, and number and date formats. So always render HeroUI components inside `I18nProvider`.

Persian text uses the Vazirmatn font from `@fontsource-variable/vazirmatn`. English uses Geist. Both are bundled. Never load a font from a remote URL, because the product runs without internet access.

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the ESLint configuration

If you are developing a production application, we recommend updating the configuration to enable type-aware lint rules:

```js
export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...

      // Remove tseslint.configs.recommended and replace with this
      tseslint.configs.recommendedTypeChecked,
      // Alternatively, use this for stricter rules
      tseslint.configs.strictTypeChecked,
      // Optionally, add this for stylistic rules
      tseslint.configs.stylisticTypeChecked,

      // Other configs...
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])
```

You can also install [eslint-plugin-react-x](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-x) and [eslint-plugin-react-dom](https://github.com/Rel1cx/eslint-react/tree/main/packages/plugins/eslint-plugin-react-dom) for React-specific lint rules:

```js
// eslint.config.js
import reactX from 'eslint-plugin-react-x'
import reactDom from 'eslint-plugin-react-dom'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      // Other configs...
      // Enable lint rules for React
      reactX.configs['recommended-typescript'],
      // Enable lint rules for React DOM
      reactDom.configs.recommended,
    ],
    languageOptions: {
      parserOptions: {
        project: ['./tsconfig.node.json', './tsconfig.app.json'],
        tsconfigRootDir: import.meta.dirname,
      },
      // other options...
    },
  },
])
```
