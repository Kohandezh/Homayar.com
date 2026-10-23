import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { execSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import path from 'node:path'

/**
 * The console build identity rules (HOM-232 V2), shared with
 * vitest.config.ts so the `__CONSOLE_BUILD__` define is computed in one
 * place. Everything environment-shaped is injected, so the rules are
 * testable without a checkout or a release pipeline (vite.config.test.ts).
 */

export interface ConsoleBuildSources {
  env: { VITE_RELEASE_VERSION?: string | undefined; VITE_SOURCE_SHA?: string | undefined }
  packageVersion: string
  /** `git rev-parse --short=7 HEAD` output, or null when git cannot run. */
  gitShortSha: () => string | null
}

export function consoleBuildValue({ env, packageVersion, gitShortSha }: ConsoleBuildSources): {
  version: string
  sha: string | null
} {
  const version = env.VITE_RELEASE_VERSION?.trim() || packageVersion
  const envSha = env.VITE_SOURCE_SHA?.trim() || null
  let sha: string | null = null
  if (envSha !== null) {
    // The env SHA wins and git never runs: a full 40-char SHA is cut to 7,
    // and an all-zero SHA (a CI placeholder) means no SHA at all.
    sha = /^0+$/.test(envSha) ? null : envSha.slice(0, 7)
  } else {
    // Git may emit more than 7 characters for uniqueness; the line shows 7.
    const fromGit = gitShortSha()
    sha = fromGit === null ? null : fromGit.slice(0, 7)
  }
  return { version, sha }
}

/** The `define` entry both configs pass to Vite/Vitest. */
export function consoleBuildDefine(sources: ConsoleBuildSources) {
  return { __CONSOLE_BUILD__: JSON.stringify(consoleBuildValue(sources)) }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')

  const pkg = JSON.parse(readFileSync(path.resolve(__dirname, 'package.json'), 'utf8')) as { version: string }
  // The console build identity (HOM-232 V2): env release values win, git is
  // the fallback. The rules live in the exports above, shared with Vitest.
  const define = consoleBuildDefine({
    env: process.env,
    packageVersion: pkg.version,
    gitShortSha: () => {
      try {
        return execSync('git rev-parse --short=7 HEAD', { cwd: __dirname, stdio: ['ignore', 'pipe', 'ignore'] }).toString().trim() || null
      } catch {
        return null
      }
    },
  })

  return {
    base: '/app/',
    plugins: [react(), tailwindcss()],
    define,
    // Tailwind runs through @tailwindcss/vite. An inline PostCSS config stops
    // Vite from searching parent directories, where the website checkout's
    // postcss.config.mjs (and its @tailwindcss/postcss dependency) would load.
    css: {
      postcss: { plugins: [] },
    },
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      proxy: {
        '/api': {
          target: env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
        '/readyz': {
          target: env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
      },
    },
  }
})
