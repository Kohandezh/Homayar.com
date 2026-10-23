# Homayar monorepo

This repo holds separate apps. The root has no app of its own. Treat each app as its own project.

| App | Folder | Stack | Package manager |
| --- | --- | --- | --- |
| Product console (admin dashboard) | `Homayar/frontend/` | React 19, Vite, react-router v7, HeroUI v3, React Query | npm |
| Backend API | `Homayar/backend/` (run from `Homayar/`) | FastAPI, Celery, PostgreSQL, Redis | uv |
| Public website | `website/` | Next.js 16.4.2, OpenNext on Cloudflare Workers | pnpm |

Other folders: `infra/` (deploy and CI scripts), `tools/linear-agent/`, `docs/` (plans, research, handoff notes).

## Required workflow for Linear tickets

Every Linear ticket (team HOM) goes through the `homlead` skill (`.claude/skills/homlead/`). There is no other way to do ticket work in this repo.

- The top session becomes `homlead`, the leader of leaders. It takes tickets from Linear by cycle and priority.
- It runs up to 6 `foreus` leaders at once, one Herdr tab per ticket. Each leader has its own maker and checker.
- Homlead verifies, merges and cleans up. It never implements a ticket itself.
- Do not implement a ticket directly in a single session, even a small one.
- If you are already inside a mission (a `foreman-<ticket>` leader, a maker or a checker), follow your mission contract. Do not start homlead or new missions.

## Stay inside one app

- First decide which app the task is about. Search only in that app's folder: pass the folder as the `path` of Grep and Glob.
- Do not open another app's files unless the task names that app. Each app has its own `CLAUDE.md` with its commands and rules. It loads when you touch a file in that folder.
- For a question that spans apps, use an Explore subagent. Its file reads then stay out of the main context.
- When the work is in one app, start the session in that folder, for example `cd Homayar/frontend && claude`.

## Skip these

- `node_modules/`, `.next/`, `Homayar/.venv/`, `__pycache__/`, lock files.
- `docs/`: read a file there only when the task points to it.
