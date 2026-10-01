# Commercial, support, and maintenance model for Homayar

Status: In Review

Date: 2026-09-27. Author role: researcher (`biz-t1-res`), foreman research team `biz-t1`.
Base commit for all repository citations: `1b34472`.

## 1. Summary and recommendation

Homayar should sell one product in four plans: **SaaS Standard** (monthly or yearly), **SaaS
Enterprise** (yearly contract), **Self-hosted** (yearly subscription license), and a **paid pilot**
as the entry step. The billing unit should be the **audio minute** (one minute of recorded
conversation, measured from accepted audio seconds), not "calls" and not "audio hours".

For self-hosted, the recommendation is an **annual subscription license with Standard support
included**, sized by a yearly band of processed audio minutes. A faster **Premium support** tier is
a paid add-on. Installation, integration, and on-site work are **separate, fixed-scope paid
services**. The customer runs the hardware, operating system, network, backups, and day-to-day
operation. Homayar owns the application package, database migrations, the AI model bundle, signed
updates, and product bug diagnosis. When a license ends, the software keeps working for a short
grace period and then moves to a **read and export mode**. There is no remote kill switch and no
data is deleted.

A subscription with support included is the most common pattern among the benchmarked vendors
that sell both SaaS and self-hosted (GitLab, Mattermost, Elastic, Rocket.Chat, Cisco's Flex plan,
Verint's term licenses). What happens at expiry differs: Mattermost, Elastic, and Rocket.Chat fall
back to a free edition, while the read and export mode proposed here is closest to GitLab and
Atlassian (read-only). The model also matches the direction already written in the repository
blueprint (`docs/planning/homayar-commercial-blueprint.md:460`, `:698`).

The Iranian market works differently. The national computer guild price list (NASR, 1405)
describes a perpetual license plus a yearly support fee of 25% to 35% of the license price, and
Iranian vendors (Sepidar, Mahak, Peyvandtel, Farsava) sell this way. So Homayar should keep
a **perpetual purchase form ready for buyers whose procurement requires it**, with a mandatory
first year of support and new versions included only while support is renewed. It is offered
**on request only** (sales-led, not on the pricing page or in the FAQ) until buyer interviews show
real demand, because a perpetual right cannot be taken back once granted (section 7.2).

Until the v0.8 release gate, **only the paid pilot can be bought**. The other plans are shown as
structure with "Contact us", and online checkout stays off for them (section 7.1).

Today Homayar must not publish an uptime SLA (a service level agreement: a contract promise about
availability, usually with money back when missed). The repository says the product is not
production ready (`docs/handoff/session-reset/PROJECT_STATE.md:3`). The old public plan text that
promises "99.9% uptime", "SOC 2 reports", and "unlimited" volume should be removed or changed.

Prices are not set here. The owner decides. Section 7 labels every proposal (yearly discount,
grace period, support targets, version policy, NASR support level) as a proposal.

## 2. The decision, in the owner's words

Translated from the owner:

> "We offer Homayar as SaaS and self-hosted. A SaaS user must buy a subscription, monthly or
> yearly. We also need an Enterprise plan. Separately, we must decide: if a user buys the
> self-hosted service, how does support for the app work? Do they have to pay for support?
> Maintenance of the service: whose responsibility is it? These must be made clear. We can look
> at existing services that offer this and measure ourselves against them."

The one decision this document settles: the plan structure (SaaS monthly, SaaS yearly,
Enterprise, self-hosted) and, for self-hosted, the support and maintenance model. That means what
is included, what is paid, and who is responsible for what.

## 3. Constraints

### From the owner

- SaaS is sold as a subscription, monthly or yearly.
- There must be an Enterprise plan.
- Self-hosted must have a clear answer on support (paid or not) and maintenance (who does it).
- Homayar should compare itself with real vendors.

### From this repository

- **The product is not production ready.** "No mandatory release gate has passed; nothing has
  been deployed; no real speech, diarization or analysis model has been qualified"
  (`docs/handoff/session-reset/PROJECT_STATE.md:3-5`). Plans, billing (WP18) and the offline
  package with licensing (WP19) are `not_started`
  (`docs/handoff/session-reset/PROJECT_STATE.md:100-101`).
- **Readiness scores are low.** SaaS readiness 3/10, on-premises readiness 3/10, enterprise
  readiness 2/10 (`docs/planning/homayar-commercial-blueprint.md:20-22`).
- **Only tested promises may be published.** For SLA and disaster recovery the blueprint says
  "Publish only tested commitments, operating hours, exclusions and escalation responsibilities"
  (`docs/planning/homayar-commercial-blueprint.md:625`). Private and cloud contracts are allowed
  only at the v0.8 milestone, "limited to supported profiles"
  (`docs/planning/homayar-commercial-blueprint.md:1505`).
- **The self-hosted package must work offline.** No model download, telemetry, license
  heartbeat, or other network call at runtime (`docs/planning/homayar-commercial-blueprint.md:452`).
- **No remote kill switch.** Expiry must not delete recordings or block export
  (`docs/planning/homayar-commercial-blueprint.md:460`, `:698`).
- **Code already meters audio seconds.** The usage metric in the product is `audio_seconds`
  (`Homayar/backend/services/tenancy_service.py:32`; the customer entitlement metric defaults to
  it in `Homayar/backend/services/entitlement_service.py:26`, `:103`, `:134` and
  `Homayar/backend/schemas/meetings.py:195`). The usage ledger allows one billable
  settlement per meeting and metric
  (`docs/architecture/decisions/0010-usage-ledger-and-reconciliation.md:32-36`).
- **Sanctions and procurement.** Selling US-linked software or cloud services for Iran needs an
  eligibility and sanctions review. The blueprint makes no legal conclusion
  (`docs/planning/homayar-commercial-blueprint.md:607`).
- **Research rules.** No final prices. No source code changes. Primary sources only.

## 4. Options considered

### 4.1 Plan structure

- **P1. Keep the current public plans** (Starter, Professional, Enterprise; per-call; monthly).
- **P2. Blueprint plans as written** (pilot, two SaaS bands, SaaS Enterprise, two private bands;
  annual only).
- **P3. Owner-shaped structure (recommended):** paid pilot; SaaS Standard (monthly or yearly);
  SaaS Enterprise (yearly); Self-hosted (yearly), with Standard and Enterprise capacity bands
  inside it.
- **P4. Defer public plans** until v0.7/v0.8 and sell only pilots.

### 4.2 Self-hosted license model

- **L1. Annual subscription license, support included (recommended, with L3 sizing).** The
  customer pays each year. The right to run the software, updates, and Standard support come in
  one price.
- **L2. Perpetual license plus yearly maintenance.** The customer pays once to own the right to
  run a version forever. A yearly maintenance fee buys updates and support. This is the Iranian
  market norm (section 5.4).
- **L3. Usage-banded license.** The license allows a yearly band of processed audio minutes (for
  example up to 1 million minutes per year) and a number of environments. This is a sizing rule,
  so it can combine with L1 or L2.
- **L4. Free self-hosted without support** (like Sentry).
- **L5. Defer self-hosted** until the offline package (WP19) is qualified.

### 4.3 Support for self-hosted

- **S1. Support included in the license** (one tier).
- **S2. Support always paid separately** (license and support are two line items).
- **S3. Standard support included, Premium support paid (recommended).**
- **S4. No vendor support, partner or community only.**

## 5. Evidence

All external pages were read on 2026-09-27. Quotes are short and exact where the page text could
be checked. Where a page was read through a summarizing fetch tool, this is noted.

### 5.1 What the repository says today, and where it disagrees

The SPEC asks to build on the blueprint (sections 20 to 22) and `docs/market/pricing-benchmark.md`,
and to name each disagreement. The Foreman addendum asks to treat `content/products/plans.json`
as claims to check.

#### 5.1.1 Plan names and structure

| Source | Plans | Citation |
|---|---|---|
| Public plan text | Starter, Professional, Enterprise | `content/products/plans.json:4`, `:46`, `:91` |
| Pricing benchmark | Starter, Professional, Enterprise (quote) | `docs/market/pricing-benchmark.md:26-33` |
| Blueprint | Paid pilot, SaaS Quality, SaaS Operations, SaaS Enterprise, Private Standard, Private Enterprise | `docs/planning/homayar-commercial-blueprint.md:685-690` |
| Product code | Free Demo, Professional, Business, Enterprise, On-Premise | `Homayar/backend/services/tenancy_service.py:27-58` |

Four sources use four different plan lists. The code plans for Professional, Business,
Enterprise, and On-Premise have empty entitlements (`Homayar/backend/services/tenancy_service.py:38`,
`:44`, `:50`, `:56`). Only the demo has a quota (1,800 audio seconds, one job, one seat,
`Homayar/backend/services/tenancy_service.py:32`).

The code also rewrites plan entitlements at every startup
(`Homayar/backend/services/tenancy_service.py:61-82`). The blueprint says "do not overwrite paid
plans during startup seeding" (`docs/planning/homayar-commercial-blueprint.md:440`). This is a
WP18 item, not a pricing decision, but it matters: a paid plan must not reset itself.

#### 5.1.2 Billing unit

| Source | Unit | Citation |
|---|---|---|
| Public plan text | analyzed calls per month | `content/products/plans.json:19`, `:63` |
| Pricing benchmark | analyzed calls per month | `docs/market/pricing-benchmark.md:26`, `:29` |
| FAQ | audio hours per month | `content/faq-about.json:45` |
| Terms of Service | "analyzed call hours or agent seats" | `content/legal/terms.md:17` |
| Dedicated Throughput add-on | "per analyzed call hour" | `content/products/modules-revenue.json:385` |
| Blueprint | audio minutes, metered from accepted audio seconds | `docs/planning/homayar-commercial-blueprint.md:438`, `:679`, `:708` |
| Product code | `audio_seconds` | `Homayar/backend/services/tenancy_service.py:32` |

Seven sources use four units (calls, hours, minutes, seconds). **Recommendation: the audio
minute.** Reasons:

1. Cost follows audio length, not call count. The blueprint cost model is per audio minute
   (`docs/planning/homayar-commercial-blueprint.md:710-722`). A per-call price makes a 20-minute
   call cost the same as a 2-minute call. Homayar would lose money on long calls.
2. The code already meters seconds. Minutes are a simple display of that number.
3. Minutes are easier for a buyer to read than hours in small plans. Speech vendors price in the
   same family of units: Deepgram per minute ("$0.0065/min", https://deepgram.com/pricing),
   Speechmatics per hour "billed to the second" (https://www.speechmatics.com/pricing), Google
   Conversational Insights per minute (`docs/market/pricing-benchmark.md:10`).
4. The blueprint already defines the rules a minute needs: rounding, failed jobs, reanalysis,
   and "a stereo minute is one conversation minute" (`docs/planning/homayar-commercial-blueprint.md:438`,
   `:679`). WP18 must publish these rules (`docs/planning/homayar-commercial-blueprint.md:1346`).

Terms, FAQ, and plan text must all say "audio minutes" once the owner accepts this.

#### 5.1.3 Prices and volumes

- The pricing benchmark keeps Starter at $499/month for 2,000 calls and Professional at
  $1,499/month for 10,000 calls (`docs/market/pricing-benchmark.md:26-31`), also in
  `content/products/plans.json:16`, `:59`.
- The blueprint proposes SaaS Quality at "$1,500/month equivalent, annual contract" with 10,000
  minutes, and SaaS Operations at $7,500 with 100,000 minutes
  (`docs/planning/homayar-commercial-blueprint.md:686-687`). It calls these "commercial
  experiments, not validated market prices" (`:681`).
- The blueprint says the $499/$1,499 figures are "Neither ... evidence of a proven pricing model"
  (`docs/planning/homayar-commercial-blueprint.md:700`).
- The two do not measure the same thing (calls vs minutes), so they cannot be compared directly.
  **Inference:** at an average call of 3 to 5 minutes, 2,000 calls is 6,000 to 10,000 minutes.
  The blueprint's variable API cost is $0.0105 per minute
  (`docs/planning/homayar-commercial-blueprint.md:722`), so about $63 to $105 per month for that
  customer. The $605 figure at `:746` includes a $500 fixed platform allocation (`:742`), and the
  blueprint says these are "deployment/fleet scenarios, not a claim that every tenant incurs a
  separate platform" (`:696`). So $499 is not shown to be below cost. The real problem is the
  unit: per-call pricing makes the margin depend on call length, which the repository does not
  record.

#### 5.1.4 Monthly vs yearly

- Public plan text: `"billingType": "monthly"` for Starter and Professional
  (`content/products/plans.json:17`, `:60`).
- Terms: renewal "monthly or annual" (`content/legal/terms.md:19`).
- Blueprint: "Annual commitment after paid pilot" (`docs/planning/homayar-commercial-blueprint.md:661`),
  and "Offer annual prepaid commitments and transparent overage"
  (`docs/planning/homayar-commercial-blueprint.md:698`).
- The owner wants both monthly and yearly SaaS. The blueprint prefers yearly. Section 7 resolves
  this: monthly is allowed for SaaS Standard only.
- **Refunds disagree.** Terms: fees are "non-refundable except where required by law or expressly
  stated in these Terms" (`content/legal/terms.md:20`). FAQ: annual contracts "include a 30-day
  evaluation window" with "a prorated refund of the unused term" (`content/faq-about.json:55`).
  The Terms do not state this window, so the two conflict. Owner and lawyer question (section 9).

#### 5.1.5 Seats

- Public plan text: "Team dashboard for up to 10 agent seats" (`content/products/plans.json:24`).
- FAQ: "no per-seat charge for agents whose calls are analyzed; only supervisor and admin seats
  count" (`content/faq-about.json:45`).
- Blueprint: "5 reviewer/admin seats, agent self-review" (`docs/planning/homayar-commercial-blueprint.md:686`),
  and "Do not charge for every viewer" (`:679`).
- The FAQ and blueprint agree (count reviewers and admins, not agents). The plan text disagrees.

#### 5.1.6 Claims that the repository does not support

| Claim | Where | Why it cannot be published now |
|---|---|---|
| "99.9% uptime SLA" | `content/products/plans.json:99`, `:113`; Persian copy `content/i18n/products.fa.json:62`, `:74` | Nothing is deployed (`docs/handoff/session-reset/PROJECT_STATE.md:3-5`). Blueprint: publish only tested commitments (`docs/planning/homayar-commercial-blueprint.md:625`). |
| "SOC 2 reports", "pen-test documentation" | `content/products/plans.json:101`, `:115` | "No Homayar certification was verified" (`docs/planning/homayar-commercial-blueprint.md:605`); "do not advertise SOC 2 reports or penetration results that do not exist" (`:626`). The FAQ says reports are "available under NDA through our trust page" (`content/faq-about.json:35`). No such report is in the repository. Unverified. |
| "Unlimited analyzed call volume" | `content/products/plans.json:98`, `:108` | Blueprint: "No unlimited inference, unbounded concurrency or untested SLA" (`docs/planning/homayar-commercial-blueprint.md:688`). |
| "unlimited teams and queues" | `content/products/plans.json:53` | No plan entitlement exists in code (`Homayar/backend/services/tenancy_service.py:38`). |
| "Email support with 1-business-day response", "4-business-hour response" | `content/products/plans.json:26`, `:70` | No support team, hours, or ticket system is described in the repository. These are promises without a staffing basis. |
| "Priority incident response with 15-minute acknowledgment" | `content/products/modules-revenue.json:383` | Same as above. |
| "Air-gapped deployment option" | `content/products/modules-revenue.json:292` | WP19 is not started (`docs/planning/work-packages/WP19/README.md:3`). |
| Production deployment in 6 to 10 weeks, $18,500 | `content/products/modules-revenue.json:278`, `:284` | Blueprint range is $8,000 to $20,000 for deployment (`docs/planning/homayar-commercial-blueprint.md:689`). No delivered installation exists to measure. |
| "annual third-party penetration testing"; reports "available under NDA through our trust page" | `content/faq-about.json:35` | No penetration test report or trust page is in the repository. Blueprint: state only actual tests and dates (`docs/planning/homayar-commercial-blueprint.md:626`). Unverified. |
| "Payments are processed by Stripe" | `content/legal/terms.md:17` | Whether Stripe can serve this business is a sanctions and eligibility question (`docs/planning/homayar-commercial-blueprint.md:607`). Needs a lawyer. |

#### 5.1.7 Paths that cannot be checked here

Some blueprint citations point outside this repository, for example
`/Users/emperor/Documents/AI/Homayar.com/docs/architecture/initial-assessment.md`
(`docs/planning/homayar-commercial-blueprint.md:36`) and
`/Users/emperor/Documents/AI/Homayar.com/infra/docker/api.Dockerfile:6`
(`docs/planning/homayar-commercial-blueprint.md:454`). These cannot be checked from this
repository. The blueprint's own statements are cited here as statements of the blueprint, not as
verified facts about those files.

#### 5.1.8 Add-on sprawl

The repository content sells many modules as separate monthly add-ons (for example
`content/products/modules-analysis.json:17-18`, `content/products/modules-governance.json:17-18`,
`content/products/modules-revenue.json:17-18`). The blueprint says "sold as one platform with two
scale bands, not as nine disconnected modules" (`docs/planning/homayar-commercial-blueprint.md:692`).
This document follows the blueprint: few plans, few add-ons.

### 5.2 Benchmark: general software sold as SaaS and self-hosted

**GitLab**

- Plans: Premium "$ 29 per user/month, billed annually" on GitLab.com and Self-Managed. Ultimate
  is "contact sales". No monthly billing option is shown. (https://about.gitlab.com/pricing/)
- Support is included in the paid tiers for both SaaS and self-managed: "Priority Support is
  included with all self-managed and GitLab.com Premium and Ultimate purchases." Free has no
  support. (https://support.gitlab.com/hc/en-us/articles/11626483177756-GitLab-Support)
- First-response targets: "Emergency... 30 minutes 24x7 / Highly Degraded... 4 hours 24x5 /
  Medium Impact 8 hours 24x5 / Low Impact 24 hours 24x5". Response is not resolution: "the SLA
  times are not to be considered as an expected time-to-resolution." (same page)
- Not covered: local source changes, customer infrastructure ("creation, configuration, or
  maintenance of on-premises, cloud, or infrastructure-as-code environments"), and versions older
  than "the current major version and previous two major versions".
  (https://support.gitlab.com/hc/en-us/articles/11625911285404-Statement-of-Support)
- License expiry: "Your instance becomes read-only". After more than 30 days "you must purchase a
  new subscription". Deleting expired licenses returns the instance to Free features.
  (https://docs.gitlab.com/administration/license_file/) Another GitLab page says manual renewal
  is possible "within 15 days after the expiration date"
  (https://docs.gitlab.com/subscriptions/manage_subscription/). GitLab's two pages disagree.
- Offline: license file for offline instances; customers are "encouraged to submit your license
  usage data monthly" (https://docs.gitlab.com/administration/license_file/). Package metadata is
  GPG-signed (https://docs.gitlab.com/omnibus/update/package_signatures/).
- Maintenance: security fixes backported "to the previous two monthly releases in addition to the
  current stable release" (https://docs.gitlab.com/policy/maintenance/).
- Installation help is a separate paid Professional Services offer
  (https://about.gitlab.com/services/).
- SaaS SLA: 99.9% with service credits, only for Ultimate bought or renewed on or after
  2026-01-01 (https://handbook.gitlab.com/handbook/engineering/infrastructure-platforms/service-level-agreement/).
- Fetch note: support.gitlab.com returned HTTP 403 to automated fetching. The two support pages
  were read in a real browser. The GitLab severity "Definitions" page was not read; exact severity
  definitions are unverified.

**Mattermost**

- Paid plans are "sold as prepaid annual subscriptions". Extra seats are added quarterly ("true
  forward"). No per-seat prices are published. (https://mattermost.com/pricing/) Cloud is also
  annual, minimum 100 users (https://docs.mattermost.com/product-overview/cloud-subscriptions).
- Each non-production server needs its own license key (https://mattermost.com/pricing/).
- Support is part of the paid plan. P1 first response: "Professional 4 Business Hours /
  Enterprise 2 hours / Enterprise Advanced 1 hour". Business hours are "9am to 5pm local time"
  of the requester. (https://mattermost.com/resources/help/support/support-terms/) Mattermost's
  pricing page and services page give different numbers (4-hour Enterprise target; 1-hour
  Premier P1), so Mattermost's own pages disagree.
- Not covered: "Integrations, installers, images, and plug-ins; Remote administration;
  Professional services... Overall installation and upgrade services", and old releases.
  (same support-terms page)
- License expiry: "your server is automatically downgraded to Mattermost Entry. Users can still
  access and use Mattermost; however, paid features will no longer be available."
  (https://docs.mattermost.com/product-overview/self-hosted-subscriptions)
- Air-gapped installs are documented with local, GPG-signed package mirrors
  (https://docs.mattermost.com/deployment-guide/air-gapped-operations). No page confirms that the
  license file works without any online check. Unverified.
- Versions: "ESRs are released every 9 months... ESRs are supported for 12 months." Security
  fixes go back to "the previous 3 monthly releases"
  (https://docs.mattermost.com/product-overview/release-policy).
- Services are separate paid packages, including a "Managed Services" option with "24x7
  monitoring, patching, backups, DR readiness" (https://mattermost.com/resources/help/services/).
- SaaS SLA: "99.9% uptime SLA guarantee (Cloud only, via dedicated virtual secure Cloud add-on
  option)" (https://docs.mattermost.com/product-overview/editions-and-offerings). A marketing page
  says "99.99% Uptime" without terms (https://mattermost.com/platform/flexible-deployment/).

**Elastic** (quotes read through a summarizing fetch tool; wording near exact)

- Cloud: from $99/month (Standard) to $184/month (Enterprise); "Pay as you go (monthly) or
  prepaid"; prepaid credits "typically at a discount"
  (https://www.elastic.co/pricing/cloud-hosted, https://www.elastic.co/pricing,
  https://www.elastic.co/docs/deploy-manage/cloud-organization/billing/billing-models).
- Self-managed is priced by "number of nodes and used RAM"; prices are not published
  (https://www.elastic.co/pricing, https://www.elastic.co/subscriptions).
- Support is part of the self-managed subscription tier. Platinum and Enterprise: 24/7/365,
  first response 1 hour for critical issues (https://www.elastic.co/subscriptions). Response
  targets are not resolution times (https://www.elastic.co/support_policy).
- Not covered: customer applications ("Installation, configuration, management, and operation of
  Customer's applications"), modified software, third-party software
  (https://www.elastic.co/support_policy).
- License expiry: the cluster "reverts to Basic and you will no longer be able to use Platinum or
  Enterprise features"; warnings start "30 days before the license expiration date"
  (https://www.elastic.co/docs/deploy-manage/license/manage-your-license-in-self-managed-cluster).
- Air-gapped install is documented (https://www.elastic.co/docs/deploy-manage/deploy/self-managed/air-gapped-install).
- End of life: a major version is maintained for "the longer of: 1) 30 months from its general
  availability ("GA") or 2) 18 months from the GA of the next Major Release", then supported 6
  more months (https://www.elastic.co/support/eol).
- Consulting is sold separately (https://www.elastic.co/consulting).
- SaaS SLA: "99.95% Monthly Uptime SLA for Platinum and Enterprise subscription tiers"
  (https://www.elastic.co/pricing).
- Shared responsibility is published for Cloud only: Elastic applies "OS updates and security
  patches"; for backups "you set the cadence, we do the work"
  (https://www.elastic.co/cloud/shared-responsibility).

**Rocket.Chat**

- No list prices are published ("Get pricing") (https://www.rocket.chat/pricing). Free Starter
  and Community editions exist (https://docs.rocket.chat/docs/our-plans).
- Support: "Every premium plan includes **Support Standard**. **Mission Critical** is a paid
  add-on". Standard is 12x5, first response "1 business day for every severity level". Mission
  Critical is 24x7, "30 minutes for high severity, 2 hours for medium, and 4 hours for low".
  (https://docs.rocket.chat/docs/support-center)
- Support requires official images or charts and supported database versions
  (https://docs.rocket.chat/customer-center/support-center/premium-support-plans/support-prerequisites-and-version-durability).
- Professional services are separate: "There is no list price" and "Buying Mission Critical does
  not include Professional Services hours" (https://docs.rocket.chat/docs/support-center).
- Expiry: "the downgrade happens automatically when your contract expires"; some data "may no
  longer be accessible" (https://docs.rocket.chat/docs/downgrade-behavior).
- Air-gapped: premium plans only. Starter workspaces "enter read-only mode after 90 days without
  online validation". Offline licenses cover "versions within the standard support window"
  (https://docs.rocket.chat/docs/air-gapped-license).
- Versions: standard releases 6 months, LTS 12 months (https://docs.rocket.chat/docs/version-durability).
- SaaS SLA: "Availability: 99.9% (ex scheduled downtime)" for Premium and Dedicated cloud; "no
  SLA guarantee for Standard Cloud"
  (https://docs.rocket.chat/docs/rocketchat-cloud-hosting-service-level-agreement-sla).

**Sentry** (quotes read through a summarizing fetch tool)

- SaaS: Team "$26/mo When billed annually", Business "$80/mo"; monthly billing exists and
  "Annual and prepaid billing options with additional discounts are available during checkout"
  (https://sentry.io/pricing/). The monthly price and discount size are not shown. Unverified.
- Self-hosted: "This version comes with no guarantees or dedicated support"; users go to the
  community (https://develop.sentry.dev/self-hosted/). License is the Functional Source License,
  which becomes Apache 2.0 after 2 years (same page, https://open.sentry.io/licensing/).
- Old self-hosted versions are not patched (https://develop.sentry.dev/self-hosted/releases/).
- SaaS terms: "reasonable efforts to respond to Support requests during business hours"
  (https://sentry.io/terms/). No uptime SLA number was found; https://sentry.io/legal/sla/
  returned 404.
- Sentry is the example of option L4. It works because Sentry is source-available and earns its
  money from SaaS.

**License model reference points**

- JetBrains: a yearly subscription (or 12 months of monthly) gives "a perpetual fallback
  license" for the version available when the subscription started
  (https://sales.jetbrains.com/hc/en-gb/articles/207240845-What-is-a-perpetual-fallback-license-and-how-do-I-use-one).
- Atlassian Data Center: subscriptions expire on "March 28, 2029... making Data Center products
  and apps read-only" (https://www.atlassian.com/licensing/data-center-end-of-life). Server
  products lost support "February 15, 2024"
  (https://www.atlassian.com/licensing/server-end-of-support). Atlassian moved from perpetual
  (Server) to subscription (Data Center) to cloud only.

### 5.3 Benchmark: contact-center and speech vendors with an on-premise option

**Verint** (public PDF: "Verint Global Maintenance and Support Plan", 20 March 2026,
https://www.verint.com/corporate/misc/verint-global-maintenance-and-support-plan-all-regions.pdf)

- One plan covers "Verint Cloud" and "Customer Hosted". Three tiers: Premium Plus, Premium,
  Standard. SaaS and term licenses get Premium support. **Inference:** perpetual licenses can buy
  Standard.
- Premium targets: P1 "15 minutes (cloud)" and "1 hour (customer hosted)", P2 "1 hour", P3
  "2 hours", P4 "Next business day". Standard: P1 "2 hours", P2 "2 hours", P3 "4 hours". These
  are "commercially reasonable efforts" targets. 24x7 only on the top two tiers.
- Updates and patches are included: "New Version and Updates", "Software patches, HotFixes".
  But for customer-hosted systems, installing them is the customer's duty ("Installation of
  Software patches, Hot Fixes and Service Updates") or a paid Verint service.
- The customer must "Perform full system backups of data and configuration" and keep a
  non-production test environment.
- Not covered, billable at time and materials: "Database disaster recovery", "Moving servers",
  "Virus remediation", professional services.
- Support needs supported versions "running unaltered" on minimum specifications, installed by
  "Verint or a Verint Verified Partner". Verint "requires remote access to diagnose".
- Lapse: no updates or patches "if Support is not renewed". Coming back costs all missed fees
  plus a "Reactivation fee".
- The product lifecycle policy is behind a login. Unverified.

**NICE** (https://www.nice.com/company/legal/nice-enterprise-terms-and-conditions/on-premise-support-policy)

- NICE Engage supports on-premises recording "alongside hybrid and cloud deployment models"
  (https://www.nice.com/products/recording-management/engage).
- On-premise support runs "twelve (12) months commencing on the Delivery Date" and renews. Fees
  may rise by "the greater of: (a) five percent (5%) or (b) the most recent annual increase in
  the Consumer Price Index". Non-renewal needs notice "at least sixty (60) days prior".
- NICE installs service packs "at no cost". A new release is the customer's cost, including
  hardware and installation services.
- The customer must "maintain third party servers and third party software... perform routine
  database maintenance" and name two trained people.
- After termination, "NiCE shall not provide, and Customer shall not be entitled to receive, any
  Updates".
- Business day: "8 AM to 5 PM Monday through Friday". The severity and hours tables did not
  render for automated fetching. Unverified.
- SaaS (CXone): "99.99% monthly availability guarantee" (https://www.nice.com/company/sla-guarantee).
  No primary evidence of an on-prem Nexidia offer was found.

**Genesys** (on-prem being retired)

- "we no longer will sell Genesys Multicloud CX™ or Genesys Multicloud CX™ private edition";
  existing on-prem customers keep maintenance and support
  (https://www.genesys.com/blog/post/all-in-on-genesys-cloud-cx, a Genesys company post dated
  11 October 2022).
- PureConnect end of life: announced "May 16, 2022", last order "January 1, 2024", end of
  support "July 31, 2025"
  (https://all.docs.genesys.com/PureConnect_End_of_Life_Announcement/EOL/All_versions).
- Lifecycle policy: PureConnect versions get "platform updates for 18 months from the date of
  release" and support "for an additional 18 months". On-prem customers "must be under a current
  Maintenance contract" to get support. "If a customer chooses not to renew their subscription,
  they must discontinue using the subscription software."
  (https://all.docs.genesys.com/special:repository/genesys_engage_&_pureconnect_lifecycle_policy.pdf?page=system/eol)

**Avaya** (Product Lifecycle Policy, 20 June 2025, https://support.avaya.com/css/public/documents/100081098)

- After end of sale, "contract renewals will be limited to one-year term and Support List price
  will be increased". End-of-sale is announced "60 days prior".
- After end of manufacturer support, "no new fixes or security updates will be made available".
- "'Per incident' support is not available for Customers that have not purchased a valid Support
  Service contract."

**Cisco** (read through a summarizing fetch tool; direct fetch returned HTTP 403,
https://www.cisco.com/c/en/us/products/collateral/unified-communications/hosted-collaboration-solution-contact-center/unified-contact-center-enterprise-eol.html)

- Perpetual contact-center licenses were retired: last order "August 1, 2022", last support
  "July 31, 2024". The replacement Flex plan "supports multiple deployment models: On-premises,
  partner hosted, and cloud", with "Software Support that is included", paid "monthly, annual,
  and full".

**Deepgram** (speech-to-text, self-hosted)

- "To self-host Deepgram ... you will need to enroll in a Deepgram Enterprise Plan."
  "Detailed troubleshooting and on-demand support require an on-going support contract"
  (https://developers.deepgram.com/docs/self-hosted-introduction).
- Self-hosted containers check a license server; with a License Proxy they keep running for "a
  pre-configured amount of time" when the server is unreachable. No number is published
  (https://developers.deepgram.com/docs/license-proxy).
- Dedicated hosting: "99.9% uptime, backed by enterprise-grade SLAs" (https://deepgram.com/dedicated).

**Speechmatics** (speech-to-text, on-prem)

- On-prem as "Docker containers, Kubernetes, or a preconfigured virtual appliance"
  (https://www.speechmatics.com/product/features-and-deployments).
- "Licensing does not require network access and works in offline deployments." "After the
  expiry date, the Container will continue to run but will not transcribe audio."
  (https://docs.speechmatics.com/deployments/container/licensing)
- No public support policy or response targets were found.

**CallMiner and Observe.AI**

- CallMiner states only "Deployed on-prem or in the cloud"
  (https://callminer.com/conversation-analytics/speech-analytics). No support or license terms
  were found. Not used as a benchmark row.
- Observe.AI uses "Amazon Web Services (AWS), exclusively" (https://www.observe.ai/contact-center-security)
  and states "≥ 99.9% uptime" (https://www.observe.ai/trust). No self-hosted option was found.

### 5.4 Benchmark: Iranian software vendors

This is the most important local evidence. It changes part of the recommendation (section 7.2).

**NASR 1405 IT price list, volume 2, "Software development and support"** (NASR is
سازمان نظام صنفی رایانه‌ای کشور, the national computer guild body; it is a guild, not a ministry.
PDF published Ordibehesht 1405:
https://tariffs.irannsr.org/images/docs_attaches/NSR_Tariffs_1405_IT-Vol-2_R1V0-FINAL.pdf.
File note: the URL says 1405, but the title page inside reads "NSR_Tariffs_1404_IT-Vol-2_R1V1"
and "اردیبهشت 1404, نسخه 1.1". The digital signature is dated 2026-05-10, which is Ordibehesht
1405.)

- The tariff describes the perpetual model: «مدل لایسنس دائمی (Perpetual License) به صورت
  پرداخت یکجا برای خرید دائمی نرم‌افزار و نیز هزینه پشتیبانی سالانه است» ("a one-time payment to
  buy the software permanently, plus a yearly support fee"). It applies to packaged and custom
  software sold this way. It does not cover SaaS.
- The first-year support fee (after the warranty) is a coefficient «بین %25 تا %35» ("between
  25% and 35%") of the sale price. Later years rise by NASR's yearly rate.
- At the 35% level, new versions are included: the vendor «حق اخذ هزینه مجزا بابت مجوز نسخه
  جدید را نداشته» ("may not charge separately for a new-version license") and may charge only
  for a "new generation".
- A customer who lets support lapse must first pay a catch-up fee
  («هزینه یکسان‌سازی») before a new contract.
- The document states a minimum software warranty of 6 months carried by the producer
  («حداقل مدت گارانتی نرم‌افزار ... 6 ماه»), citing a Plan and Budget Organization circular. The
  circular itself was not read. Unverified beyond the NASR text.
- **The 25% to 35% band assumes remote access.** The table note says it applies «در صورت وجود
  دسترسی ریموت به پنل ادمین نرم‌افزار و کارساز» ("when there is remote access to the software
  admin panel and the server"), and for one install in one environment. Table 2-2 adds an access
  coefficient when remote access is not given: 0.4 without remote access to the admin panel, 0.4
  without remote access to the server, and 0.8 without either. The worked example in the
  appendix applies it: a customer who gives no remote access adds 80% of the base support amount.
  **Inference:** with no remote access, the NASR fee becomes 25% to 35% times 1.8, about 45% to
  63% of the license price.
- NASR also sets fix deadlines when remote access exists: critical errors within 48 hours,
  non-critical errors within 20 working days. Office hours are "Saturday to Wednesday, 9 working
  hours a day".
- Support price also depends on resident engineer, SLA level, off-hours work, extra
  environments, and user count.
- The older 1403 edition says the same
  (https://www.citna.ir/sites/default/files/files/attachment/citna/NASR%20TARIFF%201403%20V1.pdf).
- **Not verified:** whether this tariff is binding for any buyer. **Inference:** Iranian public
  and bank buyers may use it as a reference in procurement. This needs a lawyer (section 9).

**Tehran IT guild union** (اتحادیه صنف فناوران رایانه تهران, a trade union, not a regulator),
notice dated 2026-05-12 (https://itunion.ir/نرخنامه-جدید-پشتیبانی-نرمافزار-بر/): support
renewal cost «به صورت مستقیم به نرخ تورم سالانه اعلامی از سوی بانک مرکزی گره خورده است»
("is tied directly to the Central Bank's yearly inflation rate").

**Hamkaran System / Systemgroup (Rahkaran)** (support brochure 1404,
https://www.systemgroup.net/wp-content/uploads/2025/11/support-1404-3.pdf)

- Support in «چهار سطح قرارداد» ("four contract levels"). Response targets 3, 2, 1.5, and 1 hour
  for levels 1 to 4. Standard hours 8:00 to 17:00.
- Channels: portal, phone, remote, chatbot, on-site; a resident engineer on request.
- Which levels include "update to latest version" is shown as images that could not be read.
  Unverified. No prices are given.
- A cloud version is sold by subscription with support and updates by the vendor
  (https://www.systemgroup.net/products/rahkaran-abri/; page summary, not an exact quote).

**Sepidar** (Systemgroup group)

- One year of free support after install. Renewal runs to the end of the Iranian fiscal year
  (29 Esfand) and is priced per day: «هزینه آن از زمان پایان گارانتی تا ۲۹ اسفند به‌صورت روزشمار
  محاسبه می‌شود» (https://www.sepidarsystem.com/support/).
- Remote support only with an active contract (same page).
- Installation is a separate fee, done by resellers only: single-user install 40,000,000 IRR
  (https://www.sepidarsystem.com/sepidar-price/).

**Mahak** (accounting)

- One-year warranty, renewable yearly «با پرداخت هزینه مصوب روز» ("at the current approved fee").
  Without renewal, each request is charged by tariff (https://www.mahaksoft.com/خدمات-پشتیبانی/).
- Active support credit is needed to contact support, and it includes free new versions
  (https://www.mahaksoft.com/پشتیبانی/تمدید-پشتیبانی/).
- Hardware, Windows, network, and virus problems are excluded.

**Peyvandtel** (call-center software, on-prem)

- «نرم‌افزار پیوندتل اشتراکی نیست و محدودیت زمانی ندارد» ("not a subscription, no time limit");
  lifetime use after purchase; one year of free support; phone or remote support first, then a
  site visit (https://peyvandtel.com/call-center-software/). The fee after year one is not
  stated.

**Amerandish, Farsava** (Persian speech-to-text, a close peer)

- On-prem is priced after assessment by volume and hardware, paid once: «شما فقط یک بار پرداخت
  خواهید کرد» ("you pay only once") (https://amerandish.com/farsava/?lang=en). Support terms are
  not stated.

**Chargoon (Didgah)**

- Offers cloud and on-server installs. Its own buyer guide says installation and training may be
  in the offer or priced separately
  (https://www.chargoon.com/blog/امکانات-سیستم-اتوماسیون-اداری-چار/).

**Could not be read (unverified):** Asr Gooyesh Pardaz / Nevisa (HTTP 502 and 504), Part AI
(HTTP 403 and timeout; its ivira.ai product shows cloud and API only), Tadbir (suspended hosting,
timeout), Behpaya (connection failed). No Supreme Informatics Council support rate card was found.

**Pattern (inference from the sources above):** Iranian on-prem software is usually sold as a
perpetual license, with 6 to 12 months of warranty or free support, then a yearly support fee
(NASR guideline: 25% to 35% of the license price, indexed yearly). Installation is often a
separate fee. The software keeps working when support lapses; updates and support stop; coming
back costs a catch-up fee. This is the same shape as Verint, NICE, and Avaya abroad.

### 5.5 Evidence per option

**Plan structure**

- P1 (keep current plans) fails on evidence: per-call unit conflicts with cost (section 5.1.2),
  and it carries unsupported claims (section 5.1.6).
- P2 (blueprint as written) is sound on unit and honesty. It has no monthly option, which the
  owner asked for. It has six packages; the blueprint itself says "one platform with two scale
  bands" (`docs/planning/homayar-commercial-blueprint.md:692`).
- P3 keeps the blueprint's unit and honesty rules and adds a monthly option. Benchmarks with a
  monthly option (Sentry, Elastic Cloud, Cisco Flex) all give a discount for yearly or prepaid.
  Vendors with a sales-led enterprise focus (GitLab, Mattermost) sell yearly only.
- P4 (defer) matches readiness, but the owner needs a clear public structure now for the website.
  P3 can be published as a structure with "pilot first" wording and no prices, which gives most
  of P4's safety.

**License model**

| Model | Who uses it (evidence) | For Homayar | For the buyer |
|---|---|---|---|
| L1 Annual subscription, support included | GitLab, Mattermost, Elastic, Rocket.Chat, Cisco Flex, Verint term | Steady yearly income. Pays for AI model updates, which never stop. One supported line is easier to staff (`docs/planning/homayar-commercial-blueprint.md:462`). Matches blueprint (`:460`, `:698`). | Lower first-year cost than perpetual. Always on a supported version. Must keep paying to keep full use. |
| L2 Perpetual plus yearly maintenance | Verint (perpetual), NICE, Avaya; retired by Cisco and Atlassian. **The Iranian norm:** NASR 1405 tariff, Sepidar, Mahak, Peyvandtel, Farsava (section 5.4) | Large first payment, then smaller maintenance. Customers who stop maintenance stay on old versions and old models, and still call for help. Blueprint rejects "a perpetual license with undefined lifetime upgrades" (`:698`); NASR's 35% level avoids this because new versions come only with renewed support. | Familiar to Iranian buyers. Fits a capital budget. A catch-up fee after a lapse is a known local rule (NASR). **Inference:** Iranian public and bank buyers may expect this form; whether they must is not verified. |
| L3 Usage band | Blueprint (`:460`); Deepgram license server reports usage; Speechmatics usage reports when online | Price follows value and cost. Needs a local, visible, signed counter because the install is offline (`:698`, WP19 step 4 at `docs/planning/work-packages/WP19/README.md:14`). | Predictable yearly budget. Must plan capacity. Hardware limits still apply (`docs/planning/homayar-commercial-blueprint.md:755`). |
| L4 Free, no support | Sentry | Not suitable. Homayar is not open source and ships licensed model weights (`docs/planning/homayar-commercial-blueprint.md:452`). | Cheap, but no help and no updates promise. |
| L5 Defer | Blueprint release plan: private contracts at v0.8 (`:1505`) | Safest technically. Loses early private buyers. | Nothing to buy yet. |

**Expiry behavior (checking the blueprint rule)**

The blueprint says: no remote kill switch, notice, bounded grace, then read/export mode
(`docs/planning/homayar-commercial-blueprint.md:460`, `:698`). Vendors do this:

| Vendor | After the license or support ends |
|---|---|
| GitLab | Instance becomes read-only; can drop to Free |
| Mattermost | Drops to free edition; keeps working |
| Elastic | Drops to free Basic tier |
| Rocket.Chat | Drops to a lower tier; some data hidden |
| Atlassian Data Center (2029) | Read-only |
| Speechmatics | Container runs but stops transcribing |
| Verint, NICE, Avaya (perpetual) | Software keeps running; no updates or patches |
| Genesys (subscription) | Customer must stop using it |

The blueprint rule is in line with the market. It is close to GitLab and Atlassian (read-only)
and kinder than Speechmatics and Genesys. It is stricter than Mattermost and Elastic, which fall
back to a free edition. Homayar has no free self-hosted edition, so read/export is the right
fallback. **Inference:** with no phone-home, the expiry check depends on the local clock. A
customer could move the clock back. The license file should record the highest time seen. This
is a WP19 design point, not a policy point.

One more point from the evidence: the product must keep **deletion** working in read/export mode.
The blueprint deletion pipeline is a privacy duty (`docs/planning/homayar-commercial-blueprint.md:564`).
A license state must never block a legal deletion request.

**Support model**

| Vendor | Self-hosted support included? | Paid higher tier? |
|---|---|---|
| GitLab | Yes, in paid tiers | No separate tier |
| Mattermost | Yes, in paid plans | Premier |
| Elastic | Yes, level depends on tier | Higher subscription tier |
| Rocket.Chat | Yes, Standard | Mission Critical |
| Verint | Term and SaaS: Premium support comes with the license ("SaaS and Term are Premium support"). Perpetual: a paid maintenance plan | Premium Plus above Premium; Standard for some products |
| NICE | Paid yearly support | Not verified |
| Deepgram | Paid support contract | Not public |
| Sentry | No support at all | None |
| Iranian vendors (Sepidar, Mahak, Peyvandtel) | First year free, then a paid yearly contract | Rahkaran: four contract levels |

Five of the eight foreign vendors include a support level in the subscription or term license
(GitLab, Mattermost, Elastic, Rocket.Chat, and Verint for term licenses). NICE and Deepgram sell
support as a separate contract, and Verint does too for perpetual licenses. Sentry gives none.
The pattern "Standard included, faster tier paid" (S3) is Mattermost and Rocket.Chat. Iranian
vendors sell the license once and support yearly, so for them support is always a separate line
after year one (S2 shape).

**What support covers and excludes (common across vendors)**

- Covered: product bugs, product questions, diagnosis of the vendor's software, security fixes
  and updates for supported versions (GitLab, Elastic, Mattermost, Verint).
- Not covered: customer infrastructure and OS (GitLab, Verint), custom code or modified software
  (GitLab, Elastic), third-party software and integrations (Elastic, Mattermost), installation
  and upgrades as a hands-on service (Mattermost, Verint), unsupported versions (all), disaster
  recovery of the customer's database (Verint), training and professional services (all).
- Response time means **first response**, not resolution (GitLab, Elastic). Business hours are
  defined explicitly (GitLab 24x5 in Pacific time; Mattermost 9 to 5 local; NICE 8 to 5).

**Installation**

Every benchmark with evidence sells installation separately: GitLab Professional Services,
Mattermost services packages, Elastic consulting, Rocket.Chat statement of work, Verint (install
by Verint or a verified partner; patch installation for a fee), NICE (release installation at the
customer's cost). The blueprint agrees: "Separate fixed-scope fee, milestones and change control"
(`docs/planning/homayar-commercial-blueprint.md:663`).

**Supported versions**

| Vendor | Security fixes for | Long-term line |
|---|---|---|
| GitLab | Current plus previous two monthly releases | None |
| Mattermost | Previous 3 monthly releases | ESR every 9 months, 12 months support |
| Rocket.Chat | 6 months | LTS 12 months |
| Elastic | Last two minors of current major | Major: 30 months plus 6 |
| Genesys PureConnect | 18 months updates | Plus 18 months support |

The blueprint says start with "one supported LTS line" and "Never advertise an LTS duration the
team cannot staff" (`docs/planning/homayar-commercial-blueprint.md:462`, `:1510`).

**SaaS uptime SLA**

| Vendor | Public SaaS SLA |
|---|---|
| GitLab | 99.9%, Ultimate only, with credits |
| Mattermost | 99.9%, dedicated cloud add-on only |
| Elastic | 99.95%, top two tiers only |
| Rocket.Chat | 99.9%, Premium and Dedicated only; none for Standard |
| NICE CXone | 99.99% |
| Sentry | None found |

Mature vendors give an uptime SLA **only on their top tier**, and some give none. None of them is
at Homayar's stage. Homayar has nothing deployed
(`docs/handoff/session-reset/PROJECT_STATE.md:3-5`) and backup objectives are "set ... outside this
repository" (`docs/operations/backup-restore.md:9`).

## 6. Comparison table

| Criterion | P1 current plans | P2 blueprint | P3 recommended | P4 defer |
|---|---|---|---|---|
| Monthly and yearly SaaS (owner ask) | Monthly only | Yearly only | Both | None |
| Enterprise plan | Yes, with false claims | Yes | Yes | No |
| Billing unit follows cost | No (calls) | Yes (minutes) | Yes (minutes) | n/a |
| Honest today | No | Yes | Yes | Yes |
| Matches benchmarks | Partly | Yes | Yes | n/a |
| Number of packages | 3 plus many add-ons | 6 | 4 | 1 |
| Failure mode | Buyers see false claims; per-call margin swings with call length | No monthly entry; small teams cannot start | A monthly customer churns fast; allowance rules need WP18 | No revenue; buyers go elsewhere |
| Cost to reverse | Low (text change) | Low | Low: prices and plan names can change at renewal | Low |
| Migration between deployment types | Not addressed | Not addressed | Not built yet; see section 7.1 | n/a |

| Criterion | L1 subscription | L2 perpetual plus maintenance | L3 usage band | L4 free | L5 defer |
|---|---|---|---|---|---|
| Common among SaaS-plus-self-hosted vendors | Most common abroad | Retired by some foreign vendors; the Iranian norm | Used as sizing | Rare | n/a |
| Pays for ongoing AI model work | Yes | Only if maintenance renews | With L1 | No | n/a |
| Works fully offline | Yes, signed file | Yes | Needs local counter | Yes | n/a |
| Fits blueprint | Yes | Only with new versions tied to renewed support (`:698`) | Yes (`:460`) | No | Partly (`:1505`) |
| Failure mode | Customer stops renewing; read/export mode stops new work | Customer stops support, stays on old versions and models, still asks for help | Band too small or too large; disputes at renewal without audit rights | No revenue from self-hosted; support burden without payment | Private buyers lost |
| Cost to reverse | Low: a subscription ends by itself; terms can change at renewal | **High:** a perpetual right cannot be taken back; old versions stay in the field for years | Medium: bands can change at renewal | High: a free edition cannot be withdrawn | Low |
| Migration between deployment types | Needs a workspace export and import tool, not built | Same | Same | Same | n/a |
| Recommended | Yes, default | Yes, but on request only until buyer evidence exists | Yes, as sizing | No | No |

## 7. Recommendation

### 7.1 Plans

| Plan | Who | Billing | What is included | Support |
|---|---|---|---|---|
| **Paid pilot** | New buyer, SaaS or self-hosted | One-time fee, part credited to the first year (`docs/planning/homayar-commercial-blueprint.md:685`) | One queue, one scorecard, fixed minute cap, baseline and outcome report | Named contact, business hours |
| **SaaS Standard** | Small and mid teams | **Monthly** (no commitment) or **yearly** (prepaid, discounted) | Monthly audio-minute allowance; reviewer and admin seats (agents free); standard retention (proposal from blueprint: 30 days audio, 90 days transcript, `:686`); core QA, evidence, review, issue trends, exports | Standard |
| **SaaS Enterprise** | Large or regulated teams | **Yearly** contract, committed volume, quote | Larger allowance; SSO; custom retention; optional dedicated database and workers (`:430`); security documents that exist | Premium included |
| **Self-hosted** | Buyers who must keep data inside | **Yearly** subscription license, quote; or the perpetual form in section 7.2 | One production and one test environment; yearly audio-minute band; local models; signed offline updates | Standard included; Premium paid |

- Overage: charged per extra minute at a published rate (`content/legal/terms.md:21`,
  `docs/planning/homayar-commercial-blueprint.md:686`). For self-hosted, the local counter shows
  use; going over the band is settled at renewal, not by blocking processing. **Inference:**
  blocking an offline install mid-year would hurt the customer's operations and trust more than
  it protects revenue.
- Upgrades mid-term take effect at once and are prorated; downgrades at renewal. This is already
  in the FAQ (`content/faq-about.json:50`) and is reasonable.
- Do not sell nine modules. Sell the platform in these plans. Keep a small add-on list:
  extra minutes, extra retention, extra reviewer packs, Premium support, dedicated capacity,
  and services.
- **Sales gate.** Until the v0.8 gate, only the paid pilot can be bought. The blueprint allows a
  scoped paid pilot at v0.6 (`docs/planning/homayar-commercial-blueprint.md:1503`) and qualified
  private or cloud contracts only at v0.8, "limited to supported profiles" (`:1505`). The other
  plans are shown as structure with "Contact us" or "Available from v0.8". Online checkout stays
  off for them.
- **Moving between deployment types.** A customer may start on SaaS and move to self-hosted, or
  the other way. The repository has no whole-workspace export and import tool; the existing
  export is for one data subject only
  (`docs/architecture/decisions/0025-subject-scope-export.md:7-8`). **Inference:** a move needs
  such a tool (recordings, transcripts, reviews, scorecards, audit history), a re-install, and
  model version matching, because scores can differ between model versions
  (`docs/planning/homayar-commercial-blueprint.md:1510`). Until the tool exists, treat a move as
  a paid, scoped service, and do not promise it on the website.

**Monthly vs yearly (proposal, owner decides):** yearly prepaid SaaS Standard costs the same as
10 months of the monthly price (about 17% off). Reason: benchmarks that offer both give a yearly
or prepaid discount (Sentry, Elastic) but do not publish its size, so no market number was found.
"Two months free" is simple to explain. Monthly exists for small teams to start; Enterprise and
self-hosted are yearly only, like GitLab and Mattermost.

### 7.2 Self-hosted license

**L1 with L3 sizing:** a yearly subscription license, sized by a yearly audio-minute band and a
number of environments. Not by GPUs (`docs/planning/homayar-commercial-blueprint.md:460`).

- Delivered as a **signed offline license file** with expiry date and band. No phone-home.
  Usage counters are visible to the customer's admin. The customer may send a usage report at
  renewal (like GitLab's monthly usage file).
- **At expiry (proposal):** 30 days' warning in the admin console before expiry, then 30 days of
  full function after expiry (grace), then **read and export mode**: users can log in, view,
  search, export, and delete; new processing stops. No data is deleted. No remote switch.
  Renewal restores full function at once. The 30-day numbers follow GitLab and Elastic warning
  and renewal windows; they are a proposal.
- Why subscription is the default: AI models, Persian accuracy, and security fixes need
  constant work. A perpetual customer who stops paying maintenance still depends on that work.
  Large foreign vendors (Cisco, Atlassian) moved away from perpetual.

**Second purchase form: perpetual license plus yearly support (for Iranian procurement), on
request only.** The Iranian norm is perpetual plus a yearly support fee (section 5.4). Offering
only a subscription may block buyers whose procurement expects the NASR form. But demand for it is
an inference, not evidence, and a perpetual right cannot be taken back once granted. So this form
is sales-led: it is not on the pricing page or in the public FAQ until buyer interviews show that
target buyers need it (section 7.8). Rules (proposal):

- The perpetual license covers one major version and the same capacity band. It never expires.
  It keeps working without support. There is no read/export step, because nothing expires.
- The first year of support is mandatory and included in the purchase (it also covers the
  warranty period; NASR mentions a 6-month minimum warranty).
- From year two, support is a yearly fee in the NASR range (25% to 35% of the license price).
  **Proposal:** use the 35% level, which includes new versions and model updates. This removes
  the "undefined lifetime upgrades" risk the blueprint warns about (`:698`), because new versions
  come only while support is paid.
- If support lapses, updates, model updates, and support stop. Coming back needs a catch-up fee,
  as NASR describes and as Verint does ("Reactivation fee").
- **The NASR band does not fit an offline install as is.** The 25% to 35% band assumes remote
  access to the admin panel and the server (section 5.4). Homayar's design gives no standing
  remote access (section 7.3; `docs/planning/homayar-commercial-blueprint.md:452`). Under the
  NASR formula, no remote access adds 80% of the base, so about 45% to 63% of the license price.
  **Proposal:** quote the support fee for an offline install either with the NASR access
  coefficient, or at 25% to 35% plus paid on-site visits per case. The owner decides (section 9,
  question 6).
- **Inference:** priced over 3 to 4 years, the two forms should cost the buyer about the same.
  The owner and an accountant should set the exact ratio.

### 7.3 Support

**S3:** Standard support is included in every paid plan (SaaS and self-hosted). Premium support
is included in SaaS Enterprise and is a paid add-on for self-hosted.

| | Standard (included) | Premium (paid for self-hosted) |
|---|---|---|
| Channels | Ticket portal and email | Ticket, email, phone, shared chat channel |
| Hours | Business hours in Tehran time (days to be set by owner) | 24x7 for severity 1; business hours for others |
| Severity 1 first response (target) | Next business hours, target 4 business hours | Target 1 hour |
| Severity 2 | 1 business day | 4 business hours |
| Severity 3 | 2 business days | 1 business day |
| Severity 4 | 5 business days | 2 business days |
| Named engineer | No | Yes |
| Upgrade plan review | Yes (like GitLab) | Yes, plus a joint upgrade window |

These numbers are a **proposal**. They are slower than GitLab and Mattermost because Homayar has
no measured support capacity yet. Publish them as "targets" (best effort), not as an SLA, until
support hours per account are measured (`docs/planning/homayar-commercial-blueprint.md:757`).
Premium 24x7 must not be sold until someone is on call.

Severity definitions (plain words):

- **Severity 1:** production is down or unusable, or a security incident; no workaround.
- **Severity 2:** a main function is badly broken; a workaround is hard.
- **Severity 3:** a problem with a workaround, or a minor function broken.
- **Severity 4:** a question, a documentation issue, or a feature request.

"Response time" means the time until a person replies with a first useful answer. It is not a
promise to fix the problem in that time.

Support covers: Homayar software bugs, product questions, diagnosis from a sanitized support
bundle (`docs/planning/homayar-commercial-blueprint.md:458`), security fixes and updates for
supported versions, upgrade plan review, and AI model bundle questions.

Support does not cover: the customer's hardware, OS, network, GPU drivers not in the supported
matrix, database recovery from the customer's own backups, PBX or third-party system problems,
custom code or changed containers, unsupported versions, installation or upgrade done for the
customer, new integrations, training, and on-site visits. These are paid services.

Support requires: a supported version, a supported topology, and the official package unchanged
(like Verint, Rocket.Chat). Homayar does not need permanent remote access; any remote session is
approved by the customer, time-limited, and logged (`docs/planning/homayar-commercial-blueprint.md:436`,
`:620`).

### 7.4 Responsibility matrix

H = Homayar, C = customer, P = partner (if the customer buys through one). "H then C" means Homayar
provides the tool or package and the customer runs it.

| Area | SaaS | Self-hosted | Hybrid (local data, optional cloud management, `docs/planning/homayar-commercial-blueprint.md:468`) |
|---|---|---|---|
| Hardware, OS, network | H (through its cloud provider) | C (or P) | C |
| Database operation | H | C, using Homayar's package and runbooks | C |
| Backups and restore tests | H | C; H provides scripts (`docs/operations/backup-restore.md:14-21`) and restore guide | C |
| Security patches: Homayar app and images | H | H builds and signs; C installs | H builds; C installs |
| Security patches: OS and third-party | H | C | C |
| Homayar upgrades | H | H provides the package and migration; C schedules and runs; H reviews the plan | Same as self-hosted |
| AI model updates | H, with evaluation and release notes (`docs/planning/homayar-commercial-blueprint.md:1510`) | H ships signed model bundle; C approves and installs | Same as self-hosted |
| Monitoring and alerting | H | C (local dashboards, `:458`) | C; optional health counters to H if C allows |
| Incident response | H for the service; C for its own users | C for infrastructure; H for product diagnosis | Same as self-hosted |
| Data retention and deletion policy | C sets policy; H executes | C | C |
| User management and access | C (admin); H for platform access with approval | C | C |
| PBX integration | C gives access; H or P builds the connector (paid service) | Same | Same |
| License and usage counters | H | H issues file; C keeps it current | H issues file |
| Third-party components in the package (database, object storage, broker, model runtimes) | H | H selects, pins, and ships tested versions and passes on security fixes; C installs. Components with no upstream support (for example the archived MinIO community line, `docs/planning/homayar-commercial-blueprint.md:464`) must be replaced or named as unsupported | Same as self-hosted |

This split follows the blueprint ("customer owns hardware/network/backup target; Homayar owns
application package, migrations, model bundle and product diagnosis",
`docs/planning/homayar-commercial-blueprint.md:462`) and the benchmarks (Verint customer duties;
NICE customer duties; Elastic Cloud shared responsibility).

Managed self-hosted (Homayar operates the customer's install) is possible later as a paid
service, like Mattermost Managed Services. It is not recommended now.

### 7.5 Updates and supported versions

- Updates for self-hosted come as **signed offline bundles**: images with fixed digests, model
  files, checksums, SBOM (a software bill of materials, the list of all components), and
  migration and rollback notes (`docs/planning/homayar-commercial-blueprint.md:452`,
  `docs/planning/work-packages/WP19/README.md:12`).
- One supported release line at first (`docs/planning/homayar-commercial-blueprint.md:462`).
- **Proposal:** Homayar supports the current release and the one before it. A release gets
  security fixes until 6 months after the next release. End of support is announced at least
  6 months ahead. Grow toward a 12-month long-term line when the team can staff it (Mattermost
  ESR and Rocket.Chat LTS both use 12 months).
- An AI model update that changes scores needs evaluation and customer release notes, even if
  the API does not change (`docs/planning/homayar-commercial-blueprint.md:1510`). For
  self-hosted, the customer decides when to install it.

### 7.6 Installation and onboarding

- **Self-hosted:** installation is a separate fixed-scope paid service with milestones
  (`docs/planning/homayar-commercial-blueprint.md:663`). It includes preflight checks, install,
  one standard source connection, handover, and admin training. PBX work beyond one standard
  source, new integrations, and on-site visits are quoted separately.
- **SaaS Standard:** self-service onboarding with documentation and one kickoff session is
  included. **Inference:** a small monthly customer cannot carry a paid onboarding fee.
- **SaaS Enterprise:** onboarding is a paid, scoped service, or included when the yearly contract
  is large enough. Owner decides.

### 7.7 SLA today

- **SaaS now:** publish no uptime percentage. Publish support targets and a public status page
  when a service is live.
- **Later (proposal):** at v1.0, after at least 3 months of measured uptime and a tested restore,
  offer a monthly uptime commitment with service credits **on SaaS Enterprise only**. Choose the
  number from measured data. The blueprint's recovery proposal (RPO 24 hours or less, RTO 8
  hours or less) is a starting point to test, not a promise
  (`docs/planning/homayar-commercial-blueprint.md:444`).
- **Self-hosted:** uptime is the customer's. Homayar commits only to support response targets.

### 7.8 What would change this recommendation

- Evidence from real buyer conversations that target buyers need the perpetual form. Then show
  it on the pricing page. If most buyers only buy perpetual, make it the default for self-hosted
  and the subscription the option.
- Evidence that buyers need support as a separate invoice line even on the subscription. Then
  show license and Standard support as two lines, while still selling them together.
- Measured support cost much higher than planned. Then move from S3 to S2 (all support paid).
- A decision to open-source a community edition. Then expiry could fall back to that edition
  (Mattermost, Elastic model) instead of read/export.
- Sanctions or payment limits that block monthly card payments. Then SaaS monthly may need
  invoice-based payment, or only yearly.

## 8. What the website must show

### 8.1 Pricing page

- **Sales gate:** until v0.8, only the paid pilot has a buy or request button. Other plans show
  "Contact us" or "Available from v0.8". Checkout stays off for them (section 7.1).
- **Top:** "Start with a paid pilot" call to action, then the plans.
- **Toggle:** Monthly / Yearly. The toggle changes only SaaS Standard. Label: "Yearly: 2 months
  free" (if the owner accepts the proposal).
- **Checkout constraint:** the current checkout rejects carts that mix monthly and yearly items
  (`src/app/api/checkout/route.ts:79-92`). When checkout is turned on later, the toggle must put
  every item in the cart on the same interval, or add-ons must be sold on both intervals.
- **Deployment switch:** "Cloud (SaaS)" / "Self-hosted". Self-hosted shows one card: "Yearly
  subscription (support included)", "Contact us". The perpetual form is not shown (section 7.2).
- **Per plan card, list:**
  1. Who it is for.
  2. Audio minutes included (per month or per year), and the price of extra minutes.
  3. Reviewer and admin seats included (agents are free).
  4. Retention included (audio days, transcript days).
  5. Main features.
  6. Support level (Standard or Premium) with a link to the support policy.
  7. Deployment (cloud, dedicated option, self-hosted).
  8. Price, or "Contact us" for Enterprise and self-hosted.
- **Below the cards:** "What counts as an audio minute" (stereo is one minute; failed processing
  is not charged; reanalysis rules). These rules are a target policy until WP18 defines billable
  completion (`docs/planning/homayar-commercial-blueprint.md:1346`); label them "planned" until
  then. Add-ons list. Services list (installation, integration, training).
- **Remove or change:** every claim in the section 5.1.6 table, including the uptime SLA, SOC 2
  and pen-test claims, "unlimited", the support response times, the 15-minute acknowledgment,
  the air-gapped option (until WP19 passes), the 6 to 10 weeks and $18,500 deployment claim, the
  Stripe line, and the trust claims in `content/faq-about.json:35`. Also replace per-call units
  with audio minutes.

### 8.2 Deployment page (responsibilities)

- Three columns: Cloud, Self-hosted, Hybrid.
- For each: where data lives, who runs it, who updates it.
- The responsibility matrix in section 7.4, written as plain sentences.
- Self-hosted requirements: supported hardware and OS list (link, when qualified), offline
  operation and signed updates (label "planned" until WP19 passes), customer runs backups.
- "What happens when a license ends": warning, grace period, read and export mode, no data
  deleted, no remote switch. Label it "target policy" until WP19 builds it.
- Status labels for anything not yet qualified (air-gapped install, hybrid management), as the
  blueprint requires (`docs/planning/homayar-commercial-blueprint.md:900`).

### 8.3 Support policy page (outline)

1. Who gets support (all paid plans).
2. Support levels: Standard and Premium; channels and hours; time zone and working days.
3. Severity definitions (section 7.3).
4. First-response targets table; "response is not resolution".
5. What support covers.
6. What support does not cover, and how to buy services instead.
7. What you need for support: supported version, official package, a support bundle.
8. How to open a ticket; what to include; support bundle privacy (no audio, transcripts, or
   secrets).
9. Remote access rules: only with your approval, time-limited, logged.
10. Supported versions and end-of-support notice.
11. Security issues: how to report, how fixes are delivered.
12. When support or license ends.

### 8.4 FAQ (plain-language answers)

**Rule for the website unit:** an answer marked **[planned: WPnn]** describes behavior that is
not built yet. WP18 (usage and billing) and WP19 (offline package, licensing, upgrades) are
`not_started` (`docs/handoff/session-reset/PROJECT_STATE.md:100-101`). Such an answer goes live
only with a visible "planned" label, or after the named work package passes its acceptance
checks. The label text can be "This is our planned policy. It is not available yet."

1. **Do I have to pay for support if I self-host?** No separate payment for Standard support. It
   is part of the yearly license. Premium support costs extra.
2. **Who maintains a self-hosted install?** You run the servers, the operating system, the
   network, and backups. Homayar builds the software, the updates, and the AI models, and fixes
   Homayar bugs. **[planned: WP19]** for signed update and model bundles.
3. **[planned: WP19] What happens if we do not renew?** You get a warning first. After a short grace period the
   system moves to read and export mode. You can still log in, read, export, and delete your data.
   New analysis stops. Nothing is deleted. Homayar cannot switch your system off remotely.
4. **Can we buy a one-time (perpetual) license?** Our standard offer is a yearly subscription.
   If your procurement rules need a different purchase form, contact us. (Sales-led answer; see
   section 7.2.)
5. **Is installation included?** No. Installation is a separate fixed-price service, agreed
   before work starts.
6. **[planned: WP19] Does self-hosted need the internet?** No. It runs offline. Updates come as signed files you
   copy in.
7. **[planned: WP19] How do updates work?** Homayar publishes signed update bundles. You choose when to install.
   We review your upgrade plan if you ask.
8. **[planned: WP18] What is an audio minute?** One minute of recorded conversation. A stereo
   call counts once. Failed processing is not charged.
9. **Monthly or yearly?** Cloud Standard can be monthly or yearly. Yearly is cheaper. Enterprise
   and self-hosted are yearly.
10. **Do agents need seats?** No. Only reviewers and admins count.
11. **Do you offer an uptime SLA?** Not yet. We will publish one only after we measure it.
12. **Are you SOC 2 certified?** No. We will share the security documents that exist today.
13. **What happens to our data if we cancel cloud?** You can export it for 30 days, then it is
    deleted (`content/legal/terms.md:48`).
14. **Do you use our calls to train AI?** Not without your written opt-in
    (`content/legal/terms.md:48`).
15. **Can we move from cloud to self-hosted, or back?** Yes, as a paid, scoped project. Your data
    is exported from one and imported into the other. We agree the scope and price before we
    start. **[planned]** A move tool does not exist yet (section 7.1).
16. **Can we get a refund?** Answer pending: the Terms and the current FAQ disagree (section
    5.1.4). The owner and a lawyer must decide first.

## 9. Open questions

### For the owner

1. Accept the audio minute as the one billing unit?
2. Accept four plans (pilot, SaaS Standard, SaaS Enterprise, Self-hosted)? Plan names?
3. Yearly discount size (proposal: 2 months free)?
4. Support working days and hours in Tehran time? Who staffs support now?
5. Grace period length after license expiry (proposal: 30 days)?
6. Offer the perpetual purchase form (section 7.2), on request only? If yes, which NASR support
   level (25% or 35%)? For an offline install with no remote access, use the NASR access
   coefficient (up to plus 80%, about 45% to 63% of the license price) or the base band plus paid
   on-site visits? Should its first-year support run to the end of the fiscal year, as Sepidar
   does?
7. Is SaaS Enterprise onboarding included or paid?
8. Currency: rial, dollar, or both? The repository has a display rate only, "not an invoice
   promise" (`docs/market/pricing-benchmark.md:39-41`).
9. Does Homayar want resale partners for installation and on-site support?
10. Refunds: keep the FAQ's 30-day evaluation window and prorated refund on annual contracts
    (`content/faq-about.json:55`), or the Terms' "non-refundable" rule (`content/legal/terms.md:20`)?
    What applies to monthly plans?

### For a lawyer

1. Can Stripe or any foreign payment service be used (`content/legal/terms.md:17`)? Sanctions and
   eligibility (`docs/planning/homayar-commercial-blueprint.md:607`).
2. Is a read/export mode after expiry enforceable and fair under Iranian contract law and public
   procurement rules?
3. Is the NASR support tariff (25% to 35%) or the 6-month minimum software warranty binding on
   Homayar when selling to government or bank buyers? The Plan and Budget Organization circular
   that NASR cites was not read.
4. Recording consent, employee monitoring, and data retention duties for customers who self-host.
5. Model weight licenses: can Homayar redistribute the chosen models to self-hosted customers
   (`docs/planning/homayar-commercial-blueprint.md:452`)?
6. A self-hosted license agreement (EULA). The current Terms are written for the hosted service
   (`content/legal/terms.md:9`, `:17`) and do not cover installed software.
7. Audit rights for usage bands. Section 7.2 relies on a voluntary usage report. Can the contract
   require a yearly usage report or an audit, and how does that work offline?
8. Source-code escrow for enterprise and government buyers: needed, and on what release terms?
9. Refund terms: resolve the conflict between `content/legal/terms.md:20` and
   `content/faq-about.json:55`.
10. Who is liable for third-party components shipped in the package (open-source database,
    storage, broker, model runtimes), including components with no upstream support.

### For an accountant

1. VAT and other taxes on software licenses vs services in Iran.
2. Revenue treatment: yearly license vs installation services (one-time services are not
   recurring revenue, `docs/planning/homayar-commercial-blueprint.md:669`).
3. Exchange-rate adjustment clauses for multi-year contracts.

### For providers to confirm

1. Speech and LLM model providers: terms for on-premise redistribution.
2. Any cloud or hosting provider used for SaaS: their own uptime commitment, which caps what
   Homayar can promise.

## 10. Sources

All read on 2026-09-27.

Repository (base `1b34472`): `docs/planning/homayar-commercial-blueprint.md`,
`docs/market/pricing-benchmark.md`, `content/products/plans.json`,
`content/products/modules-analysis.json`, `content/products/modules-governance.json`,
`content/products/modules-revenue.json`, `content/i18n/products.fa.json`,
`content/faq-about.json`, `content/legal/terms.md`, `content/site-copy.json`,
`docs/handoff/session-reset/PROJECT_STATE.md`, `docs/planning/work-packages/WP19/README.md`,
`docs/operations/backup-restore.md`,
`docs/architecture/decisions/0010-usage-ledger-and-reconciliation.md`,
`Homayar/backend/services/tenancy_service.py`, `Homayar/backend/database/saas_models.py`.

External:

- https://about.gitlab.com/pricing/
- https://about.gitlab.com/dedicated/
- https://about.gitlab.com/services/
- https://support.gitlab.com/hc/en-us/articles/11626483177756-GitLab-Support (read in browser; automated fetch blocked)
- https://support.gitlab.com/hc/en-us/articles/11625911285404-Statement-of-Support (read in browser; automated fetch blocked)
- https://docs.gitlab.com/administration/license_file/
- https://docs.gitlab.com/subscriptions/manage_subscription/
- https://docs.gitlab.com/omnibus/update/package_signatures/
- https://docs.gitlab.com/policy/maintenance/
- https://handbook.gitlab.com/handbook/engineering/infrastructure-platforms/service-level-agreement/
- https://mattermost.com/pricing/
- https://mattermost.com/resources/help/support/support-terms/
- https://mattermost.com/resources/help/services/
- https://mattermost.com/platform/flexible-deployment/
- https://docs.mattermost.com/product-overview/cloud-subscriptions
- https://docs.mattermost.com/product-overview/self-hosted-subscriptions
- https://docs.mattermost.com/deployment-guide/air-gapped-operations
- https://docs.mattermost.com/product-overview/release-policy
- https://docs.mattermost.com/product-overview/editions-and-offerings
- https://www.elastic.co/pricing
- https://www.elastic.co/pricing/cloud-hosted
- https://www.elastic.co/subscriptions
- https://www.elastic.co/support_policy
- https://www.elastic.co/support/welcome
- https://www.elastic.co/support/eol
- https://www.elastic.co/consulting
- https://www.elastic.co/cloud/shared-responsibility
- https://www.elastic.co/docs/deploy-manage/cloud-organization/billing/billing-models
- https://www.elastic.co/docs/deploy-manage/license/manage-your-license-in-self-managed-cluster
- https://www.elastic.co/docs/deploy-manage/deploy/self-managed/air-gapped-install
- https://www.rocket.chat/pricing
- https://docs.rocket.chat/docs/our-plans
- https://docs.rocket.chat/docs/support-center
- https://docs.rocket.chat/docs/our-support-ticket-classification-system-and-slas
- https://docs.rocket.chat/customer-center/support-center/premium-support-plans/support-prerequisites-and-version-durability
- https://docs.rocket.chat/docs/downgrade-behavior
- https://docs.rocket.chat/docs/air-gapped-license
- https://docs.rocket.chat/docs/version-durability
- https://docs.rocket.chat/docs/rocketchat-cloud-hosting-service-level-agreement-sla
- https://sentry.io/pricing/
- https://docs.sentry.io/pricing/
- https://sentry.io/for/enterprise/
- https://sentry.io/terms/
- https://develop.sentry.dev/self-hosted/
- https://develop.sentry.dev/self-hosted/releases/
- https://open.sentry.io/licensing/
- https://sales.jetbrains.com/hc/en-gb/articles/207240845-What-is-a-perpetual-fallback-license-and-how-do-I-use-one
- https://www.atlassian.com/licensing/data-center-end-of-life
- https://www.atlassian.com/licensing/server-end-of-support
- https://www.verint.com/corporate/misc/verint-global-maintenance-and-support-plan-all-regions.pdf
- https://www.nice.com/products/recording-management/engage
- https://www.nice.com/company/legal/nice-enterprise-terms-and-conditions/on-premise-support-policy (severity and hours tables did not render; unverified)
- https://www.nice.com/company/sla-guarantee
- https://www.genesys.com/blog/post/all-in-on-genesys-cloud-cx
- https://all.docs.genesys.com/PureConnect_End_of_Life_Announcement/EOL/All_versions
- https://all.docs.genesys.com/special:repository/genesys_engage_&_pureconnect_lifecycle_policy.pdf?page=system/eol
- https://help.genesys.com/pureconnect/desktop/genesys_pureconnect_end_of_life_policy.htm
- https://support.avaya.com/css/public/documents/100081098
- https://www.cisco.com/c/en/us/products/collateral/unified-communications/hosted-collaboration-solution-contact-center/unified-contact-center-enterprise-eol.html (direct fetch 403; read through a summarizing fetch tool)
- https://developers.deepgram.com/docs/self-hosted-introduction
- https://developers.deepgram.com/docs/license-proxy
- https://deepgram.com/pricing
- https://deepgram.com/dedicated
- https://www.speechmatics.com/product/features-and-deployments
- https://www.speechmatics.com/pricing
- https://docs.speechmatics.com/deployments/container/licensing
- https://callminer.com/conversation-analytics/speech-analytics
- https://www.observe.ai/contact-center-security
- https://www.observe.ai/trust
- https://tariffs.irannsr.org/images/docs_attaches/NSR_Tariffs_1405_IT-Vol-2_R1V0-FINAL.pdf
- https://tariffs.irannsr.org/index.php/component/nikdesk/?view=docs&dotask=main_main
- https://www.citna.ir/sites/default/files/files/attachment/citna/NASR%20TARIFF%201403%20V1.pdf
- https://itunion.ir/نرخنامه-جدید-پشتیبانی-نرمافزار-بر/
- https://www.systemgroup.net/wp-content/uploads/2025/11/support-1404-3.pdf
- https://www.systemgroup.net/products/rahkaran-abri/
- https://www.sepidarsystem.com/support/
- https://www.sepidarsystem.com/faq/
- https://www.sepidarsystem.com/sepidar-price/
- https://www.mahaksoft.com/خدمات-پشتیبانی/
- https://www.mahaksoft.com/پشتیبانی/تمدید-پشتیبانی/
- https://peyvandtel.com/call-center-software/
- https://amerandish.com/farsava/?lang=en
- https://www.chargoon.com/blog/امکانات-سیستم-اتوماسیون-اداری-چار/
- https://www.andishehpardaz.ir/support/ (a vendor that publishes the NASR rules as its own rate card)

Pages that could not be read (claims from them are unverified): Sentry support articles on
sentry.zendesk.com (login wall); https://sentry.io/legal/sla/ (404); Rocket.Chat handbook support
page (403); GitLab support definitions page (403); NICE Sunset Policy and Verint Product Life Cycle
(customer portal login); Mattermost Cloud SLA page (not found).
