# Payment gateways for Homayar: ZarinPal vs Zibal

Status: In Review

Date: 2026-09-27 (revised 2026-09-28 after review round 1)

Author role: researcher (`pay-t1-res`, research team `pay-t1`). Base commit: `1b34472`.

## 1. Summary and recommendation

**Main finding: most of Homayar's likely prices do not fit in one gateway payment.**

- ZarinPal accepts at most 100,000,000 toman per payment. Zibal accepts at most
  200,000,000 toman (section 5.5).
- Homayar has no rial prices yet. The repository has only USD list prices, which are not
  validated.
- At the repository's display rate, three kinds of sale are above **both** caps:
  - every yearly plan;
  - the Professional monthly plan;
  - every package in the blueprint's pricing section.
- The Starter monthly plan and most monthly add-ons fit.

So the payment design has two paths:

1. **Main path: pre-invoice plus bank transfer** (پیش‌فاکتور، حواله).
   - It is for yearly plans, Enterprise, self-hosted licenses, and any payment above the
     gateway cap.
   - It is built first, as WP18 step 5 already requires (section 7.1).
2. **Second path: one online gateway.**
   - It is for payments under the cap, for example a small monthly plan, an add-on, or a
     top-up.
   - It is added after the rial price list exists.

**Which gateway.** The choice depends on the rial prices, which the owner has not set yet:

- **ZarinPal** if most online payments are at or under 100,000,000 toman. Its docs loaded
  and are complete. It has a free reverse within 30 minutes. Its cap is confirmed on a live
  page.
- **Zibal** if many online payments fall between 100,000,000 and 200,000,000 toman. Its
  higher cap is then the deciding factor. Two things must be confirmed with Zibal first:
  its cap is known only from an archived page, and Zibal may set a lower cap per merchant.
- **Above 200,000,000 toman, invoice plus bank transfer in every case.** The gateway choice
  then matters little.
- The fee is **not** a reason to choose. Every catalog price is far above both fee caps, so
  each payment costs about 16,500 toman (ZarinPal) or 22,000 toman (Zibal). The difference
  is at most 5,500 toman per payment. It may be smaller, because ZarinPal's VAT on its fee
  is unknown (section 6).

**Other parts of the recommendation (unchanged):**

- **Subscriptions are prepaid periods, not automatic charges.** Neither provider documents
  automatic recurring charges today.
- **Self-hosted licenses are signed files** that the customer's server checks offline. A
  self-hosted install never calls a payment provider.
- **Card data never reaches Homayar.** The buyer types the card on the provider's hosted
  page. Homayar stores only a payment reference, the amount, and the status.

**Preconditions** before any gateway work (section 3):

- an Iranian host with a fixed outbound IP for the API (INFERENCE: both providers' terms
  point to this);
- a rial price list and a rule for the exchange rate;
- e-Namad and a tax tracking code for the merchant company.

Foreign customers cannot pay through either provider. That is an owner decision (section 9).
Tax invoices (سامانه مودیان, the Moadian e-invoice system) are Homayar's job, not the
gateway's. They need an accountant.

Confidence: medium. The provider facts come from their own docs and pages, read
2026-09-27 and 2026-09-28. Several legal facts could not be read from primary sources, and
each one is marked UNVERIFIED.

## 2. The decision, in the owner's words

Translated from the owner:

> "For cart and checkout, the options are zarinpal.com and zibal.ir. Dev docs:
> https://www.zarinpal.com/docs/ and https://help.zibal.ir/#docs. To choose the best option,
> compare these two providers, keeping in mind that we want to offer Homayar as SaaS and as
> self-hosted. Research which tools we will need. Data storage matters. Because the data is
> sensitive, it is better that it is not handled on our side, so that if we are ever hacked,
> people's data does not leak from us. If a user wants the SaaS service, they must buy a
> subscription, offered monthly and yearly. We also need an Enterprise plan."

The question this spike settles: which payment provider (ZarinPal, Zibal, both, or another
path for some cases) Homayar should integrate. It also settles what Homayar must build or
buy to do three things without storing card or other sensitive payment data:

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
   - Both rules point to an Iranian server for these calls. ZarinPal should confirm
     whether a server abroad counts as "using the tool abroad" (section 9).
2. **A designed SaaS outbound path.** A proxy or NAT with a stable public IP to register
   with the provider. It does not exist yet (`docs/operations/deployment.md:164-168`).
