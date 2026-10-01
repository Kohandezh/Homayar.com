# Legal, privacy, and transparency pages for the Homayar website

Status: In Review

Date: 2026-09-27. Revised 2026-09-28 for the owner's decision "The product will launch in Iran." Revised 2026-09-30 for review round 1.

Author role: researcher (`legal-t1-res`), foreman research team `legal-t1`, mission `20260927-homayar-website`.

Base commit for all repository citations: `1b34472`.

**This is research, not legal advice.** Every place that needs a lawyer, an accountant, or a provider to confirm is marked **[LAWYER]**, **[ACCOUNTANT]**, or **[PROVIDER]**. Section 9 collects them.

## Summary

Homayar launches in Iran. The website needs a small, honest legal set in Persian, built on Iranian law, e-Namad, the payment gateways, and what Iranian enterprise buyers expect. We recommend **11 pages at launch and 6 later** (Section 7). Persian is the binding text. English is a courtesy translation.

Findings that shape the launch:

1. **e-Namad comes first.** Zibal's own FAQ says it gives no payment gateway to a site without e-Namad [S39]. ZarinPal says the same on its own site [S84]. So the site must pass e-Namad before it can sell online. An archived official e-Namad FAQ (2022) says the gateway is cut off when the e-Namad file is cancelled [S87]. The e-Namad page list could not be read: enamad.ir was unreachable, and the requirements checklist is shown only to applicants. **[PROVIDER]**
2. **Iranian law has no data protection code, but it has hard rules.** E-Commerce Law Art 58 needs explicit consent to process data about a person's "psychological condition", with one to three years in prison for breaking it [S30]. Emotion scores of callers or agents may fall under it. Art 58 binds whoever stores or processes the data. In SaaS that includes Homayar, so an indemnity from the customer may not protect Homayar (Inference, Section 4.1). Until an Iranian lawyer answers, do not launch voice-based emotion features. Constitution Art 25 forbids recording and disclosing phone calls "except by order of law" [S33]. We found no rule that allows or forbids a business recording its own calls with notice. **[LAWYER]**
3. **Scores cannot fire anyone by themselves.** The Labour Law lets an employer end a contract for failure in the assigned duties, or for breaking the workplace disciplinary rules after written warnings, only with a positive opinion of the Islamic Labour Council (or the guild or the Board of Inquiry) (Art 27) [S83]. Staff of government-linked buyers may be under other employment rules (Section 4.2.4). Homayar's scores are evidence for a human process, not a decision.
4. **A new Iranian AI law exists.** The Guardian Council approved the National AI Development bill on 1405/06/04 and said it can now be called a "law" [S81]. Secondary copies say it lists "ranking and labeling of persons" and "employment" as risk factors [S82]. The official text could not be read. Agent scoring may fall into a higher risk class. **[LAWYER]**
5. **Government-linked buyers need AFTA.** AFTA's rules (version 1.0, 1398) say executive bodies must buy domestic enterprise software that holds AFTA's security evaluation certificate. They also put patch, vulnerability, and reporting duties on the vendor [S70, S71]. Homayar must not claim this certificate until it has one.
6. **The old legal text describes a different company.** It names Stripe, USD prices, automatic renewal, website analytics, and security claims nobody has verified (`content/legal/terms.md:17-23`, `content/legal/privacy.md:7`, `content/legal/privacy.md:56-60`). Consumer Protection Law Art 7 bans false or misleading information [S31]. Rewrite the pages. Do not translate them.

The EU rules (GDPR and the EU AI Act) do not drive the launch. They apply only if a trigger happens, for example an EU customer or an EU office. Section 4.8 keeps the verified EU evidence in short form, with the triggers. One EU finding matters now for product copy: the old catalog's agent stress and burnout features are prohibited in the EU. They also carry the Art 58 risk in Iran, so remove them in both markets.

Most official Iranian law sites could not be reached from our network, on 2026-09-27 and again on 2026-09-28 (qavanin.ir, rc.majlis.ir, enamad.ir, shaparak.ir, cbi.ir, tax.gov.ir, and others). We used official mirrors and archived copies of official pages where we could. Everything else is marked **unverified**. An Iranian lawyer must check the unverified items before launch.

## 1. Decision or question, in the owner's words

Owner (translated): "Privacy policy, terms and conditions, GDPR pages, and the laws we must follow for this work must be considered. Transparency pages must be considered. And any other topic we must follow and that must be shown on the site to inform the user must also be checked. We can look at existing services in this field and measure ourselves against them."

Owner, on PR #8 (2026-09-28): "The product will launch in Iran."

The one question this spike settles: **which legal, privacy, and transparency pages the Homayar website must have at its Iran launch, what each must contain, and which laws and rules apply, for SaaS and for self-hosted. And what would change if Homayar later sells outside Iran.**

## 2. Constraints

From the owner and the SPEC:

- Homayar analyzes recorded customer-service calls: transcription, quality scores for call-center agents, sentiment, problem detection, coaching (`Homayar/README.md:3-18`).
- Two deployment modes. SaaS: Homayar processes recordings for customers. Self-hosted: the customer runs it, and Homayar may never see the data.
- The owner wants Homayar to hold as little sensitive data as possible.
- Payments go through an Iranian gateway (ZarinPal or Zibal). Another team chooses which.
- Launch market: Iran. Target buyers include banks, telecoms, insurers, and government-linked organizations (SPEC Addendum 2).
- The new website is in Persian (fa) and English (en). Persian is the primary language of the legal pages (SPEC Addendum 2).

From this repository at `1b34472`:

- The operating company's legal name in the site config is "Kohan System Farda" (`src/lib/site.ts:3`). The old terms call it "KSF Company" (`content/legal/terms.md:5`). The contact email is `info@ksf.ir` (`src/lib/site.ts:12`).
- The old site has six locales: fa, en, de, ar, fr, es (`src/lib/content.ts:6`). The new site has two.
- The old footer has Product, Company, and Resources groups (`src/lib/site.ts:26-53`). Its only legal links are Privacy Policy and Terms of Service, in the Resources group (`src/lib/site.ts:47-52`).
- The website collects personal data through three forms:
  - Demo: name, email, company, team size, message (`src/app/api/demo/route.ts:8-12`).
  - Contact: first name, last name, mobile, email, message (`src/app/api/contact/route.ts:8-12`).
  - Booking: time slot, name, email, company, message, time zone (`src/app/api/book/route.ts:16-21`).
- Form data is stored in a database (`src/app/api/demo/route.ts:33`, `src/app/api/contact/route.ts:48`, `src/app/api/book/route.ts:66`). The database is Neon PostgreSQL (`src/lib/db.ts:1`, `README.md:13`).
- When no database is configured, the forms write personal data to the server log instead: email (`src/app/api/demo/route.ts:56`), name and mobile (`src/app/api/contact/route.ts:62`), email and slot (`src/app/api/book/route.ts:98`).
- The API routes read the client IP address from request headers for rate limiting (`src/lib/rate-limit.ts:28-35`). The IP is kept only in the memory of one server instance and removed when its time window ends (`src/lib/rate-limit.ts:6-7`, `src/lib/rate-limit.ts:17-24`).
- The documented host is Vercel (`README.md:79-85`).
- Booking confirmations are sent through Resend, a US email API (`src/lib/email.ts:30`).
- Neon, Vercel, and Resend are the old site's providers, all outside Iran. We do not know the new site's stack. The website team must confirm or replace each one. The privacy policy and subprocessor list must name whatever is really used.
- The repository shows no recording of Homayar's own sales or demo calls. Booked consultations happen "Online (link to follow by email)" (`src/app/api/book/route.ts:108`). If Homayar records its own calls, it is responsible for those recordings and must tell the participants (Section 4.5).
- The browser stores two things in `localStorage`: the chosen language (`src/lib/locale.tsx:20`, `src/lib/locale.tsx:59`) and a shopping cart (`src/lib/cart.tsx:14`, `src/lib/cart.tsx:55`).
- A search of `src/` for analytics scripts, tag managers, and `document.cookie` found none. Fonts come from `next/font/google` (`src/app/layout.tsx:2`). The Next.js docs say these fonts are "downloaded at build time and self-hosted" and "No requests are sent to Google by the browser" [S13]. Inference: the website sets no tracking cookies and calls no foreign service from the browser today. The website team must confirm this on the live site with the browser network panel. Note: the nextjs.org page describes the current Next.js release. The repository pins Next.js `16.2.9` (`package.json:22`). `AGENTS.md` says to read the docs in `node_modules/next/dist/docs/`, but `node_modules` is not installed in this worktree, so we could not check the pinned version's docs. The live network check covers this gap.
- The self-hosted package has "No model download, telemetry, font CDN, analytics beacon, package install, license heartbeat" at runtime (`docs/planning/homayar-commercial-blueprint.md:452`). Support bundles exclude "raw audio/transcripts, secrets and customer identities" unless the customer approves a separate export (`docs/planning/homayar-commercial-blueprint.md:458`). License expiry "must not delete recordings" and there is "No concealed remote kill switch" (`docs/planning/homayar-commercial-blueprint.md:460`). These are plans, not shipped features.
- Proposed SaaS retention defaults are "original audio 30 days, transcript/findings 90 days, minimal operational/audit metadata 365 days". The blueprint calls them product defaults, "not statutory retention requirements" (`docs/planning/homayar-commercial-blueprint.md:562`). The old privacy policy says a different default: 12 months (`content/legal/privacy.md:43`).
- "No Homayar certification was verified" (`docs/planning/homayar-commercial-blueprint.md:605`).
- The blueprint flags that US-linked cloud or software for Iran needs "eligibility and sanctions/export review" (`docs/planning/homayar-commercial-blueprint.md:607`). This spike did not research sanctions. It stays a **[LAWYER]** item.

## 3. Options considered

