# Current Graphify maps and safe refresh

The previous graphs under root `graphify-out/` and `Homayar/graphify-out/` describe older snapshots. They were preserved. The current handoff maps are explicitly selected with `--graph`; do not accidentally query the older default path.

## Generated maps

| Artifact | Scope |
|---|---|
| [Code report](graphify/code/GRAPH_REPORT.md) | 285 selected current Python/TypeScript/JavaScript/shell source and test files across website and product |
| [Code visualization](graphify/code/graph.html) | Simplified directed navigation view: 2,580 nodes, 7,652 edges, 106 communities |
| [Code JSON](graphify/code/graph.json) | Standard Graphify query input; same resolved view |
| [Full relationship JSON](graphify/code/graph.full.json) | Lossless multigraph: 2,714 nodes, 8,492 AST relations, including 134 explicitly unresolved reference nodes |
| [Full GraphML](graphify/code/graph.full.graphml) | Same lossless relationships for compatible external graph tools |
| [Unresolved reference list](graphify/code/UNRESOLVED_REFERENCES.json) | References not resolved to definitions; no invented source/identity claim |
| [Planning graph](graphify/planning/graph.html) | 119 nodes: 95 features plus 24 work packages, 148 explicit planning relationships |
| [Planning JSON](graphify/planning/graph.json) | Completion dependencies and feature-to-package mappings, not runtime behavior |
| [Source manifest](graphify/source-manifest.json) | Selected paths, SHA-256, exclusions and extraction scope |
| [Build summary](graphify/BUILD_SUMMARY.json) | Actual graph counts and diagnostics |

Graphify 0.9.40 was already installed. Extraction used local AST and explicit machine-readable plan relationships; no code or documents were sent to a separate LLM API, and external extraction token cost was zero. No model was downloaded. Community names describe dominant source directories deterministically.

Raw AST diagnostics found 613 edges referencing 134 undefined endpoints, one self-loop and 226 same-endpoint relationships that the simple directed view can consolidate. `extraction.json` preserves original output. `graph.full.json` retains every relation and adds visibly unresolved reference nodes, giving zero structurally missing endpoints **without claiming those references have been resolved to actual definitions**. Some are likely dependencies/config imports; verify individually before using them as evidence. Self-loops can represent recursion; they are not automatically defects.

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

Compare `graphify/source-manifest.json` hashes to current files. Any changed, removed or newly added source file means coverage needs reassessment. Current graphs contain the uncommitted source snapshot, not merely branch HEAD.

A portable, code-only refresh helper is supplied at `tools/refresh_graph.py`. It selects approved runtime source roots, excludes dependencies, caches, data/media and secrets, writes to a **new dated output directory**, and never overwrites old graphs or reads a provider key. Run it with the Python interpreter containing Graphify, after inspecting its source:

```bash
/Users/emperor/.local/share/uv/tools/graphifyy/bin/python docs/handoff/claude-fable-5.1/tools/refresh_graph.py
```

On another computer, locate Graphify's interpreter from its installed executable/tool environment. The helper deliberately does not install dependencies, select a paid model, alter Git hooks, start a watcher, push to Neo4j, or mutate application source. New snapshot output must pass its diagnostics/manifest review before replacing any canonical path.

For document semantic enrichment later, read the installed Graphify SKILL.md and extraction specification. Separate current-state facts from planned concepts, cite files/lines, mark inferred/ambiguous relationships honestly and never send customer recordings, environment files or secret-bearing legacy history to a provider. Full-repository extraction is not permission to upload every file.

## Visual limitations

The generated HTML is a navigation aid. Large views can be dense; use the community filters/search or JSON queries for specific questions. The two prepared HTML files use a bundled vis-network 9.1.6 asset with its MIT license under graphify/vendor, so their renderer does not need a CDN request. Future refresh outputs use Graphify’s default template unless bundled separately. JSON, Markdown and GraphML require no external rendering service.