3. **A rial price list and an exchange-rate rule** (section 7.1).
4. **Merchant documents:** e-Namad, a tax tracking code, and company documents (sections
   5.1 and 5.2).

## 4. Options considered

- **A. ZarinPal only.**
- **B. Zibal only.**
- **C. Both at once** (for example, one as a failover for the other).
- **D. Invoice and bank transfer as the main path, plus one gateway for payments under
  its cap.** This is not a replacement for A or B. Invoice and transfer sit next to one of
  them. After the price check in section 5.5, this is the recommended shape.
- **E. Do nothing or defer.** Keep "manual" billing (`Homayar/backend/schemas/billing.py:40`)
  and sell every plan by invoice and bank transfer. Add a gateway later.
- **F. Keep Stripe.** Listed because the old site uses it.
- **G. Split one large bill into several gateway payments.** Considered in section 5.5. It
  is not recommended without the provider's written approval.

Other gateway paths were **not researched**. Each is dropped here, with the reason:

- **A direct bank or PSP gateway** (a contract with one PSP, a payment service provider,
  instead of a facilitator):
  - The owner named only ZarinPal and Zibal.
  - It would not remove the buyer-side daily limit. A ZarinPal post says that limit is set
    by the Central Bank for all internet payments (section 5.5). That is INFERENCE, and the
    Central Bank page did not load.
- **Other payment facilitators** (پرداخت‌یار) licensed by Shaparak:
  - The owner named only two.
  - Shaparak's site timed out on 2026-09-27 and 2026-09-28, so its facilitator list could
    not be read.

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

### 5.4 Customers outside Iran and sanctions

- Neither provider accepts foreign cards (INFERENCE from sections 5.1 and 5.2).
- Stripe does not serve Iran (section 4).
- US rules:
  - 31 CFR 560.201 bans «the importation into the United States of any goods or services
    of Iranian origin».
  - 31 CFR 560.206(a) says no US person "may engage in any transaction or dealing in or
    related to ... Goods or services of Iranian origin". This was read from the Cornell LII
    mirror, not an official page.
  - The communications license (31 CFR 560.540, the former General License D-2) only lets
    US persons provide services **to** Iran.
  - INFERENCE, needs a sanctions lawyer: US customers cannot legally buy Homayar SaaS.
- Sales to customers in other countries (EU, UK, Gulf) were not researched.
- Routes such as crypto payments, a foreign legal entity, or exchange services were not
  researched. They must not be used without a sanctions lawyer.

### 5.5 Payment limits against Homayar's prices

**The limits:**

| Limit | Value | Source |
| --- | --- | --- |
| ZarinPal, one payment | 100,000,000 toman | error -41, `errorList.html` (live; re-checked 2026-09-28) |
| Zibal, one payment | 200,000,000 toman | Wayback copy (2026-05-14) of `help.zibal.ir/article/how-get-and-set-up-online-payment-gateway/`; the live page timed out again on 2026-09-28 |
| Zibal, per national code per day | 200,000,000 toman | the same archived page |
| Zibal, per merchant | may be set lower | the same archived page: «در صورتی که نوع کسب و کار شما، نیاز به حداکثر مبلغ مجاز در درگاه پرداخت نداشته باشد، سقف متناسب توسط زیبال روی درگاه شما لحاظ خواهد شد» ("if your business does not need the maximum, Zibal sets a suitable cap on your gateway") |
| Buyer, all internet payments per day | 400,000,000 toman per person in 1405 | ZarinPal blog «سقف تراکنش درگاه پرداخت اینترنتی 1405 چقدر است؟» (published 2026-07-19). The post cites a Central Bank circular. The circular itself was not read: cbi.ir and shaparak.ir timed out on 2026-09-28. **Weak evidence.** |
| Buyer, per card | not found | UNVERIFIED (open question 18) |

The same ZarinPal post also names bank-transfer limits for 1405: a SATNA order must be at
least 100,000,000 toman, and one PAYA order can be up to 200,000,000 toman. SATNA and PAYA
are Iran's interbank transfer systems. These figures are secondary evidence, for the same
reason as above. They matter for the invoice path, because a company pays an invoice by
SATNA or PAYA.

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
- The buyer's daily limits still apply: Zibal allows 200,000,000 toman per national code
  per day, and the buyer-side limit is 400,000,000 toman per day (weak evidence). So a
  large bill would take several days.
