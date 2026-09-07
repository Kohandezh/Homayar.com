# Implementation work packages

Generated from blueprint Section 35; all 64 P0/P1 acceptance criteria are carried into the package files. Completion dependencies are conservative; notes identify work that can start earlier.

| Package | Scope | Features | Completion dependencies |
|---|---|---|---|
| [WP01](WP01/README.md) | Reproducible source and release baseline | F01, F02 | None |
| [WP02](WP02/README.md) | Credential containment and deployment security | F03, F10 | None |
| [WP03](WP03/README.md) | Identity, tenancy and operational permissions | F04, F05, F06 | WP01 |
| [WP04](WP04/README.md) | Truthful states and evidence contracts | F23, F24 | WP01 |
| [WP05](WP05/README.md) | Ingestion and private object lifecycle | F12, F13, F14 | WP03, WP04 |
| [WP06](WP06/README.md) | Durable processing and recovery | F17 | WP03, WP04, WP05 |
| [WP07](WP07/README.md) | Retention, deletion and text privacy | F15, F16 | WP03, WP04, WP05, WP06 |
| [WP08](WP08/README.md) | Provider registry and privacy routing | F20, F21, F22 | WP03, WP04, WP06, WP07 |
| [WP09](WP09/README.md) | Channel-aware speech and persistent inference | F25, F26, F27, F28 | WP05, WP06, WP08, WP13 |
| [WP10](WP10/README.md) | Diarization, overlap and alignment | F30, F31, F33 | WP04, WP08, WP09, WP13 |
| [WP11](WP11/README.md) | Measured signals and versioned quality evaluation | F34, F36, F41, F42 | WP04, WP08, WP09, WP10, WP13 |
| [WP12](WP12/README.md) | Call report, summaries and correction | F37, F45 | WP03, WP04, WP09, WP10, WP11 |
| [WP13](WP13/README.md) | Corpus and promotion evaluation | F80, F81 | None |
| [WP14](WP14/README.md) | Review, calibration, appeals and coaching | F43, F44, F46, F47 | WP03, WP11, WP12 |
| [WP15](WP15/README.md) | Issue trends and role dashboards | F49, F61 | WP03, WP11, WP14 |
| [WP16](WP16/README.md) | Supported ingestion/API/integration contracts | F63, F64, F68, F69 | WP03, WP05, WP06, WP08 |
| [WP17](WP17/README.md) | Scoped retrieval and analytical Copilot | F57, F58 | WP03, WP04, WP07, WP11, WP13, WP15 |
| [WP18](WP18/README.md) | Usage, plans and commercial billing | F18, F19 | WP03, WP06 |
| [WP19](WP19/README.md) | Offline package, licensing and upgrades | F74, F75 | WP01, WP02, WP05, WP08, WP09, WP10, WP18, WP20 |
| [WP20](WP20/README.md) | Observability, audit and recovery | F09, F78, F79 | WP01, WP02, WP03, WP04, WP05, WP06 |
| [WP21](WP21/README.md) | Truthful website and measurable sales funnel | F83, F84, F85 | WP01, WP04, WP12, WP14, WP19 |
| [WP22](WP22/README.md) | Administration, i18n and documentation | F72, F86, F92 | WP03, WP07, WP08, WP11 |
| [WP23](WP23/README.md) | Enterprise federation and independent security qualification | F07, F82 | WP02, WP03, WP19 |
| [WP24](WP24/README.md) | Approved actions, alerts and exports | F38, F59, F60, F70 | WP03, WP04, WP07, WP11, WP12, WP14, WP16 |

Machine-readable source: [backlog.json](../backlog.json).
