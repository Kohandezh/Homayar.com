# Payment gateways for Homayar: ZarinPal vs Zibal

Status: In Review

Date: 2026-09-27

Author role: researcher (`pay-t1-res`, research team `pay-t1`). Base commit: `1b34472`.

## 1. Summary and recommendation

Homayar should integrate **ZarinPal** first. It should reach ZarinPal through a small
provider adapter in the FastAPI backend, so Zibal can be added later without a redesign.
Homayar should sell SaaS as **prepaid periods** (monthly or yearly), not as automatic
charges, because neither provider documents automatic recurring charges today. Enterprise
plans and large self-hosted licenses should use a **pre-invoice plus bank transfer** path.
Self-hosted licenses should be **signed license files** that the customer's server checks
offline, so a self-hosted install never calls a payment provider. Card data never reaches
Homayar, because the buyer types the card on the provider's hosted payment page. Homayar
stores only a payment reference, the amount, and the status.

Why ZarinPal and not Zibal:

- ZarinPal's fee is lower: 0.5% capped at 16,000 toman plus 500 toman per payment.
  Zibal charges 1% capped at 20,000 toman, plus 10% VAT on the fee.
- ZarinPal's docs loaded on the read date. They have a sandbox and a full error list.
- ZarinPal has a free "reverse" within 30 minutes.
- Zibal's help site timed out on the read date. Its refund API needs a paid corporate
  banking add-on.

Two things would make Zibal the better choice:

- Homayar's servers are hosted in a way that ZarinPal refuses.
- Zibal documents automatic recurring charges and ZarinPal does not.

Foreign customers cannot pay through either provider. That is an owner decision (section 9).
Tax invoices (سامانه مودیان, the Moadian e-invoice system) are Homayar's job, not the
gateway's. They need an accountant.

Confidence: medium. The provider facts come from their own docs and pages, read
2026-09-27. Several legal facts could not be read from primary sources, and each one is
marked UNVERIFIED.

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
  - ADR 0031 puts all outbound traffic behind a deny-by-default egress proxy
    (`docs/architecture/decisions/0031-egress-denial.md:58-65`).
  - The air-gapped default has "no outbound path at all"
    (`docs/architecture/decisions/0031-egress-denial.md:64-65`).
  - Non-AI outbound traffic is governed by the network layer only
    (`docs/architecture/decisions/0031-egress-denial.md:85-86`).
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
    CI skips that step when the file is missing (`.github/workflows/ci.yml:79-84`,
    `docs/operations/ci.md:48-51`).

## 4. Options considered

- **A. ZarinPal only.**
- **B. Zibal only.**
- **C. Both at once** (for example, one as a failover for the other).
- **D. A gateway for small SaaS payments, plus invoice and bank transfer for Enterprise
  and large self-hosted sales.** This is not a replacement for A or B. It is a second path
  that sits next to one of them.
- **E. Do nothing or defer.** Keep "manual" billing (`Homayar/backend/schemas/billing.py:40`)
  and sell every plan by invoice and bank transfer. Add a gateway later.
- **F. Keep Stripe.** Listed because the old site uses it.

Option F is not realistic. Stripe's own restricted-business page says it does not serve
"Persons located in, resident in, or a citizen of, or products or services originating
from ... Cuba, Iran, North Korea, and Syria" (Stripe, "Prohibited and Restricted
Businesses", updated 2026-09-22). The operator in the terms is "KSF Company"
(`content/legal/terms.md:5`) with a `.ir` contact address. INFERENCE: this is an Iranian
service, so Stripe is not available. A lawyer should confirm this before the Stripe code
and terms are removed.

## 5. Evidence

All external facts were read on 2026-09-27 unless a line says otherwise. "Wayback" means
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

## 6. Comparison

| Item | ZarinPal | Zibal |
| --- | --- | --- |
| Flow | request, hosted page, callback, verify | the same |
| Docs on 2026-09-27 | live and server-rendered; sandbox with any UUID | help site timed out; OpenAPI files on `api.zibal.ir` loaded |
| Official Python SDK | yes, young (2025), Python 3.12 or newer | no maintained one (2019 sample, 2020 PyPI) |
| Fee | 0.5% capped at 16,000 toman, plus 500 toman; VAT not stated | 1% capped at 20,000 toman, plus 10% VAT; minimum about 2,000 toman (INFERENCE) |
| Example fee on a 1,000,000-toman monthly plan (INFERENCE, arithmetic) | 5,500 toman | 11,000 toman |
| Example fee on a 10,000,000-toman yearly plan (INFERENCE, arithmetic) | 16,500 toman | 22,000 toman |
| Maximum per payment | 100,000,000 toman | 200,000,000 toman (Wayback, 2026-05-14) |
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

## 7. Recommendation