- An installment plan in a contract is different. Each installment is its own invoice.
  If an installment is under the cap, it can be paid online. That is normal use, not a
  split.

## 6. Comparison

| Item | ZarinPal | Zibal |
| --- | --- | --- |
| Flow | request, hosted page, callback, verify | the same |
| Docs on 2026-09-27 | live and server-rendered; sandbox with any UUID | help site timed out; OpenAPI files on `api.zibal.ir` loaded |
| Official Python SDK | yes, young (2025), Python 3.12 or newer | no maintained one (2019 sample, 2020 PyPI) |
| Fee | 0.5% capped at 16,000 toman, plus 500 toman; VAT not stated | 1% capped at 20,000 toman, plus 10% VAT; minimum about 2,000 toman (INFERENCE) |
| Fee on a catalog-size payment, 37.6M to 94.3M toman (arithmetic; section 5.5) | 16,500 toman (the cap is reached above 3.2M toman); VAT on the fee UNVERIFIED, so up to 18,150 toman if 10% VAT applies | 22,000 toman with VAT (the cap is reached above 2M toman) |
| Fee on a 100M to 200M toman payment | not possible (above the cap) | 22,000 toman with VAT |
| Maximum per payment | 100,000,000 toman (live) | 200,000,000 toman (Wayback, 2026-05-14); may be set lower per merchant |
| Payments that fit (section 5.5) | Starter monthly and 14 add-ons, at the display rate | the same, plus 4 add-ons between 100M and 200M toman |
| Settlement | next day; pages disagree on the hour | next business-day morning; terms say 1 to 3 business days |
| Automatic recurring | not in live docs; old "Payman" removed; UNVERIFIED | not documented; UNVERIFIED |
| Refund | free reverse within 30 minutes; later refund only through an undocumented GraphQL call | refund API with partial refunds and no time limit, but needs paid corporate banking |
| Data returned | masked card, SHA256 card hash, ref_id | masked card, ref number; hash in lazy mode; mobile in reports |
| IP rules | registered IPs required since 2026-09-06; at most 5 | registered IPs required; IPs outside Iran refused (archived article) |
| Domain rules | callback and referrer must match the registered domain | callback and referer must match; Referer header required |
| Merchant needs | ID or company documents; e-Namad "a necessity" (blog) | e-Namad, tax tracking code, business license |
| Foreign cards | no (Shetab only, INFERENCE) | no (Shetab only, INFERENCE) |
| Reconciliation API | GraphQL with OAuth2 token | REST reports with Bearer token |
| Status page | returned 502 | empty shell |

**The fee does not decide the pick.** At catalog-size amounts, both fees are flat at their
caps. The gap is at most 5,500 toman per payment, which is under 0.006% of a 94.3M-toman
Starter payment. If ZarinPal also adds 10% VAT to its fee, the gap is 3,850 toman.

**What decides the pick:**

- the cap, and how many of Homayar's final rial prices fall between 100M and 200M toman;
- how reliable the docs are (ZarinPal's loaded; Zibal's help site timed out twice);
- the refund path (ZarinPal: free reverse within 30 minutes; Zibal: a documented refund
  API that needs paid corporate banking).

## 7. Recommendation

1. **Build the invoice path first (option D, first step).**
   - The flow is:
     1. pre-invoice (پیش‌فاکتور), with the rial amount and how long it is valid;
     2. a contract or purchase order when the buyer needs one;
     3. bank transfer (حواله, by SATNA or PAYA);
     4. payment status recorded by an admin;
     5. the Moadian e-invoice.
   - Installments are separate invoices on a schedule in the contract.
   - It covers yearly plans, Enterprise, self-hosted licenses, and anything above the
     gateway cap. At the display rate, that is most of the revenue (section 5.5).
   - This follows WP18 step 5 (`docs/planning/work-packages/WP18/README.md:15`).
   - Why business buyers fit this path:
     - They need a full e-invoice for their tax credit (Moadian article 5(ب), section 5.3).
     - INFERENCE, no source: larger company and government buyers often pay by purchase
       order or in installments, and government buyers may buy through their own
       procurement channels. Government procurement was not researched (section 9).