- **Option A. Do nothing.** Keep the old `content/legal/privacy.md` and `content/legal/terms.md`, and translate them.
- **Option B. Iran legal minimum.** Only what an Iranian online seller needs: company information, terms, privacy policy, refund policy.
- **Option C. Iran launch set (recommended).** Option B, plus what Iranian enterprise buyers and the product's risks call for: self-hosted license, acceptable use policy, AI transparency (with call-recording notice templates), data retention, security and vulnerability disclosure, data processing terms, and a subprocessor list. 11 pages at launch, all in Persian first. EU pages wait for a trigger (Section 4.8).
- **Option D. Iran plus EU-ready now.** Option C, plus a GDPR page, an EU-style DPA, and a cookie policy at launch. (This was the recommendation before the owner's Iran decision.)
- **Option E. Full enterprise trust center now.** Copy the largest vendors (Gong, Atlassian): certifications, SLA with credits, bug bounty, transparency report, trust portal.
- **Option F. A legal-page generator or template service.** Fill in a form on a policy generator and embed or paste its pages.
- **Option G. A full set drafted by a lawyer from the start.** Skip the research outline and have a law firm write every page.

Option C is phased: 11 pages at launch, 6 later.

## 4. Evidence

Sources are numbered [S1], [S2], and so on. Section 10 lists each URL with the date read. Text marked "Inference:" is our reading, not a quote.

A note on archived copies: some official Iranian pages (AFTA, the Supreme Council of Cyberspace, SAMANDEHI, the Majlis Research Center, e-Namad) could not be reached live. For these we read copies saved by the Internet Archive (web.archive.org) of the official page. An archived copy proves what the official page said on that date. It does not prove the rule is still in force today.

### 4.1 Who is who: roles and data subjects

Iranian law has no controller and processor concepts in any law we could read. The E-Commerce Law ties the processing of personal data to the consent of the person the data is about, a stated purpose, and rights to access, correct, and remove (Art 59) [S30]. The personal data protection bill has not become law, as far as we could verify (Section 4.2.5).

We still use the words "controller" (the party that decides why data is processed) and "processor" (the party that processes data for someone else). Iranian enterprise buyers who know international practice use them. They also match how the EU defines the split, if Homayar sells there later [S1 Art 4(7), 4(8); S2 paras 39, 81, 82].

Data subjects (the people the data is about):

| Data subject | Where their data appears |
|---|---|
| Callers (the customer's customers) | Call recordings, transcripts, sentiment, issues |
| Call-center agents (the customer's employees) | Call recordings, QA scores, coaching, dashboards; often also user accounts |
| Account users (customer admins, supervisors) | Login, roles, audit logs |
| Website visitors and leads | Demo, contact, and booking forms; locale and cart in the browser |
| Billing contacts | Invoices, payments |

Roles per deployment (Inference):

| Data | SaaS | Self-hosted |
|---|---|---|
| Call recordings, transcripts, scores, coaching | Customer decides why and how. Homayar processes for the customer. | Customer decides and runs everything. Homayar has no role, because it never sees the data. If Homayar staff get remote support access, Homayar processes for the customer during that access. |
| Training, improving, or evaluating Homayar's own models with customer calls (including an evaluation corpus, `docs/planning/homayar-commercial-blueprint.md:526`) | This is Homayar's own purpose. Homayar would then decide, not only process. Avoid by default. If wanted, use a separate written opt-in per customer (Section 8.8 item 7). | Not applicable, unless the customer sends data. |
| Account data, login, audit logs | Homayar decides (and processes audit logs kept for the customer). | Customer decides. |
| License, billing, and support contacts | Homayar decides. | Homayar decides. |
| Website forms, bookings | Homayar decides. The email and hosting providers process for Homayar. | Same. |

**The split above does not move Iranian criminal risk (Inference).** The table uses EU concepts. Iranian law does not have them. E-Commerce Law Art 58 makes "Storing, processing or distributing" psychological-condition data "without their explicit consent" illegal. Art 59 allows processing only "Upon the consent of the person" [S30]. Neither article names a party that processes for someone else. In SaaS, Homayar stores and processes the recordings and the scores. So Homayar may carry a direct duty under Art 58 and 59, and the Art 71 penalty (one to three years in prison), if the customer did not get consent. An indemnity from the customer (Section 4.5 item 8) can cover money. It does not cover criminal liability. This is why the terms need a customer warranty on consent (Section 4.5 item 7), and why a product gate before sentiment features run is worth considering. In self-hosted mode, Homayar does not store or process the data, so this risk stays with the customer. **[LAWYER]**

**Hybrid mode is out of scope until it ships.** The blueprint plans "local ASR and redaction followed by cloud reasoning", opt-in per tenant (`docs/planning/homayar-commercial-blueprint.md:470`), and an optional management plane that receives "health/version/license counters only" (`docs/planning/homayar-commercial-blueprint.md:468`). Inference: in the cloud-reasoning mode, Homayar and the cloud AI providers would process the redacted text, so the processing terms and the subprocessor list would apply. The blueprint says "Redaction cannot guarantee anonymization" (`docs/planning/homayar-commercial-blueprint.md:470`).

**Homayar as a "hosting provider".** The Computer Crimes Law sets duties for «ارائه دهندگان خدمات میزبانی داخلی» (domestic hosting providers): keep user information for at least six months after the subscription ends, and stored content for at least fifteen days (Art 33); block criminal content on order (Art 23); hand over preserved data on a judge's order (Art 34, 35) [S32]. Inference: Homayar SaaS stores customer recordings, so it may count as a domestic hosting provider. The law text we read does not define the term. **[LAWYER]**

### 4.2 Iranian law

Sources: WIPO Lex (the UN's World Intellectual Property Organization, an official English copy) for the E-Commerce Law [S30]; the Consumers and Producers Protection Organization (cppo.ir) for the Consumer Protection Law [S31]; the Ministry of ICT for the Computer Crimes Law [S32] and the Labour Law [S83]; the Guardian Council (shora-gc.ir) for the Constitution and for law approvals [S33, S42, S81]. The official databases qavanin.ir and rc.majlis.ir failed on both days.

#### 4.2.1 Electronic Commerce Law (قانون تجارت الکترونیکی), 1382

**The Persian wording of every article below is unverified**, because rc.majlis.ir and qavanin.ir could not be reached on 2026-09-27 or 2026-09-28. We quote the WIPO English text [S30]. The Persian text has legal force.

- **Who is a consumer.** "Any person acting for any purpose other than business or professional occupation" (Art 2(o)). Inference: a company buying Homayar for its call center is probably not a "consumer" under this law. A natural person buying for private use would be.
- **Information before the contract (Art 33).** Technical specifications; "The identity of the supplier, the trade name under which he is working and his address"; email and phone; all costs "including the price of goods or services, tax"; how long the offer is valid; terms for "payment ... delivery or implementation, cancellation, return, after-sales services".
- **Information sent separately (Art 34).** Complaints address; after-sales and guarantees; termination terms under Art 37 and 38; "Terms and conditions of cancellation in service contracts". It must be given "in a durable medium" (Art 35), which includes email (Art 2(s)).
- **Silence is not acceptance.** "The supplier shall not regard the silence of the consumer as contentment" (Art 43).
- **Right of withdrawal (Art 37, 38).** "at least seven working days in which to withdraw from the contract ... without penalty and without giving any reason" (Art 37). For services, the period starts "from the day the contract is effective", and only once the Art 33 and 34 information is given (Art 38). Refunds must be made "immediately" (Art 38(c)). Exceptions are set by an executive regulation (Art 38(d)). We could not read the list of exceptions [S43].
- **Sensitive data (Art 58).** "Storing, processing or distributing private data messages which may reveal ... the physical, psychological, or sexual condition of people, without their explicit consent is illegal."
- **Personal data (Art 59).** Processing is allowed "Upon the consent of the person", with a specified and clearly described purpose, collection "to the required extent", accuracy, access and correction, and "complete removal" on request.
- **Penalties.** Breaking Art 33 to 37: a fine of 10 to 50 million rials (Art 69). Breaking Art 58: "one to three year imprisonment" (Art 71). Later fine updates were not checked.

#### 4.2.2 Consumer Protection Law (قانون حمایت از حقوق مصرف‌کنندگان), 1388

Source: CPPO PDF [S31]. Verified on an official site.

- **Who is a consumer.** «مصرف‌کننده: هر شخص حقیقی یا حقوقی است که کالا یا خدمتی را خریداری می‌کند» ("any natural or legal person who buys goods or a service") (Art 1-1). Inference: unlike the E-Commerce Law, this covers Homayar's business customers and covers services.
- Suppliers are responsible for the soundness of goods and services under the law, the contract, or custom (Art 2).
- Suppliers must give a warranty and an invoice with the price and date (Art 3-1), and needed information (Art 3-2). Which services need a warranty is set by a ministry list (Art 3, Note 3). We did not read that list.
- The price or service fee must be shown "clearly and in writing" (Art 5).
- «تبلیغات خلاف واقع و ارایه اطلاعات نادرست ... ممنوع می‌باشد» ("False advertising and giving wrong information ... is forbidden") (Art 7).
- Late or partial performance means paying damages too (Art 19, Note).
- Criminal liability falls on the CEO or managing director (Art 16, Note).
- This law has no online withdrawal right. Inference: a business buyer is a "consumer" here but not under the E-Commerce Law (Section 4.2.1), so its refund rights come from the contract.

Inference: Art 7 makes unverified claims a legal risk, not only a trust risk. This covers certifications, "AES-256", "third-party penetration testing", and a "works-council documentation pack" (`content/legal/privacy.md:56-60`, `src/app/faq/content.tsx:93`). It also covers any AFTA, ISO, or knowledge-based claim before it is real.

#### 4.2.3 Computer Crimes Law (قانون جرایم رایانه‌ای), 1388

Source: Ministry of ICT site [S32]. Verified on an official site. Articles 1 to 54 became Articles 729 to 782 of the Islamic Penal Code (Art 55).

- **Interception (Art 2).** «هر کس به طور غیرمجاز محتوای در حال انتقال ارتباطات غیرعمومی ... را شنود کند» ("anyone who, without authorization, intercepts the content in transmission of non-public communications"): 6 months to 2 years prison, or a fine, or both.
- **Publishing private audio (Art 17).** Publishing or making available another person's private audio "without their consent", in a way that harms them or their reputation: 91 days to 2 years, or a fine.
- **Company liability (Art 19, 20).** A company is liable when an employee commits the crime with the manager's knowledge or "because of the manager's lack of supervision".
- **Hosting providers (Art 23, 33, 34, 35).** See Section 4.1.

Inference: a leak of call recordings could fall under Art 17. Strong access control is a legal need, not only good practice.

#### 4.2.4 Constitution and Labour Law

- **Constitution Art 22** (verified [S33]): «حیثیت، جان، مال، حقوق، مسكن و شغل اشخاص از تعرض مصون است، مگر در مواردی كه قانون تجویز كند» ("A person's dignity, life, property, rights, home, and job are protected from violation, except where the law allows").
- **Constitution Art 25** (verified [S33]): «...ضبط و فاش كردن مكالمات تلفنی ... استراق سمع و هرگونه تجسس ممنوع است، مگر به حكم قانون» ("recording and disclosing telephone conversations ... eavesdropping and any kind of spying are forbidden, except by order of law").
- **Labour Law Art 27** (verified, Ministry of ICT copy of the 1369 text [S83]): «هرگاه کارگر در انجام وظائف محوله قصور ورزد و یا آئین نامه های انضباطی کارگاه را پس از تذکرات کتبی نقض نماید کارفرما حق دارد در صورت اعلام نظر مثبت شورای اسلامی کار ... قرارداد کاررا فسخ نماید» ("If a worker fails in the assigned duties, or breaks the workplace disciplinary rules after written warnings, the employer may end the contract if the Islamic Labour Council gives a positive opinion"). The employer pays one month of the last wage per year of service. Without a Council, the guild association must agree. Without either, the Board of Inquiry (هیات تشخیص) decides (Art 27 notes, Art 158). Unjustified dismissal means return to work with back pay (Art 165) [S83].
- **The Labour Law has no article on employee privacy, monitoring, or call recording.** We searched the full text for the Persian words for privacy, private, monitoring, camera, recording, interception, inspection, secrets, confidential, personal, dignity. The only hits are unrelated (safety inspection, medical records in the personnel file, a secrecy duty of labour inspectors) [S83].
- We read the original 1369 text. Later amendments were not checked. **[LAWYER]**
- Staff of executive bodies may be under other employment rules (for example the Civil Service Management Law, which Section 4.4 cites for AFTA scope), not the Labour Law. Not researched. This matters for government-linked buyers' call-center agents. **[LAWYER]**

#### 4.2.5 Personal data protection bill and other data laws

- Verified: the Ministry of ICT unveiled the draft bill «صیانت و حفاظت از داده‌های شخصی» (draft dated Tir 1397) [S36, page dated 2018-07-28].
- Secondary only: 1403 news reports say the government's legal commission approved it, and later that it "went back from the floor to the committee" [S37, S38].
- A search of the Guardian Council site found no review of such a bill [S45]. Its status on 2026-09-28 is **unverified**. The legal pages must not claim compliance with it.
- A «قانون مدیریت داده‌ها و اطلاعات ملی» (National Data and Information Management Law) exists in search results on the Guardian Council site, and secondary copies of the new AI law refer to it [S82, S86]. We did not read its text or check whether it applies to private companies. **[LAWYER]**

#### 4.2.6 National AI Development Law, 1405

- **Verified** (Guardian Council, news 11421, published 14 Shahrivar 1405 / 5 September 2026 [S81]): «طرح ملی توسعه هوش مصنوعی که با اصلاحاتی در جلسه مورخ بیست و هشتم مردادماه یکهزار و چهارصد و پنج به تصویب مجلس شورای اسلامی رسیده است؛ در جلسه مورخ ۱۴۰۵/۰۶/۰۴ شورای نگهبان مورد بحث و بررسی قرار گرفت و ... مغایر با موازین شرع و قانون اساسی شناخته نشد» ("The National AI Development bill, passed by the Majlis with amendments on 28 Mordad 1405, was reviewed by the Guardian Council on 1405/06/04 and found not contrary to Sharia or the Constitution"). The Council's spokesperson said on the same day that it can now be called a "law" and will go through the steps to be communicated [S81, news 11703].
- **Secondary only** (ekhtebar.ir, zoomit.ir [S82]): the law has 12 articles. Art 1(ح) lists risk factors including «رتبه بندی و برچسب زنی افراد» ("ranking and labeling of persons") and «اشتغال» ("employment"). Art 7 sets risk-based assessment of AI systems every six months. Art 9 refers to privacy and the National Data and Information Management Law. **Unverified**: the official text could not be read.
- Earlier, the Supreme Council of Cultural Revolution adopted a National AI Document (a strategy, not binding rules on vendors). It lists privacy and non-discrimination as ethics principles and calls for new bills [S80, archived official page].
- Inference: if the secondary text is right, agent QA scoring ("ranking and labeling of persons" in "employment") may fall into a higher risk class once the implementing rules exist. Homayar should watch for those rules. **[LAWYER]**

#### 4.2.7 e-Namad, payment gateways, and SAMANDEHI

- **Official e-Namad page list: unverified.** enamad.ir and trustseal.enamad.ir timed out live on 2026-09-27 and 2026-09-28.
- **Archived official e-Namad pages** (tried on 2026-09-30; newest copies are from 15 August 2022, so they prove what e-Namad said then, not today [S87, S88]):
  - The FAQ lists the steps. One step is «بررسی چک لیست الزامات نماد اعتماد الکترونیکی» ("review of the e-Namad requirements checklist"). The FAQ says the checklist items are shown in the applicant's own panel (Q3.7). We found no public copy of the checklist. So the page list is still unverified.
  - Address: the FAQ says every seller must give its postal address, citing «بند (ب) ماده 33 قانون تجارت الکترونیکی» (E-Commerce Law Art 33(b)) (Q3.2).
  - The public e-Namad profile shows: business name, website, validity, performance record, date issued, response hours, services and licenses, the holder's name, business address, landline phone, and email (Q1.6).
  - Gateway link, official: after the e-Namad file is cancelled, «درگاه پرداخت اینترنتی نیز قطع می گردد» ("the internet payment gateway is also cut off") (Q10.5).
  - Validity is two years (Q1.2). A new e-Namad gets one star. Two to five stars come from a separate rating (Q14.2, and the rating page [S88]). This differs from the ZarinPal blog's description of levels [S41]; the archived official text wins.
  - Showing the e-Namad image before it is issued counts as forging a government mark (Q1.5).
  - An archived official page says a business doing a guild activity (فعالیت صنفی) also needs a virtual-store business license (پروانه کسب), and gets e-Namad only when that license is issued. Otherwise e-Namad is enough [S89]. Whether selling SaaS is a guild activity is not known. **[LAWYER]**
- **Zibal** (verified, gateway's own site [S39]): «برای دریافت درگاه، داشتن اینماد معتبر، کد رهگیری مالیاتی به نام متقاضی و حداقل ۱۸ سال سن الزامی است» ("To get a gateway, a valid e-Namad, a tax tracking code in the applicant's name, and being at least 18 are required"). To "Is a gateway offered to sites without e-Namad?", it answers «خیر» ("No").
- **ZarinPal** (verified, gateway's own site [S84]): «دریافت اینماد برای ساخت درگاه پرداخت زرین پال امری الزامیست» ("Getting e-Namad is required to create a ZarinPal payment gateway"). The same page quotes a Shaparak circular: «از سوم آذرماه، حین ثبت اطلاعات پذیرندگان جدید، اطلاعات ثبت شده در خصوص نماد اعتماد الکترونیکی نیز بررسی می گردد و از تخصیص پایانه به موارد نامنطبق جلوگیری به عمل خواهد آمد» ("From 3 Azar, when new merchants are registered, their e-Namad data is checked, and merchants that do not match get no terminal"). The Shaparak circular itself is **unverified** (shaparak.ir unreachable).
- ZarinPal's terms forbid «استفاده از ابزار پذیرش در خارج از کشور» ("using the acceptance tool outside the country") and «اخذ هرگونه مبلغ اضافی از خریداران» ("charging buyers any extra amount") (clause 4-1). The gateway may be used only on the merchant's own domain (clause 4-4) [S40].
- ZarinPal's blog (describing e-Namad, so secondary for e-Namad's rules [S41]) says e-Namad has three levels: no star (temporary), one star, and two stars; two stars need SSL. The archived official FAQ describes stars differently (see above). It says the physical address marked on the map is shown to site visitors, and that companies apply with the Official Gazette notice, articles of association, and the CEO's ID.
- **SAMANDEHI** (ساماندهی, the website registration scheme of the Ministry of Culture): an archived official page from 2012 says «کلیه سایتها ... که دارای دامنه منحصر به فرد ( Domain ) می باشند، ملزم به تکمیل فرم ثبت نام در طرح ساماندهی سایتهای ایرانی می باشند» ("All sites ... that have their own domain must register in the Iranian websites organizing scheme") [S78]. The scheme runs under a Cabinet regulation since Dey 1385 [S78]. samandehi.ir itself returned 403 on 2026-09-28. The current scope and the seal to show are **unverified**. Inference: Homayar's own-domain site would fall under the 2012 wording. **[LAWYER]**

Inference: Homayar needs e-Namad before it can take online payments. The site must then show the e-Namad seal, the company's identity, address, and contact details. **[PROVIDER]**: the e-Namad center must confirm the exact list of required pages.

#### 4.2.8 Language, currency, and tax

- **Constitution Art 15** (verified [S33]): Persian is the official language. «اسناد و مكاتبات و متون رسمی» ("official documents, correspondence and texts") must be in Persian. Inference: this mainly covers state texts. For Iranian customers, the Persian text of the legal pages should be the binding text.
- **Law on foreign words (1375)** (secondary only [S44], unverified): it targets product names, shop names, and signs. Inference: it does not clearly forbid an English version of the site.
- **Currency.** The Guardian Council approved removing four zeros from the national currency, with "a multi-year transition period" [S42, 17 Aban 1404]. The new unit's name and ratio are unverified (cbi.ir unreachable). We found no rule that forces rials over tomans on a website. Inference: show prices in rials, or clearly label tomans, to avoid "wrong information" under Consumer Protection Law Art 7. **[ACCOUNTANT]**
- **Electronic invoices.** tax.gov.ir and intamedia.ir (the tax administration's portal, per search results) timed out. What the «سامانه مودیان» (taxpayer system) requires an online seller to issue or show is **unverified**. Zibal requires a tax tracking code to open a gateway [S39]. **[ACCOUNTANT]**

### 4.3 Scoring employees and recording calls: what Iranian law requires

This section rechecks the product rules for an Iran-only launch. The EU AI Act and GDPR do not apply to an Iran-only launch unless a trigger in Section 4.8 happens.

**Features and what Iranian law says (Inference, from Section 4.2):**

| Feature | What Iranian law says | Launch rule |
|---|---|---|
| Agent emotion, stress, or burnout from voice (old catalog: "Agent stress trend dashboards", `src/data/products.json:243`; "Early burnout signals", `src/data/products.json:232`; agent "emotional state" from "pitch, pace, energy, silence", `src/data/products.json:225`) | Likely "psychological condition" data under E-Commerce Law Art 58: explicit consent from each agent, 1 to 3 years prison if broken [S30]. Consent from an employee to the employer is hard to call free (Inference). | Do not offer. |
| Caller emotion from voice ("Fused acoustic + transcript sentiment scoring", `src/data/products.json:240`) | Same Art 58 question for callers. A call-start notice may not be explicit consent. | Do not offer at launch. Revisit after an Iranian lawyer answers. |
| Caller sentiment from transcript text | Possibly Art 58, if read as a finding about a person's mental state. Less likely if it describes the conversation ("the call turned negative"). Art 59 applies in any case: consent, stated purpose, access, correction, removal. In SaaS, Homayar stores and processes the scores, so it may share this risk (Section 4.1). | Offer, described as a property of the conversation, not of the person. Only after the customer warrants consent where Art 58 applies (Section 4.5 item 7). Consider keeping it off until the customer confirms this in the product. |
| Agent QC scores such as "empathy" and "tone" (`Homayar/README.md:15`, `Homayar/backend/database/models.py:107`, `Homayar/backend/database/models.py:112`) | Personal data under Art 59. No Iranian text we read bans AI scoring of employees. The new AI law may place "ranking and labeling of persons" in "employment" in a higher risk class (secondary [S82]). Constitution Art 22 protects dignity and jobs [S33]. | Offer, with the three product rules below. |
| Team Pulse per-agent trends ("Spot agents needing support 2–3 days earlier via sentiment and score trends", `src/data/products.json:822`) | Same as QC scores. If it infers the agent's mood or wellbeing, the Art 58 question returns. | Offer only as "call difficulty" and "score trend". Never as agent mood, stress, or wellbeing. |
| Decisions about pay, discipline, or dismissal | Labour Law Art 27: dismissal needs a positive opinion of the Islamic Labour Council, guild, or Board of Inquiry; written warnings come first when the reason is breaking disciplinary rules [S83]. Staff of executive bodies may be under other rules (Section 4.2.4). | Scores are evidence for a human process. The product never decides. |

**Three product rules for agent scoring at launch.**

1. **Text only.** Scores come from the transcript, not from voice features such as pitch, energy, or speaking rate. Today the QC prompt says "Analyze the call transcript" (`Homayar/backend/prompts/qc_en.txt:1`). The old catalog proposed "QA scorecards can weight empathy criteria with acoustic evidence" (`src/data/products.json:226`). Do not build that.
2. **Behavior, not feelings.** "Empathy" means observable acts, for example "acknowledged the customer's problem" or "offered a next step", checked against rubric evidence in the transcript. Not "the agent felt empathetic" or "the agent was stressed". The QC report's call-level `sentiment` field does not say whose sentiment it is (`Homayar/backend/database/models.py:117`). Label it as customer sentiment, or split it by speaker, before the website describes it.
3. **Human review with authority.** A supervisor can see the evidence, change a score, and record why. Agents can see their own scores (the old Team Pulse copy promises a "Transparent agent self-view", `src/data/products.json:833`; confirm it exists). Appeals are planned work, not shipped (WP14, `docs/planning/homayar-commercial-blueprint.md:1287-1299`).

**Which rules come from Iranian law, and which are EU-driven:**

- **Required by Iranian texts we read:** explicit consent for "psychological condition" data (Art 58); consent, purpose, access, correction, and removal for personal data (Art 59); the Labour Law's human process for dismissal (Art 27); no unauthorized interception (Computer Crimes Law Art 2); no publishing private audio without consent (Art 17); protection of dignity and jobs (Constitution Art 22).
- **Not required by any Iranian text we read, but recommended:** rules 1 and 2 above (text only; behavior, not feelings). Reasons: they keep emotion data out of Art 58 risk; they fit the likely risk rules of the new AI law; they cost little; and they keep the product sellable in the EU without a rebuild (in the EU, rules 1 and 2 are what keep agent scoring outside the AI Act ban, Section 4.8).
- **EU-only duties** (apply only after a Section 4.8 trigger): the AI Act ban on workplace emotion recognition and its dates; AI Act transparency and worker-information duties; GDPR Art 22 on automated decisions; GDPR Art 28 processor contracts.

**Recording calls in Iran.**

- Constitution Art 25 forbids «ضبط و فاش كردن مكالمات تلفنی» ("recording and disclosing telephone conversations") "except by order of law" [S33]. The text does not say whom it binds. Inference: it sits in the chapter on the rights of the people and is usually read as a limit on the state. Whether it limits a business recording its own service calls with notice is not answered by the text. **[LAWYER]**
- Computer Crimes Law Art 2 punishes "unauthorized" interception "in transmission" [S32]. Inference: a business recording its own call is arguably not third-party interception. Homayar receives recordings after the call, so it does not intercept in transmission. The law does not define "authorized".
- Islamic Penal Code (Ta'zirat) Art 582 punishes government employees who record or intercept calls without legal permission (secondary only [S35], unverified). Inference: it targets state officials, not private companies.
- **We found no official rule that allows or regulates a private business recording customer calls with notice.** We also found none that forbids it. The Central Bank (cbi.ir) and the telecom regulator (cra.ir) could not be reached on either day. This is a gap, not a finding. **[LAWYER]**
- Inference: the safe basis for Iranian customers is a notice at the start of every call, a written policy for agents, and consent where Art 58 or 59 needs it.

### 4.4 What Iranian enterprise buyers expect in procurement

**AFTA security certificate (government bodies and critical infrastructure).** AFTA (مرکز مدیریت راهبردی افتا, the national center for the security of information exchange space) published two documents, version 1.0, dated Aban 1398 [S70, S71]. We read archived copies of the official PDFs. We could not confirm that version 1.0 is still current.

- **Buyer side** [S70]: the scope is «تمامی دستگاههای اجرایی مشمول ماده ۵ قانون مدیریت خدمات کشوری» (all executive bodies under Article 5 of the Civil Service Management Law). «سازمان باید محصولات داخلی را انتخاب کند که گواهی ارزیابی امنیتی را از مرکز مدیریت راهبردی افتا ... دریافت کرده باشد» ("The organization must choose domestic products that have received AFTA's security evaluation certificate"). Buying foreign products needs AFTA approval that no domestic product fits.
- **Vendor side** [S71]:
  - «ارائه‌کننده محصول داخلی، باید محصولی را به سازمانها ارائه دهد که گواهی ارزیابی امنیتی را از مرکز مدیریت راهبردی افتا ... اخذ کرده باشد» ("A domestic product supplier must supply organizations only with products that hold AFTA's security evaluation certificate") (3-1).
  - For products hosted "on the internet at a location not owned by the organization" (for example SaaS), the contract must set who provides hosting and how AFTA's secure web hosting guideline is followed (4-1 الف).
  - Security patches for all customers "for four years after the sale", with or without a support contract (4-2).
  - On finding a vulnerability, tell customers over a secure channel and give a workaround «ظرف ۷۲ ساعت» ("within 72 hours") (6-1).
  - Quarterly reports to AFTA listing customer organizations and found vulnerabilities (8-1).
- **Process** [S72]: the maker goes to a licensed evaluation lab, which tests against the Common Criteria standard (an international security evaluation standard). The Iran Information Technology Organization (ITO) issues the certificate after AFTA's final approval. Duration and cost: not found. Ask a licensed lab. **[PROVIDER]** A protection profile exists for «برنامه های کاربردی تحت شبکه» (network applications). A 2020 lab list names five labs for application software [S73]. The current list is unverified.
- **Suppliers to financial infrastructure:** in 1398, AFTA briefed 70+ IT companies, with a Central Bank deputy present, on «الزامات امنیتی پایه شرکت های مرتبط با زیرساخت های حیاتی» ("baseline security requirements for companies linked to critical infrastructure"), which it calls "the government's minimum expectations from service providers" [S74]. The text of these requirements was not found.
- **ISMS (information security management system) at the buyer:** AFTA runs ISMS in critical infrastructure as part of a security plan notified to bodies nationwide [S77a]. It gave ISMS certificates to several banks [S77b].
- We found **no rule that makes ISO/IEC 27001 mandatory for a software supplier**. Inference: ISMS is a buyer duty. For a vendor, AFTA's documented duty is the product certificate. Buyers may still ask for ISO 27001 as an expectation.
- Whether banks, telecom operators, and insurers are formally "critical infrastructure" is **unverified**. Inference: AFTA's own news shows ISMS work at banks, so AFTA treats at least some banks as in scope.

**Hosting inside Iran.**

- The ICT Minister said on 5 Khordad 1404 that «همه دستگاه‌های اجرایی مکلف‌اند به‌صورت عملی به ابر دولت متصل شوند» ("all executive bodies are obliged to actually connect to the government cloud"), under Article 107 of the 7th Development Plan Law [S75, official ministry news]. The implementing regulation (Cabinet, 1403/9/12) was read only on a secondary site [S85]. Per that copy, it bans new data centers for executive bodies. **Unverified** as official text.
- The Supreme Council of Cyberspace's National Information Network master plan (1399) sets targets such as "at least three domestic cloud service providers" with 80% of the country's cloud market [S76, archived official page]. In the pages read, it is a target, not a direct rule on where a vendor must host data.
- A Ministry of ICT cloud services regulation, and any rule that "domestic services" must host Iranian users' data inside Iran, are **unverified**.
- Central Bank rules for banks (IT risk minimums, outsourcing, cloud) are known only from secondary sources and are **unverified** (cbi.ir unreachable). No telecom regulator rule on customer data or call recording was verified (cra.ir unreachable).
- Inference: government-linked buyers will likely ask Homayar to run inside the government cloud or on their own servers. The self-hosted option fits them better than Homayar-hosted SaaS. SaaS for private enterprises should be hosted inside Iran and say so clearly. The old site's providers (Neon, Vercel, Resend) are all outside Iran.

**Advantages, not requirements.**

- Knowledge-based company status (شرکت دانش‌بنیان): the Knowledge-based Production Leap Law (1401) lets an executive body skip a tender for a first-of-kind knowledge-based product with no domestic equivalent (Art 10(الف)), and gives such companies priority (Art 1) [S79, archived official text]. An advantage, not a condition to sell.
- The Supreme Council of Informatics company rating: secondary only, unverified.

**What this means for the website (Inference):**

- The security page must state facts: hosting location, deployment options, patch policy, vulnerability response time. It must not claim an AFTA certificate, ISO certificate, or knowledge-based status before Homayar has one (Consumer Protection Law Art 7 [S31]).
- A 72-hour workaround target in the vulnerability policy matches AFTA's vendor rule [S71], if Homayar can meet it.
- A later "for enterprise and government buyers" page can list certificates, hosting inside Iran, and the self-hosted option, once they are real.

### 4.4b Benchmark: what peers publish

We read the pages of 7 companies on 2026-09-27, plus one Iranian platform. "Y" means we found and read it. "N" means we did not find it on the pages we read. This does not prove that the page does not exist. "?" means the page was blocked, JavaScript-only, or behind a login.

| Page type | Gong | Observe.AI | CallMiner | Level AI | Cresta | Atlassian | GitLab | Liara (Iran) |
|---|---|---|---|---|---|---|---|---|
| Privacy policy | Y | Y | Y | Y | Y | Y | Y | Y |
| Customer terms / MSA | Y | Y | N | N | N | Y | Y | Y |
| Public DPA | Y | Y | N | Y | N | Y | Y | N |
| Subprocessor list | Y | ? | N | Y | Y | Y | Y | N |
| Trust center | Y | ? | N | Y | Y | Y | Y | N |
| Vulnerability disclosure | Y | N | N | N | Y | Y | Y | N |
| `security.txt` | N | N | N | N | Y | Y | Y | ? |
| AI transparency page | Y | Y | N | Y | Y | Y | Y | N |
| Emotion recognition statement | N | N | N | N | N | Y | N | N |
| Retention stated | Y | Y | N | N | N | Y | N | N |
| Government request report | N | N | N | N | N | Y | Y | N |
| Accessibility statement | N | N | N | N | Y | Y | Y | N |
| Acceptable use policy | N | Y | N | N | N | Y | Y | Y |
| SLA | N | Y | N | N | N | Y | ? | Y |
| Archive of old versions | N | N | N | N | N | Y | Y | N |

Sources: [S50] to [S65]. CallMiner's trust center and status page failed DNS lookup. Observe.AI's trust portal is JavaScript-only. ArvanCloud blocked all requests, so we used Liara as the Iranian example.

**Iranian practice** (Liara [S65]): its terms follow Iranian law. It may give user data to legal authorities, and «هیچ‌گونه اعتراضی پذیرفته نخواهد بود» ("no objection will be accepted"). Accounts cannot be deleted «به دلایل قانونی (مبارزه با پولشویی و کلاهبرداری‌های اینترنتی)» ("for legal reasons, anti-money-laundering and internet fraud"). It shows an e-Namad seal and publishes an SLA with account credits. We saw no DPA, subprocessor list, or AI statement. Inference: a Persian set with processing terms, a subprocessor list, and an AI page would be above the Iranian norm we saw.

Patterns worth copying:

1. **Say the same thing everywhere about training.** Observe.AI's trust page says "inference only" and "never trained on your data" [S56]. Its DPA says the customer "authorizes Observe.AI to use Customer Data to train, develop, test, and improve customer-specific AI models" [S58]. A buyer's lawyer will find this. GitLab says "GitLab does not currently train its own models" [S64a].
2. **Per-feature AI transparency** (Atlassian [S63b]): each AI feature lists models used, where it works best, and "Limitations and considerations".
3. **Subprocessor list split by deployment** (GitLab [S64b]): a column says whether each entry applies to "GitLab.com, Dedicated, and Self-Managed".
4. **Recording consent put on the customer** (Gong DPA [S52]): "Customer shall configure and utilize the consent management features ... to ensure lawful recording ... Customer will provide all necessary notices to relevant Data Subjects". Level AI's DPA requires the customer to meet "any applicable requirement to provide notice to Data Subjects of or obtain consents" [S61b].
5. **Notice before a new subprocessor.** Atlassian gives notice "at least thirty (30) days before" [S63c]. Gong gives notice "before authorizing" and allows objection "within 5 business days" [S52].
6. **Breach notice to customers.** Level AI promises notice "in no more than three (3) business days" [S61b].
7. **Retention defaults on the trust page.** Observe.AI: "13 months (configurable)" [S57].
8. **Version archive and update subscription** (Atlassian [S63d]).
9. **User notice for people added by an admin** (Atlassian [S63e]). This fits agents who are users but not the customer.
10. **No emotion inference at work** (Atlassian AUP [S63a]): forbids using its AI "to infer the emotions of an individual in workplace and educational settings". None of the five call-center vendors had such a statement.

Mistakes to avoid: "ISO 271001:2022" (CallMiner [S60b]); "ISO 27001 Type II", a term that does not exist (Level AI [S61a]); leftover "Lorem Ipsum" text on live legal pages (Cresta [S62a]).

### 4.5 Call recording: who must do what

Inference, from Sections 4.1 to 4.3:

- **The customer (the call center) must inform callers and agents.** In SaaS, it decides why the calls are processed. In self-hosted, it runs everything. In Iran, the safe basis is notice at the start of the call, a written policy for agents, and consent where Art 58 or 59 needs it [S30, S32, S33].
- **In Iran, notice may not be enough for emotion or sentiment scores** (Art 58, Section 4.3). The notice templates below give notice only. Each template must say that.
- **In SaaS, the customer's duty is not the whole picture.** Art 58 and 59 bind whoever stores or processes the data, so Homayar may carry a direct duty and criminal risk too (Inference, Section 4.1). The terms therefore need the customer's warranty below, not only an indemnity. **[LAWYER]**
- **Homayar's own calls.** If Homayar records its own sales or demo calls, Homayar must tell participants at the start and list the recordings in its own privacy policy.
- **Homayar must require this in its terms**, and must not promise that recording is lawful. The old terms already say "You are solely responsible for the lawful collection of call recordings" and require notices "for both your personnel and your end customers" (`content/legal/terms.md:46`). Keep the idea. It needs an Iranian lawyer's rewrite.

What Homayar's terms (SaaS) and license (self-hosted) must require from customers:

1. Record calls only where the law allows. Tell callers at the start of the call (for example an IVR message) that the call is recorded and analyzed with AI.
2. Tell agents in writing (employment contract or internal policy) what is analyzed, who sees scores, and how to contest them.
3. Do not use Homayar to infer the emotions, stress, or mental state of employees.
4. Use scores only as evidence in a human process. Any decision about pay, discipline, or dismissal follows the Labour Law procedure (Art 27 [S83]).
5. Do not upload calls you have no right to record, keep, or process.
6. Answer requests from callers and agents (access, correction, removal) as the party that decides. Homayar helps.
7. Warrant that, before any data reaches Homayar SaaS, the customer has the explicit consent that E-Commerce Law Art 58 needs (where it applies) and the consent Art 59 needs. Turn on sentiment features only after this is true. **[LAWYER]**
8. Indemnify Homayar for breach of these duties (**[LAWYER]** for enforceability in Iran). An indemnity covers money, not criminal liability (Section 4.1).

Homayar should publish notice templates in Persian (and English): a call-start message and an agent notice. This is a recommendation, not a legal duty.

### 4.6 Cookies and browser storage

- **Iran:** we found no Iranian rule on cookies or browser storage.
- **The website today:** no analytics, no third-party scripts, self-hosted fonts (Section 2). It stores only the language choice and the cart in `localStorage`.
- Inference: **no cookie banner is needed.** A short "Cookies and browser storage" section in the privacy policy is enough. It lists the two keys (`homayar-locale`, `homayar-cart-v1`) and says no tracking is used. Add one line next to the language switcher: "Your choice is saved in this browser." This small line also meets the EU guidance if a trigger happens later (Section 4.8).
- If anyone later adds analytics, a chat widget, embedded video, or external fonts, this conclusion changes. An Iranian site that loads foreign scripts also sends visitor data abroad, which the privacy policy would then have to say.

### 4.7 What must change from the old site (Addendum items)

| Old text | Problem | Change |
|---|---|---|
| "Payments are processed by **Stripe**" (`content/legal/terms.md:17`), Stripe as subprocessor (`content/legal/privacy.md:50`) | New path is an Iranian gateway. | Name the chosen gateway (ZarinPal or Zibal). Say the gateway collects card data, not Homayar. |
| "Fees are stated in USD" (`content/legal/terms.md:20`), USD prices (`src/data/products.json:16`) | Iranian buyers, Iranian gateway. ZarinPal forbids use of the gateway outside Iran [S40]. | Prices in rials (or clearly labeled tomans). **[ACCOUNTANT]** |
| "Subscriptions renew automatically" (`content/legal/terms.md:19`) | Not verified that the chosen gateway supports automatic recurring charges. | Do not promise auto-renewal unless the payment team confirms it and the customer explicitly agrees. Default to renewal by invoice. **[PROVIDER]** |
| Interest "1.5% per month" on late payment (`content/legal/terms.md:23`) | Written for another legal system. | **[LAWYER]**: rewrite under Iranian law. |
| "non-refundable except where required by law" (`content/legal/terms.md:20`) | Consumers have a 7-working-day withdrawal right (E-Commerce Law Art 37) [S30]. | A clear refund page (Section 8.6). |
| Governing law "the jurisdiction in which KSF Company is established" (`content/legal/terms.md:74`) | Vague. The company name differs from `src/lib/site.ts:3`. | Name the registered legal entity, Iranian law, and Iranian courts or arbitration. **[LAWYER]** |
| "website analytics" (`content/legal/privacy.md:7`) | The site has no analytics (Section 2). | Remove. Describe the real data. |
| Default retention "12 months" (`content/legal/privacy.md:43`) | Differs from the blueprint's 30/90/365 proposal (`docs/planning/homayar-commercial-blueprint.md:562`). | Publish only defaults the product enforces. |
| "AES-256", "third-party penetration testing" (`content/legal/privacy.md:56-60`) | Not verified (`docs/planning/homayar-commercial-blueprint.md:605`). Consumer Protection Law Art 7 [S31]. | Publish only controls that exist and are checked. |
| EU transfer clauses (`content/legal/privacy.md:67`) | EU-only; the tool may not even fit (Section 4.8). | Remove from the launch pages. |
| Works-council pack and codetermination claims (`src/app/faq/content.tsx:93`) | Unverified. Also signals targeting the EU. | Remove unless true. |
| Agent emotion, stress, and burnout features (`src/data/products.json:221-254`; same wording in `content/products/modules-analysis.json:49-90`, `content/i18n/products.fa.json:110`, `src/data/categories.json:11`) | Art 58 risk in Iran (Section 4.3); prohibited in the EU (Section 4.8). | Remove from product copy in both languages. |
| Six locales (`src/lib/content.ts:6`) | New site has fa and en. | Legal pages in fa and en only. |

**Languages.**

- **Persian is the binding text.** Every legal page is written first in Persian. Each page states: «در صورت هرگونه اختلاف میان متن فارسی و ترجمه‌های آن، متن فارسی معتبر است» ("If the Persian text and any translation differ, the Persian text governs"). Basis: Constitution Art 15 on official texts (Inference), Iranian customers, and Iranian law as the governing law. **[LAWYER]**
- **English is a courtesy translation.** Publish it for foreign readers and for enterprise buyers' international staff. Label it "Translation for information. The Persian text is binding."
- If a foreign customer later signs a separate contract, that contract may set its own language. **[LAWYER]**
- If a customer signs a separate contract (banks, telecoms, insurers, and government bodies often use their own contract or tender terms), that contract governs. The web pages apply to online purchases and inform everyone else. **[LAWYER]** order of precedence between a signed contract and the web pages.
- Do not publish de, ar, fr, or es versions of legal pages.

### 4.8 If Homayar sells outside Iran later

This section keeps the verified EU evidence in short form. None of it drives the Iran launch.

**Triggers that make EU law apply:**

| Trigger | What applies | Source |
|---|---|---|
| Homayar opens an office or other establishment in the EU | GDPR for all processing "in the context of the activities of an establishment ... in the Union, regardless of whether the processing takes place in the Union or not" (Art 3(1)) | [S1] |
| An EU customer (for example a call center in Germany) | The customer is under GDPR. Homayar processes for it, so it needs an Art 28 contract. If Homayar is found to offer its services to people in the EU, GDPR applies to Homayar directly (Art 3(2)), and an EU representative is likely needed (Art 27) | [S1, S3] |
| Marketing that targets the EU | EU languages or currency "with the possibility of ordering", or "mentioning of customers or users who are in the Union", can show that Homayar offers services in the EU (Recital 23). "Mere accessibility" of the website is not enough | [S1] |
| AI system placed on the EU market, or its output used in the EU | The EU AI Act applies to providers "irrespective of whether those providers are established or located within the Union or in a third country" (Art 2(1)(a)), and where "the output produced by the AI system is used in the Union" (Art 2(1)(c)) | [S20] |
| EU callers in an Iranian customer's recordings | Inference: probably not a trigger by itself, because the Iranian customer is not offering services to people in the EU or monitoring them there. Unverified. **[LAWYER]** | Inference |

**GDPR in short** [S1, S2, S4]:

- Roles: the customer decides; Homayar processes; if Homayar uses the data for its own purpose, "The processor will be considered a controller in respect of that processing" [S2 para 81].
- Processor contract: required, in writing, with the content listed in Art 28(3); subprocessor changes need notice and a chance to object (Art 28(2)).
- Information to people at collection (Art 13); answer requests "within one month" (Art 12(3)).
- Automated decisions: Art 22(1) gives "the right not to be subject to a decision based solely on automated processing"; human review must be "meaningful, rather than just a token gesture" [S4].
- Profiling covers "performance at work" (Art 4(4)); scoring agents is profiling; a DPIA (impact assessment) is likely (Art 35(3)(a)).
- Breach notice: controllers notify the authority within "72 hours" (Art 33(1)); a processor notifies the controller "without undue delay" (Art 33(2)) [S1].
- Transfers: Iran has no EU adequacy decision [S12]. The 2021 standard contractual clauses "do not work for importers whose processing operations are subject to the GDPR pursuant to Article 3" [S10]; the Commission's page still says a set for that case is "in the process of developing" [S11].
- Voice: biometric only when processed for unique identification (Art 4(14), Art 9(1)); the EDPB applies "The same reasoning ... to voice" [S8 footnote 31]. Voiceprints would be special-category data.
- Cookies: the ePrivacy Directive Art 5(3) allows storage "strictly necessary" for a service the user asked for [S5]; shopping-cart storage is exempt, and language preference is exempt short-term or with a notice next to the switcher [S7]. The EDPB counts local storage as storage [S6].
- The EU Accessibility Act covers "services provided to consumers" and exempts microenterprises (Art 2(2), Art 4(5)) [S15]. B2B SaaS is likely outside it.

**EU AI Act in short** [S20, S21, S24, S29]:

- Art 5(1)(f) prohibits "the placing on the market, the putting into service for this specific purpose, or the use of AI systems to infer emotions of a natural person in the areas of workplace", since 2 February 2025.
- Commission guidelines, para 254: "Using webcams and voice recognition systems by a call centre to track their employee's emotions, such as anger, is prohibited." Tracking customers' emotions "is not prohibited by Article 5(1)(f)". Para 257: a system "to detect burnout or depression at the workplace ... would remain prohibited". Para 251: inferring emotions "from written text (content/sentiment analyses)" is outside the ban. Para 254 also allows emotion recognition "only deployed for personal training purposes" if "the results are not shared with HR responsible persons and cannot impact the assessment, promotion etc. of the person trained" [S21].
- So the Iran launch rules 1 and 2 (text only; behavior, not feelings, Section 4.3) are exactly what keeps agent scoring outside the EU ban.
- High-risk: emotion recognition (Annex III 1(c)) and monitoring or evaluating workers (Annex III 4(b)). The 2026 Digital Omnibus (Regulation (EU) 2026/1744) moved Annex III high-risk duties ("Chapter III, Sections 1, 2, and 3, with the exception of Article 6(5)") to **2 December 2027** [S24]. Transparency duties (Art 50) apply from 2 August 2026 [S28].
- Why agent scoring would be high-risk: the Art 6(3) exception (not high-risk when a system does not "materially influenc[e] the outcome of decision making") does not apply if "the AI system performs profiling of natural persons" [S20]. Scoring agents is profiling (GDPR Art 4(4), above).
- Penalties: Art 5 breaches, up to EUR 35,000,000 or 7% of worldwide turnover, "whichever is higher" (Art 99(3)); Art 26 or Art 50 breaches, up to EUR 15,000,000 or 3% (Art 99(4)); for SMEs, the lower amount (Art 99(6)) [S20].

**Pages to add when a trigger happens:**

Start lawyer work on the EU representative and the transfer tool when an EU deal is likely, not when it is signed (estimate: weeks to months; the Commission is still developing the transfer tool for this case [S11], and high-risk duties apply from 2 December 2027 [S24]).

1. GDPR and international customers page: roles, EU representative, transfers, how to sign.
2. EU processing agreement terms (Art 28), added to the processing terms page.
3. Standalone cookie and storage page (optional; the privacy section may be enough).
4. EU notes on the AI page: which features are high-risk, dates, and what Homayar gives deployers.
5. Company information additions, if EU targeting requires them (e-Commerce Directive 2000/31/EC Art 5; not researched).

**Benchmark note:** the five call-center vendors all publish privacy policies; Gong, Level AI, and Atlassian publish DPAs; Gong, Level AI, Cresta, Atlassian, and GitLab publish subprocessor lists (Section 4.4b). These become expected once Homayar sells in the EU.

### 4.9 Evidence per option

- **Option A (do nothing).** It keeps false statements: Stripe, USD, analytics, unverified security claims (Section 4.7). This is a risk under Consumer Protection Law Art 7 [S31]. It sells agent emotion features with Art 58 risk [S30]. It lacks refund and company information (E-Commerce Law Art 33, 34 [S30]) and likely fails e-Namad [S39, S84]. Rejected.
- **Option B (Iran legal minimum).** It meets the core consumer and gateway needs (Art 33, 34, 37 [S30]; e-Namad [S39, S84]). It says nothing about recording duties, AI scoring, security, or hosting. Enterprise and government buyers will ask for these in procurement (Section 4.4). It does not fix the product copy risks.
- **Option C (Iran launch set).** It meets the Iranian legal and gateway needs. It puts the recording and scoring duties on the customer in writing (Section 4.5). It answers the first questions of enterprise buyers (hosting, security, AI use) with true statements. It avoids EU work that no trigger requires yet. Cost: 11 pages, most short, all needing Persian legal review.
- **Option D (Iran plus EU-ready now).** Covers the EU in advance, but adds pages and commitments (for example EU subprocessor notice periods) with no EU customer to need them. The AI Act and GDPR evidence stays ready in Section 4.8. Defer until a trigger.
- **Option E (full trust center).** Most items would be empty or untrue today: no certification (`docs/planning/homayar-commercial-blueprint.md:605`), no AFTA certificate, no measured uptime, no government request history. Publishing them risks Consumer Protection Law Art 7 [S31]. Defer.
- **Option F (generator).** We did not evaluate a specific generator; the following is inference. Generators are usually written for US or EU law and do not cover the E-Commerce Law, e-Namad, AFTA, or the Labour Law. Many deliver pages through an embedded script, which adds a foreign third-party script to an Iranian site. Useful at most as a drafting aid for the English translation.
- **Option G (lawyer-drafted from the start).** The best legal quality, and a lawyer must review Option C anyway. But the lawyer still needs the product facts, roles, and data inventory in this document. Inference: use Option C's outline as the lawyer's brief. This is a sequence of C and G, not a choice.

## 5. Comparison table

| Criterion | A. Do nothing | B. Iran minimum | C. Iran launch set | D. Iran plus EU now | E. Full trust center | F. Generator | G. Lawyer-drafted |
|---|---|---|---|---|---|---|---|
| Meets E-Commerce Law information and refund duties | No | Yes | Yes | Yes | Yes | Unknown | Yes |
| Supports e-Namad and gateway approval | No | Likely | Likely | Likely | Likely | Unknown | Likely |
| All statements true today | No | Yes | Yes | Yes | No | Only if edited | Yes, if given the facts |
| Puts recording and scoring duties on the customer | Partly (old terms) | No | Yes | Yes | Yes | Generic | Yes |
| Answers enterprise procurement basics (hosting, security, AI) | No | No | Yes | Yes | Yes, with claims not yet true | No | Depends on scope |
| Ready for an EU customer | No | No | Evidence ready; pages later | Yes | Yes | Partly | Depends on scope |
| Keeps the site free of third-party scripts | Yes | Yes | Yes | Yes | Yes | At risk | Yes |
| Persian-first | No | Yes | Yes | Yes | Yes | Unlikely | Yes |
| Pages at launch | 2 | 4 | 11 | 14 | 17+ | Varies | 11 or more |
| Lawyer work | Full rewrite anyway | Low | Medium | Medium to high | High | Medium (Iranian gaps) | Highest |
| Recurring upkeep (estimate) | None, but the pages stay false | About 1 to 2 hours a month | About 3 to 6 hours a month, plus a lawyer review once a year | About 4 to 8 hours a month | About 2 to 4 days a month | Subscription plus C's duties | Same as C, plus lawyer fees per change |
| Hard to reverse once published | Nothing new | Refund terms | Processing-terms promises; retention defaults; vulnerability response time | Same as C, plus EU notice periods | Same as D, plus SLA credits and certification claims | Depends on text | Same as C |

**Recurring upkeep for Option C.** Effort numbers are our estimates, not measured. Owners are proposals for the owner to confirm (Section 9).

| Duty | Frequency | Proposed owner | Rough effort |
|---|---|---|---|
| Subprocessor list and change log; tell customers before a new subprocessor | Each provider change | Engineering lead | 1 hour per change |
| "Last updated" dates, version archive, change emails to account admins | Each legal text change | Owner or operations | 1 hour per change |
| Keep the English translation in sync with the Persian text | Each legal text change | Website team plus a fluent reviewer | 1 to 2 hours per change |
| Renew `Expires` in `security.txt` (RFC 9116 recommends less than a year ahead [S16]) | At least once a year | Engineering lead | 15 minutes |
| Answer vulnerability reports within the stated time | As reports arrive | Engineering lead | Varies |
| Keep e-Namad (and SAMANDEHI, if required) valid; keep shown contact details matching them | When details change, and at renewal | Owner or operations | 1 hour per change |
| Check that retention defaults, AI page, and security page still match the product | Each release, and at least every quarter | Product owner | 2 to 4 hours a quarter |
| Watch for new Iranian rules: the AI law's implementing rules, the data bill, AFTA updates | Every quarter | Owner, with a lawyer | 1 hour a quarter plus lawyer fees |
| Lawyer review of all legal pages | Once a year, and when a law changes | Owner, with a lawyer | Lawyer fee |

A stale page is an untrue statement, which is the Consumer Protection Law Art 7 risk this document tries to avoid [S31].

**What is hard to reverse.** Once customers sign the processing terms, their promises bind Homayar until the contract ends: no training or evaluation on customer data, breach-notice time, subprocessor notice. Published retention defaults, refund terms, and the vulnerability response time apply to every sale made while they were live. Government buyers may also rely on the AFTA-style patch period. Start with commitments Homayar can surely meet. Tightening them later is easier than loosening them.

## 6. Recommendation

**Choose Option C: the Iran launch set.** Publish 11 pages at launch, written first in Persian, with the Persian text binding and English as a labeled translation. Add 6 pages later, when a trigger or a fact makes them true (Section 7).

Also, before launch:

- Remove all voice-based emotion features (agents and callers) and all agent stress and burnout features from the product copy, in both languages.
- Offer agent scoring only under the three product rules in Section 4.3: text only, behavior not feelings, human review with authority.
- Apply for e-Namad as soon as the company page, terms, refund page, and contact details are live. **[PROVIDER]**
- Do not claim AFTA, ISO, or knowledge-based status until Homayar holds it.

Why:

- It covers what Iranian law and the gateways require now: E-Commerce Law information, refund, and data rules; e-Namad; Consumer Protection Law truthfulness; the Computer Crimes Law recording and leak risks.
- It answers the first questions Iranian enterprise and government buyers ask: where data is hosted, how security is handled, how AI is used, and that self-hosted keeps data with them (Section 4.4).
- It supports the owner's goal of holding little data: self-hosted means Homayar sees no call data; SaaS states short retention and no training or evaluation on customer data.
- It keeps every public statement true.
- It leaves the EU work ready but unpublished until a trigger (Section 4.8).

**Are the empathy and tone conditions still needed for an Iran-only launch?** Human review is needed, because the Labour Law requires a human process for dismissal [S83] and Art 59 gives agents rights of access and correction [S30]. "Text only" and "behavior, not feelings" are not required by any Iranian text we read. We still recommend them: they keep scores out of the Art 58 "psychological condition" risk, they likely fit the risk rules of the new AI law, and they make an EU launch possible without rebuilding the scoring (Section 4.3).

What would change this recommendation:

- **An Iranian lawyer says sentiment or emotion scores are Art 58 "psychological condition" data.** Then customers need explicit consent from callers and agents, and the notice templates and terms change. If the lawyer says voice emotion of callers is fine with notice, the caller-emotion feature can return.
- **The official text of the National AI Development Law, or its implementing rules, classifies agent scoring as high risk.** Then add the duties it sets (for example assessments every six months, per the secondary text [S82]).
- **The Iranian personal data protection bill becomes law.** Rewrite the privacy policy, processing terms, and retention page to its terms.
- **An Iranian lawyer finds a rule on business call recording**, for example a Central Bank circular for bank call centers. Change the recording section and templates.
- **The e-Namad center gives a different page list.** Section 8 follows it.
- **A government-linked buyer is the first customer.** Then the AFTA certificate becomes the critical path, and the self-hosted package is the likely product (Section 4.4).
- **An Iranian lawyer says Homayar SaaS is directly liable under Art 58, 59, and 71 when a customer lacks consent** (Section 4.1). Then a product gate (sentiment off until the customer confirms consent) becomes a launch need, and caller text sentiment may have to wait.
- **A rule is confirmed that private vendors must host Iranian customer data inside Iran** (unverified today, Section 4.4). Then SaaS hosting must move inside Iran, the subprocessor list and privacy policy change, and the old foreign providers (Vercel, Neon, Resend) cannot hold that data.
- **A trigger in Section 4.8 happens.** Add the EU pages listed there.
- **Homayar decides to train or evaluate models on customer data** (the blueprint's corpus plan, `docs/planning/homayar-commercial-blueprint.md:526`). Add the opt-in path in Section 8.8 item 7, and callers and agents must be told.

## 7. Page matrix

The matrix counts 11 launch pages. The legal hub (`/legal`, Section 8.1) is a navigation page that links to them, so it is built at launch but not counted.

"Required" means an Iranian law, an official regulator text, or a gateway rule we cite requires the information on this page, and a page is the normal way to give it. "Required (inference)" means our reading says the page is needed, but no text we read requires it for this buyer. "Recommended" means good practice or a buyer expectation.

The E-Commerce Law consumer rules (Art 33 to 38) protect a buyer acting "for any purpose other than business or professional occupation" (Art 2(o)) [S30]. Most Homayar buyers are businesses. So rows that rest on those articles are "Required" for consumer buyers and "Required (inference)" for business buyers. The e-Namad page list may turn them into plain "Required" once confirmed (Section 4.2.7).

| # | Page | Level (Iran) | Deployment | Iranian basis | When |
|---|---|---|---|---|---|
| 1 | Company information | Required for consumer buyers; Required (inference) for business buyers | Both | E-Commerce Law Art 33(b)(c) [S30]; e-Namad shows the address to visitors [S41]; gateways need e-Namad [S39, S84] | Launch |
| 2 | Terms of service (SaaS) | Required (inference) | SaaS | E-Commerce Law Art 33(f), 34 for consumer buyers [S30]; needed to set recording and scoring duties (Section 4.5) | Launch |
| 3 | Self-hosted license | Required (inference) | Self-hosted | Software is licensed by contract; AFTA vendor duties for government buyers [S71] | Launch |
| 4 | Privacy policy (with a cookies and storage section, and a retention summary) | Required (inference) | Both | E-Commerce Law Art 59 (purpose, consent, access, correction, removal) [S30] | Launch |
| 5 | Refund and cancellation | Required for consumer buyers; Required (inference) for business buyers | Both | E-Commerce Law Art 33(f), 34(c)(d), 37, 38 [S30] | Launch |
| 6 | Acceptable use policy | Required (inference) | Both | E-Commerce Law Art 58 [S30]; Constitution Art 25 [S33]; Labour Law Art 27 [S83] | Launch |
| 7 | AI transparency, with call-recording notice templates | Required (inference) | Both | Consumer Protection Law Art 7 (no misleading claims) [S31]; E-Commerce Law Art 59(a) (clear purpose) [S30]; AI law risk factors (secondary [S82]) | Launch |
| 8 | Data processing terms | Recommended | SaaS, and self-hosted with remote support | No Iranian text requires it. Buyers expect it; AFTA requires the hosting clause in contracts with government bodies [S71] | Launch |
| 9 | Subprocessor list | Recommended | SaaS mainly; column for self-hosted | Art 59 purpose and disclosure (Inference) [S30]; buyers ask where data goes | Launch |
| 10 | Data retention and deletion | Required (inference) as content | SaaS; self-hosted note | E-Commerce Law Art 59(e) [S30]; Computer Crimes Law Art 33 minimums [S32] | Launch |
| 11 | Security, with vulnerability disclosure and `/.well-known/security.txt` | Recommended | Both | AFTA vendor duties (patches, 72-hour workaround) [S71]; RFC 9116 format [S16] | Launch |
| 12 | For enterprise and government buyers (hosting, certificates, deployment options) | Recommended | Both | AFTA [S70, S71]; government cloud [S75] | Later, when a certificate or Iranian hosting is real |
| 13 | SLA | Recommended | SaaS | Consumer Protection Law Art 19 [S31]; Liara practice [S65] | Later, after uptime is measured |
| 14 | Transparency report (government requests) | Recommended | SaaS | Computer Crimes Law Art 34, 35 [S32] | Later. **[LAWYER]** first: may Homayar publish Iranian requests? |
| 15 | Accessibility statement | Recommended | Website | No Iranian rule found | Later |
| 16 | GDPR and international customers | Only after an EU trigger | SaaS | Section 4.8 | Later, on trigger |
| 17 | Standalone cookie page | Only after an EU trigger, if the privacy section is not enough | Website | Section 4.8 | Later, on trigger |

## 8. What the website must show

**The e-Namad page list is unverified.** This outline follows the E-Commerce Law and the gateways, and must be checked against e-Namad. The e-Namad requirements checklist is shown only in the applicant's panel (Section 4.2.7).

This is the build outline for the website team. Routes are suggestions. Use the locale prefix the new site uses (for example `/fa/legal/terms` and `/en/legal/terms`).

Every page needs, at the top: the Persian title first, "Last updated" date (in the Iranian calendar, with the Gregorian date in the English version), a version number, and the language line: «در صورت هرگونه اختلاف میان متن فارسی و ترجمه‌های آن، متن فارسی معتبر است». The English version adds: "Translation for information. The Persian text is binding." Every page needs, at the bottom: contact email, phone, and a link to the legal hub.

Text marked **[LAWYER]** must come from, or be approved by, a lawyer. Do not write it from this outline.

Footer: replace the old two legal links (`src/lib/site.ts:47-52`) with a "Legal" group (hub, terms, privacy, refunds, company information) and a "Trust" group (AI, security, subprocessors, data retention). Show the e-Namad seal in the footer once issued (and the SAMANDEHI seal, if registration is required).

### Checkout and forms (build duties, apply to the whole site)

Footer links alone do not meet these duties. They follow from the E-Commerce Law (Section 4.2.1).

**Checkout (any online purchase):**

1. Before payment, show on the checkout page: what is being bought, the full price with tax in rials (or labeled tomans), the billing period, the supplier's name and contact details, and short summaries of the refund and cancellation terms with links (Art 33 [S30]).
2. Before payment, require an explicit action to accept the terms (or license) and the refund policy, for example an unticked checkbox. Silence is not acceptance (Art 43 [S30]).
3. Record the acceptance: document versions, time, and account or email. **This record is a new personal data item.** Add it to the privacy policy.
4. After payment, email a copy (or a link to the exact accepted versions): terms, refund and cancellation terms, complaints address, and invoice. Art 34 information must go "in a durable medium", and email counts (Art 35, Art 2(s)) [S30]. For consumer buyers, the withdrawal period does not start until this is given (Art 38(b)) [S30].
5. Issue the invoice the tax rules require (unverified, **[ACCOUNTANT]**).

**Forms (demo, contact, booking):**

6. Next to each submit button, one line in Persian: who receives the data, why, and a link to the privacy policy. Example: «هومایار این اطلاعات را فقط برای پاسخ به درخواست شما استفاده می‌کند. سیاست حریم خصوصی را ببینید.» E-Commerce Law Art 59(a) requires the purpose to be "specified and clearly described" [S30].
7. Collect only fields you need (Art 59(b) [S30]). The old demo form asks for team size and the booking form for time zone (`src/app/api/demo/route.ts:11`, `src/app/api/book/route.ts:21`). Keep them only if used.
8. Do not write names, phone numbers, or emails to server logs (the old site does this when no database is set, Section 2).

### 8.1 `/legal`: Legal hub (launch)

Title: «حقوقی و اعتماد» / "Legal and trust".

1. One-line intro: «این صفحات توضیح می‌دهند هومایار با داده‌های شما چه می‌کند و چه تعهدی می‌دهد.» ("These pages explain how Homayar works with your data and what we promise.")
2. List of every page with its last-updated date.
3. "Which pages apply to you": SaaS customers, self-hosted customers, website visitors.
4. "Changes": how we announce changes (email to account admins before material changes).
5. Link to the archive of old versions (Recommended [S63d]).

### 8.2 `/legal/company`: Company information (launch)

Title: «اطلاعات شرکت» / "Company information".

1. Registered legal name. **[LAWYER]** confirm: `src/lib/site.ts:3` says "Kohan System Farda"; `content/legal/terms.md:5` says "KSF Company".
2. National ID (شناسه ملی) and registration number. **[ACCOUNTANT]**
3. Postal address and postal code (the same address as in e-Namad [S41]).
4. Landline phone, email.
5. e-Namad seal; SAMANDEHI seal if required.
6. Knowledge-based status or AFTA certificate, only after issue.
7. «هومایار محصول [نام شرکت] است» ("Homayar is a product of [legal name]").

### 8.3 `/legal/terms`: Terms of service, SaaS (launch)

Title: «شرایط استفاده از خدمات» / "Terms of service".

1. Who we are and who these terms are for (businesses using Homayar SaaS). Link to the company page.
2. The service in plain words: we transcribe and analyze recorded calls. Outputs are decision support, not decisions.
3. Accounts and users: the customer is responsible for its users.
4. Prices and payment: rials (or labeled tomans), tax shown, the gateway named, how renewal works. **[LAWYER] [ACCOUNTANT]**
5. Refunds and cancellation: short summary plus link.
6. Your duties on recording and agent notice: the eight points in Section 4.5, including the warranty that consent exists where Art 58 applies. **[LAWYER]**
7. What you may not do: link to the acceptable use policy.
8. Your data: you own it. We process it only to provide the service. Hosted in [location]. We do not use it to train, improve, or evaluate our models, unless you sign a separate written opt-in. Export and deletion at the end.
9. AI outputs can be wrong. Scores are evidence for your people, not decisions.
10. Service changes and availability (no uptime number until measured).
11. Confidentiality.
12. Legal requests: we follow Iranian law and court orders (Computer Crimes Law Art 34, 35 [S32]). **[LAWYER]**
13. Warranty and liability limits. **[LAWYER]**
14. Suspension and termination.
15. Governing law (Iranian law) and disputes (named forum). **[LAWYER]**
16. Changes, binding language (Persian), contact.

Key plain-language points: "You must tell callers and agents." "Our scores never decide about your staff; your people do." "We do not use your calls to train or test our AI, unless you sign a separate opt-in."

### 8.4 `/legal/license`: Self-hosted license (launch)

Title: «مجوز استفاده از نسخه نصب در محل» / "Self-hosted license".

1. Who this is for: customers who install Homayar on their own servers or in the government cloud.
2. What you get: license scope, sites, processed-minute band, term (`docs/planning/homayar-commercial-blueprint.md:460`).
3. Your data stays with you: Homayar does not receive call data. No telemetry, no license heartbeat (`docs/planning/homayar-commercial-blueprint.md:452`). Only claim this when the package really does it.
4. Support access: only when you allow it; support bundles exclude audio and transcripts unless you approve a separate export (`docs/planning/homayar-commercial-blueprint.md:458`). During access, the processing terms apply.
5. License expiry: read-only or grace period; no data deletion; no hidden kill switch (`docs/planning/homayar-commercial-blueprint.md:460`).
6. Security updates: how long we provide patches and how we tell you about vulnerabilities. For government buyers, AFTA expects four years of patches and a 72-hour workaround [S71]. State only what Homayar will commit to.
7. Your duties on recording and agent notice: same points as the terms (Section 4.5), without the SaaS warranty, because Homayar does not receive the data.
8. Restrictions: no reverse engineering, no resale, no prohibited uses (link to the AUP).
9. Third-party and model licenses shipped in the package.
10. Warranty, liability, governing law. **[LAWYER]**

### 8.5 `/legal/privacy`: Privacy policy (launch)

Title: «سیاست حریم خصوصی» / "Privacy policy".

1. Who we are and how to contact us about privacy.
2. Two roles, in plain words: for the website and your account, we decide how data is used. For call recordings in SaaS, your employer or the company you called decides, and we only process it for them. In self-hosted mode, we do not see call data.
3. Data we collect on the website (list only what the new forms really have; the old fields are in Section 2): demo, contact, and booking form fields; checkout acceptance records; IP addresses used briefly for rate limiting, kept only in memory. Why, and how long we keep each (owner question in Section 9).
4. Account and billing data: what, why, how long. The payment gateway handles card data.
5. Who receives data: website host, database provider, email provider, payment gateway, SaaS host. Name each real provider and its country. The old site's providers (Vercel, Neon, Resend) are all outside Iran. **[LAWYER]**: sanctions and cross-border review (`docs/planning/homayar-commercial-blueprint.md:607`).
6. Where data is stored: say the country for each kind of data.
7. Cookies and browser storage: no tracking, no analytics. The two keys `homayar-locale` and `homayar-cart-v1`, and how to clear them. Fonts are served from our own server (per [S13]).
8. Your rights: see, correct, and remove your data (E-Commerce Law Art 59(d)(e) [S30]). How to ask. Our answer time (for example 30 days; owner decision).
9. If you are a caller or an agent in a recording: ask the company that recorded the call. We help them answer.
10. Automated decisions: Homayar does not decide about people.
11. Security: link to the security page.
12. Legal requests: we give data to authorities under Iranian law (Computer Crimes Law Art 34, 35 [S32]). **[LAWYER]**
13. Retention summary: link to the retention page.
14. Changes, binding language, contact.

### 8.6 `/legal/refunds`: Refund and cancellation (launch)

Title: «بازگشت وجه و لغو» / "Refunds and cancellation".

1. Who can cancel within 7 working days after purchase, without a reason, and get a full refund (consumer buyers, E-Commerce Law Art 37 [S30]). **[LAWYER]**: whether business buyers get the same, and the exceptions list.
2. For business buyers, refunds follow the contract. The Consumer Protection Law has no online withdrawal right (Section 4.2.2).
3. How to cancel: email or account page.
4. How and when we refund: to the same card or account, as soon as possible (Art 38(c): "immediately").
5. Cancelling a subscription later: end of the paid period, or pro-rata. **[LAWYER]**
6. Self-hosted licenses and installation services: when they can be refunded. **[LAWYER]**
7. If we fail to deliver: we perform and pay for the damage (Consumer Protection Law Art 19 Note [S31]).
8. Complaints address (E-Commerce Law Art 34(a) [S30]).

### 8.7 `/legal/acceptable-use`: Acceptable use policy (launch)

Title: «سیاست استفاده مجاز» / "Acceptable use policy".

1. Why this page exists: to protect callers, agents, and other customers.
2. Recording: only lawfully recorded calls, with notice at the start of the call.
3. **Never use Homayar to infer the emotions, stress, or mental state of employees or job candidates.** Agent scores such as "empathy" and "tone" rate what the agent said and did in the transcript. Do not configure rubrics that score how an agent feels (Section 4.3; E-Commerce Law Art 58 [S30]; pattern from Atlassian [S63a]).
4. Scores are evidence, not decisions. Any action about pay, discipline, or dismissal follows your human process and the Labour Law (Art 27 [S83]).
5. No voice identification or voiceprints unless a separate written agreement allows it.
6. No unlawful, discriminatory, or deceptive use.
7. Security rules: no probing, no bypassing limits, no reverse engineering.
8. What we do if the policy is broken: notice, then suspension.

### 8.8 `/legal/data-processing`: Data processing terms (launch)

Title: «شرایط پردازش داده» / "Data processing terms".

1. Short summary first: "These terms apply when we process call recordings for you in SaaS, or when we access your self-hosted system for support."
2. Roles: you decide why calls are processed; we process only for you.
3. What we process: data types (audio, transcripts, scores), people (callers, agents, users), purpose, duration.
4. Our duties: only on your instructions; staff confidentiality; security; subprocessors; help with requests from callers and agents; delete or return at the end; allow audits.
5. Hosting: where your data is stored, and for government buyers, how the hosting guideline in your contract is met (AFTA 4-1 الف [S71]).
6. Subprocessors: link to the list; notice before a new one; how to object.
7. **No training, improvement, or evaluation of Homayar models on your data, unless you sign a separate written opt-in.** The opt-in states: which data (for example a sample of calls), the purpose (for example the evaluation corpus, `docs/planning/homayar-commercial-blueprint.md:526`), access limits, how long it is kept, and how to withdraw. With an opt-in, Homayar decides for that use and needs the callers' and agents' consent basis (Art 59 [S30]). **[LAWYER]**
8. Security incidents: we tell you within a fixed time (owner decision; Level AI uses three business days [S61b]).
9. Iranian legal duties that may override deletion (Computer Crimes Law Art 33, 34 [S32]). **[LAWYER]**
10. Downloadable PDF, version, and signature process.

Whole text: **[LAWYER]**. EU Art 28 additions come only after a trigger (Section 4.8).

### 8.9 `/legal/subprocessors`: Subprocessor list (launch)

Title: «پردازشگرهای فرعی» / "Subprocessors".

Table columns: name, purpose, data involved, country, applies to (SaaS / self-hosted / website), date added. Pattern from GitLab [S64b].

Known rows from the old site, to confirm or replace: website hosting (Vercel, `README.md:79`, website only), database (Neon PostgreSQL, `src/lib/db.ts:1`, website only), email delivery (Resend, `src/lib/email.ts:30`, website only). Pending: payment gateway (ZarinPal or Zibal) and SaaS hosting (inside Iran recommended, Section 4.4). List each AI model provider only when it is really used for customer data (`docs/planning/homayar-commercial-blueprint.md:520`).

Below the table: a dated change log.

### 8.10 `/trust/ai`: AI transparency (launch)

Title: «هومایار چگونه از هوش مصنوعی استفاده می‌کند» / "How Homayar uses AI".

1. What the AI does and does not do: it transcribes, scores against your rubric, finds issues, suggests coaching. It does not decide about people.
2. One entry per feature (transcription, QA scoring, issue detection, customer text sentiment, summaries, coaching). Each entry states: models used (by family and where they run, inside or outside Iran), input, output, works best when, known limits (for example Persian accents, 8 kHz audio, overlapping speech; `docs/planning/homayar-commercial-blueprint.md:528`), and the human review step.
3. What humans decide: final scores, disputes, any action about an agent. Only when WP14 ships: "Agents can see and contest their scores." WP14 is planned work (`docs/planning/homayar-commercial-blueprint.md:1287-1299`). Until then, do not state it.
4. What we do not offer: no inference of emotions, stress, or burnout of employees; no emotion analysis from voice. Agent "empathy" and "tone" scores come from transcript text only and rate observable behavior against your rubric. Team views show call difficulty and score trends, not agent mood. Customer sentiment describes the conversation, not a person's mental state. State this only while it is true.
5. Your data, training, and evaluation: same words as the terms and the processing terms (lesson from [S56, S58]).
6. Accuracy: publish measured results only (the blueprint's gates are "targets, not achieved results", `docs/planning/homayar-commercial-blueprint.md:532`).
7. Iranian AI law: a short note that Homayar follows the National AI Development Law as its rules are published. Do not claim a risk class until the official text and rules are read. **[LAWYER]**
8. Notice templates for customers, in Persian and English: a call-start message and an agent notice. Each says it gives notice only; explicit consent may be needed for some data (Section 4.5).

### 8.11 `/trust/data-retention`: Data retention and deletion (launch)

Title: «نگهداری و حذف داده» / "Data retention and deletion".

1. SaaS default periods, only those the product enforces (proposed: audio 30 days, transcripts and findings 90 days, audit metadata 365 days; `docs/planning/homayar-commercial-blueprint.md:562`). Customers can change them.
2. How deletion works: access blocked at once, then removal from storage, search indexes, and backups within a stated window (`docs/planning/homayar-commercial-blueprint.md:564`). No promise of instant deletion from backups.
3. Legal minimums: Iranian law may require us to keep some account data and content for a minimum time (Computer Crimes Law Art 33 [S32]). **[LAWYER]**
4. End of contract: export window, then deletion.
5. Self-hosted: you set and run retention. We hold nothing.
6. Website form data: how long we keep leads and bookings. No period is decided (owner question). Proposal to decide on: delete leads with no follow-up after 12 months, and bookings 12 months after the meeting. A product choice, not a legal rule.

### 8.12 `/trust/security`: Security, with vulnerability disclosure (launch)

Title: «امنیت» / "Security".

1. Controls that exist today, stated plainly. No certification claims until issued (`docs/planning/homayar-commercial-blueprint.md:605`; Consumer Protection Law Art 7 [S31]).
2. Where SaaS data is hosted (country and provider type).
3. Deployment options: SaaS, self-hosted, government cloud (only if supported).
4. Tenant isolation and access control, as built.
5. Incident handling: how and when we tell customers.
6. Patch policy: how long each version gets security fixes.
7. Reporting a vulnerability: email, what to include, response time. Recommended: acknowledge fast and give customers a workaround within 72 hours, matching AFTA's vendor rule [S71], if Homayar can meet it. Safe-harbour wording **[LAWYER]**. No bounty unless one exists.
8. `/.well-known/security.txt` with `Contact` (using `mailto:`) and `Expires` (less than a year ahead), served over HTTPS as `text/plain` [S16].
9. AFTA status: say nothing until a certificate exists; then name the certificate and its date.

### 8.13 Later pages

- `/trust/enterprise` (for enterprise and government buyers): when a certificate or Iranian hosting is real. Lists AFTA certificate, hosting location, self-hosted and government-cloud options, patch commitment, and knowledge-based status.
- `/trust/sla`: after uptime is measured. Availability target, how measured, credits.
- `/trust/transparency-report`: after a lawyer confirms what may be published about Iranian government requests.
- `/accessibility`: a short statement of the WCAG level targeted and a contact.
- EU pages: only after a trigger (Section 4.8).

## 9. Open questions

### For the owner

1. Which buyers come first: private enterprises, banks, telecoms, insurers, or government-linked bodies? Government-linked buyers make the AFTA certificate and self-hosted package the critical path (Section 4.4).
2. Will Homayar apply for an AFTA security evaluation certificate? When?
3. Where will SaaS be hosted, and by which Iranian provider?
4. Do you agree to remove voice-based emotion features and agent stress and burnout features (Section 4.3)?
5. Will Homayar ever use customer calls to train, improve, or evaluate its models (for example the corpus in `docs/planning/homayar-commercial-blueprint.md:526`)? The recommendation assumes "no, unless a customer signs a separate opt-in".
6. What is the registered legal name: "Kohan System Farda" or "KSF Company"?
7. How long should website leads and bookings be kept? Section 8.11 item 6 has a proposal (12 months).
8. What answer time for privacy requests, and what breach-notice time for customers, can Homayar commit to?
9. Who owns the recurring duties in Section 5?
10. Does Homayar record its own sales or demo calls?
11. Will Homayar seek knowledge-based company status?

### For a lawyer in Iran

1. Does Constitution Art 25 limit a private business recording its own calls with notice? Is there any rule for call centers (Central Bank, telecom regulator)?
2. Do emotion, stress, or sentiment scores count as "psychological condition" data under E-Commerce Law Art 58? Ask separately for: (a) callers' emotion from voice, (b) callers' sentiment from text, (c) agents' emotion or stress, (d) agent QC scores such as "empathy" and "tone" from text. If yes, is a call-start notice enough, or is explicit consent from each person needed? Can an employee give valid consent to the employer?
3. Under Art 58, 59, and 71, is Homayar, as the SaaS party that stores and processes the data, liable if the customer did not get explicit consent? Does a customer warranty or indemnity reduce that risk?
4. What does the National AI Development Law (1405) require, from its official text? Does agent scoring fall in a higher risk class? What are the implementing rules and dates?
5. Does the National Data and Information Management Law apply to a private company processing customers' call data?
6. Is Homayar SaaS a "domestic hosting provider" under Computer Crimes Law Art 23 and 33? How does the 6-month and 15-day minimum fit customer-chosen deletion?
7. Does the 7-working-day withdrawal right (E-Commerce Law Art 37) apply to Homayar's buyers? What are the exceptions in the Art 38/42 regulation?
8. Confirm the Persian text of the E-Commerce Law articles cited from the English translation.
9. Were Labour Law Articles 27, 158, 159, and 165 amended after 1369? Which employment rules cover call-center agents of executive bodies and other government-linked buyers?
10. Current status of the personal data protection bill.
11. Is SaaS on the ministry list that requires a warranty and invoice (Consumer Protection Law Art 3 Note 3)?
12. Does the site need SAMANDEHI registration today, and must it show the seal? Is selling SaaS a guild activity that needs a virtual-store business license (پروانه کسب) before e-Namad (Section 4.2.7)?
13. Are the AFTA documents of 1398 (version 1.0) still current? Are banks, telecoms, and insurers "critical infrastructure"? What are AFTA's baseline requirements for suppliers to financial infrastructure?
14. Is there a rule requiring customer call data of banks, telecoms, or government bodies to be hosted inside Iran (Central Bank, telecom regulator, government cloud rule under the 7th Plan Art 107)?
15. Liability, indemnity, late-payment, and dispute clauses under Iranian law. The binding-language clause. The order of precedence between a signed customer contract and the web pages (Section 4.7).
16. May Homayar publish statistics on Iranian government data requests?
17. Sanctions and export review for foreign providers (Resend, cloud, AI APIs) (`docs/planning/homayar-commercial-blueprint.md:607`).

### For an EU data protection lawyer (only when a Section 4.8 trigger happens)

1. Is Homayar under GDPR Art 3(2) for an EU customer's data? Does it need an EU representative (Art 27)?
2. Which transfer tool fits [S9, S10, S11]?
3. Are text-based agent scores (behavior against a rubric) outside the AI Act Art 5(1)(f) ban? Are Team Pulse trends from customer text sentiment?
4. Is Homayar the provider of a high-risk system (Annex III 4(b)) for agent scores? What is needed by 2 December 2027?
5. National workplace monitoring and works-council rules (GDPR Art 88).
6. Does SaaS call QA trigger a data protection officer duty (Art 37(1)(b))?
7. What must the company page add under the e-Commerce Directive 2000/31/EC Art 5?
8. Do EU callers in an Iranian customer's recordings trigger GDPR by themselves?

### For an accountant

1. Rial or toman display, and the four-zeros change [S42].
2. What the taxpayer system (سامانه مودیان) requires online sellers to issue and show.
3. National ID and registration number for the company page. Tax tracking code for Zibal [S39].

### For providers

1. e-Namad center: the exact pages and details a site must show (the requirements checklist in the applicant's panel); star levels; the rules for companies.
2. The chosen gateway: recurring charges, refund process, required pages.
3. The chosen SaaS host inside Iran: location, security terms, and whether it is on any approved list.
4. A licensed AFTA evaluation lab: how long the security evaluation takes, and what it costs (Section 4.4).

### Unverified claims in this document

1. All Persian wording of the E-Commerce Law (WIPO English text used).
2. "Seven working days" in the Persian text of Art 37.
3. The exceptions list of the Art 38/42 regulation.
4. Ta'zirat Art 582 and the Citizens' Rights Law 1383 (secondary only).
5. Any official rule on business call recording (none found).
6. The data protection bill's status.
7. The official e-Namad page list (the requirements checklist is not public; archived FAQ of 2022 read, Section 4.2.7); whether the archived 2022 e-Namad FAQ is still current; the Shaparak circular tying gateways to e-Namad (quoted only by ZarinPal).
8. The official text of the National AI Development Law (approval verified; content secondary).
9. The National Data and Information Management Law's text and scope.
10. Later amendments to the Labour Law articles cited.
11. Whether the AFTA documents of 1398 are still current; the current lab list; AFTA's baseline supplier requirements.
12. Whether banks, telecoms, and insurers are formally critical infrastructure.
13. The official text of the 7th Plan Art 107 regulation; any cloud or data-localization rule for private vendors; Central Bank IT-risk and outsourcing rules; any telecom regulator rule.
14. SAMANDEHI's current scope and seal (2012 archived rule only).
15. The Supreme Council of Informatics rating rule.
16. The 1375 foreign-words law (secondary only).
17. The four-zeros law text; any rial-versus-toman rule; e-invoice duties.
18. The Next.js font claim is from the current nextjs.org docs, not the pinned version `16.2.9` (Section 2).
19. EU items (only relevant after a trigger): the Council and Parliament press releases on the AI Omnibus (403 or empty; the Official Journal text was read); the EDPB endorsement of WP194; the draft high-risk guidelines [S29] are a draft.

## 10. Sources

Sources [S1] to [S65] were read on 2026-09-27. Sources [S70] to [S86] were read on 2026-09-28. Sources [S87] to [S89] were read on 2026-09-30. "Failed" means the page could not be fetched that day. "Archived" means a web.archive.org copy of the official page was read.

### Iran

- [S30] Electronic Commerce Law 1382, English, WIPO Lex: https://www.wipo.int/wipolex/en/legislation/details/7711 and PDF https://wipolex-res.wipo.int/edocs/lexdocs/laws/en/ir/ir008en.pdf (read 2026-09-27). The Persian original https://rc.majlis.ir/fa/law/show/93997 failed on 2026-09-27 and 2026-09-28.
- [S31] Consumer Protection Law 1388, CPPO: https://cppo.ir/قانون-حمایت-از-حقوق-مصرف-کنندگان and PDF https://cppo.ir/uploads/1/2024/Sep/17/107.pdf (read 2026-09-27)
- [S32] Computer Crimes Law 1388, Ministry of ICT: https://chb.ict.gov.ir/fa/computercrimes/otherlaw-سایر-مقررات (read 2026-09-27)
- [S33] Constitution, Guardian Council: https://www.shora-gc.ir/fa/news/5898/قانون-اساسی-جمهوری-اسلامی-ایران (read 2026-09-27 and 2026-09-28)
- [S34] Secondary: Law on Respecting Legitimate Freedoms 1383, ekhtebar.ir: https://www.ekhtebar.ir/قانون-احترام-به-آزادی-های-مشروع-و-حفظ-حق/ (read 2026-09-27)
- [S35] Secondary: Islamic Penal Code Book 5 (Ta'zirat), ekhtebar.ir: https://www.ekhtebar.ir/كتاب-پنجم-قانون-مجازات-اسلامي-‌-تعزير/ (read 2026-09-27)
- [S36] Ministry of ICT news on the draft data protection bill: https://www.ict.gov.ir/fa/newsagency/21691/ (read 2026-09-27; page dated 2018-07-28)
- [S37] Secondary: alef.ir: https://www.alef.ir/news/4030215063.html (read 2026-09-27)
- [S38] Secondary: aftabnews.ir: https://aftabnews.ir/fa/news/943575/ (read 2026-09-27)
- [S39] Zibal gateway page and FAQ: https://zibal.ir/ipg (read 2026-09-27)
- [S40] ZarinPal terms: https://www.zarinpal.com/terms (read 2026-09-27)
- [S41] ZarinPal blog, "What is e-Namad": https://www.zarinpal.com/blog/اینماد-چیست/ (read 2026-09-27 and 2026-09-28)
- [S42] Guardian Council news on removing four zeros: https://www.shora-gc.ir/fa/news/11280/ (read 2026-09-27)
- [S43] CPPO page on the Art 38/42 regulation: https://cppo.ir/آیین-نامه-اجرایی-38-و-42-قانون-تجارت-الکترونیکی and https://cppo.ir/uploads/1/2025/Mar/01/masrafi_1.pdf (read 2026-09-27; the attachment is a list of goods)
- [S44] Secondary: Law prohibiting foreign names and terms 1375, ekhtebar.ir copy (read 2026-09-27)
- [S45] Guardian Council site search for data protection terms: https://www.shora-gc.ir/fa/search (read 2026-09-27; no matching result)
- [S70] AFTA, «الزامات امنیتی زیرساختهای حیاتی در استفاده از محصولات نرم افزاری سازمانی», v1.0, Aban 1398, archived official PDF: https://web.archive.org/web/20200122231215id_/http://afta.gov.ir:80/Portal/file/?240419/%D8%A7%D9%84%D8%B2%D8%A7%D9%85%D8%A7%D8%AA-%D8%A7%D9%85%D9%86%DB%8C%D8%AA%DB%8C-%D8%B2%DB%8C%D8%B1%D8%B3%D8%A7%D8%AE%D8%AA%D9%87%D8%A7%DB%8C-%D8%AD%DB%8C%D8%A7%D8%AA%DB%8C-%D8%AF%D8%B1-%D8%A7%D8%B3%D8%AA%D9%81%D8%A7%D8%AF%D9%87-%D8%A7%D8%B2-%D9%85%D8%AD%D8%B5%D9%88%D9%84%D8%A7%D8%AA-%D9%86%D8%B1%D9%85-%D8%A7%D9%81%D8%B2%D8%A7%D8%B1%DB%8C-%D8%B3%D8%A7%D8%B2%D9%85%D8%A7%D9%86%DB%8C.pdf (read 2026-09-28)
- [S71] AFTA, «الزامات امنیتی ارائه محصولات نرم افزاری سازمانی به زیرساختهای حیاتی», v1.0, Aban 1398, archived official PDF: https://web.archive.org/web/20200122231221id_/http://afta.gov.ir:80/Portal/file/?240420/%D8%A7%D9%84%D8%B2%D8%A7%D9%85%D8%A7%D8%AA-%D8%A7%D9%85%D9%86%DB%8C%D8%AA%DB%8C-%D8%A7%D8%B1%D8%A7%D8%A6%D9%87-%D9%85%D8%AD%D8%B5%D9%88%D9%84%D8%A7%D8%AA-%D9%86%D8%B1%D9%85-%D8%A7%D9%81%D8%B2%D8%A7%D8%B1%DB%8C-%D8%B3%D8%A7%D8%B2%D9%85%D8%A7%D9%86%DB%8C-%D8%A8%D9%87-%D8%B2%DB%8C%D8%B1%D8%B3%D8%A7%D8%AE%D8%AA%D9%87%D8%A7%DB%8C-%D8%AD%DB%8C%D8%A7%D8%AA%DB%8C.pdf (read 2026-09-28)
- [S72] AFTA, product security evaluation process, archived official page (1396/4/18): https://web.archive.org/web/20210118113452id_/https://afta.gov.ir/Portal/home/?news/237316/237333/237520/%D9%86%D8%B8%D8%A7%D9%85-%D8%A7%D8%B1%D8%B2%DB%8C%D8%A7%D8%A8%DB%8C-%D9%85%D8%AD%D8%B5%D9%88%D9%84%D8%A7%D8%AA- (read 2026-09-28). Also https://web.archive.org/web/20170711184359id_/http://afta.gov.ir:80/Portal/home/?news/235046/236701/236730/%D9%86%D8%B8%D8%A7%D9%85-%D8%A7%D8%B1%D8%B2%DB%8C%D8%A7%D8%A8%DB%8C-%D9%88-%D8%A7%D8%AE%D8%B0-%D9%85%D8%AC%D9%88%D8%B2-%D9%87%D8%A7%DB%8C-%D8%A7%D9%81%D8%AA%D8%A7 (read 2026-09-28)
- [S73] AFTA, licensed labs, archived official page (2020 snapshot): https://web.archive.org/web/20200809002937id_/https://www.afta.gov.ir/portal/home/?news/237316/237333/236732/%D9%81%D8%B1%D8%A2%DB%8C%D9%86%D8%AF-%D8%A7%D8%B9%D8%AA%D8%A8%D8%A7%D8%B1%D8%A8%D8%AE%D8%B4%DB%8C-%D9%88-%D8%A7%D8%AE%D8%B0-%D9%BE%D8%B1%D9%88%D8%A7%D9%86%D9%87-%D8%A2%D8%B2%D9%85%D8%A7%DB%8C%D8%B4%DA%AF%D8%A7%D9%87 (read 2026-09-28)
- [S74] AFTA news, baseline security requirements for companies linked to critical infrastructure (1398/9/19), archived: https://web.archive.org/web/20191213033713id_/http://afta.gov.ir:80/Portal/home/?news/235046/237264/240274/%D8%AA%D8%A8%DB%8C%DB%8C%D9%86-%D8%A7%D9%84%D8%B2%D8%A7%D9%85%D8%A7%D8%AA-%D8%A7%D9%85%D9%86%DB%8C%D8%AA%DB%8C-%D9%BE%D8%A7%DB%8C%D9%87-%D8%B4%D8%B1%DA%A9%D8%AA-%D9%87%D8%A7%DB%8C-%D9%85%D8%B1%D8%AA%D8%A8%D8%B7-%D8%A8%D8%A7-%D8%B2%DB%8C%D8%B1%D8%B3%D8%A7%D8%AE%D8%AA-%D9%87%D8%A7%DB%8C-%D8%AD%DB%8C%D8%A7%D8%AA%DB%8C (read 2026-09-28)
- [S75] Ministry of ICT news on the government cloud (5 Khordad 1404): https://www.ict.gov.ir/fa/newsagency/29429/ (read 2026-09-28)
- [S76] Supreme Council of Cyberspace, National Information Network master plan (1399), archived official page: https://web.archive.org/web/20210131080344id_/http://majazi.ir/circularinformation/90343-%D8%B7%D8%B1%D8%AD-%DA%A9%D9%84%D8%A7%D9%86-%D9%85%D8%B9%D9%85%D8%A7%D8%B1%DB%8C-%D8%B4%D8%A8%DA%A9%D9%87%E2%80%8C-%D9%85%D9%84%DB%8C-%D8%A7%D8%B7%D9%84%D8%A7%D8%B9%D8%A7%D8%AA.html (read 2026-09-28)
- [S77a] AFTA news on ISMS in critical infrastructure (1398/2/21), archived: https://web.archive.org/web/20190720180544id_/http://afta.gov.ir/Portal/home/?news/235046/237268/239672/%D8%A7%D8%AC%D8%B1%D8%A7%DB%8C-%D8%A7%D8%B3%D8%AA%D8%A7%D9%86%D8%AF%D8%A7%D8%B1%D8%AF-%D9%85%D8%AF%DB%8C%D8%B1%DB%8C%D8%AA-%D8%A7%D9%85%D9%86%DB%8C%D8%AA-%D8%A7%D8%B7%D9%84%D8%A7%D8%B9%D8%A7%D8%AA-%D8%AF%D8%B1-%D8%B2%DB%8C%D8%B1%D8%B3%D8%A7%D8%AE%D8%AA%E2%80%8C%D9%87%D8%A7%DB%8C-%D8%AD%DB%8C%D8%A7%D8%AA%DB%8C-%D8%A7%D8%B3%D8%AA%D8%A7%D9%86-%D9%81%D8%A7%D8%B1%D8%B3 (read 2026-09-28)
- [S77b] AFTA news on ISMS certificates to banks (1397/3/11), archived: https://web.archive.org/web/20180722221949id_/http://www.afta.gov.ir:80/Portal/home/?news/235046/237267/238014/%D8%A7%D8%B9%D8%B7%D8%A7%DB%8C-%DA%AF%D9%88%D8%A7%D9%87%DB%8C%D9%86%D8%A7%D9%85%D9%87-ISMS-%D8%A8%D9%87-%D8%A8%D8%A7%D9%86%DA%A9-%D9%87%D8%A7%DB%8C-%D8%AA%D9%88%D8%B3%D8%B9%D9%87-%D8%AA%D8%B9%D8%A7%D9%88%D9%86-%D9%88-%D8%A7%D9%82%D8%AA%D8%B5%D8%A7%D8%AF-%D9%86%D9%88%DB%8C%D9%86-%D8%A7%D8%B2-%D8%B3%D9%88%DB%8C-%D9%85%D8%B1%DA%A9%D8%B2-%D8%A7%D9%81%D8%AA%D8%A7- (read 2026-09-28)
- [S78] SAMANDEHI help and about pages (2012), archived: https://web.archive.org/web/20120128171250id_/http://www.samandehi.ir/help/help.html;jsessionid=1F4EBB961971DF9C1845F64D139749B4 and https://web.archive.org/web/20120409163602id_/http://samandehi.ir/help/aboutUs.html;jsessionid=6A5ABC24C7A70D2566AC2CC50763386A (read 2026-09-28). Live samandehi.ir returned 403.
- [S79] Knowledge-based Production Leap Law 1401, archived rc.majlis.ir text: https://web.archive.org/web/20220709061110id_/https://rc.majlis.ir/fa/law/show/1745108 (read 2026-09-28)
- [S80] Supreme Council of Cultural Revolution, National AI Document, archived official page: https://web.archive.org/web/20240913141026id_/https://sccr.ir/News/25503/1/%D8%AA%D8%B5%D9%88%DB%8C%D8%A8-%D8%B3%D9%86%D8%AF-%D9%87%D9%88%D8%B4-%D9%85%D8%B5%D9%86%D9%88%D8%B9%DB%8C-%D8%AC%D8%B2%D8%A6%DB%8C%D8%A7%D8%AA-%D8%B3%D9%86%D8%AF (read 2026-09-28)
- [S81] Guardian Council news 11421, approval of the National AI Development bill (14 Shahrivar 1405): https://www.shora-gc.ir/fa/news/11421/تایید-طرح-ملی-توسعه-هوش-مصنوعی (read 2026-09-28). Also news 11703, spokesperson's press conference: https://www.shora-gc.ir/fa/news/11703/ (read 2026-09-28). Found via https://www.shora-gc.ir/fa/services/8/14 (read 2026-09-28).
- [S82] Secondary: National AI Development Law text, ekhtebar.ir: https://www.ekhtebar.ir/قانون-ملی-توسعه-هوش-مصنوعی/ and zoomit.ir: https://www.zoomit.ir/iran-news/467113-iran-national-ai-development-law-explained/ (read 2026-09-28 by the research sub-agent)
- [S83] Labour Law 1369, Ministry of ICT Inspection Center copy: https://bazresi.ict.gov.ir/fa/laws/l7-%D9%82%D8%A7%D9%86%D9%88%D9%86-%DA%A9%D8%A7%D8%B1 (read 2026-09-28)
- [S84] ZarinPal blog, gateway without e-Namad (published 1400-09-06): https://www.zarinpal.com/blog/درگاه-پرداخت-بدون-اینماد/ (read 2026-09-28)
- [S85] Secondary: 7th Plan Art 107(الف) implementing regulation, ekhtebar.ir: https://www.ekhtebar.ir/آیین-نامه-اجرایی-بند-الف-ماده-107-قانون-ب/ (read 2026-09-28)
- [S86] Search results only (pages not opened): Guardian Council news items on the National Data and Information Management Law, for example https://www.shora-gc.ir/fa/news/9589/ and https://www.shora-gc.ir/fa/news/9683/ (seen 2026-09-28)
- [S87] e-Namad FAQ, archived official page (capture of 2022-08-15): https://web.archive.org/web/20220815194248/https://enamad.ir/Faq (read 2026-09-30)
- [S88] e-Namad rating page, archived official page (capture of 2022-08-15): https://web.archive.org/web/20220815194243/https://enamad.ir/AboutRating (read 2026-09-30)
- [S89] e-Namad business license page, archived official page (capture of 2022-08-15): https://web.archive.org/web/20220815194243/https://enamad.ir/License (read 2026-09-30)
- Failed on 2026-09-27 and again on 2026-09-28: qavanin.ir (JavaScript bot check); rc.majlis.ir (connection closed); enamad.ir and trustseal.enamad.ir, shaparak.ir, cbi.ir, tax.gov.ir, intamedia.ir, mcls.gov.ir, afta.gov.ir, ito.gov.ir, sec.ito.gov.ir, cra.ir, sccr.ir (timeout); samandehi.ir and majazi.ir (403, location block); isiri.gov.ir (host not found); scict.ir (certificate error). Also failed on 2026-09-27: dolat.ir, rrk.ir, dotic.ir, ecommerce.gov.ir, judiciary.ir, police.ir, cyberpolice.ir, fata.gov.ir, arvancloud.ir.

### EU (used only in Section 4.8)

- [S1] GDPR, Regulation (EU) 2016/679: https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32016R0679 (read 2026-09-27; Art 3(1) re-checked in the saved copy 2026-09-28)
- [S2] EDPB Guidelines 07/2020 on controller and processor, v2.1: https://www.edpb.europa.eu/system/files/documents/2023-10/EDPB_guidelines_202007_controllerprocessor_final_en.pdf (read 2026-09-27)
- [S3] EDPB Guidelines 3/2018 on territorial scope, v2.1: https://edpb.europa.eu/sites/default/files/files/file1/edpb_guidelines_3_2018_territorial_scope_after_public_consultation_en_1.pdf (read 2026-09-27)
- [S4] WP29 Guidelines on automated decision-making and profiling, WP251rev.01: https://ec.europa.eu/newsroom/article29/redirection/document/49826 (read 2026-09-27). EDPB endorsement: https://www.edpb.europa.eu/our-work-tools/our-documents/guidelines/automated-decision-making-and-profiling_en (read 2026-09-27)
- [S5] ePrivacy Directive 2002/58/EC, consolidated: https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:02002L0058-20091219 (read 2026-09-27)
- [S6] EDPB Guidelines 2/2023 on Art 5(3) ePrivacy, v2.0: https://www.edpb.europa.eu/system/files/2024-10/edpb_guidelines_202302_technical_scope_art_53_eprivacydirective_v2_en_0.pdf (read 2026-09-27)
- [S7] WP29 Opinion 04/2012 on the cookie consent exemption (WP194): https://ec.europa.eu/justice/article-29/documentation/opinion-recommendation/files/2012/wp194_en.pdf (read 2026-09-27)
- [S8] EDPB Guidelines 02/2021 on virtual voice assistants, v2.0: https://www.edpb.europa.eu/system/files/2021-07/edpb_guidelines_202102_on_vva_v2.0_adopted_en.pdf (read 2026-09-27)
- [S9] Commission Implementing Decision (EU) 2021/914 (SCCs): https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32021D0914 (read 2026-09-27)
- [S10] Commission, New SCCs Questions and Answers: https://commission.europa.eu/system/files/2022-05/questions_answers_on_sccs_en.pdf (read 2026-09-27)
- [S11] Commission, SCC page: https://commission.europa.eu/law/law-topic/data-protection/international-dimension-data-protection/standard-contractual-clauses-scc_en (read 2026-09-27)
- [S12] Commission, Adequacy decisions: https://commission.europa.eu/law/law-topic/data-protection/international-dimension-data-protection/adequacy-decisions_en (read 2026-09-27)
- [S15] European Accessibility Act, Directive (EU) 2019/882, EU Publications Office: https://publications.europa.eu/resource/celex/32019L0882 (read 2026-09-27; the EUR-Lex copy failed with a bot check)
- [S20] AI Act, Regulation (EU) 2024/1689: https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32024R1689 (read 2026-09-27)
- [S21] Commission Guidelines on prohibited AI practices, C(2025) 5052 final: https://ec.europa.eu/newsroom/dae/redirection/document/112367 (read 2026-09-27)
- [S22] C(2025) 884 final: https://ec.europa.eu/newsroom/dae/redirection/document/112366 (read 2026-09-27)
- [S23] Commission library page for the guidelines: https://digital-strategy.ec.europa.eu/en/library/commission-publishes-guidelines-prohibited-artificial-intelligence-ai-practices-defined-ai-act (read 2026-09-27)
- [S24] Regulation (EU) 2026/1744 (Digital Omnibus on AI), OJ L 24.7.2026: https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=OJ:L_202601744 (read 2026-09-27)
- [S25] Commission news, "AI Omnibus enters into force": https://digital-strategy.ec.europa.eu/en/news/ai-omnibus-enters-force (read 2026-09-27)
- [S26] Commission, Digital Omnibus on AI proposal: https://digital-strategy.ec.europa.eu/en/library/digital-omnibus-ai-regulation-proposal (read 2026-09-27)
- [S27] Council document ST 10752/26 ADD 1: https://data.consilium.europa.eu/doc/document/ST-10752-2026-ADD-1/en/pdf (read 2026-09-27)
- [S28] Commission guidelines on Art 50, C(2026) 5054 final: https://ai-act-service-desk.ec.europa.eu/sites/default/files/2026-07/guidelines_on_the_implementation_of_the_transparency_obligations_for_certain_ai_systems_under_article_50_of_the_ai_act_bzptwqhk0ikg1dtlddap41psfy_131215.pdf (read 2026-09-27)
- [S29] DRAFT Commission guidelines on high-risk classification (for consultation): https://ec.europa.eu/newsroom/dae/redirection/document/128561 (read 2026-09-27)
- Failed on 2026-09-27: Council press releases of 7 May and 29 June 2026 (403); European Parliament Legislative Train, press release 20260427IPR42011, EPRS briefing 782651 (empty pages); EUR-Lex procedure page 2025/0359(COD) (empty).

### Web and standards

- [S13] Next.js docs, Font Module: https://nextjs.org/docs/app/api-reference/components/font and https://nextjs.org/docs/app/getting-started/fonts (read 2026-09-27)
- [S16] RFC 9116, security.txt: https://www.rfc-editor.org/rfc/rfc9116.txt (read 2026-09-27)

### Benchmark (read 2026-09-27)

- [S50] Gong privacy policy: https://www.gong.io/legal/privacy-policy
- [S51] Gong terms: https://www.gong.io/legal/terms-and-conditions
- [S52] Gong DPA: https://www.gong.io/legal/data-processing-addendum
- [S53] Gong subprocessors: https://www.gong.io/sub-processors
- [S54] Gong trust: https://www.gong.io/platform/trust and https://trust.gong.io/. Failed: https://www.gong.io/.well-known/security.txt (404); https://vdp.gong.io/p/Welcome (JavaScript only).
- [S56] Observe.AI trust, security and responsible AI: https://www.observe.ai/platform/trust-security-responsible-ai
- [S57] Observe.AI trust: https://www.observe.ai/trust
- [S58] Observe.AI DPA: https://www.observe.ai/data-processing-addendum
- [S59] Observe.AI terms and privacy: https://www.observe.ai/terms, https://www.observe.ai/privacy-policy. Failed: https://trust.observe.ai/ (JavaScript only); https://www.observe.ai/end-user-license-agreement (401).
- [S60a] CallMiner privacy policy: https://callminer.com/privacy-policy
- [S60b] CallMiner security: https://callminer.com/our-company-security
- [S60c] CallMiner blog: https://callminer.com/blog/the-future-of-ai-call-center-automation-in-2025-and-beyond. Failed: trust.callminer.com and status.callminer.com (DNS).
- [S61a] Level AI security: https://thelevel.ai/security
- [S61b] Level AI DPA and subprocessors: https://thelevel.ai/legal/dpa, https://thelevel.ai/legal/subprocessors
- [S62a] Cresta privacy policy and trust center: https://cresta.com/legal/privacy-policy, https://trust.cresta.com/ (summary only; direct download blocked)
- [S62b] Cresta Responsible AI: https://8998024.fs1.hubspotusercontent-na2.net/hubfs/8998024/Cresta-Responsible-AI-Principles.pdf, https://cresta.com/responsible-ai
- [S63a] Atlassian acceptable use policy: https://www.atlassian.com/legal/acceptable-use-policy
- [S63b] Atlassian AI transparency: https://www.atlassian.com/trust/ai/transparency
- [S63c] Atlassian DPA and subprocessors: https://www.atlassian.com/legal/data-processing-addendum, https://www.atlassian.com/legal/sub-processors
- [S63d] Atlassian legal updates and archives: https://www.atlassian.com/legal/legal-updates, https://www.atlassian.com/legal/archives
- [S63e] Atlassian user notice: https://www.atlassian.com/legal/user-notice
- [S63f] Atlassian transparency report: https://www.atlassian.com/trust/privacy/transparency-report
- Also read: https://www.atlassian.com/legal/impressum, https://www.atlassian.com/legal/sla, https://www.atlassian.com/accessibility, https://www.atlassian.com/.well-known/security.txt
- [S64a] GitLab AI Transparency Center: https://about.gitlab.com/ai-transparency-center/
- [S64b] GitLab subprocessors: https://about.gitlab.com/privacy/subprocessors/
- [S64c] GitLab transparency reports: https://handbook.gitlab.com/handbook/legal/privacy/transparency-reports/
- Also read: https://gitlab.com/.well-known/security.txt, https://handbook.gitlab.com/handbook/legal/acceptable-use-policy/, https://handbook.gitlab.com/handbook/legal/data-processing-agreement/
- [S65] Liara terms, privacy policy, SLA: https://liara.ir/terms/, https://liara.ir/privacy-policy/, https://liara.ir/sla/. Failed: all arvancloud.ir pages (JavaScript bot check).

### Repository (at `1b34472`)

- `content/legal/terms.md`, `content/legal/privacy.md`
- `src/lib/site.ts`, `src/lib/content.ts`, `src/lib/locale.tsx`, `src/lib/cart.tsx`, `src/lib/email.ts`, `src/lib/db.ts`, `src/lib/rate-limit.ts`, `src/app/layout.tsx`, `package.json`, `README.md`
- `src/app/api/demo/route.ts`, `src/app/api/contact/route.ts`, `src/app/api/book/route.ts`
- `src/data/products.json`, `content/products/modules-analysis.json`, `content/i18n/products.fa.json`, `src/data/categories.json`, `src/app/faq/content.tsx`
- `Homayar/README.md`, `Homayar/backend/database/models.py`, `Homayar/backend/prompts/qc_en.txt`, `docs/planning/homayar-commercial-blueprint.md`