1. **Gateway: ZarinPal, through a provider adapter.**
   - The reasons are in section 1 and the table in section 6.
   - "Both at once" (option C) is not worth it at launch. It would double the merchant
     onboarding, the secrets, the IP registration, and the reconciliation work. The two
     APIs have the same shape, so a second provider is cheap to add later.
2. **Subscriptions are prepaid periods.**
   - A monthly plan is paid each month through the hosted page.
   - A yearly plan is paid once a year.
   - Homayar sends reminders before the end of the period.
   - After the end date there is a short grace period (`PAST_DUE`). Then the subscription
     becomes `EXPIRED`.
   - There are no automatic charges. The old terms promise automatic renewal
     (`content/legal/terms.md:19`), so they must change.
3. **Enterprise and large self-hosted sales use invoice and bank transfer (option D).**
   - The flow is: pre-invoice (پیش‌فاکتور), contract, bank transfer (حواله), then the
     Moadian e-invoice.
   - The reasons:
     - these buyers are companies that need a full e-invoice for their tax credit;
     - they often pay in installments or on a purchase order;
     - their amounts can pass ZarinPal's 100,000,000-toman cap.
   - Smaller self-hosted licenses can use the gateway on Homayar's website.
4. **Self-hosted licenses are signed files checked offline (section 7.4).**
5. **Iran only at launch.** Foreign sales wait for an owner decision and a lawyer
   (section 9).

**What would change this recommendation:**

- Homayar's API servers cannot get a fixed outbound IP that ZarinPal accepts. Then check
  Zibal, but Zibal refuses IPs outside Iran.
- ZarinPal publishes a working direct-debit API. Then add optional automatic renewal.
  Or Zibal documents one first, which would favor Zibal.
- Homayar needs frequent refunds after 30 minutes. Then Zibal's documented refund API may
  be worth the corporate-banking cost.
- Fees change. Recheck both pricing pages before signing up.
- ZarinPal refuses Homayar's business category, or its level rules (error -16) block a
  new merchant for too long.

### 7.1 How a gateway connects to this repository

All of this is proposed work. Nothing here exists yet.

- **A new payment table is needed.**
  - `Subscription.provider_subscription_id` (`Homayar/backend/database/saas_models.py:885`)
    fits a Stripe-style subscription.
  - An Iranian gateway has no subscription object, only one payment per period.
  - Keep `Subscription.provider = "zarinpal"` and leave the two ID columns empty.
  - Add a `payments` table (INFERENCE, design proposal) with:
    - `workspace_id`, `plan_key`, `interval`;
    - `amount_rial` (integer);
    - `provider` and `provider_ref` (the `authority` or `trackId`), unique together;
    - `status`, `ref_id`, `created_at`, `verified_at`.
