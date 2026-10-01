# Payment options for Homayar: gateways, direct debit, and invoice payment

Status: In Review

Date: 2026-09-27 (revised 2026-09-28 after review round 1; revised again 2026-09-28 for the
owner's request to find alternatives; revised 2026-09-28 after the review of that turn)

Author role: researcher (`pay-t1-res`, research team `pay-t1`). Base commit: `1b34472`.

## 1. Summary and recommendation

**Main finding: most of Homayar's likely prices do not fit in one online card payment.
That is true for every provider checked, not only ZarinPal and Zibal.**

- Every card gateway has a cap per payment:
  - ZarinPal: 100M toman.
  - Zibal: 200M toman.
  - Vandar: 50M or 100M toman (its pages disagree).
  - IDPay and PayPing: 50M toman.
- The buyer's bank also caps card purchases per day. A Central Bank circular of 1403 set
  200M toman per person per day (section 5.11).
- A direct contract with a bank PSP does not remove the payer's card cap (section 5.7).
- Homayar has no rial prices yet. At the repository's display rate, three kinds of sale
  are above every cap:
  - every yearly plan;
  - the Professional monthly plan;
  - every package in the blueprint.
  (Section 5.5.)

**The alternatives do not change that for large payments:**

- **Direct debit** (پرداخت مستقیم, automatic withdrawal from the payer's account under a
  signed mandate) is offered by Vandar, Jibit and Payman.
  - It is tied to a person's national ID, card and mobile.
  - Each bank sets its own limits. The example in Vandar's docs runs from 1M toman per
    withdrawal (Tejarat, Melli) to 100M toman per withdrawal (Ayandeh, Blu Bank). These are
    example values, not live ones.
  - The mandate belongs to a person, not a company. So it does not fit a company renewal,
    even where the bank limit is high enough (section 5.8).
- **Payment links** are suspended at ZarinPal "by order of the Central Bank". Zibal does
  not offer them (section 5.9).
- **Installment credit (BNPL)** exists only for consumers. No provider offers credit to a
  company that buys SaaS (section 5.9).

**The one alternative that does help is identified deposits** (واریز شناسه‌دار). The
buyer's bank transfer carries a deposit ID, so it can be matched to the invoice
automatically.

- Zibal, Jibit and Vandar sell this. Jibit and Vandar say there is no amount limit.
- All three need a bank account owned by a registered company (section 5.10).
- This turns the invoice path from a manual chore into an automated path.

**Recommendation:**

1. **Main path: pre-invoice, bank transfer with a deposit ID, and automatic matching.**
   - It covers yearly plans, Enterprise, self-hosted licenses, and anything above a card
     cap.
   - It is built first, as WP18 step 5 requires (section 7.1).
   - Buy the deposit-matching service. Do not build a bank connection yourself.
   - This path brings payer bank details and a money-moving API key to Homayar (item 6).
     The owner should accept that knowingly (Q41).
2. **Second path: one online gateway for small payments,** behind a provider adapter.
   - **First choice: Zibal.** Three reasons:
     - its identified-deposit API is in a public, machine-readable spec;
     - that same corporate-banking account also unlocks its documented refund API;
     - its per-payment cap may be the highest found (200M toman, from an archived page).
       This depends on Q21. Other providers say the Central Bank or facilitator rules
       set 50M or 100M, so Zibal's 200M is the figure to confirm (section 5.5).
     One provider then covers both paths. Being on Shaparak's archived list does not
     count as a reason. The archived page is only the first 12 of 21 names, in
     data-entry order (section 5.11).
   - **Second choice: Vandar.** All of its docs loaded. It has a gateway, a full
     direct-debit API, and identified deposits. Its gateway cap is lower and unclear (50M
     or 100M toman).
   - **ZarinPal is still a good gateway-only choice.** It has the best gateway docs and a
     live, confirmed 100M cap. No deposit-matching product was found for it, and its direct
     debit was removed.
   - **Five checks come before signing** (section 9):
     - Zibal's cap, confirmed live;
     - whether the corporate-banking key can be limited to reading deposits (Q42);
     - Zibal's current licence status (the listed validity date, 1405/05/27, has passed;
       several other active entries also showed passed dates, so this is a check, not a
       red flag);
     - Zibal's corporate-banking activation cost ("from 30M toman");
     - the reliability of Zibal's help site, which timed out on 2026-09-27 and 2026-09-28.
3. **No direct debit, payment links, or BNPL at launch.** Revisit direct debit only for
   monthly plans paid by a sole trader whose bank limit covers the plan (section 7).
4. **Subscriptions are prepaid periods.** Neither path charges automatically.
5. **Self-hosted licenses are signed files** checked offline. A self-hosted install never
   calls a payment provider.
6. **Card data never reaches Homayar through the gateway.** For a gateway payment, Homayar
   stores a payment reference, the amount, and the status. The identified-deposit service
   is different: it returns the payer's name, IBAN, and card number for each deposit.
   Section 7.4 says which of these to keep and which to drop after matching. Section 7.3
   covers the deposit API key, which can also move money out of the company account.

**Preconditions** (section 3):

- an Iranian host with a fixed outbound IP for the gateway calls, and a registered IP for
  the deposit API;
- a rial price list and an exchange-rate rule;
- e-Namad, a tax tracking code, and a bank account owned by the company.

Customers outside Iran are out of scope for launch (owner). Tax invoices (سامانه مودیان, the
Moadian e-invoice system) are Homayar's job, not the gateway's. They need an accountant.

Confidence: medium. Provider facts come from their own pages, read 2026-09-27 and
2026-09-28. Many Iranian sites did not load from the research machine. Where only an
archived copy or a secondary page was available, the text says so. Several legal and
regulator facts stay UNVERIFIED.

## 2. The decision, in the owner's words

Translated from the owner:

> "For cart and checkout, the options are zarinpal.com and zibal.ir. Dev docs:
> https://www.zarinpal.com/docs/ and https://help.zibal.ir/#docs. To choose the best option,
> compare these two providers, keeping in mind that we want to offer Homayar as SaaS and as
> self-hosted. Research which tools we will need. Data storage matters. Because the data is
> sensitive, it is better that it is not handled on our side, so that if we are ever hacked,
> people's data does not leak from us. If a user wants the SaaS service, they must buy a
> subscription, offered monthly and yearly. We also need an Enterprise plan."