2. **Then add one gateway, through a provider adapter, for payments under its cap.**
   - The choice rule is in section 1:
     - ZarinPal if most online payments are at or under 100M toman;
     - Zibal if many fall between 100M and 200M toman;
     - invoice in every case above 200M toman.
   - At the display rate, today's catalog fits ZarinPal for Starter monthly and 14
     add-ons. Four add-ons fit only Zibal. Final rial prices can move items across these
     lines.
   - "Both at once" (option C) is not worth it at launch. It would double the merchant
     onboarding, the secrets, the IP registration, and the reconciliation work. The two
     APIs have the same shape, so a second provider is cheap to add later.
   - Split payments (option G) only with the provider's written approval (section 5.5).
3. **Subscriptions are prepaid periods.**
   - A monthly plan under the cap is paid each month through the hosted page.
   - A yearly plan is paid by invoice, because no realistic yearly price fits one gateway
     payment (section 5.5).
   - Homayar sends reminders before the end of the period.
   - After the end date there is a short grace period (`PAST_DUE`). Then the subscription
     becomes `EXPIRED`.
   - There are no automatic charges. The old terms promise automatic renewal
     (`content/legal/terms.md:19`), so they must change.
4. **Self-hosted licenses are signed files checked offline (section 7.5).**
5. **Iran only at launch.** Foreign sales wait for an owner decision and a lawyer
   (section 9).

**What would change this recommendation:**

- **Rial prices set low.** If most plans come in under 100M toman per payment and yearly
  plans under about 8.3M toman per month, the gateway becomes the main path. The invoice
  path then becomes the exception.
- **Hosting.** If the API cannot run on an Iranian host with a fixed outbound IP, then
  neither gateway fits (section 3, precondition 1). Every sale then goes through invoice
  and bank transfer until that changes.
- **Direct debit.** If ZarinPal publishes a working direct-debit API, add optional
  automatic renewal for monthly plans under the cap. If Zibal documents one first, that
  favors Zibal.
- **Refunds.** If Homayar needs frequent refunds after 30 minutes, Zibal's documented
  refund API may be worth the corporate-banking cost.
- **Split payments.** If a provider approves in writing that one bill can be paid in
  parts, the gateway can cover larger bills. The buyer's daily limits still apply.
- **Merchant approval.** If ZarinPal refuses Homayar's business category, or its level
  rules (error -16) block a new merchant for too long, choose Zibal.

### 7.1 How a gateway connects to this repository

All of this is proposed work. Nothing here exists yet.

- **Build order: follow WP18.**
  - Step 5 says invoices and payment status come "before adding an eligible
    payment-service adapter" (`docs/planning/work-packages/WP18/README.md:15`).
  - This document follows that order:
    1. invoices, manual payment status, and the admin view;
    2. then the gateway adapter.
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
    - `provider` and `provider_ref` (the `authority`, the `trackId`, or the bank transfer
      reference), unique together;
    - `status`, `ref_id`, `created_at`, `verified_at`, `recorded_by` (the admin, for a
      bank transfer).
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
  - match bank transfers against open invoices, using the payment reference on each
    transfer and the company's bank statement. This is the main path, so it matters most.
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
    - for Zibal: `gateway.zibal.ir:443`, plus `api.zibal.ir:443` only if its report or
      refund API is used.
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
  `GET /api/v1/billing/payments/zarinpal/callback`.
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
   - Bind it to the workspace from our row, not from the query or the browser session.
     The buyer may come back with no session.
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

### 7.4 Data: what Homayar stores and what it never stores

- **Never reaches Homayar**, because of the hosted page: the full card number, CVV2,
  expiry date, PIN, and one-time password. The ZarinPal docs confirm this (section 5.1).
