# Current Graphify maps and safe refresh

The previous graphs under root `graphify-out/` and `Homayar/graphify-out/` describe older snapshots. They were preserved. The current handoff maps are explicitly selected with `--graph`; do not accidentally query the older default path.

## Generated maps

| Artifact | Scope |
|---|---|
| [Code report](graphify/code/GRAPH_REPORT.md) | 440 selected current Python/TypeScript/JavaScript/shell source and test files of the integrated repository, product included under the tracked `Homayar/` (refreshed 2026-09-27, Session A backend closeout, at `4428756`) |
| Code visualization | Not generated for this refresh: the resolved view has 5,510 nodes (the tool skips HTML above 5,000). `graphify/code/graph.json` (local/ignored) holds the simplified directed navigation view: 5,510 nodes, 20,952 edges, 182 communities |
| [Code JSON](graphify/code/graph.json) | Standard Graphify query input; same resolved view |
| Lossless raw extraction (`graphify/refreshes/20260927T032552294074Z/extraction.json`, local/ignored) | 5,512 nodes, 22,829 AST relations; 164 explicitly unresolved reference ids (see below). The `graph.full.json`/GraphML exports were not regenerated |
| [Unresolved reference list](graphify/code/UNRESOLVED_REFERENCES.json) | 164 references not resolved to definitions (derived from extraction.json); no invented source/identity claim |
| [Planning graph](graphify/planning/graph.html) | 119 nodes: 95 features plus 24 work packages, 148 explicit planning relationships (built 2026-09-07; not rebuilt on 2026-09-09/10/11/16/24/25/26 — only task statuses/notes changed, no relationship did) |
| [Planning JSON](graphify/planning/graph.json) | Completion dependencies and feature-to-package mappings, not runtime behavior |
| [Source manifest](graphify/source-manifest.json) | Selected paths relative to the website worktree root, SHA-256, website/product commits the snapshot was taken at, exclusions and extraction scope |
| [Build summary](graphify/BUILD_SUMMARY.json) | Actual graph counts and diagnostics |

Graphify 0.9.40 was already installed. Extraction used local AST and explicit machine-readable plan relationships; no code or documents were sent to a separate LLM API, and external extraction token cost was zero. No model was downloaded. Community names describe dominant source directories deterministically.

Raw AST diagnostics of the 2026-09-11 snapshot (after WP04-T02/T03/T06) found 852 edges referencing 145 undefined endpoints, one self-loop and 435 same-endpoint relationships that the simple directed view consolidates. `extraction.json` preserves the original output; `UNRESOLVED_REFERENCES.json` and `FULL_GRAPH_INTEGRITY.json` are derived from it, listing the unresolved ids **without claiming those references have been resolved to actual definitions**. Some are likely dependencies/config imports; verify individually before using them as evidence. Self-loops can represent recursion; they are not automatically defects.

No free-form semantic extraction of every document, image or recording was performed. The planning layer is complete for the 95-feature/24-package explicit mapping. The production Neo4j conversation graph, runtime tenant safety and feature implementation are separate open tasks. Static code connectivity does not prove execution or authorization.

## Query from workspace root

```bash
graphify query "tenant workspace authorization" --graph docs/handoff/claude-fable-5.1/graphify/code/graph.json --budget 1200
graphify query "durable processing WP06" --graph docs/handoff/claude-fable-5.1/graphify/planning/graph.json --budget 1200
graphify god-nodes --graph docs/handoff/claude-fable-5.1/graphify/code/graph.json --top 10
graphify affected "process_meeting_job" --graph docs/handoff/claude-fable-5.1/graphify/code/graph.json --depth 2
```

Resolve queries against actual graph vocabulary if no seed matches. Consult the installed CLI help rather than assuming an unsupported option. Use the full JSON/GraphML when inspecting all parallel relations; normal queries can use the resolved navigation view.

## Freshness and rebuild

The canonical code map was refreshed on 2026-09-27 (Session A backend closeout) from an isolated clone of the repository at `4428756` (branch `fix/r02-media-diagnostics-and-drill`; the product is tracked under `Homayar/`, ADR 0033): 440 files, every sha256 checked against the committed blob at `4428756` (440/440). It replaces the 2026-09-26 map (`2a30213`), which was stale in five files (`backend/media/validation.py` and `backend/tests/conftest.py` from PR #5; `validation.py`, `test_media_validation.py`, `test_media_fixtures.py` and `test_transport_drills.py` from this session). The dated snapshot lives under `graphify/refreshes/` (ignored by Git); the canonical `graphify/code/` report, diagnostics, unresolved references and integrity counts were copied or derived from it. No HTML was generated (more than 5,000 resolved nodes), so VISUAL_CHECK records `not_run` — HTML rendering was not inspected. Extraction ran sequentially through the wrapper described below; no model or provider API was called. Diagnostics: 0 missing endpoints, 1,233 dangling, 1 self-loop, 631 directed / 649 undirected collapsed. The previous refresh paragraph (Session C, `2a30213`: 5,480 resolved nodes, 1,224 dangling) is superseded.

Compare `graphify/source-manifest.json` hashes to current files. Any changed, removed or newly added source file means coverage needs reassessment. Current graphs contain the uncommitted source snapshot, not merely branch HEAD.

A portable, code-only refresh helper is supplied at `tools/refresh_graph.py`. It selects approved runtime source roots, excludes dependencies, caches, data/media and secrets, writes to a **new dated output directory**, and never overwrites old graphs or reads a provider key. Run it with the Python interpreter containing Graphify, after inspecting its source:

```bash
/Users/emperor/.local/share/uv/tools/graphifyy/bin/python docs/handoff/claude-fable-5.1/tools/refresh_graph.py
```

A sandbox that forbids the process pool's semaphore check (`os.sysconf("SC_SEM_NSEMS_MAX")` → *Operation not permitted*, as in the Claude Code session sandbox) makes the helper fail before writing; run it through a small wrapper that imports `graphify.extract`, wraps `extract` to force `parallel=False`, and executes the helper with `runpy` — extraction of ~300 files then takes about 15 s. On another computer, locate Graphify's interpreter from its installed executable/tool environment. The helper deliberately does not install dependencies, select a paid model, alter Git hooks, start a watcher, push to Neo4j, or mutate application source. New snapshot output must pass its diagnostics/manifest review before replacing any canonical path.

For document semantic enrichment later, read the installed Graphify SKILL.md and extraction specification. Separate current-state facts from planned concepts, cite files/lines, mark inferred/ambiguous relationships honestly and never send customer recordings, environment files or secret-bearing legacy history to a provider. Full-repository extraction is not permission to upload every file.

## Visual limitations

The generated HTML is a navigation aid. Large views can be dense; use the community filters/search or JSON queries for specific questions. The two prepared HTML files use a bundled vis-network 9.1.6 asset with its MIT license under graphify/vendor, so their renderer does not need a CDN request. Future refresh outputs use Graphify’s default template unless bundled separately. JSON, Markdown and GraphML require no external rendering service.
