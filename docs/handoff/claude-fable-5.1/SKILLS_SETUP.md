# Claude skills: verified inventory and setup plan

Preparation only: no skill was installed, upgraded, removed or activated by this handoff. This follows the user's clarification to prepare the work for Claude rather than perform product/environment implementation. The core relevant skills are already present on this computer.

## Existing skill files

All paths below were checked on 2026-09-07. File presence is verified; session activation and most runtime dependencies were not exercised. See SKILLS_INVENTORY.json for exact paths and hashes.

| Skill | Existing location under ~/.claude/skills | Use |
|---|---|---|
| Graphify | graphify/SKILL.md | Source relationships and impact queries; installed CLI 0.9.40 successfully rebuilt this handoff's map |
| Gstack plan-eng-review | gstack/plan-eng-review/SKILL.md | One package's architecture and implementation review |
| Gstack review | gstack/review/SKILL.md | Review changed code before completion |
| Gstack investigate | gstack/investigate/SKILL.md | Reproduce and locate defects |
| Gstack qa / browse | gstack/qa/SKILL.md; gstack/browse/SKILL.md | Existing browser workflow QA |
| Gstack cso | gstack/cso/SKILL.md | Threat/security review |
| UI UX Pro Max | uiux-pro-max/SKILL.md | RTL/LTR, accessibility and review workflow |
| SPARC methodology | ruflo-skills/sparc-methodology/SKILL.md | Optional bounded spec→code→verification process |
| Swarm orchestration | ruflo-skills/swarm-orchestration/SKILL.md | Optional independent multi-agent work |
| Verification quality | ruflo-skills/verification-quality/SKILL.md | Optional evidence discipline |

The minimal workflow is Graphify + one engineering/review skill + the existing test tools. Load UI/QA/security skills when relevant. Ruflo/agents are optional; do not start a swarm merely because the files exist. Do not install a second browser stack, RAG runtime, scheduler or model framework for this kickoff.

## Claude preflight

In Claude Code or another filesystem-enabled Claude environment, inspect the installed skill descriptions and use the environment's skill discovery/help mechanism. Open a fresh Claude session if newly installed skills are not discovered. In Claude web, these local folders are not automatically accessible; upload the needed instructions or connect the local workspace. Never claim a skill is active based only on this inventory.

Useful read-only checks:

```bash
command -v claude
command -v graphify
graphify --version
command -v uv
command -v pnpm
command -v node
```

Read the relevant SKILL.md before running its workflow. Follow the user's task scope over generic suggestions to install hooks, modify memory or auto-publish. Preserve existing skill/custom settings; do not overwrite them during discovery.

## Missing-only installation plan for Claude

No additional skill is required to read this handoff or begin WP01. If a later session discovers an absent Graphify skill but the CLI is installed, the documented native installer is:

```bash
graphify install --platform claude
```

Before executing it, inspect the target directory and back up any custom skill contents. A Graphify binary upgrade is separate from rebuilding its output. Version 0.9.40 was used here; no claim is made that it is the latest available release.

If Graphify itself is missing on another computer, read its official package/project installation documentation, select a pinned supported release and install into an isolated tool environment. Do not copy private `.env`, tokens or agent settings. For optional skills, inspect source/license/instructions before installation, install only what the chosen package needs and verify discovery in the next session.

Gstack, UI UX Pro Max and Ruflo are already installed here. Use their existing local instructions; no speculative model-specific “Fable plugin” or API model ID is necessary. Model selection remains in the user's Claude UI.

## Product dependencies are not skills

Python, Node/pnpm, PostgreSQL, a broker, object storage, FFmpeg and GPU/model runtimes belong to product development/deployment. WP01/05/06/09/19 qualify them. Do not download many model weights or install global servers merely to prepare a prompt. Actual model licenses, hardware, offline dependencies and evaluation are still open work.