- **Store:**
  - `provider`, `provider_ref`, `ref_id` or `refNumber`;
  - the amount in rial, status, and timestamps;
  - `workspace_id` and `plan_key`.
  This is enough for support and reconciliation.
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
| **Second:** hosted payment page, card entry, 3-D Secure, PIN | Gateway | the chosen provider's page |
| Provider adapter (request, verify, inquiry, unVerified, reverse) | Build | thin HTTP client in FastAPI; skip the young SDKs |
| Callback route | Build | FastAPI route on the app origin (section 7.2) |
| SaaS egress proxy or NAT with a fixed Iranian IP | Build (deployment) | section 3, preconditions 1 and 2 |
| Reconciliation job (payments and payouts) | Build | same pattern as ADR 0010 |
| Admin views (payments, manual bank transfers, refunds) | Build | for option D and for refunds after 30 minutes |
| Refunds after 30 minutes | Build (manual bank transfer) | the ZarinPal refund API is undocumented |
| Moadian e-invoices | Buy | a trusted company (شرکت معتمد) or accounting software; details UNVERIFIED |
| VAT calculation on invoices | Buy, with an accountant | rate and exemption UNVERIFIED |
| Pre-invoice and contract for Enterprise | Build a simple template, or use accounting software | |
| License file signer and offline checker | Build | section 7.5 |
| e-Namad | Obtain from enamad.ir | required by the providers |
| Payout settlement | Gateway | next-day settlement |
| Status and uptime monitoring | Build a simple check | the provider status pages were not usable |

## 8. What the website must show

This section feeds the next website work unit. Items marked (law) come from e-commerce
law articles 33 and 34. Items marked (Zibal) come from Zibal's terms. They apply only if
Zibal is chosen. (e-Namad) marks the trust mark.

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
     - the payment reference to put on the transfer.
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
   gateway. Say what payment data Homayar keeps (section 7.4).
9. **e-Namad badge:** shown on the site (e-Namad). Placement per e-Namad rules, which were
   UNVERIFIED.

**Plain-language points about data handling for the buyer:**

- "You enter your card on the bank's payment page, not on Homayar."
- "We never see or store your card number, CVV2, or PIN."
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
4. **Foreign customers.** Stay Iran-only at launch (recommended), or ask a sanctions lawyer
   about a foreign entity or other routes? US customers look blocked (section 5.4).
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
19. Stripe and sanctions: confirm that Homayar cannot use Stripe, and what foreign sales
    are allowed.
20. What is the status of any Iranian personal-data protection law, and what does it
    require for billing data? Not researched.

For the provider, or Shaparak and the Central Bank (ask the chosen provider first):

21. **Per-card and per-person daily limits** for internet payments in 1405. The only
    figure found (400M toman per person per day) comes from a ZarinPal blog post, not from
    the Central Bank circular (section 5.5).
22. **Split payments.** May one invoice be paid as several gateway payments under the cap?
23. **Zibal's cap for Homayar.** Confirm 200M toman per payment live, and whether Zibal
    would set a lower cap for Homayar's business category.
24. **Using the gateway from abroad.** Does a server outside Iran count as "using the
    acceptance tool abroad" (ZarinPal terms 4-1)? Does ZarinPal accept registered IPs
    outside Iran? Zibal says no.
25. Is direct debit ("Payman") still offered by ZarinPal, or offered by Zibal? At what fee,
    and with which limits?
26. What is ZarinPal's verify time window before the automatic return of funds?
27. Is ZarinPal's GraphQL refund API supported for merchants? What are its fee and time
    limit?
28. Does a subdomain match the "registered domain" for callbacks?
29. Is VAT added to ZarinPal's 0.5% fee?
30. **Holding funds.** When may the provider hold, delay, or freeze settlement? ZarinPal's
    terms allow it to block an account, for example for use on unrelated domains (terms
    4-4). They do not state rules for holding funds. Not researched for Zibal.
31. **Disputes.** How does a card holder dispute a payment, through the provider or
    Shaparak? What must Homayar provide? Not researched.
32. **Lead time and order.** How long do e-Namad, the tax tracking code, and merchant
    approval take? In what order must they be done? Zibal's archived page says a no-star
    e-Namad activates "within 20 minutes". Merchant review times were not found for
    ZarinPal.
33. **Provider outage.** Is there an official status feed or notice channel? Both status
    pages were unusable on the read date. Until then, the checkout should show "online
    payment is not available now; request an invoice" when the provider does not answer.

For the build (not open decisions, but not designed here):

34. The audit events for payment verified, payment recorded by an admin, refund, and
    license issued. They should use the existing audit pattern (ADR 0010 audits
    `usage.reconciled`, `0010-usage-ledger-and-reconciliation.md:45-47`).

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

**Secondary** (support only; they carry no claim alone): news sites on the 1405 VAT rate
(entekhab.ir, eghtesadonline.com, nabzgheymat.ir); blogs on invoice types (yassystem.com,
keysuntsp.com). None of these was used as the only source for a claim.