After reading the first version (PR #9), the owner added: "Search and find alternatives."
The owner also confirmed that the product launches in Iran.

The question this spike settles: which payment path Homayar should use. The candidates are
ZarinPal, Zibal, another provider, several providers, or a different pattern for some
cases. It also settles what Homayar must build or buy to do three things without storing
card or other sensitive payment data:

- sell monthly and yearly SaaS subscriptions,
- sell Enterprise plans,
- sell self-hosted licenses.

## 3. Constraints

From the owner:

- Two candidate providers: ZarinPal and Zibal.
- Two products: SaaS (monthly, yearly, Enterprise) and self-hosted.
- Sensitive payment data should not be stored or handled by Homayar.

From this repository (base `1b34472`):

- **Backend billing is read-only and "manual" today.**
  - The billing router only has `GET` routes (`Homayar/backend/routers/v1/billing.py:64`,
    `:72`, `:98`, `:110`, `:142`).
  - The management response says `mode: "manual_read_only"` and
    `write_operations_enabled: False` (`Homayar/backend/schemas/billing.py:81-89`).
  - Plans are `pricing_mode: "manual_quote"` with `price_published: False`
    (`Homayar/backend/schemas/billing.py:14-15`).
- **The plan table has no price field** (`Homayar/backend/database/saas_models.py:731-739`).
- **Subscriptions already have a provider slot.** `provider` defaults to `"manual"`.
  There are also `provider_customer_id` and `provider_subscription_id` columns
  (`Homayar/backend/database/saas_models.py:883-885`). Nothing in the backend writes them.
  They appear only in the model and the first migration.
- **The self-hosted product must not call out.**
  - In the on-premise profile, ADR 0031 puts all outbound traffic behind a deny-by-default
    egress proxy (`docs/architecture/decisions/0031-egress-denial.md:50-65`).
  - The air-gapped default has "no outbound path at all"
    (`docs/architecture/decisions/0031-egress-denial.md:64-65`).
  - Non-AI outbound traffic is governed by the network layer only
    (`docs/architecture/decisions/0031-egress-denial.md:85-86`).
- **The SaaS profile has no designed outbound path yet.** In the cloud profile, egress
  control "must be enforced by the platform", through a proxy "you provide". The runbook
  adds: "This is not verified by the repository's tests"
  (`docs/operations/deployment.md:164-168`). So the SaaS outbound IP that a gateway would
  see is not designed yet.
- **SaaS hosting is not stated anywhere in the repository.** The only hosting that is
  documented is the static cPanel site (below) and a Vercel deploy of the old site
  (`README.md:79-87`).
- **Prices are USD list prices and are not validated.**
  - The blueprint calls its USD figures "**commercial experiments**, not validated market
    prices, active Homayar offers or conversion into Iranian currency"
    (`docs/planning/homayar-commercial-blueprint.md:681`).
  - The catalog lists USD prices (`content/products/plans.json:16`, `:59`;
    `content/products/modules-*.json`).
  - The rial rate in the repository is "a display reference, not an invoice promise"
    (`docs/market/pricing-benchmark.md:39-41`).
- **Billing is organization-level in the blueprint, but workspace-level in the code.**
  - The blueprint says: "Billing is organization/contract-level with workspace allocations"
    (`docs/planning/homayar-commercial-blueprint.md:438`).
  - `Subscription` is keyed on `workspace_id`
    (`Homayar/backend/database/saas_models.py:876-878`).
- **WP18 sets the build order.** Its step 5 says: "Generate approved annual/manual invoices
  and payment status before adding an eligible payment-service adapter"
  (`docs/planning/work-packages/WP18/README.md:15`).
  - Step 6: "Authenticate/deduplicate payment events and separate entitlement activation
    from untrusted callback content" (`:16`).
  - Acceptance rule F19: "startup does not overwrite negotiated entitlements" (`:33`).
- **The old public site sells with Stripe in USD, with automatic renewal.** (Addendum.)
  - Checkout builds USD prices with `recurring` intervals
    (`src/app/api/checkout/route.ts:96-121`) and creates a Stripe Checkout Session
    (`src/app/api/checkout/route.ts:124-137`).
  - The webhook checks the Stripe signature (`src/app/api/webhooks/stripe/route.ts:21-37`).
  - The legal terms name Stripe, USD, and automatic renewal
    (`content/legal/terms.md:17-20`).
  - The privacy policy lists Stripe as a subprocessor (`content/legal/privacy.md:50`).
- **The cPanel public site is static.** (Addendum.)
  - "cPanel can host only the static public site"
    (`docs/operations/deployment.md:272-274`).
  - Its build "temporarily excludes Next route handlers, forces commerce off"
    (`docs/operations/deployment.md:287-291`).
  - Note: the build script it names, `scripts/build-cpanel.mjs`, is not committed at base.
    CI skips that step when the file is missing (`.github/workflows/ci.yml:76-84`,
    `docs/operations/ci.md:48-51`).
  - So "commerce off" is not shown at base. The README still documents Stripe Checkout
    (`README.md:14`) and a Vercel deploy (`README.md:79-87`). The Stripe path is live
    wherever that Vercel deploy runs with Stripe keys. Whether such a deploy runs today is
    unknown.

**Preconditions for any gateway** (they come from the evidence in section 5):

1. **An Iranian host with a fixed outbound IP for the API calls** (request, verify,
   reverse). INFERENCE from two provider rules:
   - ZarinPal's terms 4-1 ban «انتقال ابزار پذیرش به خارج از کشور و یا استفاده از ابزار
     پذیرش در خارج از کشور» ("moving the acceptance tool abroad, or using it abroad").
     ZarinPal also requires registered IPs (section 5.1).
   - Zibal's archived setup article refuses IPs outside Iran (section 5.2).
   - The same "using the tool abroad" line is a merchant violation in a Central Bank
     procedure (section 5.11). So it is not only one provider's rule.
   - These rules point to an Iranian server for these calls. The provider should confirm
     whether a server abroad counts as "using the tool abroad" (section 9).
2. **A designed SaaS outbound path.** A proxy or NAT with a stable public IP to register
   with the provider. It does not exist yet (`docs/operations/deployment.md:164-168`).
3. **A rial price list and an exchange-rate rule** (section 7.1).
4. **Merchant documents:** e-Namad, a tax tracking code, and company documents (sections
   5.1, 5.2, 5.6 and 5.7).
5. **A bank account owned by the company.** Every identified-deposit service found needs
   one (section 5.10).
6. **A registered IP for the deposit API.** Zibal's corporate-banking API answers only
   requests from the IPs the merchant declared ([EBank spec]; section 5.10). So the deposit
   path needs the same designed outbound IP as precondition 2. Whether that IP must be in
   Iran is not stated (Q26).

Preconditions 5 and 6 are for the identified-deposit path. Precondition 2 (the designed
outbound path) is needed by both paths.

## 4. Options considered

Online card gateways (one per provider, or several):

- **A. ZarinPal.**
- **B. Zibal.**
- **C. Two providers at once** (for example, one as a failover for the other).
- **H. Vandar.** A licensed facilitator with a gateway, direct debit, and identified
  deposits (section 5.6).
- **I. Jibit.** A licensed facilitator. Its gateway, direct debit, and identified deposits
  are on its price list, but its developer docs did not load (section 5.6).
- **J. PayPing.** A licensed facilitator with a gateway, invoices, and payment links, but a
  50M toman cap and no direct debit (section 5.6).
- **K. A direct contract with a bank PSP** (a payment service provider, for example SEP,
  Pasargad, Parsian, or Irankish) instead of a facilitator (section 5.7).
- **L. Digipay.** A gateway layer over several PSPs, plus consumer credit (section 5.7).

Other patterns:

- **D. Invoice and bank transfer as the main path, plus one gateway for payments under its
  cap.** After the price check in section 5.5, this is the recommended shape.
- **M. Identified deposits** (واریز شناسه‌دار) to match bank transfers to invoices
  automatically. This is an add-on to D (section 5.10).
- **N. Direct debit** mandates for automatic renewals (section 5.8).
- **O. Payment links and online invoices** (section 5.9).
- **P. Installments through a BNPL or credit provider** (section 5.9).
- **G. Split one large bill into several gateway payments** (section 5.5). Not
  recommended without the provider's written approval.
- **E. Do nothing or defer.** Keep "manual" billing (`Homayar/backend/schemas/billing.py:40`)
  and sell every plan by invoice and bank transfer. Add a gateway later.
- **F. Keep Stripe.** Listed because the old site uses it.

Dropped early, with the reason:

- **IDPay:**
  - its certificate expired on 2025-05-27;
  - its newest blog post is from 1401;
  - its API URL redirects to the home page.
  INFERENCE: dormant (section 5.6).
- **Pay.ir:** its domain did not resolve. The archived pages since 2025-09 show no gateway
  (section 5.6).
- **NextPay:** its site did not load. The last archived content is from 2025-10-05
  (section 5.6).
- **Behpardakht Mellat, Asan Pardakht, Sadad** (as direct PSPs): their sites did not load,
  and no usable archived docs were found. They are UNVERIFIED, not rejected (section 5.7).

Option F is not realistic. Stripe's own restricted-business page says it does not serve
"Persons located in, resident in, or a citizen of, or products or services originating
from ... Cuba, Iran, North Korea, and Syria" (Stripe, "Prohibited and Restricted
Businesses", updated 2026-09-22). The operator in the terms is "KSF Company"
(`content/legal/terms.md:5`) with a `.ir` contact address. INFERENCE: this is an Iranian
service, so Stripe is not available. A lawyer should confirm this before the Stripe code
and terms are removed.

Option E becomes the first step of option D. WP18 already puts invoices and payment status
before any gateway adapter (`docs/planning/work-packages/WP18/README.md:15`).

## 5. Evidence

All external facts were read on 2026-09-27 unless a line says otherwise. Section 5.5 and
the ZarinPal terms were re-checked on 2026-09-28. "Wayback" means
a web.archive.org copy of the provider's own page. It is used only when the live page did
not load, and the copy date is given. 1 toman = 10 rial.

### 5.1 ZarinPal

**Payment flow** (docs: `paymentGateway/connectToGateway.html`):

1. Homayar's server sends `POST https://payment.zarinpal.com/pg/v4/payment/request.json`
   with `merchant_id`, `amount`, `description`, and `callback_url`.
   - Optional fields: `currency` ("IRR" or "IRT") and `metadata` (`mobile`, `email`,
     `order_id`).
   - The reply has `authority`, a 36-character string that starts with "A".
2. The buyer's browser goes to `https://payment.zarinpal.com/pg/StartPay/{authority}`.
   This is the hosted payment page. The buyer types the card there, not on Homayar.
3. The browser comes back to `callback_url?Authority=...&Status=OK|NOK`. The docs say to
   call verify only when `Status` is `OK`.
4. Homayar's server sends `POST .../pg/v4/payment/verify.json` with `merchant_id`,
   `amount` (in rial), and `authority`.
   - The reply has `code`, `ref_id`, `card_pan` (masked), `card_hash` (SHA256), `fee_type`,
     and `fee`.
   - Quote: «تنها یکبار کد100 رخ می‌دهد و در دفعات بعدی verify همان تراکنش، کد 101 اتفاق
     می‌افتد» ("code 100 happens only once; later verify calls for the same payment
     return 101").
- **If verify is late:** the money goes back to the buyer. Quote: «اگر این مرحله در
  بازه‌ی زمانی مقرر انجام نگیرد، مبلغ تراکنش به حساب خریدار بازگردانده خواهد شد» (docs:
  `moreFeatures/session-validation.html`). The length of this window is not stated.
  UNVERIFIED.
- **Other methods:**
  - `unVerified.json`: paid but not verified payments, "limited to the last 100".
  - `inquiry.json`: status check. Quote: "never use this method to verify".
  - `feeCalculation.json`: fee check.
- **Sandbox:** change the host to `https://sandbox.zarinpal.com/`. Any UUID works as
  `merchant_id` (docs: `paymentGateway/sandBox.html`).
- **API style:** REST with JSON, v4. The current docs describe no SOAP API.
- **Official SDKs** (docs: `/docs/sdkDocs/`; GitHub org `ZarinPal`):
  - PHP: last push 2025-08-30.
  - Node.js: last push 2025-07-01.
  - Android: last push 2025-08-27.
  - Python (`Zarinpal-Python-Sdk`): created 2025-06-25, last push 2025-08-03, needs
    Python 3.12 or newer. INFERENCE: it is young. A thin client of our own is safer.
- **Amount limits** (docs: `errorList.html`):
  - Maximum: error -41 says the maximum is 100,000,000 toman per payment.
  - Minimum: the fee page says the minimum is 1,000 rial.
- **Errors that matter for hosting** (docs: `errorList.html`):
  - -10: "merchant IP or merchant code not valid".
  - -14: the callback domain does not match the registered domain.
  - -18: the referrer does not match the domain.
- **IP allowlist is now required.** Quote: «طبق اعلام شاپرک از ۱۵ شهریور 1405 درخواست ایجاد
  تراکنش تنها در صورتی پذیرفته می‌شود که از IPهای ثبت‌شده پذیرنده ارسال شده باشد»
  ("per Shaparak, from 15 Shahrivar 1405, payment requests are accepted only from the
  merchant's registered IPs").
  - Shaparak is Iran's national card-payment network.
  - 15 Shahrivar 1405 is 2026-09-06. That is my conversion.
  - The limit is 5 IPs per gateway. You register the server's outbound IP.
  - Source: ZarinPal blog post, published 2026-09-02.
- **Automatic recurring charges ("Payman", direct debit):** not in the live docs.
  - The docs index (128 pages, built 2026-09-15) has no direct-debit page.
  - `/docs/directDebit/` now redirects to `/docs/`.
  - `/landing/direct-debit/` returns 404.
  - A Wayback copy dated 2025-07-18 shows the old API:
    - Activation was by support ticket.
    - A mandate lasted at least 30 days.
    - Each bank had a daily amount and count limit.
    - The merchant had to store a "signature".
    - The merchant had to give users a way to cancel, or «زرین پال میتواند کل سرویس را
      غیرفعال کند» ("ZarinPal can disable the whole service").
  - Current availability and fee: UNVERIFIED. INFERENCE: it was withdrawn or hidden
    after mid-2025.
- **Refunds:**
  - Reverse (docs: `moreFeatures/reverse.html`): «تراکنش های موفقی که از پرداخت آنها
    نهایت ۳۰ دقیقه گذشته باشد را بدون کارمزد به حساب خریدار سریعا استرداد بزنید»
    ("successful payments up to 30 minutes old can be returned to the buyer at once,
    without a fee"). It needs the server IP set on the gateway.
  - A refund API for later refunds is **not in the public docs**. It exists only in the
    official SDK source as a GraphQL mutation `AddRefund` on
    `https://next.zarinpal.com/api/v4/graphql/`, with a Bearer token.
    - Its input has an `amount` field. INFERENCE: partial refunds are possible.
    - Time limit and fee: UNVERIFIED.
  - The terms put refund responsibility on the merchant (`/terms`, section 5-6).
- **Fees** (`/pricing`, no date on the page):
  - «۰.۵٪ تا سقف ۱۶,۰۰۰ تومان + ۵۰۰ تومان ثابت» ("0.5% capped at 16,000 toman, plus
    500 toman fixed").
  - The first month is free, apart from the Shaparak fee.
  - Merchants with more than 150 payments a day should contact sales.
  - Who pays is set in the panel: the merchant or the payer (`fee_type`).
  - Whether VAT is added on top: UNVERIFIED.
- **Settlement.** The official pages disagree:
  - `/payment-gateway`: "one day after the payment, by 17:00".
  - FAQ: "by 11:00 the next day, not on public holidays".
  - `/landing/fast-settlement/`: direct Shaparak settlement in an early-morning cycle.
  - The terms say Shaparak settles the funds in its announced cycles (terms 4-5,
    updated 8 Tir 1404, which is 2025-06-29).
  - INFERENCE: expect next-day settlement and plan cash flow for the slowest case.
- **Merchant requirements** (`/terms`, section 3):
  - Individuals: 18 or older, national code, and contact details.
  - Companies: articles of association, the official gazette notice, and the signatory's ID.
  - Membership levels are "new, blue, silver". Error -16 blocks merchants below silver.
  - e-Namad: a ZarinPal blog post (modified 2026-09-20) calls it «یک ضرورت» ("a
    necessity") for any site that uses a gateway. Whether the terms or docs make it a hard
    rule: UNVERIFIED.
  - SSL is "not mandatory" per the docs. Homayar uses HTTPS anyway.
  - A checklist of website pages for review: not found. UNVERIFIED.
- **Banned uses** (`/terms` 4-1, 4-4):
  - currency exchange;
  - charging buyers extra;
  - «انتقال ابزار پذیرش به خارج از کشور و یا استفاده از ابزار پذیرش در خارج از کشور»
    ("moving or using the acceptance tool outside the country");
  - using the gateway on domains the merchant does not own.
  - There is no rule against SaaS. The gateway page lists «سرویس‌دهنده‌ها» (service
    providers) as target customers.
- **Data returned:**
  - Verify returns `card_pan` (masked, for example `502229******5995`), `card_hash`,
    `ref_id`, and the fee.
  - It does not return a mobile, email, or national ID.
  - The GraphQL session query can also return `payer_ip` and `rrn`.
  - The docs say: «پذیرنده و زرین‌پال از خریدار هیچ نوع اطلاعات مالی و بانکی دریافت
    نمی‌کنند» ("neither the merchant nor ZarinPal receives the buyer's card data").
- **Foreign cards:** the docs say payment is through «کارت‌های عضو شبکه شتاب» (cards of
  the Shetab network, Iran's domestic card network). No page says foreign cards work.
  INFERENCE: Iranian cards only.
- **Reconciliation:** GraphQL `Reconciliation` (payouts with `amount`, `payable_at`,
  `reference_id`, `reconciled_at`) and `Session` queries. They need an OAuth2 token. The
  docs say the token flow must run server-side. There are no webhooks.
- **Reliability:**
  - `status.zarinpal.com` returned HTTP 502 on the read date. Its contents are UNVERIFIED.
  - No uptime SLA was found.
  - The gateway page claims routing across six PSPs (payment service providers) with
    automatic failover. That is a marketing claim, so it is weak evidence.
  - ZarinPal's payment-link product is «موقتاً غیرفعال» ("temporarily disabled") by
    Central Bank order (`/zarinlink`). This is a sign of regulatory risk.

### 5.2 Zibal

The live help site (`help.zibal.ir`, including `#docs` and `/IPG/API`) **timed out** on
the read date (curl: no response in 30 seconds; WebFetch: HTTP 504). The current API docs
were read from Zibal's own machine-readable OpenAPI files, which the help site loads:

- `https://api.zibal.ir/static/helpdocs/ipg.json` (called "[IPG spec]" below)
- `https://api.zibal.ir/static/helpdocs/platform.json` ("[Platform spec]")
- `https://api.zibal.ir/static/helpdocs/ebank.json` ("[EBank spec]")

Help articles were read only as Wayback copies.

**Payment flow** ([IPG spec]):

1. Homayar's server sends `POST https://gateway.zibal.ir/v1/request` with `merchant`,
   `amount` (rial), and `callbackUrl`.
   - Optional fields: `orderId`, `mobile`, `allowedCards`, `nationalCode` (payment is
     blocked if the card owner's national code does not match), and `feeMode`.
   - The reply has `trackId` and `result: 100`.
2. The browser goes to `GET https://gateway.zibal.ir/start/{trackId}`. A `Referer` header
   is required. «دامنه آن با وب‌سایت ثبت‌شده درگاه شما مطابقت داشته باشد؛ در غیر این
   صورت کاربر به درگاه پرداخت منتقل نخواهد شد» ("its domain must match your registered
   site, or the user is not sent to the gateway").
3. The callback is a `GET` to `callbackUrl` with `success`, `trackId`, `orderId`, and
   `status` in the query string.
4. Homayar's server sends `POST https://gateway.zibal.ir/v1/verify` with `merchant` and
   `trackId`.
   - The reply has `paidAt`, `cardNumber` (masked), `amount`, `refNumber`, `orderId`,
     and `result`.
   - Result `100` means verified now. `201` means «قبلا تایید شده» ("already verified").
- **"Lazy" mode:** the callback is a JSON `POST` that adds `hashedCardNumber`. «در صورتی
  که پس از ۲۰ دقیقه پس از پرداخت تراکنش در درگاه پرداخت، تاییدیه پرداخت ارسال نشود، مبلغ
  تراکنش به شماره کارت پرداختی بازگشت داده می‌شود» ("if verify is not sent within 20
  minutes, the money goes back to the paying card").
- **Inquiry:** `POST /v1/inquiry` returns `createdAt`, `paidAt`, `verifiedAt`, and the
  status.
- **Sandbox:** «با قراردادن `merchant: zibal` تمام امکانات و سرویس‌ها را آزمایش کنید»
  ("test everything with `merchant: zibal`"). How the test payment page behaves is not
  described. UNVERIFIED.
- **API style:** REST with JSON, HTTPS only. The IPG call has no auth header. The
  `merchant` value is the secret.
- **SDKs** (GitHub org `zibalco`):
  - There are many samples and CMS plugins. The WooCommerce plugin was updated 2026-09-02.
  - The Python sample was last pushed in 2019. PyPI `zibal` 1.1.0 is from 2020-08-10.
  - The npm `zibal` package is maintained by `zibalco`.
  - INFERENCE: there is no maintained Python SDK, so we would write a thin client.
  - The official WooCommerce plugin sets `'sslverify' => false`. Do not copy that.
- **Amount limits:**
  - Minimum: more than 1,000 rial (result code 105, [IPG spec]).
  - Maximum: 200,000,000 toman per payment, and 200,000,000 toman per national code per
    day. Source: Wayback copy dated 2026-05-14 of
    `help.zibal.ir/article/how-get-and-set-up-online-payment-gateway/`.
- **IP rules:**
  - Result 115: «ip شما در پنل کاربری ثبت نشده است» ("your IP is not registered in the
    panel"), from [IPG spec].
  - The archived setup article says: «اتصال به درگاه با IP های غیر تعریف شده و همچنین IP
    خارج از ایران، امکان پذیر نمی باشد» ("connecting from unregistered IPs, or from IPs
    outside Iran, is not possible").
  - INFERENCE: the server that calls Zibal must have a registered Iranian IP.
- **Automatic recurring charges:** not in any of the three specs. A Zibal blog post titled
  «دایرکت دبیت چیست؟» (`zibal.ir/blog/direct-debit/`) returned HTTP 504 and has no Wayback
  copy. Everything about it is UNVERIFIED.
- **Refunds** ([Platform spec]):
  - `POST https://api.zibal.ir/v1/account/refund` (Bearer token). Quote: «امکان استرداد
    بخشی از مبلغ تراکنش در این روش وجود دارد. محدودیت زمانی و یا تعدادی روی درخواست ریفاند
    تراکنش وجود ندارد» ("partial refunds are possible; there is no time or count limit").
  - Prerequisite: «باید ابتدا دسترسی بانکداری شرکتی خود را فعال کرده باشید» ("you must
    first activate corporate banking").
  - Reverse returns the full amount through Shaparak, usually within one hour, and only on
    some PSPs.
  - Pricing: refund fee capped at 50,000 toman. Corporate banking «فعال‌سازی از ۳۰٬۰۰۰٬۰۰۰
    تومان» ("activation from 30,000,000 toman") (`/pricing`).
- **Fees** (`/pricing`):
  - «۱٪ هر تراکنش · سقف ۲۰٬۰۰۰ تومان» ("1% per payment, capped at 20,000 toman").
  - «به کارمزد هر سرویس ۱۰ درصد مالیات بر ارزش افزوده افزوده می‌شود» ("10% VAT is added
    to each service fee").
  - No setup or monthly fee.
  - The merchant pays the fee, either from the payment or from a prepaid fee wallet.
  - INFERENCE from the page's calculator settings: the minimum fee is 2,000 toman.
- **Settlement:**
  - `/ipg` FAQ: "amounts recorded by 21:00 are paid the next business-day morning".
  - The terms say "usually 1 to 3 business days".
  - An archived help article (Wayback, 2026-06-18) says settlement is free.
- **Merchant requirements** (`/ipg` FAQ): «برای دریافت درگاه، داشتن اینماد معتبر، کد
  رهگیری مالیاتی به نام متقاضی و حداقل ۱۸ سال سن الزامی است» ("a valid e-Namad, a tax
  tracking code in the applicant's name, and age 18 or older are required").
  - The archived setup article also asks for a business license from `mojavez.ir`.
  - It says a no-star e-Namad is enough.
  - It says the callback and referer must match the registered domain.
- **Website rules** (`/privacy-policy`, terms tabs):
  - Show a support phone, an email, and a contact link.
  - «شماره رسید تایید‌شده‌ی بانک باید به‌صورت شفاف در صفحه پرداخت نمایش داده شود» ("the
    bank's confirmed receipt number must be shown clearly on the payment page").
  - Show Zibal's terms to end users.
  - Identify your end users.
  - Banned categories include gambling, VPN tools, and unlicensed goods. SaaS is not
    listed.
- **Data returned:**
  - Verify and inquiry return a masked `cardNumber`, `refNumber`, the amount, and times.
  - Lazy mode adds `hashedCardNumber`.
  - The transaction report API with `verbose=true` also returns the payer's `mobile`
    ([Platform spec]).
  - The national code is an input check only. It is not returned.
- **Foreign cards:**
  - The payer guide (Wayback, 2026-05-14) needs «کارت بانکی عضو شتاب» (a Shetab card).
  - The `/ipg` page data says `"areaServed":"IR"`.
  - INFERENCE: Iranian cards only.
- **Reconciliation:** [Platform spec] has a transaction report, payout (checkout)
  reports, and wallet balance, all with Bearer tokens. There are no webhooks. Only the
  callback and Telegram, SMS, or email notices exist.
- **Reliability:**
  - `status.zibal.ir` returned only an empty app shell, and its data call returned an
    empty body.
  - `/status` is a description page.
  - The `/ipg` page claims a 99.9% payment success rate. That is marketing, so it is weak
    evidence.
- **Facilitator status:** «زیبال پرداخت‌یار رسمی بانک مرکزی ایران است» ("Zibal is an
  official Central Bank payment facilitator") (`zibal.ir`). A payment facilitator
  (پرداخت‌یار) is a licensed company that collects payments for many small merchants.

### 5.3 Iran tax, e-invoices, and consumer law

**Moadian law** (قانون پایانه‌های فروشگاهی و سامانه مؤدیان, approved 1398/07/21, text on
rc.majlis.ir, read 2026-09-27):

- **A gateway counts as a "store terminal".** Article 1(ب) defines پایانه فروشگاهی as
  «رایانه، دستگاه کارتخوان بانکی (pos) ، درگاه پرداخت الکترونیکی یا هر وسیله دیگری ...»
  (a computer, a card reader, an online payment gateway, or any other device).
- **A gateway receipt can count as an invoice, in some cases.** Article 1(ث): «در مواردی
  که از دستگاه کارتخوان بانکی یا درگاه پرداخت الکترونیکی به عنوان پایانه فروشگاهی استفاده
  می شود، رسید یا گزارش الکترونیکی پرداخت خرید صادره در حکم صورتحساب الکترونیکی است»
  ("when a card reader or gateway is used as a store terminal, its payment receipt counts
  as an electronic invoice").
- **Businesses that do not sell to end consumers must issue every invoice through the
  Moadian system.** Article 2: «مؤدیان مزبور موظفند کلیه صورتحساب های خود را ... از طریق
  سامانه مؤدیان صادر کنند».
- **Business buyers get the tax credit.** Article 5(ب): when the buyer is itself a member
  of the system, the seller's e-invoice goes to the buyer's folder and counts as the
  buyer's tax credit. INFERENCE: Homayar's business customers (call centers) will want a
  full e-invoice with their details, not only a gateway receipt.
- **Homayar must tell the tax authority about its gateways.** Article 10: taxpayers must
  report their bank accounts and «درگاههای پرداخت الکترونیکی» (payment gateways).
- **Gateway payments are visible to the tax authority.** Article 11:
  - The Central Bank links each gateway to the business's license and economic code.
  - It gives the tax authority account data online.
  - Its note bans connecting a gateway whose owner the tax authority has not confirmed.
- **Trusted companies.** Article 1(چ) defines «شرکتهای معتمد ارائه کننده خدمات مالیاتی»:
  licensed companies that help taxpayers issue e-invoices and install the equipment for it.

**Moadian facilitation law** (قانون تسهیل تکالیف مؤدیان, 1402, rc.majlis.ir):

- **Deadlines.** Article 1 set the dates. Legal entities had to issue e-invoices from
  1402/7/1, and all remaining persons from 1402/10/1. My reading of the date format: about
  2023-09-23 and 2023-12-22. INFERENCE: the obligation is already in force for Homayar's
  company.
- **Gateway reports go to Moadian daily.** Article 6 adds a note to article 5. The Central
  Bank must give every gateway payment report a unique tax number. It must send each day's
  reports to the Moadian system by the end of the next day.

What this means (INFERENCE, needs an accountant):

- The gateway reports each payment to the tax system.
- The gateway does not issue Homayar's full B2B tax invoice.
- The gateway does not charge or file VAT for Homayar.
- Homayar must issue its own e-invoices, or pay a trusted company to do it.

Could not be verified from primary sources:

- **VAT rate for 1405.** The page on rc.majlis.ir (`law/show/134203`) is the old 1387 VAT
  law (1.5% in its article 16). That law was replaced by the 1400 VAT law, whose text I
  did not reach. News sites disagree between 10% and 12%, and they are secondary sources.
  Zibal adds "10% VAT" to its own fee, but that is not the law. UNVERIFIED.
- **VAT exemption for software, SaaS, or knowledge-based (دانش‌بنیان) companies.**
  UNVERIFIED.
- **The invoice types.** Blogs name types 1, 2, and 3, where type 3 is a gateway receipt.
  I found no primary text. UNVERIFIED.
- **The Moadian API and how trusted companies connect to it.** UNVERIFIED.

**e-Commerce law** (قانون تجارت الکترونیکی, approved 1382/10/17, rc.majlis.ir):

- **Seven-day right to cancel.** Article 37: «در هر معامله از راه دور مصرف کننده باید
  حداقل هفت روز کاری، وقت‌برای انصراف (‌حق انصراف) از قبول خود بدون تحمل جریمه و یا ارائه
  دلیل داشته باشد» ("in every distance sale, the consumer has at least seven working days
  to withdraw, without a penalty or a reason").
- **When the seven days start, and refunds.** Article 38:
  - For services, the period starts on the day of the contract (38 الف).
  - It starts only after the seller gives the information that articles 33 and 34 require
    (38 ب).
  - The seller must return the full amount (38 ج).
  - Exceptions are set by a by-law (38 د). I did not read that by-law, so the exceptions
    are UNVERIFIED.
- **Required information.** Articles 33 and 34 list what the seller must give before the
  contract. Section 8 of this document uses that list.
- Whether these consumer rules apply to business buyers: needs a lawyer.

**e-Namad** (نماد اعتماد الکترونیکی, the government trust mark for online shops), from
`enamad.ir`, read 2026-09-27:

- It is «یک نشان دولتی» (a government mark), issued only by the E-Commerce Development
  Center of the Ministry of Industry, Mine and Trade.
- It is shown «به شكل يك علامت در بالای سایت‌ها» ("as a mark on the site").
- Businesses can get 2 to 5 stars through a rating process (`/AboutRating`).
- The FAQ and rules pages load their text with JavaScript, and I could not read it. The
  requirements for getting e-Namad are UNVERIFIED.
- Both providers say they need it (sections 5.1 and 5.2).

**Shaparak rules** (card-data storage, per-payment caps, recurring rules):
`www.shaparak.ir` redirected and was not read in depth. UNVERIFIED. The only Shaparak rule
confirmed here is the IP allowlist, and it comes through ZarinPal's own page.

### 5.4 Customers outside Iran (out of scope for launch)

The owner confirmed that Homayar launches in Iran. So this stays short.

- No provider checked says it accepts foreign cards. ZarinPal and Zibal say they take only
  Shetab cards, the cards of Iran's domestic card network (INFERENCE, sections 5.1 and 5.2).
- Stripe does not serve Iran (section 4).
- US rules bar US persons from buying Iranian-origin services (31 CFR 560.201 and 560.206).
- Any later foreign sales need a sanctions lawyer first (section 9).

### 5.5 Payment limits against Homayar's prices

**The limits:**

| Limit | Value | Source |
| --- | --- | --- |
| ZarinPal, one payment | 100,000,000 toman | error -41, `errorList.html` (live; re-checked 2026-09-28) |
| Zibal, one payment | 200,000,000 toman | Wayback copy (2026-05-14) of `help.zibal.ir/article/how-get-and-set-up-online-payment-gateway/`; the live page timed out again on 2026-09-28 |
| Zibal, per national code per day | 200,000,000 toman | the same archived page |
| Zibal, per merchant | may be set lower | the same archived page: «در صورتی که نوع کسب و کار شما، نیاز به حداکثر مبلغ مجاز در درگاه پرداخت نداشته باشد، سقف متناسب توسط زیبال روی درگاه شما لحاظ خواهد شد» ("if your business does not need the maximum, Zibal sets a suitable cap on your gateway") |
| Buyer, card purchases per day, per person (all cards) | 200,000,000 toman (2 billion rial) | Central Bank circular 22848/03 of 1403/02/04, Wayback copy (2025-04-18) of `cbi.ir/showitem/29680.aspx`: «حداکثر تراکنش خرید از هر کارت‌پرداخت و نیز کلیه کارت‌های پرداخت متعلق به مشتری حقیقی، به روزانه مبلغ دو میلیارد ریال افزایش می‌یابد» ("the purchase maximum per card, and for all cards of a natural person, rises to 2 billion rial per day"). The card-issuing bank enforces it. |
| The same, newer figure | 400,000,000 toman per person per day | **Secondary only.** A ZarinPal blog post (published 2026-07-19) and a news site both name a newer Central Bank circular (10 Esfand 1404). The circular itself was not read: cbi.ir timed out, and its 2026 archive copies are bot-check pages. |
| Other gateways, one payment | 50M toman (IDPay, PayPing); 50M or 100M toman (Vandar) | section 5.6 |
| Bank PSP pages, per card | 50M toman per card per day (SEP, June 2026 copy) up to 200M toman per day (Sepehr news, 2024) | section 5.7; the pages are from different dates and disagree |

**Is the per-payment cap a regulator rule or a provider choice? Still open.**

- Vandar says its caps come from rules. Both quotes are from Vandar's own pages, read
  2026-09-28:
  - `vandar.io/payment-gateway/`: «در حال حاضر طبق مقررات بانک مرکزی به‌ازای هر کد ملی
    امکان تراکنش اینترنتی تا سقف ۱۰۰ میلیون تومان وجود دارد و به این ترتیب سقف هر تراکنش در
    درگاه پرداخت وندار تا ۱۰۰ میلیون تومان تعریف شده است» ("per Central Bank rules,
    internet transactions per national code are capped at 100M toman, so Vandar's
    per-transaction cap is 100M").
  - `vandar.io/faq/`: «طبق قوانین حاکم بر حوزه‌ی پرداخت‌یاری ... حداکثر آن ۵۰میلیون تومان
    است» ("per the rules that govern payment facilitation, the maximum is 50M").
- These are provider claims about a rule. They are not the rule. The rule text was not
  found.
- The figures disagree: 50M (Vandar FAQ, IDPay, PayPing), 100M (Vandar gateway page,
  ZarinPal live), and 200M (Zibal, archived).
- So Zibal's 200M is the outlier to confirm, not an advantage to count on. "Highest cap"
  depends on Q21.

INFERENCE: the per-person card cap belongs to the **payer's** bank. So no merchant
contract, facilitator or direct PSP removes it. The payer here is an employee paying with
a personal card.

**Bank-transfer limits** matter for the invoice path, because a company pays an invoice by
SATNA or PAYA. SATNA and PAYA are Iran's interbank transfer systems.

- **Official, but older:** a Central Bank notice of 20 Dey 1400 set two limits. Source:
  Wayback copy (2024-09-18) of `cbi.ir/showitem/22624.aspx`.
  - The SATNA customer-order floor became 500 million rial (50M toman).
  - The PAYA cap became 1 billion rial (100M toman) per order.
- **Newer, secondary only:** ZarinPal blog posts (2026) and news snippets name a circular
  of Ordibehesht 1404. It gives a SATNA floor of 100M toman and a PAYA cap of 200M toman
  per order.
  - ZarinPal's own posts disagree on the year, the fees, and the hours.
  - The circular itself was not read.
- **Company accounts:** a Central Bank rule on commercial accounts (1401) says «تمامی
  حساب‌های سپرده بانکی اشخاص حقوقی، حساب تجاری محسوب می‌شوند» ("all bank accounts of
  legal persons count as commercial accounts"). Source: Wayback copy (2025-03-24) of
  `cbi.ir/showitem/24156.aspx`.
  - Its article 16 lists incentives that banks must apply "as suitable" to commercial
    account holders. One is raising non-in-person withdrawals up to 5 billion rial (500M
    toman) per day and 30 billion rial (3 billion toman) per month.
  - The same rule asks for supporting documents (a contract or the reason for the
    transfer) when a customer's commercial accounts send more than 10 billion rial (1
    billion toman) in a day. INFERENCE: Homayar's pre-invoice or contract is that
    document for the buyer.
- INFERENCE: a large yearly deal can be paid by several PAYA or SATNA orders, or by a
  SATNA at a branch. Each order carries the invoice's deposit ID (section 5.10). So one
  invoice can receive several deposits. Section 7.1 gives the invoice states for this.

**Homayar's prices.** Homayar has **no rial prices** yet. Setting them is an owner
decision (section 9).

- The table below converts the repository's USD list prices at its display rate of
  189,000 toman per USD (`docs/market/pricing-benchmark.md:39-41`). Neither the prices nor
  the rate is final (section 3).
- The point of the table is the **price levels** at which the gateway works. It is not a
  list of final prices.
- "Fits" means one payment is at or under the cap.

| Item (source) | USD list | Toman at 189,000 | One payment fits | Yearly (x12) fits |
| --- | --- | ---: | --- | --- |
| `homayar-starter` (`content/products/plans.json:16`) | 499 monthly | 94.3M | both | neither (1,131.7M) |
| `homayar-professional` (`plans.json:59`) | 1,499 monthly | 283.3M | **neither** | neither (3,399.7M) |
| `homayar-enterprise` (`plans.json:104`) | custom | quote | invoice path | invoice path |
| Monthly add-ons at 199 to 499 USD (14 items, for example `dispute-appeal-workflow` `modules-analysis.json:241`, `automated-qa-scorecards` `modules-analysis.json:151`) | 199 to 499 | 37.6M to 94.3M | both | neither (451.3M and up) |
| `ai-coaching-plans` (`modules-governance.json:247`) | 549 monthly | 103.8M | Zibal only | neither |
| `deal-risk-scoring` (`modules-revenue.json:62`) | 599 monthly | 113.2M | Zibal only | neither |
| `script-adherence-monitor` (`modules-governance.json:17`) | 699 monthly | 132.1M | Zibal only | neither |
| `pci-pii-redaction-engine` (`modules-governance.json:63`) | 849 monthly | 160.5M | Zibal only | neither |
| One-time audits and reports (`modules-analysis.json:374`, `modules-governance.json:109`) | 2,900 | 548.1M | neither | not applicable |
| Yearly add-ons (`modules-governance.json:201`, `modules-revenue.json:329`) | 4,800 and 7,200 | 907.2M and 1,360.8M | neither | not applicable |
| Implementation packages (`modules-governance.json:293`, `modules-revenue.json:284`) | 9,500 and 18,500 | 1,795.5M and 3,496.5M | neither | not applicable |
| Blueprint "SaaS Quality" (`homayar-commercial-blueprint.md:686`) | 1,500 per month, annual | 283.5M per month | neither | neither |
| Blueprint "SaaS Operations" (`:687`) | 7,500 per month | 1,417.5M per month | neither | neither |
| Blueprint "Private Standard" (`:689`) | 40,000 to 60,000 per year | 7,560M to 11,340M | neither | not applicable |
| Blueprint pilot (`:685`) | 3,000 to 8,000 once | 567M to 1,512M | neither | not applicable |

The 14 monthly add-ons in the "both" row are:

- `content/products/modules-analysis.json`: lines 17, 63, 107, 151, 197, 241, 285;
- `content/products/modules-governance.json`: lines 155, 339;
- `content/products/modules-revenue.json`: lines 17, 106, 150, 195, 239.

The rows are arithmetic on repository figures. They are not quotes.

**The price levels where each path works.** All of this is arithmetic at the display rate:

- **ZarinPal works for one payment up to 100,000,000 toman.** That is about USD 529.
- **Zibal works for one payment up to 200,000,000 toman.** That is about USD 1,058, if the
  archived cap still holds for Homayar.
- **A yearly plan fits in one payment only if its monthly price is small.** For ZarinPal,
  the monthly price must be under about 8.3M toman (USD 44). For Zibal, it must be under
  about 16.7M toman (USD 88). No catalog item or blueprint package is that cheap.
- **Anything above 200,000,000 toman needs invoice plus bank transfer.** This includes
  every yearly plan, every Enterprise or private deal, and a cart that adds several monthly
  items together.

**Split payments (option G).**

- In theory, one bill can be paid as several gateway payments, each under the cap. No
  provider page I read allows or forbids this.
- The ZarinPal limits post says the caps exist to prevent money laundering and large
  unmonitored transfers. INFERENCE: splitting one bill on purpose to get around the cap may
  break the provider's rules. Do not build it without written approval from the provider
  (section 9).
- The payer's daily limits still apply. Zibal allows 200,000,000 toman per national code
  per day. The Central Bank's card cap is 200,000,000 toman per person per day (1403
  circular; a newer 400M figure is secondary only). So a large bill would take several
  days and several cards.
- An installment plan in a contract is different. Each installment is its own invoice.
  If an installment is under the cap, it can be paid online. That is normal use, not a
  split.

### 5.6 Other payment facilitators

A payment facilitator (پرداخت‌یار) is a licensed company that runs a gateway for many
merchants on top of the bank PSPs. All pages below are the providers' own, read
2026-09-28, unless marked Wayback. Licence claims are the providers' own words. Section
5.11 covers what Shaparak's list shows.

**Vandar (vandar.io, docs.vandar.io). All pages loaded.**

- **Status:** «وندار «پرداخت‌یار» و دارای مجوز از بانک مرکزی است» ("Vandar is a payment
  facilitator licensed by the Central Bank") (`/faq/`). It is part of Ayandeh Bank's group.
- **Gateway:**
  - REST: `POST https://ipg.vandar.io/api/v4/send`, then redirect, then `/verify`.
  - A transaction expires after 20 minutes.
  - Verify returns a masked card, `cid` (a SHA256 of the card), and `mobile`.
  - There is a refund API, full or partial
    (`/v3/business/:business/transaction/:id/refund`).
  - A sandbox is given on request.
  - No official Python SDK was found.
- **Gateway cap: the pages disagree.**
  - `/payment-gateway/`: «سقف هر تراکنش ... ۱۰۰ میلیون تومان» ("per-transaction cap 100M
    toman"). The page gives the reason: «طبق مقررات بانک مرکزی» ("per Central Bank
    rules").
  - `/faq/`: «حداکثر آن ۵۰میلیون تومان» ("the maximum is 50M toman"). The page gives the
    reason: «طبق قوانین حاکم بر حوزه‌ی پرداخت‌یاری» ("per the rules that govern payment
    facilitation").
  - The docs say limits depend on the business level.
  - Both reasons are Vandar's claims about a rule. The rule text was not found (section
    5.5).
- **Gateway fee: the pages disagree.** `/pricing/` says 1%, from 1,500 to 15,000 toman.
  Other pages give lower figures.
- **Direct debit (پرداخت خودکار), with a full API:**
  - create a mandate, send the payer to the bank, verify, withdraw, revoke, refund;
  - fee: 2%, from 2,500 to 12,000 toman per withdrawal (`/pricing/`);
  - details in section 5.8.
- **Identified deposits:** «بدون محدودیت مبلغ» ("no amount limit") (`/cashin-code/`).
  Details in section 5.10.
- **Payment form (a payment link):** up to 50M toman per form. It needs e-Namad and an
  active gateway (`/form/`).
- **Requirements:** «برای فعال‌سازی درگاه پرداخت داشتن اینماد و کد مالیاتی الزامیست»
  ("e-Namad and a tax code are required"). Companies also send their national ID, the
  official gazette notice, and a letter. `callback_url` and Referer must match the
  registered domain. More than one IP can be allowlisted.

**Jibit (jibit.ir). Main site loaded. Developer docs (`docs.jibit.ir`,
`developers.jibit.ir`, `napi.jibit.ir`) timed out.**

- **Status:** «یکی از بزرگ‌ترین پرداخت‌یار‌های رسمی شاپرک» ("one of the largest official
  Shaparak facilitators") (home page).
- **Price list** (`/pricing/`, headed «به‌روزرسانی 1405», "1405 update"):
  - gateway: 1% up to 10,000 toman, for up to 200 transactions a day;
  - direct debit: «۲٪ حداقل: ۲۰,۰۰۰ ریال حداکثر: ۷۵,۰۰۰ ریال» (2%, from 2,000 to 7,500
    toman);
  - identified deposit ("دریافت وجه"): 0.03%, minimum 3,000 toman;
  - adding a bank account: 10M toman;
  - 10% VAT on top of all fees.
- **Identified deposits:** «بدون محدودیت در مبلغ تراکنش» ("no limit on the transaction
  amount") (home page).
- **Requirements:** e-Namad, a tax tracking code, and business documents.
  «امکان پذیرش تراکنش فقط از IPها و دامنه‌های تعیین‌شده» ("transactions only from the IPs
  and domains you set").
- **API, caps, direct-debit rules, and verify fields: UNVERIFIED**, because the docs did
  not load.

**PayPing (payping.ir). All pages loaded.**

- **Status:** «دارای مجوز رسمی پرداخت یاری از شرکت شاپرک» ("holds an official
  facilitator licence from Shaparak").
- **Cap:** «سقف آن 50 میلیون تومان برای هر تراکنش» ("the cap is 50M toman per
  transaction") (`/faq/`).
- **Fee:** 1% capped at 5,000 toman, plus 500 toman. Paid plans lower the cap on the fee.
- **API:** a public OpenAPI file (`statics.payping.ir/openapi.json`). Verify must happen
  within 10 minutes. Reverse is allowed within 30 minutes, only if it was enabled when the
  payment was created. The sandbox is marked "soon".
- **Recurring:** scheduled invoices, not direct debit. A payment link is sent on a
  schedule, and the customer pays by card each time.
- **Invoices and links:** a full invoice API. Its terms ban payment links on foreign social
  networks.

**IDPay (idpay.ir). Loaded, but the TLS certificate expired on 2025-05-27.**

- It claims to be «پرداخت یار رسمی بانک مرکزی» (an official Central Bank facilitator).
- Cap: «سقف آن 50 میلیون تومان» ("the cap is 50M toman") (`/faq`).
- Default settlement: «۱۴ الی ۴۵ روز کاری» (14 to 45 working days). It is next-day only
  with e-Namad and a tax code.
- Signs it is not maintained:
  - the expired certificate;
  - the newest blog post is from 1401;
  - the API URL redirects to the home page.
- INFERENCE: dormant. Dropped.

**Pay.ir: dropped.** The domain did not resolve on 2026-09-28. Wayback copies from
2025-09 onward show a one-line personal page, then an admin login page. There is no
gateway content.

**NextPay: dropped.**

- `nextpay.org` timed out. `nextpay.ir` returned HTTP 522.
- The last archived content (2025-10-05) says:
  - «پرداختیار رسمی شاپرک و بانک مرکزی با شماره مجوز 98/ص/1971» ("an official Shaparak
    and Central Bank facilitator, licence 98/ص/1971");
  - a cap of «تا سقف 100 میلیون تومان از هر مشتری خود در هر روز» ("up to 100M toman per
    customer per day").
- Current status: UNVERIFIED.

**Not checked for Vandar, Jibit, and PayPing** (listed as open questions, section 9):

- **Reliability signals.** Status pages, uptime history, or outage reports were looked
  for only at ZarinPal and Zibal (sections 5.1 and 5.2). For the other three: not
  researched (Q47).
- **Moadian e-invoices.** Whether any of these providers issues Moadian e-invoices for the
  merchant, or connects to a trusted company, was not checked. It was not searched for at
  ZarinPal and Zibal either. The working assumption stays: Homayar issues its own
  e-invoices (section 5.3) (Q46).

### 5.7 Direct bank PSP gateways and Digipay

A direct PSP gateway is a contract with one of Shaparak's 12 PSPs (section 5.11), not a
facilitator. Most PSP sites did not load from the research machine. Only `pec.ir` and
`irankish.com` loaded live. The rest come from Wayback copies of their own pages, with the
copy date.

- **No PSP page shows a recurring or direct-debit API for merchants.** Sadad Informatics
  (part of Bank Melli's group) lists «برداشت مستقیم» (direct withdrawal) as a product, with
  no API, fees, or limits (Wayback, 2025-12-08).
- **No page says a direct PSP merchant gets a higher payer cap than a facilitator
  merchant.**
  - The PSP pages repeat per-card caps. SEP's page (Wayback 2026-06-07) says: «طبق
    الزامات بانک مرکزی هر کارت بانکی روزانه حداکثر 500 میلیون ریال می تواند تراکنش
    الکترونیکی داشته باشد» ("per Central Bank rules, each card may make at most 500 million
    rial of electronic transactions a day").
  - Parsian's contract (1405) sets a per-merchant cap by type of business. It allows a
    higher cap for business types that Shaparak permits, if the merchant implements
    «تطبیق» (matching). INFERENCE: this means matching the card to the payer's national ID.
- **Fees are lower but make no real difference at these amounts.**
  - Parsian (`pec.ir/karmozd`, "last update 1404"): «0/02 درصد مبلغ خرید را تا سقف حداکثر
    40 هزار ریال» ("0.02% of the purchase, up to 40,000 rial", that is 4,000 toman).
  - Pasargad (Wayback 2025-10-03): up to 80,000 rial. The two pages disagree.
  - No page says the gateway is free for merchants who bank at that bank.
- **Contracts are heavier.**
  - Pasargad: «تا اطلاع ثانوی امکان ارائه سرویس به متقاضیان حقیقی وجود ندارد» ("until
    further notice, no service for natural persons"). So Pasargad is company-only.
  - Parsian: a five-year contract, and technical docs by email after approval.
  - Every PSP asks for e-Namad and company documents.
- **API quality varies:**
  - Pasargad: a public REST API on GitHub (`pep-ipg/Dorsa-IPG-pasargad`, last push
    2026-08-15).
  - Irankish: a public PDF guide (dated 1405/02/26), with modules that include Python.
  - SEP: REST, with a 30-minute verify window and reverse within 50 minutes.
  - Parsian: docs are not public.
- **Data rule:** SEP's technical document v4.1 says: «پذیرنده به هیچ وجه مجاز به ذخیره
  شماره کارت نیست. فقط مقدار هش شده ...» ("the merchant is never allowed to store the card
  number; only the hashed value ..."). This supports section 7.4.
- **Behpardakht Mellat, Asan Pardakht, Sadad PSP:** not loaded, and no usable archived
  docs. UNVERIFIED.

**Digipay (mydigipay.com). Live site timed out. Wayback copies of 2026.**

- Its gateway routes each payment to several PSPs. INFERENCE: it is a facilitator-type
  layer.
- Its credit products (BNPL, loans) are for **consumers**: «هر مشتری می‌تواند تا سقف 200
  میلیون تومان وام و 20 میلیون تومان اعتبار دریافت کند» ("each customer can get up to a
  200M toman loan and 20M toman credit") (Wayback 2026-08-31).
- The merchant gets the full price minus a fee, and does not carry the default risk.
- One marketing sentence mentions B2B installment credit. There is no product page, limit,
  or terms. UNVERIFIED.
- A FAQ category named «پرداخت مستقیم» exists, but its answers were not archived.
  UNVERIFIED.

### 5.8 Direct debit (پرداخت مستقیم)

Direct debit means the payer signs an electronic mandate once. The merchant can then take
money from the payer's bank account, within limits, without the payer entering a gateway
each time.

- **ZarinPal:** removed (section 5.1). The docs index (built 2026-09-15) has 0 results for
  «پیمان», "payman" and "direct debit". `/docs/directDebit/` redirects.
- **Zibal:** none found. Its blog post on direct debit returned 504 again. None of the
  three API specs has direct debit.
- **Vandar:** a full, documented API (`docs.vandar.io/direct_debit_service/...`).
  - To create a mandate you send `bank_code`, `mobile`, `count` (withdrawals per month),
    `limit` (rial per withdrawal), and `expiration_date`. Optional: `daily_limit` and
    `national_code`.
  - «هر کاربر مجاز به ایجاد یک قرارداد فعال نزد هر بانک به ازای هر کسب و کار» ("each user
    may have one active mandate per bank per business").
  - Withdrawals are closed from 23:00 to 06:00.
  - Onboarding: pre-register, a sales call, then a contract.
  - **Per-bank limits come from its bank-list API.** The docs show an example response in
    rial (`docs.vandar.io/direct_debit_service/banks`, re-read 2026-09-30). Converted to
    toman, per withdrawal:
    - Ayandeh and Blu Bank: 100M per withdrawal and 100M per day;
    - Keshavarzi: 15M;
    - most other banks, for example Post Bank: 10M;
    - Mellat: 3M;
    - Pasargad digital: 1.5M;
    - Melli: 1M per withdrawal, 10M per day;
    - Tejarat: `"max_withdrawal_amount_per_transaction": 10000000` (1M) per withdrawal,
      but `"max_withdrawal_amount": 1000000000` (100M) per day.
    So the range is 1M to 100M toman per withdrawal. These are example values. The live
    values come from the API.
- **Jibit:** priced (2% up to 7,500 toman per debit). No docs loaded. UNVERIFIED.
- **Payman (paymanpay.ir)**, a company that does only direct debit:
  - It says it serves «بیش از ۱4۰ کسب‌وکار» (140+ businesses) across 13 banks.
  - On limits: «این سقف، به‌طور کلی به سیاست‌های بانک و کسب‌وکار مد نظر شما ارتباط دارد»
    ("the cap depends on the bank's and the business's policy"). No number is published.
  - The payer needs a card and a mobile number registered to the same national ID.
  - Its CEO, on Payman's own blog: «پیمان، از یک زیرساخت غیرمتمرکز و خارج از شبکه پرداخت
    کشور (شاپرک) استفاده می‌کند» ("Payman uses a decentralized infrastructure outside the
    national payment network, Shaparak").
- **No Shaparak or Central Bank rule on direct debit was found.** Limits for direct debit
  are UNVERIFIED at the regulator level.

INFERENCE, why direct debit does not fit Homayar's large renewals:

- The mandate belongs to a **person**, identified by national ID, card, and mobile. A
  company paying from its own account does not fit this model. This is the main reason.
- The limits depend on the payer's bank. In Vandar's example, most banks allow 1M to 15M
  toman per withdrawal. Two banks (Ayandeh, Blu Bank) allow 100M toman per withdrawal.
  That would carry the Starter monthly plan (94.3M toman at the display rate, section
  5.5), but not a yearly plan or Professional monthly.
- The public use cases are small consumer debits.

It could fit a monthly plan paid by a sole trader, if the payer's bank has a high enough
limit and the provider confirms the live value. It does not fit a company paying from its
own account.

**If direct debit is revisited, two questions first** (section 9, Q43 and Q44):

- **What does a mandate store, and can it charge?** Vandar's mandate holds the payer's
  bank, mobile, optional national code, and the limits. The old ZarinPal API asked the
  merchant to store a "signature" (section 5.1). INFERENCE: that stored value can start a
  withdrawal, so it is a secret like a payment key. Not researched further.
- **National scheme or provider scheme?** Payman says it runs outside Shaparak (above).
  Vandar's mandate is "one per bank per business". No regulator rule was found. So it is
  UNVERIFIED whether a mandate can move from one provider to another.

### 5.9 Payment links, online invoices, and installment credit

- **Payment links:**
  - ZarinPal's ZarinLink: «این سرویس حسب دستور بانک مرکزی، موقتاً غیرفعال است» ("this
    service is temporarily disabled by order of the Central Bank") (`zarinpal.com/zarinlink`).
  - Zibal: «لینک پرداخت شخصی ارائه نمی‌شود» ("no personal payment links") (`zibal.ir/ipg`).
  - Vandar's payment form (up to 50M toman) and PayPing's invoices still appear on their
    sites.
  - The Central Bank order itself was not found. UNVERIFIED.
  - A Central Bank rule of 1397 (Wayback copy of `cbi.ir/page/18213.aspx`) says a
    facilitator may serve an internet payment only when the request comes from the address
    registered for the merchant. INFERENCE: this may be why links were suspended.
  - For Homayar, links would not help anyway. They carry the same card caps as the gateway.
- **Installment credit (BNPL):**
  - Digipay, SnappPay, and ZarinPal's «زرین پلاس» (Zarin Plus) are consumer credit, from
    20M to 50M toman per person, with bank loans up to 100M to 200M toman.
  - SnappPay's and Digipay's "organizational" products are credit an **employer gives its
    staff**. They are not credit for a company that buys from a vendor.
  - No provider page offers credit to a company that buys SaaS.
  - Supply-chain factoring exists (for example scfinance.ir), but it has no public fees
    and targets goods. UNVERIFIED for SaaS.
  - INFERENCE: installments for Homayar's buyers should be a schedule in the contract, each
    part paid by invoice (section 7).

### 5.10 Identified deposits: making the invoice path automatic

An identified deposit (واریز شناسه‌دار) is a bank transfer that carries a deposit ID
(شناسه واریز). Homayar puts a unique ID on each pre-invoice. The buyer adds it to the PAYA
or SATNA transfer. A service then reports each incoming deposit with its ID, so it can be
matched to the invoice without a person checking the bank statement.

| Provider | What it says | Fee | Needs |
| --- | --- | --- | --- |
| Zibal corporate banking (بانکداری شرکتی) | Deposits are identified «از طریق شناسه، شماره شبا یا کارت مبدا» (by deposit ID, source IBAN, or source card), from any bank. API: `GET /ebank/v1/account/identified-payment/list/` returns amount, `paymentId`, `refCode`, and the payer's `sourceName`, `sourceIban`, and `sourceCard` (the spec does not say the card is masked); `change-status` confirms or rejects (a reject returns the money to the payer) ([EBank spec]). | «۰٫۰۳٪ انتقال وجه» ("0.03% money transfer"); whether this is also the fee for an incoming deposit is unclear (Q23); «فعال‌سازی از ۳۰٬۰۰۰٬۰۰۰ تومان» ("activation from 30M toman"); plus 10% VAT (`zibal.ir/pricing`) | «تنها برای کسب‌وکارهای حقوقی امکان‌پذیر است» ("only for legal-entity businesses"); activation «بین ۲ تا ۲۰ روز کاری» (2 to 20 working days) (`zibal.ir/ebank`) |
| Jibit | «بدون محدودیت در مبلغ تراکنش» (no amount limit) | 0.03%, min 3,000 toman per deposit, and no maximum on the price list, so the fee grows with the invoice (for example 300,000 toman on a 1 billion toman invoice, my arithmetic); adding an account 10M toman; plus VAT (`jibit.ir/pricing/`) | a company account (Wayback FAQ); API docs did not load |
| Vandar Avand (بانکداری تعبیه‌شده, embedded banking) | «بدون محدودیت مبلغ» (no amount limit), via PAYA and SATNA; a webhook for deposits (`vandar.io/cashin-code/`, `docs.vandar.io/avand_service/...`) | 0.02%, from 2,000 to 15,000 toman (`vandar.io/avand/`); whether it applies to incoming deposits is unclear | «صرفا ... حساب بانکی متعلق به کسب‌وکار حقوقی» ("only an account owned by a company"); 4 to 20 working days; forms at a bank branch |

- INFERENCE: incoming deposits must be polled at Zibal, because its spec has a webhook only
  for outgoing transfers.
- **The deposit API needs a registered IP.** The [EBank spec] says: «زیبال تنها به
  درخواست‌هایی که از طرف IP های اعلامی شما ارسال شوند پاسخ خواهد داد» ("Zibal answers
  only requests sent from your declared IPs"). Its error 7 is «IP ارسال‌کننده درخواست
  نامعتبر می‌باشد» ("the sending IP is not valid"). Whether the IP must be in Iran is not
  stated (Q26).
- **The same API can move money out.** The [EBank spec] uses one `bearerAuth` key for
  the deposit list and also for `POST /ebank/v1/account/checkout/create/` («ثبت درخواست
  انتقال وجه», "create a money transfer"), the account balance, and the statement. So a
  leaked key can send money out of the company account, not only read deposits. Section
  7.3 covers this.
- **A reject moves money too.** `change-status` with a reject returns the deposit to the
  payer. Its response has a `refundRequestTrackerId` for «بازگشت وجه واریز به حساب مبدا»
  ("returning the deposit to the source account") ([EBank spec]).
- **The deposit record holds the payer's bank details:** `sourceName` (payer name),
  `sourceIban` (payer IBAN), and `sourceCard` (payer card number). The spec does not say
  the card number is masked. Section 7.4 says what to keep.
- **Vandar Avand has the same shape.** Its activation needs the bank forms for «انتقال
  وجه غیر حضوری» ("non-in-person transfers") (`vandar.io/avand/`). INFERENCE: its API can
  also move money out of the account.
- **Exit cost (lock-in).** The gateway sits behind an adapter, so it is cheap to change.
  The deposit provider is harder to change:
  - deposit IDs are printed on issued pre-invoices, so open invoices must be paid out
    under the old provider or re-issued;
  - the company account is linked to one provider, with branch forms (Vandar) and 2 to 20
    working days to activate (Zibal);
  - a new provider may charge its own setup fee (Zibal "from 30M toman"; Jibit 10M toman
    per account), and the old one is not refunded (INFERENCE).
  - INFERENCE: generate deposit IDs in Homayar, in a format any provider accepts, if the
    provider allows it. Ask each provider (Q23). The manual admin record stays as a
    fallback.
- Finnotech sells a bank-statement API for reconciliation. Its price is not public. It is
  an option for matching deposits on the company's own bank account.
- **This is the tool that makes invoice plus bank transfer workable at scale.** No card
  is entered on this path. But the deposit record can carry the payer's card number, IBAN,
  and name (above), so the path is not free of sensitive data. Section 7.4 handles it.

### 5.11 What the regulator's pages show

Shaparak and Central Bank sites did not load from the research machine. These facts come
from Wayback copies of the official pages, with the copy dates.

- **Shaparak's active facilitator list** (Wayback, 2026-02-18 and 2026-02-27, of
  `shaparak.ir/fa/companies/pardakhtyar`):
  - The page shows `total: 21` companies «فعال دارای موافقتنامه معتبر» ("active, with a
    valid agreement"). Only the first page (12 names) was archived.
  - The 12 archived records are ordered by the record's `createdAt` (2025-03-09, 06:58 to
    13:16). So which 12 names were archived depends on data-entry order. It does not
    depend on status or size (Wayback copy of 2026-02-27, re-read 2026-09-30).
  - **Zibal is on it:** «راهکار نوین زیبال فعال ... تاریخ پایان اعتبار : 1405/05/27»
    ("Zibal, active, validity end date 1405/05/27").
  - ZarinPal, Vandar, Jibit, PayPing, and the others were not on the archived page. They
    may be among the 9 names that were not archived. Their status on Shaparak's list is
    **UNVERIFIED**, not negative.
  - **So presence on this page does not separate Zibal from the others.** It is not used
    as a reason to choose Zibal (section 7).
  - Zibal's listed validity date (1405/05/27, about 2026-08-18) has passed. But several
    entries listed as "active" already showed passed dates on the capture date: Bitpay
    and Polcard show 1404/12/07 on a page captured 1404/12/08 (2026-02-27). Four others
    show dates from 1405/02/01 to 1405/03/04, which have also passed by now. Three
    companies (Zibal, Payweb, and Paystar) share 1405/05/27.
  - INFERENCE: the dates look like batch agreement terms that are renewed. A passed date
    alone is weak evidence of a lapse. Q22 is a check before signing, not a red flag.
  - An older list (Wayback, 2021) had about 98 names, including ZarinPal, IDPay, NextPay,
    Jibit, PayPing, Digipay, and Zibal.
- **Shaparak's PSP list** (Wayback, 2026-02-27) has 12 PSPs: Pasargad, Saman (SEP), Novin
  Arian, Parsian, Sadad, Faraprdazan Arvand Omid, Fanava Card, Sepehr, Irankish,
  Behpardakht Mellat, Electronic Card Damavand, and Asan Pardakht.
- **The card cap belongs to the payer:** circular 22848/03 of 1403 (section 5.5).
- **"Using the tool abroad" is a violation.** A Central Bank procedure for merchant
  violations, approved 1403/12/19, lists this as item 2-4: «انتقال ابزار پذیرش به خارج از
  کشور و یا استفاده از ابزار پذیرش در خارج از کشور» ("moving the acceptance tool abroad, or
  using it abroad").
  - The penalties include disabling the gateway and a blacklist.
  - It was read from a copy hosted by Vandar (`vandar.io/blog/wp-content/uploads/2025/05/`),
    not from cbi.ir. So it is SECONDARY hosting of a Central Bank text.
  - It does not mention hosting. INFERENCE: it supports precondition 1 (section 3).
- **Direct debit and payment links:** no official rule was found. UNVERIFIED.

## 6. Comparison

**Table 1: providers that are active and could be used.** IDPay, Pay.ir, and NextPay are
left out (section 4). Digipay is left out because its gateway is a layer over PSPs and its
credit is for consumers (section 5.7).

| Item | ZarinPal | Zibal | Vandar | Jibit | PayPing | Direct PSP (SEP, Pasargad, Parsian, Irankish) |
| --- | --- | --- | --- | --- | --- | --- |
| On Shaparak's archived active list (Feb 2026; page 1 of 2, in data-entry order, so this row does not separate the facilitators) | not on the archived page (UNVERIFIED) | yes, validity to 1405/05/27 | not on the archived page (UNVERIFIED) | not on the archived page (UNVERIFIED) | not on the archived page (UNVERIFIED) | all four on the PSP list |
| Max per payment | **100M toman** (live) | **200M toman** (Wayback 2026-05-14); may be set lower per merchant; to confirm (Q21), because other providers cite rules for 50M or 100M | 50M or 100M toman (pages disagree; both cite rules) | UNVERIFIED | 50M toman | per contract; payer card caps apply |
| Gateway fee at catalog amounts | 16,500 toman (VAT unknown) | 22,000 toman with VAT | up to 15,000 toman | up to 10,000 toman, plus VAT | up to 5,500 toman, plus VAT | up to 4,000 or 8,000 toman |
| Docs reachable on 2026-09-28 | **yes**, full; sandbox with any UUID | OpenAPI files yes; help site timed out | **yes**, full; sandbox on request | no (all three docs hosts timed out) | yes, OpenAPI file; sandbox "soon" | Pasargad and Irankish yes; Parsian private; SEP archived |
| Direct debit | removed | none found | **yes, full API** | priced; docs UNVERIFIED | no (scheduled invoices) | none found |
| Identified deposits (section 5.10) | none found | **yes**, public API spec; company only; activation from 30M toman | **yes** (Avand); company only; branch forms | **yes** (marketing and price list); API UNVERIFIED | none found | not offered by the gateway |
| Refund after the short reverse window | undocumented GraphQL call | documented API, needs corporate banking | documented API | offered; API UNVERIFIED | reverse within 30 minutes only | varies; Parsian has an online refund service |
| Payment links | suspended by Central Bank order | not offered | payment form up to 50M toman | not found | yes, invoice API | no |
| Data returned on verify | masked card, SHA256 hash | masked card; hash in lazy mode | masked card, SHA256 hash, mobile | UNVERIFIED | masked card, hash | masked card, hash (SEP, Pasargad, Irankish) |
| Needs | e-Namad (blog); company or personal documents | e-Namad, tax code, licence | e-Namad, tax code | e-Namad, tax code | company registration | e-Namad, company documents; Pasargad company-only |

**Table 2: patterns for Homayar's sales.**

| Pattern | One payment above 200M toman | Monthly renewals | Company buyer | Status today |
| --- | --- | --- | --- | --- |
| Card gateway (any provider) | no (caps) | yes, if under the cap, with a reminder each period | yes, but an employee pays with a personal card | works |
| Direct debit | no (per-bank limits of 1M to 100M toman per withdrawal in Vandar's example) | only where the payer's bank limit covers the plan | no (mandate is tied to a person's national ID) | Vandar live; Jibit and Payman unverified |
| Payment link | no (same caps) | no | yes | suspended at ZarinPal; not at Zibal |
| BNPL or credit | no | no | no (consumer and staff credit only) | not for B2B |
| Split payment | only with written provider approval, and the payer's daily caps still apply | no | yes | not recommended |
| **Invoice + bank transfer + identified deposit** | **yes** (PAYA or SATNA orders; no amount limit claimed by Jibit and Vandar) | yes (one invoice per period) | **yes** (paid from the company's own account) | works; needs a company bank account and a service |

**The gateway fee does not decide the pick.** At catalog-size amounts, every gateway fee
is flat at its cap. The fees run from about 4,000 to 22,000 toman per payment. That is
under 0.03% of even a 94.3M-toman Starter payment. ZarinPal's VAT on its fee is still
unknown.

**What decides the pick:**

- the per-payment cap, for payments that go online;
- whether the same provider can also match bank transfers (identified deposits);
- whether the docs can be read, and how reliable they are;
- the refund path;
- regulatory standing. The archived Shaparak page cannot show this for the facilitators,
  so it is a check before signing (Q22, Q30), not a point for one provider.

## 7. Recommendation

1. **Build the invoice path first, and make it automatic with identified deposits.**
   - The flow is:
     1. pre-invoice (پیش‌فاکتور), with the rial amount, how long it is valid, and a unique
        deposit ID (شناسه واریز);
     2. a contract or purchase order when the buyer needs one;
     3. the buyer's bank transfer by PAYA or SATNA, carrying the deposit ID;
     4. the identified-deposit service reports the deposit, and Homayar matches it to the
        invoice (an admin can also record a payment by hand);
     5. the Moadian e-invoice.
   - Installments are separate invoices on a schedule in the contract. No BNPL provider
     serves company buyers (section 5.9).
   - It covers yearly plans, Enterprise, self-hosted licenses, and anything above a card
     cap. INFERENCE from the display-rate table in section 5.5, with no sales data: that is
     most of the revenue.
   - This follows WP18 step 5 (`docs/planning/work-packages/WP18/README.md:15`).
   - **Buy** the deposit-matching service. Candidates:
     - Zibal corporate banking: public API spec; activation "from 30M toman"; 0.03% per
       money transfer (whether it is charged on incoming deposits is unclear, Q23).
     - Vandar Avand: docs loaded; forms at a bank branch; 0.02% (the same doubt).
     - Jibit: docs did not load; 0.03% per deposit with no maximum, so the fee grows with
       large invoices.
     All three need a bank account owned by the company. Start with the manual admin
     record, so the path works before the service is active (2 to 20 working days).
   - **The deposit service is sensitive.** It returns the payer's name, IBAN, and card
     number, and its API key can also send money out of the company account (section
     5.10). Buying it does not remove this data from Homayar. Sections 7.3 and 7.4 set the
     rules. This is the main exception to the owner's rule that sensitive data should not
     be handled on Homayar's side, and the owner should accept it knowingly (Q41, section
     9).
   - **Exit cost.** Changing the deposit provider later is harder than changing the
     gateway (section 5.10, "Exit cost").
   - Why business buyers fit this path:
     - They need a full e-invoice for their tax credit (Moadian article 5(ب), section 5.3).
     - They pay from a company account, which card gateways and direct debit do not
       serve well (sections 5.5 and 5.8).
     - INFERENCE, no source: larger company and government buyers often pay by purchase
       order or in installments. Government procurement was not researched (section 9).
2. **Then add one online gateway, through a provider adapter, for payments under its cap.**
   - **First choice: Zibal.** Reasons:
     - the same corporate-banking account gives both identified deposits (item 1) and its
       documented refund API;
     - one provider, one contract, one IP registration, and one set of secrets for both
       paths;
     - possibly the highest per-payment cap (200M toman). This depends on Q21: other
       providers attribute 50M or 100M caps to Central Bank or facilitator rules, so
       Zibal's figure is the one to confirm (section 5.5).
     - Shaparak's archived list is not a reason. It does not separate Zibal from the
       others (section 5.11).
     - Risks, to clear before signing (section 9):
       - its help site timed out on both research days;
       - its cap comes from an archived page;
       - its Shaparak validity date (1405/05/27) has passed. Other active entries also
         showed passed dates, so this is a routine check (Q22);
       - the activation cost of corporate banking, and whether its fee applies to
         incoming deposits (Q23);
       - whether the corporate-banking key can be limited to deposits only (Q42).
   - **Second choice: Vandar.**
     - All of its docs loaded.
     - It has a gateway, identified deposits, and the only direct-debit API that could be
       read.
     - Its gateway cap is 50M or 100M toman (its pages disagree), so fewer catalog items
       fit online.
     - It was not on the archived Shaparak page. That page shows only 12 of 21 names, so
       this is UNVERIFIED, not negative.
   - **Third choice: ZarinPal, as a gateway only.**
     - It has the best gateway docs and a confirmed live cap of 100M toman.
     - It has no identified-deposit product that was found, so deposit matching would need
       a second provider or Finnotech.
     - Its direct debit and payment links are off.
   - **Not chosen for launch:**
     - Jibit, because its docs did not load;
     - PayPing, because of its 50M toman cap;
     - direct PSP contracts, because they are heavier and lower fees make no real
       difference here (section 5.7).
     A direct PSP contract can be revisited if online volume grows.
   - "Two providers at once" (option C) is not worth it at launch. It would double the
     onboarding, the secrets, the IP registration, and the reconciliation work. The adapter
     keeps a second provider cheap to add later.
   - Split payments (option G) only with the provider's written approval (section 5.5).
3. **No direct debit, payment links, or BNPL at launch** (sections 5.8 and 5.9).
   - The main reason is that a mandate belongs to a person, not to a company.
   - Direct debit can be a later option for monthly plans paid by a sole trader, where
     the payer's bank limit covers the plan. Vandar's API would be the first one to test.
4. **Subscriptions are prepaid periods.**
   - A monthly plan under the cap is paid each month through the hosted page, or by
     invoice.
   - A yearly plan is paid by invoice, because no realistic yearly price fits one gateway
     payment (section 5.5).
   - Homayar sends reminders before the end of the period.
   - After the end date there is a short grace period (`PAST_DUE`). Then the subscription
     becomes `EXPIRED`.
   - There are no automatic charges. The old terms promise automatic renewal
     (`content/legal/terms.md:19`), so they must change.
5. **Self-hosted licenses are signed files checked offline (section 7.5).**
6. **Iran only at launch** (owner).

**What would change this recommendation:**

- **Rial prices set low.** If most plans come in under the gateway cap and yearly plans
  under about 8.3M toman per month (ZarinPal) or 16.7M toman per month (Zibal), the gateway
  becomes the main path.
- **Zibal checks fail.** If its cap is lower, its licence was not renewed, or its corporate
  banking is too costly, choose Vandar. Choose ZarinPal instead if gateway docs matter most
  and deposit matching is bought elsewhere.
- **Hosting.** If the API cannot run on an Iranian host with a fixed outbound IP, then no
  gateway fits (section 3, precondition 1). The deposit API also needs a registered IP:
  Zibal «تنها به درخواست‌هایی که از طرف IP های اعلامی شما ارسال شوند پاسخ خواهد داد»
  ("answers only requests from your declared IPs") ([EBank spec]; section 5.10). Whether
  that IP must be in Iran is open (Q26). Without a registered IP, the fallback is manual
  matching from the bank statement by an admin. The invoice path still works that way,
  but it is not automatic.
- **Direct debit with higher limits.** If a provider confirms, in writing, limits that
  cover Homayar's monthly prices for company payers, add optional automatic renewal. For a
  sole trader, a confirmed 100M toman per-withdrawal limit at the payer's bank (Ayandeh and
  Blu Bank in Vandar's example) would cover Starter monthly at the display rate.
- **Split payments.** If a provider approves in writing that one bill can be paid in parts,
  the gateway can cover larger bills. The payer's daily caps still apply.
- **B2B credit.** If a provider offers credit to company buyers for SaaS, it could replace
  contract installments.

### 7.1 How a gateway connects to this repository

All of this is proposed work. Nothing here exists yet.

- **Build order: follow WP18.**
  - Step 5 says invoices and payment status come "before adding an eligible
    payment-service adapter" (`docs/planning/work-packages/WP18/README.md:15`).
  - This document follows that order:
    1. invoices with deposit IDs, manual payment status, and the admin view;
    2. then the identified-deposit adapter (section 5.10);
    3. then the gateway adapter.
  - The invoice path is needed first anyway, because most amounts are above the cap
    (section 5.5).
- **Billing unit: organization or workspace? This is a decision to make, and it is open.**
  - The blueprint says: "Billing is organization/contract-level with workspace
    allocations" (`docs/planning/homayar-commercial-blueprint.md:438`).
  - The code keys `Subscription` on `workspace_id`
    (`Homayar/backend/database/saas_models.py:876-878`).
  - Proposal (INFERENCE):
    - key invoices and payments on the organization, which is the buyer and the payer;
    - keep subscriptions and entitlements per workspace, as the allocation;
    - link each payment to the subscriptions it pays for.
  - This decides the schema before any gateway work. It is listed in section 9.
- **New invoice and payment tables are needed.**
  - `Subscription.provider_subscription_id` (`Homayar/backend/database/saas_models.py:885`)
    fits a Stripe-style subscription.
  - An Iranian gateway has no subscription object, only one payment per period.
  - Keep `Subscription.provider` for the source of the period, for example `"zarinpal"`
    or `"manual"`. Leave the two ID columns empty.
  - Add an `invoices` table and a `payments` table (INFERENCE, design proposal):
    - `organization_id` (or `workspace_id`, per the decision above);
    - `invoice_id`, `plan_key`, `interval`;
    - `amount_rial` (integer), and the price source (see "Rial prices" below);
    - `method`: gateway or bank transfer;
    - `deposit_id` on the invoice: the unique شناسه واریز the buyer puts on the transfer;
    - `provider` and `provider_ref` (the `authority`, the `trackId`, or the deposit
      service's reference such as Zibal's `refCode`), unique together;
    - `status`, `ref_id`, `created_at`, `verified_at`, `recorded_by` (the admin, for a
      bank transfer).
  - **One invoice, several deposits.** A large invoice may be paid by several PAYA or
    SATNA orders (section 5.5). So each deposit is its own `payments` row, and the invoice
    has a state that comes from the sum of its matched deposits (INFERENCE, design
    proposal):
    - `open`: no deposit matched yet;
    - `partly_paid`: the sum is above 0 and below the invoice amount;
    - `paid`: the sum equals the invoice amount;
    - `overpaid`: the sum is above the invoice amount.
    - Only `paid` activates or extends a subscription or issues a license.
    - `partly_paid` and `overpaid` go to an admin queue. The admin asks for the rest, or
      returns the extra amount by a manual transfer, or records an agreed difference.
    - A part payment is never rejected automatically. At Zibal, a reject sends the money
      back to the payer ([EBank spec]), so a reject is a money movement (section 7.3).
- **Plan prices.** `Plan` has no price columns (`saas_models.py:731-739`). Add monthly and
  yearly prices in rial, or a separate `plan_prices` table. The API's `price_published:
  False` and `pricing_mode: "manual_quote"` (`schemas/billing.py:14-15`) change only for
  plans that have a price.
- **Rial prices and the exchange rate.** Both gateways charge in rial. The repository has
  only USD list prices and a display rate that is "not an invoice promise"
  (`docs/market/pricing-benchmark.md:39-41`). The same file says: "Contract documents must
  record their own agreed exchange rate".
  - Two ways to get a rial price:
    - **(a) a fixed rial price list**, set by the owner and reviewed on a schedule;
    - **(b) USD list prices converted** at a stated rate source.
  - Recommendation (INFERENCE): use (a) for the gateway plans.
    - The buyer sees one stable rial price before paying. The e-commerce law asks for all
      costs and the offer's validity period before the contract (law 33 د, هـ; section
      5.3).
    - It needs no live rate feed.
  - For invoices, fix the rate when the pre-invoice is issued. Keep it for the invoice's
    validity period. The contract records it.
  - On every invoice and payment row, store:
    - `amount_rial`;
    - the price-list version;
    - for a converted price, the USD amount, the rate, the rate source, and the rate date.
    This lets a later reader explain every amount. That is WP18's "done when" rule
    (`WP18/README.md:19`).
  - Choosing (a) or (b), and who sets the rate, is an owner decision (section 9).
- **Negotiated entitlements are safe from payment events.**
  - F19 requires that "replayed payment events do not duplicate activation/credit" and
    that "startup does not overwrite negotiated entitlements" (`WP18/README.md:33`).
  - Step 6 says to "separate entitlement activation from untrusted callback content"
    (`:16`).
  - Proposal (INFERENCE):
    - A verified gateway payment may only extend the period of a self-serve subscription
      that was bought through that gateway. It never writes entitlements.
    - Entitlements come from the plan version, or from the signed contract for Enterprise.
      `Entitlement.source` already records where a limit comes from
      (`saas_models.py:901`).
    - A contract subscription (`provider = "manual"`) ignores gateway events completely.
    - Payment rows are append-only, so a replay finds the existing row and changes nothing.
- **Periods and grace.**
  - `get_current_billing` already treats `ACTIVE`, `TRIALING`, and `PAST_DUE` as current
    (`Homayar/backend/services/billing_service.py:17-21`).
  - It already respects `starts_at` and `ends_at` (`billing_service.py:66-70`).
  - A verified payment creates or extends a subscription period.
  - A scheduled job moves an unpaid subscription to `PAST_DUE`, then to `EXPIRED`
    (`saas_models.py:89-94`).
  - ADR 0010 leaves billing periods to WP18
    (`docs/architecture/decisions/0010-usage-ledger-and-reconciliation.md:68-71`). This
    work belongs there.
- **Idempotency.**
  - Reuse the ledger idea from ADR 0010: one stable business key per operation, unique in
    the database (`saas_models.py:1440`, `:1454`; ADR 0010 lines 19-25).
  - For payments, the key is `(provider, provider_ref)`. A second callback or a verify
    reply of `101` (ZarinPal) or `201` (Zibal) must not extend the period twice.
- **Reconciliation is separate from charging, as in ADR 0010.** "Reconciliation never
  creates a customer charge" (ADR 0010 lines 49-50). A payment reconciliation job should
  do four things:
  - read the provider's list of paid but unverified payments (ZarinPal `unVerified` and
    `inquiry`; Zibal `inquiry` and its transaction report);
  - verify payments that were paid but never verified (for example, the buyer closed the
    browser), before the provider returns the money;
  - compare payouts with the provider's payout data (ZarinPal GraphQL `Reconciliation`;
    Zibal checkout reports);
  - read the identified-deposit list (for example Zibal
    `GET /ebank/v1/account/identified-payment/list/`) and match each deposit to the
    invoice with the same deposit ID. Add it to that invoice's sum and set the invoice
    state (`partly_paid`, `paid`, or `overpaid`, above). Use the deposit's `refCode` as the
    unique key, so a deposit seen twice is counted once. Flag a part payment, an
    overpayment, or an unknown ID for an admin. Never reject a deposit automatically.
    This is the main path, so it matters most.
  - ADR 0010 lists "invoice reconciliation" as not covered (line 70). This job fills that
    gap.
- **Who may pay.** `Permission.BILLING_MANAGE` already exists
  (`Homayar/backend/security/authorization.py:68`) and guards the management route
  (`routers/v1/billing.py:144`). Payment start routes should require it.
- **Emails.** SMTP settings exist (`Homayar/backend/config.py:229-233`) for reminders and
  receipts.
- **Egress.**
  - On the SaaS deployment, add only the payment hosts to
    `infra/egress/allowlist.txt`, next to the other optional non-AI providers the file
    already lists (`infra/egress/allowlist.txt:10-13`):
    - for ZarinPal: `payment.zarinpal.com:443`, plus `next.zarinpal.com:443` only if the
      GraphQL reconciliation is used;
    - for Zibal: `gateway.zibal.ir:443`, plus `api.zibal.ir:443` for its reports,
      refunds, and identified deposits;
    - for Vandar, if chosen instead: `ipg.vandar.io:443` and `api.vandar.io:443`.
  - **The IP to register is not designed yet.**
    - ADR 0031's single `egress-proxy` with one route out describes the **on-premise**
      Compose file (`0031-egress-denial.md:50-57`).
    - The SaaS deployment is the one that takes payments. It uses the cloud profile, where
      egress "must be enforced by the platform" through a proxy "you provide". That is
      "not verified by the repository's tests" (`docs/operations/deployment.md:164-168`).
    - So the stable public IP for the provider allowlist depends on a SaaS egress proxy or
      NAT that does not exist yet.
    - This is a deployment prerequisite (section 3, precondition 2). It must be on an
      Iranian host (precondition 1).
  - The on-premise and air-gapped profiles never get these lines.

### 7.2 What the callback needs from hosting

- The callback is a browser redirect (`GET` with a query string) to a URL on the
  **registered domain** (ZarinPal error -14; Zibal archived setup article). It must run
  server code, because it calls verify with the secret `merchant_id`.
- The static cPanel site cannot do this. It has no route handlers
  (`docs/operations/deployment.md:272-274`, `:287-291`).
- The container deployment can. It serves `/`, `/app/`, and `/api/v1/` from one origin
  through nginx (`docs/operations/deployment.md:5-10`). So the request and callback routes
  belong in FastAPI, for example `POST /api/v1/billing/payments` and
  `GET /api/v1/billing/payments/{provider}/callback`.
- The server that calls `request` and `verify` needs a **fixed outbound IP** registered
  with the provider (at most 5 for ZarinPal).
- INFERENCE: for either provider, that server should be in Iran:
  - Zibal refuses IPs outside Iran.
  - ZarinPal's terms ban using the acceptance tool abroad (section 3, precondition 1).
- If the public site stays on cPanel at one host name and the app runs on another, the
  gateway must be registered to the host that serves the callback. Whether a subdomain
  counts as "the registered domain" is UNVERIFIED. Ask the provider.
- Checkout starts from the logged-in console (`/app/`), not from the static pricing page.
  The static pricing page only links to it.

### 7.3 Security of the integration

1. **Verify before you deliver.** Only a server-side verify reply of `100` (or a `101` or
   `201` that matches a payment already marked paid in our database) may extend a
   subscription. Never trust `Status=OK` or `success=1` in the callback query. Anyone can
   type those.
2. **Amount check.**
   - Send verify the amount from our own `payments` row, never from the request.
   - Check that the returned amount equals it. ZarinPal returns error -50 on a mismatch.
     Zibal returns `amount`, which we must compare.
   - Always send `currency: "IRR"` to ZarinPal. The request page does not state the
     default unit.
3. **Idempotency and replay.** Use a unique `(provider, provider_ref)` key and lock the row
   during verify. A replayed old callback finds the row already `paid` and does nothing
   more.
4. **Callback tampering.**
   - Look up the payment only by the `authority` or `trackId` we stored.
   - Bind it to the billing owner from our row (the organization or the workspace; the
     billing unit is still open, section 7.1 and Q3), not from the query or the browser
     session. The buyer may come back with no session.
   - Use a random, opaque `order_id`.
5. **Late verify.** The provider returns the money if verify does not happen in time
   (ZarinPal: window UNVERIFIED; Zibal lazy mode: 20 minutes). So:
   - the callback verifies at once;
   - a background job retries using `unVerified` and `inquiry`.
6. **Secrets.**
   - Keep `merchant_id` (ZarinPal) and `merchant` (Zibal) in server settings only, like
     other secrets.
   - Never put them in the console or the static site.
   - ZarinPal's GraphQL token must be obtained server-side (docs: `apiDocs/connect.html`).
7. **IP allowlist.** Register only the egress IP (section 7.1).
8. **TLS.** Keep certificate checks on. Zibal's own plugin turns them off (section 5.2).
9. **Logs.** Log `provider_ref`, status, and amount. Do not log `card_hash` or tokens.
10. **The identified-deposit API key can move money.** At Zibal, the same `bearerAuth`
    key that reads deposits can also create a money transfer out of the company account
    (`checkout/create`), and read the balance and statement ([EBank spec]; section 5.10).
    So treat it as the most sensitive secret Homayar holds.
    - Keep it in server secrets only. Only the reconciliation job reads it. Never put it
      in the console, the static site, or logs.
    - Ask the provider for a read-only or deposit-only key (Q42). INFERENCE: the spec's
      error 4, «اجازه دسترسی به این سرویس صادر نشده‌است» ("access to this service has not
      been granted"), hints that permissions are set per service. That is not confirmed.
    - Register only the egress IP for this API. It answers only declared IPs ([EBank
      spec]).
    - Homayar's adapter calls only the deposit list. It does not implement transfer,
      reject, or refund calls. Returning money for a bank-transfer payment is a manual
      step by an admin. (Gateway refunds use the separate refund API, section 5.2.)
    - Idempotency: the deposit's `refCode` is unique in the `payments` table. A deposit
      seen twice is counted once.
    - **Never reject a deposit automatically.** A reject returns the money to the payer
      (`refundRequestTrackerId`, [EBank spec]). So it is a money movement. A part payment,
      an overpayment, or an unknown ID goes to an admin (section 7.1).

### 7.4 Data: what Homayar stores and what it never stores

- **Never reaches Homayar through the gateway**, because of the hosted page: the full card
  number, CVV2, expiry date, PIN, and one-time password. The ZarinPal docs confirm this
  (section 5.1). The identified-deposit path is different (below).
- **Store (gateway payments):**
  - `provider`, `provider_ref`, `ref_id` or `refNumber`;
  - the amount in rial, status, and timestamps;
  - the billing owner key (`organization_id` or `workspace_id`; the billing unit is open,
    section 7.1 and Q3) and `plan_key`.
  This is enough for support and reconciliation.
- **Identified deposits (the main path) carry more data.** Each deposit record from the
  provider has the payer's `sourceName`, `sourceIban`, and `sourceCard` ([EBank spec];
  section 5.10). The spec does not say the card number is masked. So card data can reach
  Homayar on this path, even though no card is entered. Rules (INFERENCE, design
  proposal):
  - **Store:** the deposit ID (شناسه واریز), the amount, the provider's `refCode`, the
    deposit time, the matched invoice, and the match status.
  - **Store for support, optional:** the payer name (`sourceName`), so an admin can answer
    "who paid?". Keep it with the invoice, under the same retention as billing records.
  - **Use only to match, then drop:** `sourceIban` and `sourceCard`. The matching job
    reads them in memory. If an admin needs them for an unknown deposit, keep them only
    on the unmatched row, and delete them as soon as the deposit is matched or returned.
    Suggested limit: 30 days (to confirm with an accountant, Q16).
  - **Never store the full `sourceCard`.** If a card number is needed at all, keep only
    the last four digits.
  - **Never log** these fields, and never send them to the product database or to
    analytics.
  - The provider keeps the full record. Homayar can fetch it again from the provider if
    a dispute needs it.
- **Optional:** the masked card (`card_pan` or `cardNumber`), only if support needs to
  answer "which card paid?". Keep it for a short time. The exact period is an open
  question (section 9).
- **Retention of payment and invoice rows.** The privacy policy says billing records are
  kept "as required by tax and accounting regulations, typically 7 years"
  (`content/legal/privacy.md:45`). The legal period under Iranian law was not checked.
  An accountant must confirm it (section 9).
- **Do not store `card_hash` or `hashedCardNumber`.** INFERENCE: a card number has few
  unknown digits once the bank prefix is known, so a SHA256 of it can be reversed by
  trying every possible number. Treat it like the card number. Homayar has no use for it.
- **Do not send `mobile`, `email`, or `nationalCode` to the gateway.** The gateway works
  without them. Sending them only spreads personal data.
- **The tax invoice needs buyer data.** A full B2B e-invoice needs the buyer company's
  identity and tax details (INFERENCE from Moadian article 5(ب); an accountant must list
  the fields). This is personal and business data that Homayar must keep for tax reasons.
  It is not card data. Keep it in the billing records, not in the product database, with
  the same access control as other admin data.

### 7.5 Self-hosted licenses without calling a provider

Proposed design (INFERENCE; no license code exists at base, and a search for license
activation code found none):

1. The customer buys on Homayar's side, almost always by invoice and bank transfer (item 6).
2. Homayar's billing backend issues a **license file**. It is a small JSON document with
   the customer, edition, seat or hour limits, and start and end dates. It is signed with a
   private key that only Homayar's billing server holds.
3. The self-hosted install checks the signature with a public key built into the release.
   It makes **no network call**, so ADR 0031's "no outbound path at all" still holds
   (`0031-egress-denial.md:64-65`).
4. **Renewal means a new file.** The behavior at expiry must follow two documents.
   - WP19 F75 asks for this acceptance rule: "Valid/expired/tampered offline license tests
     follow documented grace/export behavior" (`docs/planning/work-packages/WP19/README.md:33`).
   - The blueprint gives the policy: "Expiration must not destroy data or abruptly lock
     customer exports; use notice, a bounded grace period and an agreed read/export mode.
     Avoid remote kill switches" (`docs/planning/homayar-commercial-blueprint.md:698`).
   - So, after the end date, the install:
     - shows a notice;
     - keeps working during a bounded grace period;
     - then moves to a read and export mode. It never deletes data and never locks
       exports.
   - A tampered file (bad signature) is treated as "no valid license". The same read and
     export mode applies. Data is never destroyed.
   - The grace length is a product rule to document. The contract may extend it, but not
     remove it.
5. **Limit:** an offline file cannot be revoked early. The contract covers that case. The
   blueprint's "avoid remote kill switches" agrees with this.
6. **Who buys this way:** at the blueprint's price levels (USD 40,000 and more per year,
   `homayar-commercial-blueprint.md:689-690`), every self-hosted license goes through
   invoice and bank transfer (section 5.5). The gateway is not used for it.

### 7.6 Tools Homayar needs

| Tool | Build, buy, or gateway | Notes |
| --- | --- | --- |
| **First (WP18 step 5):** pre-invoice, invoice, and manual payment status | Build, or use accounting software | the main path (section 7, item 1) |
| Rial price list and exchange-rate record | Build (data and admin), owner sets the prices | section 7.1, "Rial prices" |
| Invoices and payments tables | Build | organization or workspace key is open; unique `(provider, provider_ref)` |
| Plan prices, periods, grace, expiry | Build | extends `Plan` and `Subscription` (section 7.1) |
| Renewal reminders and dunning emails | Build | dunning means reminders after a missed payment; uses the SMTP settings |
| Identified-deposit service (deposit IDs, deposit list) | Buy (Zibal corporate banking, Vandar Avand, or Jibit), plus a small adapter to build | section 5.10; needs a company bank account; the adapter reads only, never rejects or transfers; its key can move money (section 7.3, item 10) |
| **Second:** hosted payment page, card entry, 3-D Secure, PIN | Gateway | the chosen provider's page |
| Provider adapter (request, verify, inquiry, unVerified, reverse) | Build | thin HTTP client in FastAPI; skip the young SDKs |
| Callback route | Build | FastAPI route on the app origin (section 7.2) |
| SaaS egress proxy or NAT with a fixed Iranian IP | Build (deployment) | section 3, preconditions 1 and 2 |
| Reconciliation job (payments and payouts) | Build | same pattern as ADR 0010 |
| Admin views (invoices, deposits to match by hand, refunds) | Build | for the invoice path and for exceptions |
| Refunds after the short reverse window | The chosen provider's refund API (Zibal and Vandar document one), or a manual bank transfer | ZarinPal's refund API is undocumented |
| Moadian e-invoices | Buy | a trusted company (شرکت معتمد) or accounting software; details UNVERIFIED |
| VAT calculation on invoices | Buy, with an accountant | rate and exemption UNVERIFIED |
| Pre-invoice and contract for Enterprise | Build a simple template, or use accounting software | |
| License file signer and offline checker | Build | section 7.5 |
| e-Namad | Obtain from enamad.ir | required by the providers |
| Payout settlement | Gateway | next-day settlement |
| Status and uptime monitoring | Build a simple check | the provider status pages were not usable |

## 8. What the website must show

This section feeds the next website work unit. Items marked (law) come from e-commerce
law articles 33 and 34. Items marked (Zibal) come from Zibal's terms. Zibal is the first
choice (section 7), so plan for them. (e-Namad) marks the trust mark.

**Pages:**

1. **Pricing page** (static site is fine):
   - Plans and what each plan includes (law 33 الف).
   - Monthly and yearly prices in rial or toman, from the owner's rial price list (section
     7.1). Not USD converted on the page. The USD figures in the repository are not
     validated.
   - Whether prices include VAT, and the tax amount (law 33 د). The rate is UNVERIFIED, so
     ask an accountant.
   - How long the prices are valid (law 33 هـ).
   - A plain line: "Subscriptions do not renew automatically. We remind you before your
     period ends."
   - For each plan, how it is paid:
     - "Pay online" only for amounts under the gateway cap;
     - "Request an invoice" for yearly plans, Enterprise, self-hosted, and anything above
       the cap (section 5.5).
   - Enterprise: "Contact us for a quote, a contract, and invoice payment."
2. **Invoice request (main path):**
   - A form or contact route to ask for a pre-invoice. It asks for the buyer's company
     details for the tax invoice. Keep the list short and confirm it with an accountant.
   - The pre-invoice shows:
     - the rial amount and tax;
     - how long it is valid (law 33 هـ);
     - the bank account for the transfer;
     - the deposit ID (شناسه واریز) to put on the transfer, with a plain instruction:
       "Put this ID on your PAYA or SATNA transfer so we can match your payment."
3. **Online checkout (in the console, `/app/`), only under the cap:**
   - Step 1: choose a plan and a period. Hide "pay online" when the total is above the
     cap, and offer "request an invoice" instead.
   - Step 2: review the total with tax. Show the terms and refund policy links, with a
     clear "I agree" (law 33 و, law 34 ج).
   - Step 3: "You will now go to the secure bank payment page of the chosen gateway.
     Homayar never sees your card number."
   - Step 4: result page with the provider's reference number (Zibal requires this; show
     it with ZarinPal too).
   - Step 5: a receipt email with the invoice or a link to it.
4. **Terms of service:** rewrite section 3 (`content/legal/terms.md:15-25`):
   - prices in rial, and how long a quoted price is valid;
   - two ways to pay: online under the gateway cap, or by invoice and bank transfer;
   - prepaid periods with no automatic renewal;
   - the grace period and what happens after it;
   - the seven-working-day withdrawal right and when it starts (law 37, 38);
   - how the service works (law 34 د).
   - A lawyer must decide how article 38 د exceptions and business buyers apply.
5. **Refund and cancellation policy:** how to ask, how long a refund takes, and the rule
   that the full amount comes back within the withdrawal period (law 38 ج).
6. **Contact page:**
   - company name and trade name;
   - postal address for complaints;
   - phone and email (law 33 ب, ج; law 34 الف);
   - a support phone, email, and contact link (Zibal).
7. **About page:** who runs Homayar (the legal entity named in the terms, "KSF Company",
   `content/legal/terms.md:5`).
8. **Privacy policy:** replace Stripe (`content/legal/privacy.md:50`) with the chosen
   gateway and the identified-deposit provider. Say what payment data Homayar keeps,
   including the payer details from bank transfers (section 7.4).
9. **e-Namad badge:** shown on the site (e-Namad). Placement per e-Namad rules, which were
   UNVERIFIED.

**Plain-language points about data handling for the buyer:**

- "You enter your card on the bank's payment page, not on Homayar."
- "When you pay online, we never see or store your card number, CVV2, or PIN."
- "When you pay by bank transfer, the payment service tells us the payer's name, account
  number, and card number for that transfer. We use them only to match your payment. We
  keep the payer name with the invoice and delete the account and card numbers after
  matching." (Wording follows the rules in section 7.4; the privacy policy must say the
  same.)
- "We keep the payment reference number, the amount, and the date so we can support you
  and issue your tax invoice."
- "For company invoices we keep your company's invoice details, as tax law requires."

## 9. Open questions

For the owner (these decide the design):

1. **Rial prices.** Set a fixed rial price list, or convert USD list prices at a stated
   rate? Who sets the rate, and how often is it reviewed? Section 5.5 shows which price
   levels fit a gateway payment. The final rial prices decide how much goes online and
   which provider fits (section 1).
2. **Hosting.** Where will the SaaS API run? Precondition 1 (section 3) says it should be
   an Iranian host with a fixed outbound IP for either provider. Who designs the SaaS
   egress proxy or NAT (`docs/operations/deployment.md:164-168`)?
3. **Billing unit.** Should invoices and payments belong to the organization (as the
   blueprint says, `homayar-commercial-blueprint.md:438`) or to the workspace (as the
   code does, `saas_models.py:876-878`)? This is also an architecture decision.
4. **Company bank account.** Which bank holds the company's account for identified
   deposits? Zibal's activation takes 2 to 20 working days depending on the bank, and
   Vandar needs forms at a branch (section 5.10). Customers outside Iran are out of scope
   for launch (owner).
5. **Merchant identity.** Which legal entity signs up with the provider (the terms name
   "KSF Company")? Does it already have e-Namad and a tax tracking code?
6. **Domains.** Will the public site and the app share one host name? This affects the
   callback domain rule (section 7.2).
7. **Grace period and reminders.** How many days of grace for SaaS, and when to send
   reminders? How long is the self-hosted grace before read and export mode (section 7.5)?
8. **Old Stripe code.** The cPanel "commerce off" step is not shown at base, because its
   script is not committed (`.github/workflows/ci.yml:76-84`). The README still documents
   Stripe and a Vercel deploy (`README.md:14`, `:79-87`). Is a Vercel deploy with Stripe
   keys running today? If so, turn it off or remove the Stripe path.
9. **Plan changes.** How to handle an upgrade or downgrade in the middle of a period
   (proration)?
10. **Mixed carts.** Can one checkout mix monthly and yearly items? The old Stripe
    checkout refused this (`src/app/api/checkout/route.ts:79-90`). With prepaid periods,
    one invoice could carry both. The owner decides.
11. **Government buyers.** Are government or public-sector buyers expected? Their
    procurement channels were not researched.

For an accountant:

12. The VAT rate for 1405, and whether Homayar's SaaS or licenses are exempt.
13. Is a gateway receipt enough for B2C sales, or must Homayar issue its own e-invoice for
    every sale? Which invoice type fits B2B buyers?
14. Which trusted company or accounting software to use for Moadian e-invoices.
15. After a refund, what corrective e-invoice (or cancellation) must be issued in Moadian?
16. How long must payment and invoice records be kept under Iranian law? The privacy
    policy says "typically 7 years" (`content/legal/privacy.md:45`). How long may the
    masked card be kept?
17. Which buyer fields must a B2B e-invoice carry? Keep only those (section 7.4).

For a lawyer:

18. How article 37 and 38 withdrawal rights and the by-law exceptions apply to SaaS that
    starts at once, and to business buyers.
19. Stripe and sanctions: confirm that Homayar cannot use Stripe. Any later foreign sales
    need a sanctions review first (section 5.4).
20. What is the status of any Iranian personal-data protection law, and what does it
    require for billing data? Not researched.

For Zibal, the first choice (ask before signing):

21. **Cap.** Confirm 200M toman per payment on a live page or in writing. Would Zibal set a
    lower cap for Homayar's business category (section 5.5)?
22. **Licence.** Shaparak's archived list shows Zibal's validity ending 1405/05/27. Was it
    renewed (section 5.11)?
23. **Corporate banking.** What does "activation from 30M toman" cover? Is there a yearly
    fee? Which banks are supported? Is there a webhook for incoming identified deposits, or
    must Homayar poll? Is the "0.03% money transfer" fee also charged on incoming
    deposits? Can Homayar create its own deposit IDs, so they still work after a change of
    provider?
24. **Refunds.** Does the refund API work for all transactions once corporate banking is
    active? What is the fee?
25. **Reliability.** Is there a status feed? The help site timed out on 2026-09-27 and
    2026-09-28, and `status.zibal.ir` showed an empty page.
26. **Servers abroad.** Do the gateway calls and the corporate-banking API calls both need
    an Iranian IP?

For Vandar, the second choice:

27. What is the real gateway cap for a company merchant: 50M or 100M toman? What are the
    live direct-debit limits per bank, and can a company account sign a mandate?

For ZarinPal, if chosen as a gateway only:

28. Is direct debit coming back? What is the verify time window? Is the GraphQL refund
    API supported for merchants? Is VAT added to the 0.5% fee? Does a registered IP abroad
    count as "using the acceptance tool abroad"?

For any provider, or Shaparak and the Central Bank:

29. **Payer daily limits in 1405.** The official figure found is 200M toman per person per
    day (circular of 1403). A newer 400M figure comes only from a ZarinPal post and a news
    site (section 5.5). What is the current circular?
30. **Shaparak list.** Are Vandar, ZarinPal, Jibit and PayPing on the current list of
    active facilitators? Only 12 of 21 names were archived (section 5.11).
31. **Split payments.** May one invoice be paid as several gateway payments under the cap?
32. **Callback domain.** Does a subdomain match the "registered domain" for callbacks?
33. **Holding funds.** When may the provider hold, delay, or freeze settlement? ZarinPal's
    terms allow it to block an account, for example for use on unrelated domains (terms
    4-4). Not researched for the others.
34. **Disputes.** How does a card holder dispute a payment, through the provider or
    Shaparak? What must Homayar provide? Not researched.
35. **Lead time and order.** How long do e-Namad, the tax tracking code, the company bank
    account forms, and merchant approval take? In what order? Zibal's archived page says a
    no-star e-Namad activates "within 20 minutes".
36. **Provider outage.** The checkout should show "online payment is not available now;
    request an invoice" when the gateway does not answer. The invoice path does not depend
    on the gateway.
37. **Direct debit rules.** Is there a Central Bank or Shaparak rule for direct debit
    limits? None was found (section 5.8).

For the build (not open decisions, but not designed here):

38. The audit events for payment verified, deposit matched, payment recorded by an admin,
    refund, and license issued. They should use the existing audit pattern (ADR 0010 audits
    `usage.reconciled`, `0010-usage-ledger-and-reconciliation.md:45-47`).

Added after review of turn 3 (numbered on, so earlier numbers stay stable):

For the owner:

39. **A bought billing service.** Should Homayar buy a billing or subscription service
    instead of building the billing module (section 7.6)? No Iranian billing service was
    researched. The document assumes a build.
40. **Old Stripe records.** The old site's Stripe webhook writes to `orders`,
    `order_items`, and `checkout_sessions` in its own database, including the buyer's
    email (`src/app/api/webhooks/stripe/route.ts:6`, `:47-62`, `:87-90`). Are there real
    records? If so, decide how long to keep them and when to delete them, before the
    Stripe path is removed (Q8).
41. **Deposit data.** The identified-deposit path gives Homayar the payer's name, IBAN,
    and card number, and a key that can move money (sections 5.10, 7.3, 7.4). Does the
    owner accept this, with the rules in section 7.4? The other choice is manual matching
    from the bank statement, which still shows payer details to an admin.

For Zibal (and the same for Vandar Avand or Jibit, if chosen):

42. **Key scopes for the corporate-banking API.** Can the API key be limited to reading
    identified deposits, with no transfer (`checkout/create`), reject, or refund rights?
    Can transfers need a second approval in the panel? Is `sourceCard` returned in full or
    masked?

If direct debit is revisited (section 5.8):

43. **What a mandate stores.** What does the merchant have to store for a mandate, and can
    that stored value start a withdrawal on its own? If yes, it is a secret like the
    deposit key.
44. **National scheme or provider scheme.** Is direct debit one national scheme, or does
    each provider run its own (Payman says it runs outside Shaparak)? Can mandates move to
    another provider?

For any provider:

45. **Change notice.** How much notice does the provider give before it changes its API
    or its fees? Is that in the contract? ZarinPal announced its IP rule on 2026-09-02 for
    a start on 2026-09-06 (section 5.1), and its payment links were stopped by Central
    Bank order.
46. **Moadian e-invoices.** Does any candidate (ZarinPal, Zibal, Vandar, Jibit, PayPing)
    issue Moadian e-invoices for the merchant, or link to a trusted company? Not checked
    for the new candidates (section 5.6).
47. **Reliability of Vandar, Jibit, and PayPing.** Is there a status page or uptime
    history? Not researched (section 5.6).

## 10. Sources

All read on 2026-09-27 unless marked. "Wayback" means an archived copy of the provider's
own page, with the copy date.

**ZarinPal (primary):**

- https://www.zarinpal.com/docs/paymentGateway/ (root `/docs/` is a JS shell; subpages
  loaded)
- https://www.zarinpal.com/docs/paymentGateway/connectToGateway.html
- https://www.zarinpal.com/docs/paymentGateway/sandBox.html
- https://www.zarinpal.com/docs/paymentGateway/errorList.html
- https://www.zarinpal.com/docs/paymentGateway/moreFeatures/session-validation.html
- https://www.zarinpal.com/docs/paymentGateway/moreFeatures/reverse.html
- https://www.zarinpal.com/docs/paymentGateway/moreFeatures/currency.html
- https://www.zarinpal.com/docs/paymentGateway/otherMethods/unVerified.html
- https://www.zarinpal.com/docs/paymentGateway/otherMethods/Inquiry.html
- https://www.zarinpal.com/docs/paymentGateway/otherMethods/feeCalculation.html
- https://www.zarinpal.com/docs/apiDocs/connect.html
- https://www.zarinpal.com/docs/apiDocs/auth.html
- https://www.zarinpal.com/docs/apiDocs/query/sessions.html
- https://www.zarinpal.com/docs/apiDocs/query/reconciles.html
- https://www.zarinpal.com/docs/apiDocs/query/bankaccount.html
- https://www.zarinpal.com/docs/sdkDocs/
- https://www.zarinpal.com/docs/mcp (docs index; health reports 128 pages, built
  2026-09-15)
- https://www.zarinpal.com/pricing
- https://www.zarinpal.com/payment-gateway
- https://www.zarinpal.com/terms
- https://www.zarinpal.com/faq (answers read from the site's JS bundle)
- https://www.zarinpal.com/zarinlink
- https://www.zarinpal.com/landing/fast-settlement/
- https://www.zarinpal.com/features/direct-reconcile
- https://www.zarinpal.com/blog/ثبت-ip-در-درگاه-پرداخت-زرینپال/ (published 2026-09-02)
- https://www.zarinpal.com/blog/اینماد-چیست/ (modified 2026-09-20)
- https://www.zarinpal.com/blog/unverified/ (published 2021-05-09; outdated)
- https://www.zarinpal.com/blog/سقف-تراکنش-درگاه-پرداخت-اینترنتی/ (published 2026-07-19;
  1405 limits; cites a Central Bank circular that was not read; weak evidence)
- Re-checked 2026-09-28: https://www.zarinpal.com/docs/paymentGateway/errorList (error -41)
  and https://www.zarinpal.com/terms (clause 4-1)
- https://github.com/ZarinPal/zarinpal-php-sdk and
  https://github.com/ZarinPal/Zarinpal-Python-Sdk (refund GraphQL mutation in the source)
- Wayback: https://web.archive.org/web/20250718034131/https://www.zarinpal.com/docs/directDebit/
  (copy of 2025-07-18)
- Failed:
  - https://status.zarinpal.com/ (HTTP 502)
  - https://www.zarinpal.com/docs/directDebit/ (redirects to /docs/)
  - https://www.zarinpal.com/landing/direct-debit/ (404)

**Zibal (primary):**

- https://api.zibal.ir/static/helpdocs/ipg.json
- https://api.zibal.ir/static/helpdocs/platform.json
- https://api.zibal.ir/static/helpdocs/ebank.json
- https://zibal.ir/
- https://zibal.ir/ipg
- https://zibal.ir/ipg/refund
- https://zibal.ir/pricing
- https://zibal.ir/privacy-policy (terms tabs)
- https://zibal.ir/enterprise
- https://zibal.ir/status
- https://status.zibal.ir/ (empty app shell)
- https://github.com/zibalco (org and sample repositories)
- Wayback copies (the live pages timed out):
  - help.zibal.ir/article/how-get-and-set-up-online-payment-gateway/ (2026-05-14)
  - help.zibal.ir/article/online-payment-guide/ (2026-05-14)
  - help.zibal.ir/article/web-service-activation/ (2026-05-14)
  - help.zibal.ir/article/settlement-payment-gateway-transactions/ (2026-06-18)
  - help.zibal.ir/article/registration/ (2026-05-14)
- Failed:
  - https://help.zibal.ir/#docs and https://help.zibal.ir/IPG/API (timeout, HTTP 504; timed out again on 2026-09-28)
  - https://docs.zibal.ir/ (timeout)
  - https://zibal.ir/blog/direct-debit/ (HTTP 504; no Wayback copy)

**Iranian law and regulators (primary):**

- https://rc.majlis.ir/fa/law/show/1348789 (Moadian law, 1398)
- https://rc.majlis.ir/fa/law/show/1794193 (Moadian facilitation law, 1402)
- https://rc.majlis.ir/fa/law/show/93997 (e-commerce law, 1382, articles 33 to 38)
- https://rc.majlis.ir/fa/law/show/134203 (the old 1387 VAT law; not the law in force)
- https://enamad.ir/, https://enamad.ir/About, https://enamad.ir/AboutRating
- Not usable:
  - https://enamad.ir/Faq and https://enamad.ir/Laws (content loads by JavaScript)
  - https://www.shaparak.ir/ (redirect on 2026-09-27; timed out on 2026-09-28)
  - https://www.cbi.ir/ (timed out on 2026-09-28)
  - the 1400 VAT law text (not reached)

**Sanctions and Stripe (primary):**

- https://stripe.com/legal/restricted-businesses (updated 2026-09-22)
- https://www.ecfr.gov/api/versioner/v1/full/2026-09-01/title-31.xml?part=560&section=560.201
- https://www.ecfr.gov/api/versioner/v1/full/2026-09-01/title-31.xml?part=560&section=560.540
- https://www.law.cornell.edu/cfr/text/31/560.206 (mirror of the CFR text)
- https://ofac.treasury.gov/media/928871/download?inline= (General License D-2, 87 FR 62003)
- https://ofac.treasury.gov/sanctions-programs-and-country-information/iran-sanctions

**Alternatives research (turn 3). All read 2026-09-28 unless a Wayback date is given.**

Facilitators:

- Vandar:
  - https://vandar.io/faq/
  - https://vandar.io/payment-gateway/
  - https://vandar.io/pricing/
  - https://vandar.io/direct-debit/
  - https://vandar.io/cashin-code/
  - https://vandar.io/avand/
  - https://vandar.io/form/
  - https://docs.vandar.io/ (`direct_debit_service/*`, `ipg_service/*`,
    `avand_service/cash_in_by_code`)
- Jibit:
  - https://jibit.ir/
  - https://jibit.ir/pricing/ ("به‌روزرسانی 1405")
  - https://jibit.ir/refund/
  - https://jibit.ir/internet-payment-gateway/
  - Failed: https://docs.jibit.ir, https://developers.jibit.ir, https://napi.jibit.ir
    (timeouts)
- PayPing:
  - https://payping.ir/faq/
  - https://payping.ir/pricing/
  - https://payping.ir/terms/
  - https://docs.payping.ir
  - https://statics.payping.ir/openapi.json
- IDPay (read with an expired certificate): https://idpay.ir/faq,
  https://idpay.ir/web-service/v1.1/
- Pay.ir:
  - DNS failed.
  - Wayback copies of https://pay.ir/: 2024-09-12, 2025-09-07, 2026-02-18.
- NextPay:
  - Failed live.
  - Wayback https://nextpay.org/ and /faq/ (2025-10-05).
- Payman: https://web.paymanpay.ir/, /faq/, /api-reference/ (API PDF v1.6.3, dated
  1405/04/01), /blog/news/asretarakonesh-interview-1405/
- Finnotech: https://finnotech.ir/service/سرویس‌های-بر-پایه-حساب, https://finnotech.ir/pricing

Zibal and ZarinPal, re-checked:

- https://zibal.ir/ebank
- https://zibal.ir/ebank/deposit
- https://api.zibal.ir/static/helpdocs/ebank.json
- https://www.zarinpal.com/docs/mcp (search for direct debit: 0 results)
- Failed: https://zibal.ir/blog/direct-debit/ (504; no Wayback copy)

Bank PSPs and Digipay:

- Parsian:
  - https://pec.ir/karmozd
  - https://pec.ir/faq
  - https://pec.ir/uploads/1/2026/Aug/22/ (the merchant contract PDF, 1405)
- Irankish:
  - https://www.irankish.com/page/faq
  - https://www.irankish.com/App_Data_Public/IPG/IPG_TechnicalGuide.V11.pdf
- Pasargad:
  - https://github.com/pep-ipg/Dorsa-IPG-pasargad
  - Wayback pep.co.ir/ipg/ and /wage/ (2025-10-03)
- SEP:
  - Wayback sep.ir internet-gateway page (2026-06-07)
  - Wayback SEP merchant technical document v4.1 (archive of 2025-07-02)
- Sepehr: Wayback sepehrpay.com news and FAQ (2024)
- Sadad: Wayback sadad.co.ir (2025-12-08)
- Digipay: Wayback mydigipay.com pages (2026-02 to 2026-09), including
  /developers/docs/upg/
- SnappPay: https://snapppay.ir/, https://snapppay.ir/merchant-acquisition/
- Failed or not usable:
  - behpardakht.com (timeout, no captures since 2022)
  - asanpardakht.ir (HTTP 550)
  - sadadpsp.ir (timeout)
  - mydigipay.com live (timeout)

Regulator pages (Wayback copies of official pages; the live sites timed out):

- https://www.shaparak.ir/fa/companies/pardakhtyar (2026-02-18 and 2026-02-27)
- https://shaparak.ir/fa/companies/psp (2026-02-27)
- https://cbi.ir/showitem/29680.aspx (2025-04-18), circular 22848/03 of 1403/02/04
- https://cbi.ir/showitem/24156.aspx (2025-03-24), commercial-account rule of 1401
- https://www.cbi.ir/showitem/22624.aspx (2024-09-18), SATNA and PAYA limits of 1400
- https://www.cbi.ir/page/18213.aspx (2022-05-09), facilitator rules v3.0 of 1397
- Central Bank procedure for merchant violations (approved 1403/12/19), read from the copy
  at https://vandar.io/blog/wp-content/uploads/2025/05/ (SECONDARY hosting)
- Failed live: shaparak.ir, cbi.ir, enamad.ir, intamedia.ir, tax.gov.ir (timeouts)

Added after the review of turn 3 (first read in review on 2026-09-28; each item below was
re-read by the researcher on 2026-09-30 and the quotes match):

- https://api.zibal.ir/static/helpdocs/ebank.json: `sourceName`, `sourceIban`,
  `sourceCard`; `bearerAuth`; `POST /ebank/v1/account/checkout/create/`; `balance`;
  `statement/list`; `change-status` and `refundRequestTrackerId`; the declared-IP rule;
  errors 4 and 7
- https://docs.vandar.io/direct_debit_service/banks (the full example bank list)
- https://vandar.io/payment-gateway/ and https://vandar.io/faq/ (the reason clauses for
  the caps)
- https://vandar.io/avand/ (forms for «انتقال وجه غیر حضوری»)
- https://zibal.ir/pricing («۰٫۰۳٪ انتقال وجه»)
- https://jibit.ir/pricing/ (no maximum on the «دریافت وجه» fee)
- Wayback https://www.shaparak.ir/fa/companies/pardakhtyar (2026-02-27): record order by
  `createdAt`, and the validity dates of other entries
- Repository: `src/app/api/webhooks/stripe/route.ts` (the old Stripe order tables)

Secondary (support only):

- ZarinPal blog posts on SATNA and PAYA (2026)
- etemadonline.com and news snippets on the circular of 10 Esfand 1404

**Secondary** (support only; they carry no claim alone): news sites on the 1405 VAT rate
(entekhab.ir, eghtesadonline.com, nabzgheymat.ir); blogs on invoice types (yassystem.com,
keysuntsp.com). None of these was used as the only source for a claim.