- **Plan prices.** `Plan` has no price columns (`saas_models.py:731-739`). Add monthly and
  yearly prices in rial, or a separate `plan_prices` table. The API's `price_published:
  False` and `pricing_mode: "manual_quote"` (`schemas/billing.py:14-15`) change only for
  plans that have a price.
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
  do three things:
  - read ZarinPal's `unVerified` list and `inquiry`;
  - verify payments that were paid but never verified (for example, the buyer closed the
    browser), before the provider returns the money;
  - compare payouts with the GraphQL `Reconciliation` data.
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
    - `payment.zarinpal.com:443`;
    - `next.zarinpal.com:443`, only if the GraphQL reconciliation is used.
  - INFERENCE: in the ADR 0031 topology only `egress-proxy` has a route out
    (`0031-egress-denial.md:50-57`). Its public IP is the one IP to register with
    ZarinPal.
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
  with the provider (at most 5 for ZarinPal). Zibal also needs that IP to be in Iran.
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
  answer "which card paid?". Keep it for a short time.
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

1. The customer buys on Homayar's side: the SaaS console with the gateway, or an invoice
   and bank transfer for Enterprise.
2. Homayar's billing backend issues a **license file**. It is a small JSON document with
   the customer, edition, seat or hour limits, and start and end dates. It is signed with a
   private key that only Homayar's billing server holds.
3. The self-hosted install checks the signature with a public key built into the release.
   It makes **no network call**, so ADR 0031's "no outbound path at all" still holds
   (`0031-egress-denial.md:64-65`).
4. Renewal means a new file. After the end date the install warns during a grace period,
   then limits features. The rule is set by contract.
5. Limit: an offline file cannot be revoked early. The contract covers that case.

### 7.6 Tools Homayar needs

| Tool | Build, buy, or gateway | Notes |
| --- | --- | --- |
| Hosted payment page, card entry, 3-D Secure, PIN | Gateway | ZarinPal StartPay page |
| Provider adapter (request, verify, inquiry, unVerified, reverse) | Build | thin HTTP client in FastAPI; skip the young SDK |
| Callback route | Build | FastAPI route on the app origin (section 7.2) |
| Plan prices, periods, grace, expiry | Build | extends `Plan` and `Subscription` (section 7.1) |
| Renewal reminders and dunning emails | Build | dunning means reminders after a missed payment; uses the SMTP settings |
| Payments table and ledger | Build | unique `(provider, provider_ref)` |
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
   - Plans, monthly and yearly prices in toman or rial, and what each plan includes
     (law 33 الف, د).
   - Whether prices include VAT, and the tax amount (law 33 د). The rate is UNVERIFIED, so
     ask an accountant.
   - A plain line: "Subscriptions do not renew automatically. We remind you before your
     period ends."
   - Enterprise: "Contact us for a quote, a contract, and invoice payment."
   - How long the prices are valid (law 33 هـ).
2. **Checkout (in the console, `/app/`):**
   - Step 1: choose a plan and a period.
   - Step 2: review the total with tax. Show the terms and refund policy links, with a
     clear "I agree" (law 33 و, law 34 ج).
   - Step 3: "You will now go to the secure bank payment page of ZarinPal. Homayar never
     sees your card number."
   - Step 4: result page with the provider's reference number (Zibal requires this; show
     it with ZarinPal too).
   - Step 5: a receipt email with the invoice or a link to it.
3. **Terms of service:** rewrite section 3 (`content/legal/terms.md:15-25`):
   - prices in rial;
   - prepaid periods with no automatic renewal;
   - the grace period and what happens after it;
   - the seven-working-day withdrawal right and when it starts (law 37, 38);
   - how the service works (law 34 د).
   - A lawyer must decide how article 38 د exceptions and business buyers apply.
4. **Refund and cancellation policy:** how to ask, how long a refund takes, and the rule
   that the full amount comes back within the withdrawal period (law 38 ج).
5. **Contact page:**
   - company name and trade name;
   - postal address for complaints;
   - phone and email (law 33 ب, ج; law 34 الف);
   - a support phone, email, and contact link (Zibal).
6. **About page:** who runs Homayar (the legal entity named in the terms, "KSF Company",
   `content/legal/terms.md:5`).
7. **Privacy policy:** replace Stripe (`content/legal/privacy.md:50`) with the chosen
   gateway. Say what payment data Homayar keeps (section 7.4).
8. **e-Namad badge:** shown on the site (e-Namad). Placement per e-Namad rules, which were
   UNVERIFIED.

**Plain-language points about data handling for the buyer:**

- "You enter your card on the bank's payment page, not on Homayar."
- "We never see or store your card number, CVV2, or PIN."
- "We keep the payment reference number, the amount, and the date so we can support you
  and issue your tax invoice."
- "For company invoices we keep your company's invoice details, as tax law requires."

## 9. Open questions

For the owner:

1. **Foreign customers.** Stay Iran-only at launch (recommended), or ask a sanctions lawyer
   about a foreign entity or other routes? US customers look blocked (section 5.4).
2. **Hosting.** Where will the API run, and can it have a fixed outbound IP? Zibal refuses
   IPs outside Iran.
3. **Merchant identity.** Which legal entity signs up with ZarinPal (the terms name "KSF
   Company"), and does it already have e-Namad and a tax tracking code?
4. **Domains.** Will the public site and the app share one host name? This affects the
   callback domain rule (section 7.2).
5. **Grace period and reminders.** How many days of grace, and when to send reminders?
6. **Old Stripe code.** Remove it now, or leave it off? It is already disabled on cPanel
   builds ("forces commerce off").

For an accountant:

7. The VAT rate for 1405, and whether Homayar's SaaS or licenses are exempt.
8. Is a gateway receipt enough for B2C sales, or must Homayar issue its own e-invoice for
   every sale? Which invoice type fits B2B buyers?
9. Which trusted company or accounting software to use for Moadian e-invoices.

For a lawyer:

10. How article 37 and 38 withdrawal rights and the by-law exceptions apply to SaaS that
    starts at once, and to business buyers.
11. Stripe and sanctions: confirm that Homayar cannot use Stripe, and what foreign sales
    are allowed.

For the provider (ZarinPal):

12. Is direct debit ("Payman") still offered? At what fee, and with which limits?
13. What is the verify time window before the automatic return of funds?
14. Is the GraphQL refund API supported for merchants? What are its fee and time limit?
15. Does a subdomain match the "registered domain" for callbacks?
16. Is VAT added to the 0.5% fee?
17. Does ZarinPal accept registered IPs outside Iran?

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
- Failed:
  - https://help.zibal.ir/#docs and https://help.zibal.ir/IPG/API (timeout, HTTP 504)
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
  - https://www.shaparak.ir/ (redirect; not read in depth)
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
