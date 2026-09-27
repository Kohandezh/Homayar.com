# Legal, privacy, and transparency pages for the Homayar website

Status: In Review

Date: 2026-09-27

Author role: researcher (`legal-t1-res`), foreman research team `legal-t1`, mission `20260927-homayar-website`.

Base commit for all repository citations: `1b34472`.

**This is research, not legal advice.** Every place that needs a lawyer, an accountant, or a provider to confirm is marked **[LAWYER]**, **[ACCOUNTANT]**, or **[PROVIDER]**. Section 9 collects them.

## Summary

Homayar needs a small, honest legal set that is written for Iran first and is ready for EU customers. We recommend 12 pages at launch and 4 more later (Section 8).

Two findings change the product, not only the pages:

1. **Agent emotion features are prohibited in the EU today.** Since 2 February 2025, the EU AI Act prohibits placing on the market or using AI "to infer emotions of a natural person in the areas of workplace" (Art 5(1)(f)) [S20]. The Commission's official guidelines give a call-centre example. Tracking employees' emotions "such as anger" is prohibited. Tracking customers' emotions is not [S21 para 254]. The old catalog sells "agent stress trend dashboards" and "early burnout signals" (`src/data/products.json:232`, `src/data/products.json:243`). The guidelines name burnout detection at work as prohibited [S21 para 257]. The new site must not offer or describe these agent features.
2. **The old legal text describes a different company.** It names Stripe, USD prices, automatic renewal, website analytics, and security claims nobody has verified (`content/legal/terms.md:17-23`, `content/legal/privacy.md:7`, `content/legal/privacy.md:56-60`). The new site uses an Iranian gateway, has no analytics, and has no verified certification (`docs/planning/homayar-commercial-blueprint.md:605`). The old pages must be rewritten, not translated.

The roles are simple in principle. For call recordings in SaaS, the customer is the controller and Homayar is the processor. In self-hosted mode, Homayar is neither, unless it gets remote support access. For website forms and accounts, Homayar is the controller. The customer, not Homayar, must tell callers and agents that calls are recorded and analyzed. Homayar's terms must require this.

Many Iranian official law sites could not be reached from our network (qavanin.ir, rc.majlis.ir, enamad.ir, and others). We used official mirrors where we could. The claims we could not check are marked **unverified**. An Iranian lawyer must check them before launch.

## 1. Decision or question, in the owner's words

Owner (translated): "Privacy policy, terms and conditions, GDPR pages, and the laws we must follow for this work must be considered. Transparency pages must be considered. And any other topic we must follow and that must be shown on the site to inform the user must also be checked. We can look at existing services in this field and measure ourselves against them."

The one question this spike settles: **which legal, privacy, and transparency pages the Homayar website must have, what each must contain, and which laws and rules apply, for SaaS and for self-hosted.**

## 2. Constraints

From the owner and the SPEC:

- Homayar analyzes recorded customer-service calls: transcription, quality scores for call-center agents, sentiment, problem detection, coaching (`Homayar/README.md:3-18`).
- Two deployment modes. SaaS: Homayar processes recordings for customers. Self-hosted: the customer runs it, and Homayar may never see the data.
- The owner wants Homayar to hold as little sensitive data as possible.
- Payments go through an Iranian gateway (ZarinPal or Zibal). Another team chooses which.
- The new website is in Persian (fa) and English (en). Customers are mostly in Iran, possibly abroad.

From this repository at `1b34472`:

- The operating company's legal name in the site config is "Kohan System Farda" (`src/lib/site.ts:3`). The old terms call it "KSF Company" (`content/legal/terms.md:5`). The contact email is `info@ksf.ir` (`src/lib/site.ts:12`).
- The old site has six locales: fa, en, de, ar, fr, es (`src/lib/content.ts:6`). The new site has two.
- The old footer links only to FAQ, Privacy Policy, and Terms of Service (`src/lib/site.ts:47-52`).
- The website collects personal data through three forms. Demo: name and email (`src/app/api/demo/route.ts:8-9`). Contact: first name, last name, mobile, email, message (`src/app/api/contact/route.ts:8-12`). Booking: name, email, time slot (`src/app/api/book/route.ts:17-18`). They are stored in a database (`src/app/api/demo/route.ts:33`, `src/app/api/contact/route.ts:48`, `src/app/api/book/route.ts:66`).
- Booking confirmations are sent through Resend, a US email API (`src/lib/email.ts:30`).
- The browser stores two things in `localStorage`: the chosen language (`src/lib/locale.tsx:20`, `src/lib/locale.tsx:59`) and a shopping cart (`src/lib/cart.tsx:14`, `src/lib/cart.tsx:55`).
- A search of `src/` for analytics scripts, tag managers, and `document.cookie` found none. Fonts come from `next/font/google` (`src/app/layout.tsx:2`). The Next.js docs say these fonts are "downloaded at build time and self-hosted" and "No requests are sent to Google by the browser" [S13]. Inference: the website sets no tracking cookies today. The website team must confirm this on the live site with the browser network panel.
- The self-hosted package has "No model download, telemetry, font CDN, analytics beacon, package install, license heartbeat" at runtime (`docs/planning/homayar-commercial-blueprint.md:452`). Support bundles exclude "raw audio/transcripts, secrets and customer identities" unless the customer approves a separate export (`docs/planning/homayar-commercial-blueprint.md:458`). License expiry "must not delete recordings" and there is "No concealed remote kill switch" (`docs/planning/homayar-commercial-blueprint.md:460`). These are plans, not shipped features.
- Proposed SaaS retention defaults are "original audio 30 days, transcript/findings 90 days, minimal operational/audit metadata 365 days". The blueprint calls them product defaults, "not statutory retention requirements" (`docs/planning/homayar-commercial-blueprint.md:562`). The old privacy policy says a different default: 12 months (`content/legal/privacy.md:43`).
- "No Homayar certification was verified" (`docs/planning/homayar-commercial-blueprint.md:605`).
- The blueprint already flags that US-linked cloud or software for Iran needs "eligibility and sanctions/export review" (`docs/planning/homayar-commercial-blueprint.md:607`). This spike did not research sanctions. It stays a **[LAWYER]** item.

## 3. Options considered

- **Option A. Do nothing.** Keep the old `content/legal/privacy.md` and `content/legal/terms.md`, and translate them to fa and en.
- **Option B. Iran minimum only.** Publish only what an Iranian online seller needs: company information, terms, privacy policy, refund policy. Defer everything about the EU and AI.
- **Option C. Iran-first, EU-ready set, split by deployment (recommended).** Publish 12 pages at launch: the Iranian minimum, plus a DPA, subprocessor list, cookie statement, acceptable use policy, AI transparency page, data retention page, security page, and vulnerability disclosure. Write each page in fa and en. Say clearly what differs between SaaS and self-hosted. Remove agent emotion features from the product description. Defer the SLA, transparency report, and accessibility statement until there is something true to publish.
- **Option D. Full enterprise trust center now.** Copy the largest vendors (Gong, Atlassian): certifications, SLA with credits, bug bounty, transparency report, trust portal.

## 4. Evidence

Sources are numbered [S1], [S2], and so on. Section 10 lists each URL with the date read. Text marked "Inference:" is our reading, not a quote.

### 4.1 Who is who: controller, processor, data subjects

Definitions from GDPR [S1]:

- Controller (Art 4(7)): the body that "determines the purposes and means of the processing of personal data".
- Processor (Art 4(8)): the body that "processes personal data on behalf of the controller".

The EDPB (the board of EU data protection authorities) explains the split [S2]:

- A cloud provider is a processor "insofar as the cloud service provider does not process the personal data for its own purposes" [S2, example after para 30].
- "Decisions on the purpose of the processing are clearly always for the controller to make" [S2 para 39].
- If a processor starts using the data for its own purpose, "The processor will be considered a controller in respect of that processing and may be subject to sanctions" [S2 para 81]. GDPR Art 28(10) says the same [S1].
- "the same entity may act at the same time as a controller for certain processing operations and as a processor for others" [S2 para 82].

Data subjects (the people the data is about):

| Data subject | Where their data appears |
|---|---|
| Callers (the customer's customers) | Call recordings, transcripts, sentiment, issues |
| Call-center agents (the customer's employees) | Call recordings, QA scores, coaching, dashboards; often also user accounts |
| Account users (customer admins, supervisors) | Login, roles, audit logs |
| Website visitors and leads | Demo, contact, and booking forms; locale and cart in the browser |
| Billing contacts | Invoices, payments |

Roles per deployment (Inference, from [S2] paras 39, 81, 82 and the IT-support example after para 83):

| Data | SaaS | Self-hosted |
|---|---|---|
| Call recordings, transcripts, scores, coaching | Customer = controller. Homayar = processor. | Customer = controller. Homayar = neither, because it never sees the data. If Homayar staff get remote support access, Homayar becomes a processor for that access. |
| Training or improving Homayar's own models with customer calls | Homayar would become a controller for that use [S2 para 81]. Avoid by default. | Not applicable, unless the customer sends data. |
| Account data, login, audit logs | Homayar = controller (and processor for audit logs kept for the customer). | Customer = controller. |
| License, billing, and support contacts | Homayar = controller. | Homayar = controller. |
| Website forms, bookings | Homayar = controller. Resend = Homayar's processor. | Same. |

Iranian law does not use the words "controller" and "processor" in any law we could read. The E-Commerce Law ties processing to the consent of the person the data is about (Art 59) [S30]. The draft personal data protection bill has not become law, as far as we could verify (Section 4.4.5).

### 4.2 GDPR (EU data protection law)

**When it applies.** GDPR applies to a controller or processor outside the EU when processing relates to "the offering of goods or services ... to such data subjects in the Union" or "the monitoring of their behaviour" in the EU (Art 3(2)) [S1]. Recital 23 says "the mere accessibility of the controller's ... website in the Union" is not enough. The use of an EU language or currency "with the possibility of ordering", or "mentioning of customers or users who are in the Union", can show intent [S1]. When a controller targets the EU, "any processor instructed to carry out that processing activity" is also in scope [S3].

Inference for Homayar:

- A Persian and English site for Iranian customers is probably not "offering" to the EU by itself.
- An EU customer (for example a call center in Germany) changes this. The customer is under GDPR. Homayar, as its processor, must sign an Art 28 contract.
- The old site had German, French, and Spanish locales (`src/lib/content.ts:6`). Its FAQ says Homayar "routinely" adapts deployments "to codetermination agreements in Germany, Austria, and the Netherlands" (`src/app/faq/content.tsx:93`). Both are signs of targeting the EU. They should not move to the new site unless they are true and intended.

**EU representative.** A non-EU controller or processor under Art 3(2) must name an EU representative in writing (Art 27(1)). The exemption covers only processing that "is occasional" and low risk (Art 27(2)(a)) [S1]. The EDPB reads "occasional" as "not carried out regularly" [S3]. Inference: SaaS call analysis for EU customers is regular, so the exemption would likely not apply. **[LAWYER]**

**What must be told to people (Art 12 to 14).** The information must be "concise, transparent, intelligible and easily accessible ... using clear and plain language" (Art 12(1)) [S1]. At collection time, the controller must give its identity and contact details, purposes and legal basis, recipients, transfers to third countries "and the existence or absence of an adequacy decision", the storage period, the rights, the right to complain, and "the existence of automated decision-making, including profiling ... and ... meaningful information about the logic involved" (Art 13(1), 13(2)) [S1]. Requests must be answered "within one month" (Art 12(3)) [S1].

Inference: Art 13 applies to Homayar as controller of the website forms and accounts. For call data, Art 13 and 14 fall on the customer as controller. Homayar can help with template notices.

**Rights (Art 15 to 21).** Access, rectification, erasure, restriction, portability "in a structured, commonly used and machine-readable format", and objection to processing based on legitimate interests, "including profiling" [S1].

**Profiling and automated decisions (Art 4(4), Art 22).**

- Profiling means automated evaluation of a person, "in particular to analyse or predict aspects concerning that natural person's performance at work" (Art 4(4)) [S1]. Inference: automated QA scores of agents are profiling.
- Art 22(1): "the right not to be subject to a decision based solely on automated processing, including profiling, which produces legal effects ... or similarly significantly affects him or her" [S1].
- The EDPB-endorsed guidelines (WP251rev.01) [S4] say Art 22(1) is "a general prohibition". "Based solely" means "there is no human involvement". "The controller cannot avoid the Article 22 provisions by fabricating human involvement." Oversight must be "meaningful, rather than just a token gesture", "carried out by someone who has the authority and competence to change the decision". Decisions that "deny someone an employment opportunity or put them at a serious disadvantage" can be significant.
- Inference: if a customer uses Homayar scores alone to decide pay, discipline, or dismissal, Art 22 is likely engaged. The customer is responsible as controller. Homayar's product and terms should keep a human reviewer with authority. They should also show the evidence behind each score. The old terms already call outputs "decision support" and forbid fully automated consequential decisions (`content/legal/terms.md:9`, `content/legal/terms.md:38`). Keep this.

**Processor contract (Art 28).** A controller may use only processors under a written contract (Art 28(9)) [S1]. Art 28(3) lists what the contract must say. The processor:

- processes only on the controller's documented instructions, including for transfers;
- keeps its staff under confidentiality;
- applies Art 32 security;
- follows the subprocessor rules;
- helps with data subject requests;
- helps with security, breach notification, and impact assessments (Art 32 to 36);
- deletes or returns data at the end;
- supports audits.

For subprocessors under a general authorisation, the processor must "inform the controller of any intended changes ... thereby giving the controller the opportunity to object" (Art 28(2)) [S1]. The first processor stays "fully liable" for its subprocessors (Art 28(4)) [S1].

**Records, security, breach, impact assessment.**

- Processors keep their own records (Art 30(2)). The under-250-staff exemption does not apply if "the processing is not occasional" (Art 30(5)) [S1].
- Art 32 lists security measures, including "pseudonymisation and encryption" and "a process for regularly testing" [S1].
- Controllers notify the authority within "72 hours" (Art 33(1)). A processor notifies the controller "without undue delay" (Art 33(2)) [S1].
- An impact assessment (DPIA) is needed for "a systematic and extensive evaluation of personal aspects ... based on automated processing, including profiling" with significant effects (Art 35(3)(a)) [S1]. The EDPB said voice assistant services are "very likely" to need a DPIA [S8]. Inference: customers who use Homayar for agent scoring will likely need a DPIA, and Homayar must help (Art 28(3)(f)).

**Voice and biometric data.**

- Biometric data needs "specific technical processing" that "allow[s] or confirm[s] the unique identification" of a person (Art 4(14)) [S1]. Art 9(1) forbids processing "biometric data for the purpose of uniquely identifying a natural person", with exceptions [S1].
- The EDPB said "The same reasoning applies to voice" as for photographs [S8 footnote 31]. The same document also says "voice data is inherently biometric personal data" [S8 para 31].
- Inference: transcription and scoring without speaker identification is likely outside Art 9. Voice enrollment or voiceprints (the blueprint's optional "Known-voice enrollment", `docs/planning/homayar-commercial-blueprint.md:485`) would be inside it. **[LAWYER]**

**Transfers.**

- Iran has no EU adequacy decision. The Commission's list names Andorra, Argentina, Brazil, Canada, and others, and "Iran" does not appear [S12].
- Transfers without adequacy need "appropriate safeguards", for example standard contractual clauses (SCCs) (Art 46) [S1].
- The 2021 SCCs are for importers "whose processing of the data is not subject to that Regulation" (Decision 2021/914 Art 1(1)) [S9]. The Commission says they "do not work for importers whose processing operations are subject to the GDPR pursuant to Article 3" [S10]. Its SCC page still says a set for that case is "in the process of developing" [S11].
- The old privacy policy relies on SCCs (`content/legal/privacy.md:67`). Inference: which transfer tool fits depends on whether Homayar is itself under Art 3(2). **[LAWYER]**

**Employment.** EU countries may add rules for "processing of employees' personal data in the employment context", with "particular regard to ... monitoring systems at the work place" (Art 88) [S1]. We did not research national employment laws. **[LAWYER]** for each EU country with a customer.

**Data protection officer.** Art 37(1)(b) requires one when "core activities of the controller or the processor" need "regular and systematic monitoring of data subjects on a large scale" [S1]. Whether Homayar's SaaS reaches this is a fact question. **[LAWYER]**

### 4.3 EU AI Act

**Scope.** The AI Act (Regulation (EU) 2024/1689) applies to providers placing AI systems on the EU market "irrespective of whether those providers are established or located within the Union or in a third country" (Art 2(1)(a)). It also applies to providers and deployers in third countries "where the output produced by the AI system is used in the Union" (Art 2(1)(c)) [S20]. A "provider" develops an AI system and "places it on the market or puts the AI system into service under its own name or trademark" (Art 3(3)). A "deployer" uses it "under its authority" (Art 3(4)) [S20].

Inference: Homayar is the provider for both SaaS and self-hosted if it sells to an EU customer. The customer is the deployer.

**What counts as emotion recognition.**

- An "emotion recognition system" identifies or infers "emotions or intentions of natural persons on the basis of their biometric data" (Art 3(39)) [S20]. Biometric data includes "behavioural characteristics" (Art 3(34)) [S20].
- Recital 18 excludes "physical states, such as pain or fatigue", and "mere detection of readily apparent expressions ... unless they are used for identifying or inferring emotions" [S20].
- The Commission guidelines list "voice" as a behavioural biometric [S21 para 251].
- Text is different: "An AI system inferring emotions from written text (content/sentiment analyses) ... is not based on biometric data and therefore does not fall within the scope of the prohibition" [S21 para 251].

**The prohibition.** Art 5(1)(f) prohibits "the placing on the market, the putting into service for this specific purpose, or the use of AI systems to infer emotions of a natural person in the areas of workplace and education institutions, except where the use of the AI system is intended to be put in place or into the market for medical or safety reasons" [S20]. It applies from 2 February 2025 (Art 113(a)) [S20]. The 2026 amendments did not change point (f) [S24].

The Commission guidelines on prohibited practices (C(2025) 5052 final, 29 July 2025) [S21] say:

- "Using webcams and voice recognition systems by a call centre to track their employee's emotions, such as anger, is prohibited." [S21 para 254]
- "If only deployed for personal training purposes, emotion recognition systems are allowed if the results are not shared with HR responsible persons and cannot impact the assessment, promotion etc. of the person trained" [S21 para 254].
- "Using voice recognition systems by a call centre to track their customers emotions, such as anger or impatience, is not prohibited by Article 5(1)(f)" [S21 para 254].
- "The general monitoring of stress levels at the workplace is not permitted under health or safety aspects. For example, an AI system intended to detect burnout or depression at the workplace ... would not be covered by the exception and would remain prohibited." [S21 para 257]
- "Workplace" covers "any specific physical or virtual space where natural persons engage in tasks ... assigned by their employer", whatever the person's status (employee, contractor, trainee) [S21 para 254].

**High-risk categories.**

- Annex III point 1(c): "AI systems intended to be used for emotion recognition" [S20].
- Annex III point 4(b): AI used "to monitor and evaluate the performance and behaviour of persons in such relationships" (work relationships) [S20].
- The Art 6(3) exception (not high-risk when it does not "materially influenc[e] the outcome of decision making") does not apply if "the AI system performs profiling of natural persons" [S20].
- A draft Commission guideline (for consultation, not adopted) gives "An AI system used in call centres to infer emotions of customers" from "vocal tone, pitch and volume" as an Annex III 1(c) example. It also treats "feedback scores, performance assessments, appraisals or rankings generated by AI systems that feed directly into employee files" as performance evaluation [S29 paras 274-275]. This is a draft. Do not treat it as final.

**Transparency and worker information.**

- Art 50(3): "Deployers of an emotion recognition system ... shall inform the natural persons exposed thereto of the operation of the system" [S20]. It applies from 2 August 2026 [S28 para 153].
- Art 26(7): employers who deploy a high-risk system at work "shall inform workers' representatives and the affected workers" before use [S20].
- Art 4 (AI literacy), as amended in 2026, requires providers and deployers to "take measures to support the development of AI literacy of their staff" [S24].

**Dates, after the 2026 "Digital Omnibus" amendment.** Regulation (EU) 2026/1744 of 8 July 2026 was published in the Official Journal on 24 July 2026 [S24]. It entered into force "on the third day following that of its publication" [S24], which the Commission gives as 27 July 2026 [S25]. It replaced Art 113(c) [S24]:

| Obligation | Applies from | Source |
|---|---|---|
| Art 5 prohibitions, incl. 5(1)(f) | 2 February 2025 | [S20] Art 113(a) |
| Penalties chapter | 2 August 2025 | [S20] Art 113(b) |
| Art 50 transparency, incl. 50(3) | 2 August 2026 | [S20] Art 113; [S28] para 153 |
| High-risk duties for Annex III systems (Chapter III Sections 1 to 3, incl. Art 26(7)) | 2 December 2027 | [S24] new Art 113(c)(i) |
| High-risk duties for Annex I systems | 2 August 2028 | [S24] new Art 113(c)(ii) |

The Council and Parliament press releases on the Omnibus returned HTTP 403 or empty pages. We relied on the Official Journal text [S24] and the Council adoption note [S27].

**Penalties.** Breaking Art 5: up to EUR 35,000,000 or 7% of worldwide annual turnover, "whichever is higher" (Art 99(3)). Breaking Art 26 or Art 50: up to EUR 15,000,000 or 3% (Art 99(4)). For SMEs, the lower amount applies (Art 99(6)) [S20].

**Mapping Homayar features (Inference, from the sources above):**

| Feature (old catalog wording) | AI Act status for EU use | Action |
|---|---|---|
| "track customer and agent emotional state" from "pitch, pace, energy, silence" (`src/data/products.json:225`) | Agent side: prohibited (Art 5(1)(f)). Customer side: allowed but high-risk (Annex III 1(c)). | Remove the agent side. Keep the customer side only with notices. |
| "Agent stress trend dashboards" (`src/data/products.json:243`) | Prohibited [S21 para 257]. | Remove. |
| "Early burnout signals surfaced weeks before attrition events" (`src/data/products.json:232`) | Prohibited [S21 para 257]. | Remove. |
| "Fused acoustic + transcript sentiment scoring" (`src/data/products.json:240`) | Uses voice, so it is biometric-based. Same split as the first row. | Customer side only. |
| Transcript (text) sentiment of the customer | Outside the definition of emotion recognition [S21 para 251]. | Allowed. Call it "text sentiment", not "emotion recognition". |
| Agent QA scores sent to supervisors | Likely high-risk, Annex III 4(b), from 2 December 2027. Art 6(3) exception blocked by profiling. | Plan the high-risk provider duties. Keep human review. |
| Transcription, issue detection, summaries | Not listed in Annex III (Inference). | Transparency page only. |

The same emotion wording also appears in `content/products/modules-analysis.json:49-90`, `content/i18n/products.fa.json:110`, and `src/data/categories.json:11`.

**Iranian law on emotion data.** E-Commerce Law Art 58 makes it illegal to store or process data about "the physical, psychological, or sexual condition of people, without their explicit consent" [S30, English translation; the Persian text is unverified]. Inference: emotion or stress scores of agents may be "psychological condition" data under Art 58. They would then need explicit consent in Iran too. **[LAWYER]**

**How peers handle this.** Atlassian's acceptable use policy forbids using its AI "to infer the emotions of an individual in workplace and educational settings" [S63a]. We found no emotion statement at Gong, Observe.AI, CallMiner, Level AI, or Cresta [S50-S62]. CallMiner's blog quotes an outside source that welcomes the ban, but this is a quotation, not CallMiner policy [S60c].

### 4.4 Iranian law

Access problem: qavanin.ir and dolat.ir show a JavaScript bot check. rc.majlis.ir, rrk.ir (the official gazette), enamad.ir, shaparak.ir, cbi.ir, and cra.ir time out or drop the connection. dotic.ir and ecommerce.gov.ir return 403 (all tried 2026-09-27). We used official mirrors: the Guardian Council (shora-gc.ir), the Ministry of ICT (ict.gov.ir), the Consumers and Producers Protection Organization (cppo.ir), and WIPO Lex (an official UN copy, English only).

#### 4.4.1 Electronic Commerce Law (قانون تجارت الکترونیکی), 1382

Source: WIPO Lex English text [S30]. **The Persian wording of every article below is unverified**, because rc.majlis.ir and qavanin.ir could not be reached. The English is a translation. The Persian text has legal force.

- **Who is a consumer.** "Any person acting for any purpose other than business or professional occupation" (Art 2(o)). Inference: a company buying Homayar for its call center is probably not a "consumer" under this law. A natural person buying for private use would be.
- **Information before the contract (Art 33).** Technical specifications; "The identity of the supplier, the trade name under which he is working and his address"; email and phone; all costs "including the price of goods or services, tax"; how long the offer is valid; terms for "payment ... delivery or implementation, cancellation, return, after-sales services".
- **Information sent separately (Art 34).** Complaints address; after-sales and guarantees; termination terms under Art 37 and 38; "Terms and conditions of cancellation in service contracts". It must be given "in a durable medium" (Art 35), which includes email (Art 2(s)).
- **Right of withdrawal (Art 37, 38).** "at least seven working days in which to withdraw from the contract ... without penalty and without giving any reason" (Art 37). For services, the period starts "from the day the contract is effective", and only once the Art 33 and 34 information is given (Art 38). Refunds must be made "immediately" (Art 38(c)). Exceptions are set by an executive regulation (Art 38(d)). We could not read the list of exceptions [S43]. Whether "working days" is in the Persian text is unverified.
- **Data protection (Art 58, 59).** Art 58 is quoted in 4.3 above. Art 59 allows processing "Upon the consent of the person", with a specified purpose, collection "to the required extent", accuracy, access and correction, and "complete removal" on request.
- **Penalties.** Breaking Art 33 to 37: a fine of 10 to 50 million rials (Art 69). Breaking Art 58: "one to three year imprisonment" (Art 71). Later fine updates were not checked.

#### 4.4.2 Consumer Protection Law (قانون حمایت از حقوق مصرف‌کنندگان), 1388

Source: CPPO PDF [S31]. Verified on an official site.

- **Who is a consumer.** «مصرف‌کننده: هر شخص حقیقی یا حقوقی است که کالا یا خدمتی را خریداری می‌کند» ("any natural or legal person who buys goods or a service") (Art 1-1). Inference: unlike the E-Commerce Law, this covers Homayar's business customers and covers services.
- Suppliers are responsible for the soundness of goods and services under the law, the contract, or custom (Art 2).
- Suppliers must give a warranty and an invoice with the price and date (Art 3-1), and needed information (Art 3-2). Which services need a warranty is set by a ministry list (Art 3, Note 3). We did not read that list, so it is unverified whether SaaS is on it.
- The price or service fee must be shown "clearly and in writing" (Art 5).
- «تبلیغات خلاف واقع و ارایه اطلاعات نادرست ... ممنوع می‌باشد» ("False advertising and giving wrong information ... is forbidden") (Art 7).
- Late or partial performance means paying damages too (Art 19, Note).
- Criminal liability falls on the CEO or managing director (Art 16, Note).
- This law has no online withdrawal right.

Inference: Art 7 makes unverified claims a legal risk, not only a trust risk. This covers certifications, "AES-256", "third-party penetration testing", and "works-council documentation pack" (`content/legal/privacy.md:56-60`, `src/app/faq/content.tsx:93`).

#### 4.4.3 Computer Crimes Law (قانون جرایم رایانه‌ای), 1388

Source: Ministry of ICT site [S32]. Verified on an official site. Articles 1 to 54 became Articles 729 to 782 of the Islamic Penal Code (Art 55).

- **Interception (Art 2).** «هر کس به طور غیرمجاز محتوای در حال انتقال ارتباطات غیرعمومی ... را شنود کند» ("anyone who, without authorization, intercepts the content in transmission of non-public communications"): 6 months to 2 years prison, or a fine, or both.
- **Publishing private audio (Art 17).** Publishing or making available another person's private audio "without their consent", in a way that harms them or their reputation: 91 days to 2 years, or a fine.
- **Company liability (Art 19, 20).** A company is liable when an employee commits the crime with the manager's knowledge or "because of the manager's lack of supervision".
- **Hosting providers (Art 23).** They must block criminal content on an order from the committee or a judge. They must also report criminal content when they learn of it.
- **Retention minimums.** Access providers keep traffic data and user information for at least 6 months (Art 32). «ارائه دهندگان خدمات میزبانی داخلی موظفند اطلاعات کاربران خود را حداقل تا شش ماه پس از خاتمه اشتراک و محتوای ذخیره شده ... را حداقل تا پانزده روز نگهداری کنند» ("domestic hosting providers must keep user information for at least six months after the subscription ends, and stored content for at least fifteen days") (Art 33).
- **Orders.** A judge can order data preserved (Art 34) and handed over (Art 35).

Inference: if Homayar SaaS counts as a "domestic hosting provider", Art 33 sets minimum retention. A customer's "delete after 7 days" setting, or the blueprint's immediate-deletion option (`docs/planning/homayar-commercial-blueprint.md:562`), may conflict with it. The law text we read does not define "hosting provider". **[LAWYER]**

#### 4.4.4 Call recording

- **Constitution Art 25** (verified [S33]): «...ضبط و فاش كردن مكالمات تلفنی ... استراق سمع و هرگونه تجسس ممنوع است، مگر به حكم قانون» ("recording and disclosing telephone conversations ... eavesdropping and any kind of spying are forbidden, except by order of law"). The text does not say whom it binds. Inference: it is in the chapter on the rights of the people and is usually read as a limit on the state. Whether it limits a business recording its own calls with notice is not answered by the text. **[LAWYER]**
- **Islamic Penal Code (Ta'zirat) Art 582** (secondary source only [S35], unverified): punishes "government employees and officers" who record or intercept calls without legal permission. Inference: it targets state officials, not private companies.
- **Law on Respecting Legitimate Freedoms (1383)** (secondary only [S34], unverified): it binds courts and judicial officers. We found no clause on phone calls.
- **Computer Crimes Law Art 2** (above): the key word is "unauthorized" (غیرمجاز). Inference: a business recording its own call is arguably not third-party interception. Homayar receives recordings after the call, so it does not intercept "in transmission". The law does not define "authorized". Notice and consent are the safe basis.
- **We found no official rule that allows or regulates a private business recording customer calls with notice.** We also found none that forbids it. The Central Bank (cbi.ir) and the telecom regulator (cra.ir) could not be reached. This is a gap, not a finding. **[LAWYER]**

#### 4.4.5 Personal data protection bill

- Verified: the Ministry of ICT unveiled the draft bill «صیانت و حفاظت از داده‌های شخصی» (draft dated Tir 1397) at the Elecomp exhibition [S36, page dated 2018-07-28].
- Secondary only: a 1403 news report says the government's legal commission approved it and it would go to the Majlis after cabinet approval [S37]. Another 1403 report quotes the ICT minister: the bill "went back from the floor to the committee" [S38].
- A search of the Guardian Council site found no review of such a bill [S45]. The Council does publish its reviews of other laws (for example [S42]).
- **Status as of 2026-09-27: unverified.** We found no official source saying it became law. The legal pages must not claim compliance with it. They should be easy to update if it passes.

#### 4.4.6 e-Namad and payment gateways

- **The official e-Namad requirements are unverified.** enamad.ir and ecommerce.gov.ir could not be reached.
- Zibal's own FAQ (verified, gateway's own site [S39]): «برای دریافت درگاه، داشتن اینماد معتبر، کد رهگیری مالیاتی به نام متقاضی و حداقل ۱۸ سال سن الزامی است» ("To get a gateway, a valid e-Namad, a tax tracking code in the applicant's name, and being at least 18 are required"). To the question whether sites without e-Namad get a gateway, it answers «خیر» ("No").
- ZarinPal's terms (gateway's own site [S40]) do not mention e-Namad. They forbid «انتقال ابزار پذیرش به خارج از کشور و یا استفاده از ابزار پذیرش در خارج از کشور» ("moving the acceptance tool abroad or using it outside the country"). They also forbid selling outside the registered business category, and «اخذ هرگونه مبلغ اضافی از خریداران» ("charging buyers any extra amount") (clause 4-1). The gateway may be used only on the merchant's own domain (clause 4-4).
- ZarinPal's blog (the gateway's marketing, not a rule document [S41]) says: e-Namad has three levels (no star (temporary), one star, two stars); two stars need SSL; and "under Central Bank and Shaparak decisions" a gateway is not possible without e-Namad. It says the applicant's postal address, postal code, and landline are checked. The Central Bank or Shaparak rule it cites is unverified.

Inference: Homayar needs e-Namad before it can take online payments through Zibal, and very likely through ZarinPal. The site must then show the e-Namad seal, the company's identity, address, and contact details. **[PROVIDER]**: the e-Namad center must confirm the exact list of required pages.

#### 4.4.7 Language and currency

- **Constitution Art 15** (verified [S33]): Persian is the official language. «اسناد و مكاتبات و متون رسمی» ("official documents, correspondence and texts") must be in Persian. Inference: this mainly covers state texts. For Iranian customers, the Persian version of the legal pages should be the governing version. **[LAWYER]**
- **Law on foreign words (1375)** (secondary only [S44], unverified): it targets product names, shop names, and signs. Inference: it does not clearly forbid an English version of the site.
- **Currency.** The Guardian Council approved removing four zeros from the national currency, with "a multi-year transition period" [S42, dated 17 Aban 1404]. The new unit's name and ratio are unverified. We found no rule that forces rials over tomans on a website. Inference: show prices in rials, or clearly label tomans, to avoid "wrong information" under Consumer Protection Law Art 7. **[ACCOUNTANT]**

### 4.5 Call recording consent: who must do what

Inference, from 4.1 to 4.4:

- **The customer (the call center) must inform callers and agents.** In SaaS, it is the controller [S2]. In self-hosted, it runs everything. In the EU, it has the Art 13 and 14 duties [S1], the Art 50(3) duty for customer emotion recognition [S20], and (from 2 December 2027) the Art 26(7) duty to inform workers [S20, S24]. In Iran, the safe basis is notice at the start of the call plus consent where needed (Constitution Art 25, Computer Crimes Law Art 2, E-Commerce Law Art 58 and 59) [S30, S32, S33].
- **Homayar must require this in its terms**, and must not promise that recording is lawful. The best peer clause we found is Gong's DPA: "Customer shall configure and utilize the consent management features of the Services, including dual-party consent functionality, to ensure lawful recording ... Customer will provide all necessary notices to relevant Data Subjects" [S52]. Level AI's DPA requires the customer to meet "any applicable requirement to provide notice to Data Subjects of or obtain consents" [S61b].
- The old terms already say "You are solely responsible for the lawful collection of call recordings" and require notices "for both your personnel and your end customers" (`content/legal/terms.md:46`). Keep this idea. It needs an Iranian lawyer's rewrite.

What Homayar's terms (SaaS) and license (self-hosted) must require from customers:

1. Record calls only where the law allows. Tell callers at the start of the call (for example an IVR message) that the call is recorded and analyzed with AI.
2. Tell agents in writing (employment contract or policy) what is analyzed, who sees scores, and how to contest them. In the EU, inform workers' representatives where the law requires it.
3. Do not use Homayar to infer the emotions of employees. Do not enable voice-based emotion features for agents.
4. Keep a human reviewer, with authority to change the result, for any decision about pay, discipline, promotion, or dismissal.
5. Do not upload calls you have no right to record, keep, or process.
6. Handle data subject requests as controller. Homayar helps as processor.
7. Indemnify Homayar for breach of these duties (**[LAWYER]** for enforceability in Iran).

Homayar should publish template notice texts in fa and en (a call-start message and an agent notice) to help customers comply. This is a recommendation, not a legal duty.

### 4.6 Cookies and browser storage on the website

- ePrivacy Directive Art 5(3): storing or reading information on a user's device needs consent, except "as strictly necessary in order for the provider of an information society service explicitly requested by the subscriber or user to provide the service" [S5].
- The EDPB says this covers "both non-personal data and personal data" [S6 para 12] and storage through browser mechanisms [S6 para 36]. It names "local storage" [S6 para 44]. It does not decide which uses are exempt [S6 para 4].
- The older WP29 opinion on exemptions (WP194) [S7] says shopping cart storage is "exempted under CRITERION B". Language preference is an exempt "UI customization" use, but "only session (or short term) cookies storing such information are exempted". It adds that a notice in "a prominent location (e.g. "uses cookies" written next to the flag)" is enough to remember the choice longer. We did not check whether the EDPB formally endorsed WP194.

Inference for the website:

- The cart in `localStorage` fits the shopping-cart exemption.
- The locale choice in `localStorage` has no expiry. Add a short line next to the language switcher, for example "Your choice is saved in this browser." This follows the WP194 suggestion.
- With no analytics, no third-party scripts, and self-hosted fonts, **no cookie banner is needed**. A short "Cookies and browser storage" statement is enough. It must list the two storage keys (`homayar-locale`, `homayar-cart-v1`) and say that no tracking is used.
- If anyone later adds analytics, a chat widget, embedded video, or external fonts, this conclusion changes. They would need a consent step first.
- This applies only to visitors the EU rules cover. We found no Iranian cookie rule. The statement is cheap and helps everyone.

### 4.7 Benchmark: what peers publish

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

Sources: [S50] to [S65]. Full notes list every fetch failure. CallMiner's trust center and status page failed DNS lookup. Observe.AI's trust portal is JavaScript-only. ArvanCloud blocked all requests, so we used Liara as the Iranian example.

Patterns worth copying:

1. **Say the same thing everywhere about training.** Observe.AI's trust page says "inference only" and "never trained on your data" [S56]. Its DPA says the customer "authorizes Observe.AI to use Customer Data to train, develop, test, and improve customer-specific AI models" [S58]. A buyer's lawyer will find this. Cresta says openly that its models "can be further customized using each individual customer's proprietary data" [S62b]. GitLab says "GitLab does not currently train its own models" [S64a].
2. **Per-feature AI transparency** (Atlassian [S63b]): each AI feature lists models used, where it works best, and "Limitations and considerations".
3. **Subprocessor list split by deployment** (GitLab [S64b]): a column says whether each entry applies to "GitLab.com, Dedicated, and Self-Managed". This fits Homayar's two modes.
4. **Notice before a new subprocessor.** Atlassian gives notice "at least thirty (30) days before" [S63c]. Gong gives notice "before authorizing" a new subprocessor and allows objection "within 5 business days" [S52]. Observe.AI notifies "promptly after engaging" [S58], which is weaker.
5. **Breach notice to customers.** Level AI promises notice "in no more than three (3) business days" [S61b].
6. **Retention defaults on the trust page.** Observe.AI: "13 months (configurable)" [S57].
7. **Version archive and update subscription** (Atlassian [S63d]).
8. **User notice for people added by an admin** (Atlassian [S63e]). This fits call-center agents who are users but not the customer.
9. **Government request transparency report** (Atlassian [S63f], GitLab [S64c]). Only the two general SaaS companies publish one.
10. **Iranian practice** (Liara [S65]): its terms say it follows Iranian law. It may give user data to legal authorities, and «هیچ‌گونه اعتراضی پذیرفته نخواهد بود» ("no objection will be accepted"). Accounts cannot be deleted «به دلایل قانونی (مبارزه با پولشویی و کلاهبرداری‌های اینترنتی)» ("for legal reasons, anti-money-laundering and internet fraud"). It shows an e-Namad seal. We saw no DPA, subprocessor list, or AI statement.

Mistakes to avoid, seen on peer pages: "ISO 271001:2022" (CallMiner [S60b]); "ISO 27001 Type II", a term that does not exist (Level AI [S61a]); leftover "Lorem Ipsum" template text in live legal pages (Cresta [S62a]).

### 4.8 What must change from the old site (Addendum items)

| Old text | Problem | Change |
|---|---|---|
| "Payments are processed by **Stripe**" (`content/legal/terms.md:17`), Stripe as subprocessor (`content/legal/privacy.md:50`) | New path is an Iranian gateway. | Name the chosen gateway (ZarinPal or Zibal). Say the gateway collects card data, not Homayar. |
| "Fees are stated in USD" (`content/legal/terms.md:20`), USD prices (`src/data/products.json:16`) | Iranian buyers, Iranian gateway. ZarinPal forbids use of the gateway outside Iran [S40]. | Prices in rials (or clearly labeled tomans) for Iranian customers. Foreign customers need a separate contract and payment path. **[LAWYER] [ACCOUNTANT]** |
| "Subscriptions renew automatically" (`content/legal/terms.md:19`) | Not verified that the chosen gateway supports automatic recurring charges. | Do not promise auto-renewal unless the payment team confirms the gateway supports it and the customer explicitly agrees. Default to renewal by invoice. **[PROVIDER]** |
| Interest "1.5% per month" on late payment (`content/legal/terms.md:23`) | Written for another legal system. | **[LAWYER]**: rewrite under Iranian law. |
| "non-refundable except where required by law" (`content/legal/terms.md:20`) | Consumers under the E-Commerce Law have a 7-working-day withdrawal right (Art 37) [S30]. | A clear refund page (Section 8). |
| Governing law "the jurisdiction in which KSF Company is established" (`content/legal/terms.md:74`) | Vague. The company name differs from `src/lib/site.ts:3`. | Name the registered legal entity, Iranian law, and Iranian courts or arbitration. **[LAWYER]** |
| "website analytics" (`content/legal/privacy.md:7`) | The site has no analytics (Section 2). | Remove. Describe the real data: form fields, bookings, Resend email. |
| Default retention "12 months" (`content/legal/privacy.md:43`) | Differs from the blueprint's 30/90/365 proposal (`docs/planning/homayar-commercial-blueprint.md:562`). | Publish only defaults that the product actually enforces. |
| "AES-256", "third-party penetration testing" (`content/legal/privacy.md:56-60`) | Not verified (`docs/planning/homayar-commercial-blueprint.md:605`). Consumer Protection Law Art 7 [S31]. | Publish only controls that exist and are checked. |
| SCCs as the transfer tool (`content/legal/privacy.md:67`) | May not fit (Section 4.2). | **[LAWYER]** before any EU customer. |
| Works-council pack and codetermination claims (`src/app/faq/content.tsx:93`) | Unverified. Also signals EU targeting. | Remove unless true. |
| Agent emotion, stress, and burnout features (`src/data/products.json:221-254`) | Prohibited in the EU [S20, S21]. | Remove from product copy in all languages. |
| Six locales (`src/lib/content.ts:6`) | New site has fa and en. | Legal pages in fa and en only (see below). |

**Languages.** Publish every legal page in fa and en.

- The Persian text should be the governing version for Iranian customers (Section 4.4.7).
- English is needed for foreign customers and for EU visitors, because information must be "intelligible" (GDPR Art 12(1)) [S1]. Inference: EU customers would receive English, not German or French.
- Do not publish de, ar, fr, or es versions of legal pages. Removing those locales also removes one sign of targeting the EU (Recital 23) [S1].
- Each page must say which language version governs if the two differ. **[LAWYER]**

### 4.9 Evidence per option

- **Option A (do nothing).** It keeps false statements: Stripe, USD, analytics, unverified security claims (Section 4.8). This is a risk under Consumer Protection Law Art 7 [S31]. It sells a prohibited EU feature [S20, S21]. It lacks the refund and company information that the E-Commerce Law asks for (Art 33, 34) [S30], and very likely e-Namad [S39, S41]. Rejected.
- **Option B (Iran minimum).** It meets the core Iranian needs (Art 33, 34, 37 [S30]; e-Namad [S39]). It leaves the agent emotion feature and AI claims unaddressed. It cannot support even one EU customer (no Art 28 DPA [S1]). It does not match what B2B buyers expect: 6 of 7 peers publish a DPA or subprocessor list (Section 4.7). Acceptable only if the owner decides never to sell outside Iran.
- **Option C (Iran-first, EU-ready).** It meets the Iranian needs. It covers GDPR processor duties (Art 28), the AI Act prohibition and transparency, and the cookie rules. It matches the peer pattern at the level of the mid-size vendors. It publishes only true statements. The extra cost is about 8 more pages, mostly templates that a lawyer must review.
- **Option D (full trust center).** Most items would be empty or untrue today. There is no certification (`docs/planning/homayar-commercial-blueprint.md:605`), no measured uptime, and no government request history. Publishing them risks Consumer Protection Law Art 7 [S31]. Only the two largest general SaaS companies publish transparency reports (Section 4.7). Defer.

## 5. Comparison table

| Criterion | A. Do nothing | B. Iran minimum | C. Iran-first, EU-ready | D. Full trust center |
|---|---|---|---|---|
| Meets E-Commerce Law information and refund duties | No | Yes | Yes | Yes |
| Supports e-Namad and gateway approval | No | Likely | Likely | Likely |
| All statements true today | No | Yes | Yes | No |
| Removes the AI Act-prohibited feature | No | No | Yes | Yes |
| Can sign an EU customer (Art 28 DPA, subprocessors) | No | No | Yes, after lawyer review | Yes |
| Covers self-hosted (license, roles) | No | No | Yes | Yes |
| Matches peer practice | Partly | Below Iranian and global peers | Mid-size vendor level | Largest vendor level |
| Pages at launch | 2 | 4 | 12 | 16+ |
| Lawyer work | Full rewrite anyway | Low | Medium | High |

## 6. Recommendation

**Choose Option C.** Publish 12 pages at launch in fa and en, with the Persian text governing for Iranian customers. Add 4 more later, when there are true facts to publish. Remove all agent emotion, stress, and burnout features from the product copy before launch.

Why:

- It is the smallest set that covers the laws that apply now: the E-Commerce Law information and refund rules, e-Namad for the gateway, the AI Act prohibition in force since February 2025, and GDPR processor duties for any EU customer.
- It keeps every public statement true. That protects Homayar under Consumer Protection Law Art 7 and with security reviewers.
- It supports the owner's goal of holding little data. The pages say clearly that self-hosted means Homayar does not see call data. SaaS pages state short default retention and no training on customer data.
- It follows the useful patterns from peers without copying claims Homayar cannot support.

What would change this recommendation:

- **The owner decides never to sell or deliver outside Iran.** Then the DPA, the GDPR page, and the EU representative drop to "not needed". Option B plus the AI transparency page and the AUP would be enough. The AI Act emotion rules would still apply to any EU use of the output (Art 2(1)(c)) [S20].
- **The Iranian personal data protection bill becomes law.** Then the privacy policy, DPA, and retention page must be rewritten to its terms.
- **An Iranian lawyer finds a rule on business call recording** (for example a Central Bank circular for bank call centers). Then the recording section and customer notice templates change.
- **The e-Namad center publishes a different page list.** Then Section 8 must follow it.
- **Homayar decides to train shared models on customer data.** Then Homayar becomes a controller for that use [S2]. The DPA, privacy policy, and AI page need a new legal basis and an opt-in.
- **The Commission adopts final high-risk guidelines** that differ from the draft [S29]. Then the classification of agent QA scores must be reviewed.
- **Homayar gains a certification or measured uptime.** Then add the SLA page and certification lines.

## 7. Page matrix

"Required" means a law, an official regulator text, or a gateway rule we cite asks for it, or it is needed to meet a duty we cite. "Required (inference)" means our reading of the sources says it is needed, but no text names the page. "Recommended" means good practice, mostly from the benchmark.

| # | Page | Level | Deployment | Basis | Launch |
|---|---|---|---|---|---|
| 1 | Company information (imprint) | Required | Both | E-Commerce Law Art 33(b)(c) [S30]; gateway checks of address and phone [S41] | Launch |
| 2 | Terms of service (SaaS) | Required | SaaS | E-Commerce Law Art 33(f), 34 [S30]; Consumer Protection Law Art 2, 3 [S31] | Launch |
| 3 | Self-hosted license (EULA) | Required (inference) | Self-hosted | Software is licensed by contract; also Consumer Protection Law Art 3 [S31] | Launch |
| 4 | Privacy policy | Required | Both | E-Commerce Law Art 59 [S30]; GDPR Art 13 for EU visitors [S1] | Launch |
| 5 | Refund and cancellation policy | Required | Both | E-Commerce Law Art 33(f), 34(c)(d), 37, 38 [S30] | Launch |
| 6 | Acceptable use policy | Required (inference) | Both | AI Act Art 5(1)(f) [S20]; GDPR Art 22 [S1]; Constitution Art 25 [S33] | Launch |
| 7 | Data processing agreement (DPA) | Required for EU customers; Recommended otherwise | SaaS (and self-hosted with remote support) | GDPR Art 28(3), 28(9) [S1] | Launch |
| 8 | Subprocessor list | Required for EU customers; Recommended otherwise | SaaS mainly; column for self-hosted | GDPR Art 28(2), 13(1)(e) [S1] | Launch |
| 9 | Cookies and browser storage statement | Required (inference) for EU visitors; Recommended otherwise | Website | ePrivacy Art 5(3) [S5]; WP194 [S7] | Launch |
| 10 | AI transparency | Required (inference) | Both | AI Act Art 50(3), 26(7) duties of customers need provider information [S20]; GDPR Art 13(2)(f) [S1]; Consumer Protection Law Art 7 [S31] | Launch |
| 11 | Data retention and deletion | Required (as content; may be part of the privacy policy) | SaaS; self-hosted note | GDPR Art 13(2)(a), 17, 28(3)(g) [S1]; E-Commerce Law Art 59(e) [S30]; Computer Crimes Law Art 33 [S32] | Launch |
| 12 | Security page, with vulnerability disclosure and `/.well-known/security.txt` | Recommended | Both | GDPR Art 32 [S1]; RFC 9116 format [S16]; peers (Section 4.7) | Launch |
| 13 | GDPR and international customers | Recommended (content can live in 4, 7, 8) | SaaS | GDPR Art 3(2), 27 [S1, S3] | Later, before the first EU customer |
| 14 | SLA | Recommended | SaaS | Peers (Section 4.7); Consumer Protection Law Art 19 [S31] | Later, after uptime is measured |
| 15 | Transparency report (government requests) | Recommended | SaaS | Computer Crimes Law Art 34, 35 [S32]; peers [S63f, S64c] | Later. **[LAWYER]** first: may Homayar publish Iranian requests? |
| 16 | Accessibility statement | Recommended | Website | EU Accessibility Act likely does not apply: it covers "services provided to consumers" (Art 2(2)) and exempts microenterprises (Art 4(5)) [S15] | Later |
| 17 | Call-recording notice templates for customers | Recommended | Both | Section 4.5 | Launch, as part of the AI or trust area |

## 8. What the website must show

This is the build outline for the website team. Routes are suggestions. Use the locale prefix the new site uses (for example `/fa/legal/terms` and `/en/legal/terms`). Every page needs, at the top: title, "Last updated" date, a version number, and a line saying which language version governs. Every page needs, at the bottom: contact email and a link to the legal hub.

Text marked **[LAWYER]** must come from, or be approved by, a lawyer. Do not write it from this outline.

Footer: replace the old three links (`src/lib/site.ts:47-52`) with a "Legal" group (hub, terms, privacy, refunds, company information) and a "Trust" group (AI transparency, security, subprocessors, data retention). Show the e-Namad seal in the footer once it is issued.

### 8.1 `/legal`: Legal hub

Title: "Legal and trust" / «حقوقی و اعتماد».

Sections, in order:

1. One-line intro: "These pages explain how Homayar works with your data and what we promise."
2. List of every page with its last-updated date.
3. "Which pages apply to you": SaaS customers, self-hosted customers, website visitors.
4. "Changes": how we announce changes (email to account admins, 30 days before material changes; Recommended, from [S63c]).
5. Link to the archive of old versions (Recommended [S63d]).

### 8.2 `/legal/company`: Company information

Title: "Company information" / «اطلاعات شرکت».

Sections:

1. Registered legal name. **[LAWYER]** confirm: `src/lib/site.ts:3` says "Kohan System Farda"; `content/legal/terms.md:5` says "KSF Company".
2. National ID (شناسه ملی) and registration number. **[ACCOUNTANT]**
3. Postal address and postal code.
4. Landline phone, email.
5. e-Namad seal (after issue).
6. Brand: "Homayar is a product of [legal name]."

Key point: "This is who you are contracting with."

### 8.3 `/legal/terms`: Terms of service (SaaS)

Title: "Terms of service" / «شرایط استفاده از خدمات».

Sections, in order:

1. Who we are and who these terms are for (businesses using Homayar SaaS). Link to the company page.
2. The service in plain words: we transcribe and analyze recorded calls. Outputs are decision support, not decisions.
3. Accounts and users: the customer is responsible for its users.
4. Plans, prices, and payment: prices in rials (or labeled tomans), tax shown, the gateway named, how renewal works. **[LAWYER] [ACCOUNTANT]**
5. Refunds and cancellation: short summary plus link to the refund page.
6. Your duties on recording and consent: the seven points from Section 4.5. **[LAWYER]**
7. What you may not do: link to the acceptable use policy.
8. Your data: you own it. We process it only to provide the service. We do not train shared models on it. Export and deletion at the end. Link to the DPA and retention page.
9. AI outputs: they can be wrong. Keep human review for decisions about people. Link to the AI page.
10. Service changes and availability (no uptime number until measured).
11. Confidentiality.
12. Warranty and liability limits. **[LAWYER]**
13. Suspension and termination.
14. Governing law and disputes: Iranian law, named forum. **[LAWYER]**
15. Changes to these terms, language that governs, contact.

Key plain-language points: "You must tell callers and agents." "We never decide about your staff; your people do." "We do not train shared AI models on your calls."

### 8.4 `/legal/license`: Self-hosted license (EULA)

Title: "Self-hosted license" / «مجوز نسخه نصب در محل».

Sections, in order:

1. Who this is for: customers who install Homayar on their own servers.
2. What you get: license scope, sites, processed-minute band, term (`docs/planning/homayar-commercial-blueprint.md:460`).
3. Your data stays with you: Homayar does not receive call data. No telemetry, no license heartbeat (`docs/planning/homayar-commercial-blueprint.md:452`). Only claim this when the package really does it.
4. Support access: only when you allow it; support bundles exclude audio and transcripts unless you approve a separate export (`docs/planning/homayar-commercial-blueprint.md:458`). If we get access, the DPA applies to that access.
5. License expiry: read-only or grace period; no data deletion; no hidden kill switch (`docs/planning/homayar-commercial-blueprint.md:460`).
6. Your duties on recording and consent: same seven points as the terms.
7. Restrictions: no reverse engineering, no resale, no prohibited uses (link to the AUP).
8. Third-party and model licenses: link to the license notices shipped in the package.
9. Updates and support windows.
10. Warranty, liability, governing law. **[LAWYER]**

### 8.5 `/legal/privacy`: Privacy policy

Title: "Privacy policy" / «سیاست حریم خصوصی».

Sections, in order:

1. Who we are and how to contact us about privacy.
2. Two roles, in plain words: for the website and your account, we decide how data is used. For call recordings in SaaS, your employer or the company you called decides, and we only process it for them. In self-hosted mode, we do not see call data.
3. Data we collect on the website: demo form (name, email), contact form (name, mobile, email, message), booking (name, email, time slot). Why, and how long we keep it.
4. Account and billing data: what, why, how long. The payment gateway handles card data.
5. Who receives data: hosting provider, email provider (Resend, United States), payment gateway. Link to the subprocessor list. **[LAWYER]**: sanctions review of US providers (`docs/planning/homayar-commercial-blueprint.md:607`).
6. Browser storage: link to the cookies statement. No tracking, no analytics.
7. International transfers: where data is stored. Iran has no EU adequacy decision [S12]. **[LAWYER]**
8. Your rights: access, correction, deletion, objection. How to ask. We answer within one month [S1 Art 12(3)].
9. If you are a caller or an agent in a recording: ask the company that recorded the call. We help them answer.
10. Automated decisions: Homayar does not make decisions about people. Customers must keep human review.
11. Security: link to the security page.
12. Legal requests: we give data to authorities only under Iranian law (for example Computer Crimes Law Art 34 and 35 [S32]). **[LAWYER]**
13. Changes, governing language, contact.

### 8.6 `/legal/refunds`: Refund and cancellation

Title: "Refunds and cancellation" / «بازگشت وجه و لغو».

Sections, in order:

1. Who can cancel within 7 working days after purchase, without a reason, and get a full refund (consumers, E-Commerce Law Art 37 [S30]). **[LAWYER]**: confirm whether it covers business buyers, and the exceptions list.
2. How to cancel: email or account page. What we need.
3. How and when we refund: to the same card or account, as soon as possible (Art 38(c) says "immediately").
4. Cancelling a subscription later: effective at the end of the paid period, or pro-rata. **[LAWYER]**
5. Self-hosted licenses and installation services: when they can be refunded. **[LAWYER]**
6. If we fail to deliver: we perform and pay for the damage (Consumer Protection Law Art 19 Note [S31]).
7. Complaints address (E-Commerce Law Art 34(a) [S30]).

### 8.7 `/legal/acceptable-use`: Acceptable use policy

Title: "Acceptable use policy" / «سیاست استفاده مجاز».

Sections, in order:

1. Why this page exists: to protect callers, agents, and other customers.
2. Recording: only lawfully recorded calls, with notice.
3. **Never use Homayar to infer the emotions, stress, or mental state of employees or job candidates** (AI Act Art 5(1)(f) [S20]; pattern from Atlassian [S63a]).
4. No decisions about pay, discipline, promotion, or dismissal based only on Homayar scores. A person with authority must review (GDPR Art 22 [S1], WP251 [S4]).
5. No voice identification or voiceprints unless a separate written agreement allows it (GDPR Art 9 [S1]).
6. No unlawful, discriminatory, or deceptive use.
7. Security rules: no probing, no bypassing limits, no reverse engineering.
8. What we do if the policy is broken: notice, then suspension.

### 8.8 `/legal/dpa`: Data processing agreement

Title: "Data processing agreement" / «قرارداد پردازش داده».

Sections, in order:

1. Short summary page first: "This agreement applies when we process call recordings for you in SaaS, or when we access your self-hosted system for support."
2. Parties and roles: customer controller, Homayar processor.
3. Subject, duration, nature, purpose, data types, data subjects (callers, agents, users) (Art 28(3) [S1]).
4. Processor duties (Art 28(3)(a) to (h) [S1]): instructions only; confidentiality; security; subprocessors; help with rights requests; help with security, breach, and DPIA; delete or return at the end; audits.
5. Subprocessors: link to the list, notice before any new one, and how to object (Art 28(2) [S1]). Recommended: 30 days' notice [S63c].
6. Breach notice to the customer without undue delay (Art 33(2) [S1]). Recommended: a fixed number of days [S61b].
7. No training on customer data without separate written opt-in (to stay a processor [S2 para 81]).
8. International transfers and the transfer tool. **[LAWYER]**
9. Iranian law duties that may override deletion (Computer Crimes Law Art 33 [S32]). **[LAWYER]**
10. Downloadable PDF, version, and signature process.

Whole text: **[LAWYER]**.

### 8.9 `/legal/subprocessors`: Subprocessor list

Title: "Subprocessors" / «پردازشگرهای فرعی».

Table columns: name, purpose, data involved, location, applies to (SaaS / self-hosted / website), date added. Pattern from GitLab [S64b].

Rows known today from the repository: email delivery (Resend, `src/lib/email.ts:30`, website only), payment gateway (ZarinPal or Zibal, pending), hosting provider (pending). List each AI model provider (speech-to-text, LLM) only when it is really used for customer data (`docs/planning/homayar-commercial-blueprint.md:520`).

Below the table: how to subscribe to changes, and a dated change log (pattern from Cresta [S62a]).

### 8.10 `/legal/cookies`: Cookies and browser storage

Title: "Cookies and browser storage" / «کوکی‌ها و ذخیره‌سازی مرورگر».

Sections:

1. "We use no tracking, no analytics, and no advertising cookies."
2. What we store in your browser: `homayar-locale` (your language) and `homayar-cart-v1` (your cart). Both stay on your device.
3. How to clear them: clear site data in your browser.
4. Fonts are served from our own server. No request goes to Google (per [S13]).
5. "If we ever add anything else, we will ask first and update this page."

Next to the language switcher, add a small line: "Your choice is saved in this browser" (per WP194 [S7]).

### 8.11 `/trust/ai`: AI transparency

Title: "How Homayar uses AI" / «هومایار چگونه از هوش مصنوعی استفاده می‌کند».

Sections, in order (per-feature pattern from Atlassian [S63b]):

1. What the AI does and does not do: it transcribes, scores against your rubric, finds issues, suggests coaching. It does not decide about people.
2. One entry per feature (transcription, QA scoring, issue detection, customer text sentiment, summaries, coaching). Each entry states: models used (by family and where they run), input, output, works best when, known limits (for example Persian accents, 8 kHz audio, overlapping speech; `docs/planning/homayar-commercial-blueprint.md:528`), and the human review step.
3. What humans decide: final scores, disputes, any action about an agent. Agents can see and contest their scores (`docs/planning/homayar-commercial-blueprint.md:1287`, WP14 review and appeals).
4. What we do not offer: no inference of employees' emotions, stress, or burnout. Customer sentiment is text-based unless you enable voice features, and those need notices (AI Act Art 5(1)(f), 50(3) [S20]).
5. Your data and training: we do not train shared models on your calls. Say the same thing in the DPA (lesson from [S56, S58]).
6. Accuracy: publish measured results only (the blueprint's gates are "targets, not achieved results", `docs/planning/homayar-commercial-blueprint.md:532`).
7. EU AI Act: which features may be high-risk, dates (Section 4.3), and what Homayar will provide to deployers. **[LAWYER]**
8. Notice templates for customers (page 17 in Section 7): a call-start message and an agent notice, in fa and en.

### 8.12 `/trust/data-retention`: Data retention and deletion

Title: "Data retention and deletion" / «نگهداری و حذف داده».

Sections:

1. SaaS default periods, only those the product enforces (proposed: audio 30 days, transcripts and findings 90 days, audit metadata 365 days; `docs/planning/homayar-commercial-blueprint.md:562`). Customers can change them.
2. How deletion works: access blocked at once, then removal from storage, search indexes, and backups within a stated window (`docs/planning/homayar-commercial-blueprint.md:564`). Do not promise instant deletion from backups.
3. Legal holds and legal minimums: Iranian law may require us to keep some account data (Computer Crimes Law Art 33 [S32]). **[LAWYER]**
4. At the end of a contract: export window, then deletion.
5. Self-hosted: you set and run retention. We hold nothing.
6. Website form data: how long we keep leads and bookings.

### 8.13 `/trust/security`: Security, with vulnerability disclosure

Title: "Security" / «امنیت».

Sections:

1. Controls that exist today, stated plainly. No certification claims (`docs/planning/homayar-commercial-blueprint.md:605`).
2. Tenant isolation and access control, as built.
3. Incident handling: how and when we tell customers.
4. Reporting a vulnerability: email, what to include, response time, safe-harbour wording (**[LAWYER]**), no bounty unless one exists.
5. Link to `/.well-known/security.txt`. It must contain `Contact` (with `mailto:`) and `Expires` (less than a year ahead) and be served over HTTPS as `text/plain` [S16].

### 8.14 Later pages

- `/legal/gdpr` (GDPR and international customers): before the first EU customer. Contents: when GDPR applies, roles, EU representative (**[LAWYER]**), transfers, how to sign the DPA.
- `/trust/sla`: after uptime is measured. Contents: availability target, how measured, credits.
- `/trust/transparency-report`: after a lawyer confirms what may be published about government requests.
- `/accessibility`: a short statement of the WCAG level targeted and a contact for problems.

## 9. Open questions

### For the owner

1. Will Homayar sell to or deliver output to customers outside Iran? This decides whether pages 7, 8, and 13 are required or only recommended.
2. Do you agree to remove agent emotion, stress, and burnout features from the product and the site (Section 4.3)?
3. Should customer-side voice emotion remain, with notices, or should Homayar offer only text sentiment? Text sentiment avoids the "emotion recognition" category [S21 para 251].
4. Will Homayar ever train shared models on customer calls? The recommendation assumes "no".
5. What is the registered legal name: "Kohan System Farda" or "KSF Company"?
6. Should the Persian version govern for all customers, or English for foreign customers?

### For a lawyer in Iran

1. Does Constitution Art 25 limit a private business recording its own calls with notice? Is there any rule for call centers (Central Bank, telecom regulator)?
2. Is Homayar SaaS a "domestic hosting provider" under Computer Crimes Law Art 23 and 33? If so, how does the 6-month and 15-day retention minimum fit customer-chosen deletion?
3. Do agent emotion or stress scores count as "psychological condition" data under E-Commerce Law Art 58 (explicit consent)?
4. Does the 7-working-day withdrawal right (E-Commerce Law Art 37) apply to Homayar's buyers? What are the exceptions in the Art 38/42 regulation?
5. Is SaaS on the ministry list that requires a warranty and invoice (Consumer Protection Law Art 3 Note 3)?
6. Confirm the Persian text of the E-Commerce Law articles we cited from the English translation.
7. Current status of the personal data protection bill.
8. Liability, indemnity, late-payment, and dispute clauses under Iranian law.
9. May Homayar publish statistics on Iranian government data requests?
10. Sanctions and export review for US providers (Resend, cloud, AI APIs) and for any sale to EU customers (`docs/planning/homayar-commercial-blueprint.md:607`).

### For an EU data protection lawyer (before the first EU customer)

1. Is Homayar under GDPR Art 3(2) for an EU customer's data? Does it need an EU representative (Art 27)?
2. Which transfer tool fits: the 2021 SCCs, or something else [S9, S10, S11]?
3. Is Homayar the "provider" of a high-risk system (Annex III 4(b)) for agent QA scores? What documentation is needed by 2 December 2027?
4. Is voice-based customer emotion worth offering, given the high-risk duties (Annex III 1(c))?
5. National workplace monitoring and works-council rules in each target country (Art 88).
6. Does SaaS call QA trigger a data protection officer duty (Art 37(1)(b))?

### For an accountant

1. Rial or toman display, and the effect of the four-zeros law [S42].
2. Tax lines on invoices and what the e-invoice system requires the site to show (unverified in this spike).
3. National ID and registration number for the company page.

### For providers

1. e-Namad center: the exact list of pages and details the site must show, and one-star versus two-star requirements (enamad.ir could not be reached).
2. The chosen gateway: support for recurring charges, refund process, and any required pages.
3. Resend: data location and data processing terms. Whether it is under the EU-US Data Privacy Framework was not checked.

### Unverified claims in this document

1. All Persian wording of the E-Commerce Law (we used the WIPO English text).
2. "Seven working days" in the Persian text of E-Commerce Law Art 37.
3. The exceptions list of the Art 38/42 regulation.
4. Ta'zirat Art 582 and the Citizens' Rights Law 1383 (secondary sources only).
5. Any official rule on business call recording (none found).
6. The data protection bill's status after 1403.
7. Official e-Namad requirements and the Central Bank or Shaparak rule tying gateways to e-Namad.
8. The 1375 foreign-words law (secondary only).
9. The four-zeros law text, and any rial-versus-toman rule.
10. The Council and Parliament press releases on the AI Omnibus (403 or empty). The Official Journal text was read.
11. The EDPB endorsement status of WP194.
12. The draft high-risk guidelines [S29] are a draft for consultation, not final.

## 10. Sources

All sources were read on 2026-09-27 unless stated otherwise. "Failed" means the page could not be fetched that day.

### EU data protection and web

- [S1] GDPR, Regulation (EU) 2016/679: https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32016R0679 (read 2026-09-27)
- [S2] EDPB Guidelines 07/2020 on controller and processor, v2.1: https://www.edpb.europa.eu/system/files/documents/2023-10/EDPB_guidelines_202007_controllerprocessor_final_en.pdf (read 2026-09-27)
- [S3] EDPB Guidelines 3/2018 on territorial scope, v2.1: https://edpb.europa.eu/sites/default/files/files/file1/edpb_guidelines_3_2018_territorial_scope_after_public_consultation_en_1.pdf (read 2026-09-27)
- [S4] WP29 Guidelines on automated decision-making and profiling, WP251rev.01: https://ec.europa.eu/newsroom/article29/redirection/document/49826 (read 2026-09-27). EDPB endorsement page: https://www.edpb.europa.eu/our-work-tools/our-documents/guidelines/automated-decision-making-and-profiling_en (read 2026-09-27)
- [S5] ePrivacy Directive 2002/58/EC, consolidated 19.12.2009: https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:02002L0058-20091219 (read 2026-09-27)
- [S6] EDPB Guidelines 2/2023 on the technical scope of Art 5(3) ePrivacy, v2.0: https://www.edpb.europa.eu/system/files/2024-10/edpb_guidelines_202302_technical_scope_art_53_eprivacydirective_v2_en_0.pdf (read 2026-09-27)
- [S7] WP29 Opinion 04/2012 on the cookie consent exemption (WP194): https://ec.europa.eu/justice/article-29/documentation/opinion-recommendation/files/2012/wp194_en.pdf (read 2026-09-27)
- [S8] EDPB Guidelines 02/2021 on virtual voice assistants, v2.0: https://www.edpb.europa.eu/system/files/2021-07/edpb_guidelines_202102_on_vva_v2.0_adopted_en.pdf (read 2026-09-27)
- [S9] Commission Implementing Decision (EU) 2021/914 (SCCs): https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32021D0914 (read 2026-09-27)
- [S10] Commission, New SCCs Questions and Answers: https://commission.europa.eu/system/files/2022-05/questions_answers_on_sccs_en.pdf (read 2026-09-27)
- [S11] Commission, Standard contractual clauses page: https://commission.europa.eu/law/law-topic/data-protection/international-dimension-data-protection/standard-contractual-clauses-scc_en (read 2026-09-27)
- [S12] Commission, Adequacy decisions page: https://commission.europa.eu/law/law-topic/data-protection/international-dimension-data-protection/adequacy-decisions_en (read 2026-09-27)
- [S13] Next.js docs, Font Module API reference: https://nextjs.org/docs/app/api-reference/components/font (read 2026-09-27). Also https://nextjs.org/docs/app/getting-started/fonts (read 2026-09-27)
- [S15] European Accessibility Act, Directive (EU) 2019/882, from the EU Publications Office: https://publications.europa.eu/resource/celex/32019L0882 (read 2026-09-27). The EUR-Lex copy https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32019L0882 failed (bot check).
- [S16] RFC 9116, security.txt: https://www.rfc-editor.org/rfc/rfc9116.txt (read 2026-09-27)

### EU AI Act

- [S20] AI Act, Regulation (EU) 2024/1689, original Official Journal text: https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32024R1689 (read 2026-09-27)
- [S21] Commission Guidelines on prohibited AI practices, C(2025) 5052 final, 29.7.2025: https://ec.europa.eu/newsroom/dae/redirection/document/112367 (read 2026-09-27)
- [S22] Commission approval of the guidelines, C(2025) 884 final, 4.2.2025: https://ec.europa.eu/newsroom/dae/redirection/document/112366 (read 2026-09-27)
- [S23] Commission library page for the guidelines: https://digital-strategy.ec.europa.eu/en/library/commission-publishes-guidelines-prohibited-artificial-intelligence-ai-practices-defined-ai-act (read 2026-09-27)
- [S24] Regulation (EU) 2026/1744 (Digital Omnibus on AI), OJ L 24.7.2026: https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=OJ:L_202601744 (read 2026-09-27)
- [S25] Commission news, "AI Omnibus enters into force", 27 July 2026: https://digital-strategy.ec.europa.eu/en/news/ai-omnibus-enters-force (read 2026-09-27)
- [S26] Commission, Digital Omnibus on AI proposal, COM(2025) 836: https://digital-strategy.ec.europa.eu/en/library/digital-omnibus-ai-regulation-proposal (read 2026-09-27)
- [S27] Council document ST 10752/26 ADD 1, 22 June 2026: https://data.consilium.europa.eu/doc/document/ST-10752-2026-ADD-1/en/pdf (read 2026-09-27)
- [S28] Commission guidelines on Art 50 transparency, C(2026) 5054 final, 20.7.2026: https://ai-act-service-desk.ec.europa.eu/sites/default/files/2026-07/guidelines_on_the_implementation_of_the_transparency_obligations_for_certain_ai_systems_under_article_50_of_the_ai_act_bzptwqhk0ikg1dtlddap41psfy_131215.pdf (read 2026-09-27)
- [S29] DRAFT Commission guidelines on high-risk classification, Annex III part (for consultation): https://ec.europa.eu/newsroom/dae/redirection/document/128561 (read 2026-09-27). Library page: https://digital-strategy.ec.europa.eu/en/library/draft-commission-guidelines-classification-high-risk-ai-systems
- Failed: Council press releases of 7 May 2026 and 29 June 2026 on consilium.europa.eu (HTTP 403); European Parliament Legislative Train, press release 20260427IPR42011, and EPRS briefing 782651 (empty pages); EUR-Lex procedure page 2025/0359(COD) (empty page).

### Iran

- [S30] Electronic Commerce Law 1382, English, WIPO Lex: https://www.wipo.int/wipolex/en/legislation/details/7711 and PDF https://wipolex-res.wipo.int/edocs/lexdocs/laws/en/ir/ir008en.pdf (read 2026-09-27). The Persian original https://rc.majlis.ir/fa/law/show/93997 failed.
- [S31] Consumer Protection Law 1388, CPPO: https://cppo.ir/قانون-حمایت-از-حقوق-مصرف-کنندگان and PDF https://cppo.ir/uploads/1/2024/Sep/17/107.pdf (read 2026-09-27)
- [S32] Computer Crimes Law 1388, Ministry of ICT: https://chb.ict.gov.ir/fa/computercrimes/otherlaw-سایر-مقررات (read 2026-09-27)
- [S33] Constitution of the Islamic Republic of Iran, Guardian Council: https://www.shora-gc.ir/fa/news/5898/قانون-اساسی-جمهوری-اسلامی-ایران (read 2026-09-27)
- [S34] Secondary: Law on Respecting Legitimate Freedoms 1383, ekhtebar.ir: https://www.ekhtebar.ir/قانون-احترام-به-آزادی-های-مشروع-و-حفظ-حق/ (read 2026-09-27). Official https://rc.majlis.ir/fa/law/show/94150 failed.
- [S35] Secondary: Islamic Penal Code Book 5 (Ta'zirat), ekhtebar.ir: https://www.ekhtebar.ir/كتاب-پنجم-قانون-مجازات-اسلامي-‌-تعزير/ (read 2026-09-27). Official https://rc.majlis.ir/fa/law/show/92683 failed.
- [S36] Ministry of ICT news on the draft data protection bill: https://www.ict.gov.ir/fa/newsagency/21691/ (read 2026-09-27; page dated 2018-07-28)
- [S37] Secondary: alef.ir, 15 Ordibehesht 1403: https://www.alef.ir/news/4030215063.html (read 2026-09-27)
- [S38] Secondary: aftabnews.ir: https://aftabnews.ir/fa/news/943575/ (read 2026-09-27)
- [S39] Zibal payment gateway page and FAQ: https://zibal.ir/ipg (read 2026-09-27)
- [S40] ZarinPal terms: https://www.zarinpal.com/terms (read 2026-09-27)
- [S41] ZarinPal blog, "What is e-Namad" (gateway marketing): https://www.zarinpal.com/blog/اینماد-چیست/ (read 2026-09-27)
- [S42] Guardian Council news on removing four zeros, 17 Aban 1404: https://www.shora-gc.ir/fa/news/11280/ (read 2026-09-27)
- [S43] CPPO page on the E-Commerce Law Art 38/42 regulation: https://cppo.ir/آیین-نامه-اجرایی-38-و-42-قانون-تجارت-الکترونیکی and attachment https://cppo.ir/uploads/1/2025/Mar/01/masrafi_1.pdf (read 2026-09-27; the attachment is a list of goods, not the regulation text)
- [S44] Secondary: Law prohibiting foreign names and terms 1375, ekhtebar.ir copy (read 2026-09-27). Official sources failed: irprint.farhang.gov.ir (403) and https://rc.majlis.ir/fa/law/show/90477.
- [S45] Guardian Council site search for data protection terms: https://www.shora-gc.ir/fa/search (read 2026-09-27; no matching result)
- Failed (2026-09-27): qavanin.ir and dolat.ir (JavaScript bot check); rc.majlis.ir (connection closed); majlis.ir (body "updating"); dotic.ir and ecommerce.gov.ir (403); rrk.ir, enamad.ir, trustseal.enamad.ir, shaparak.ir, cbi.ir, cra.ir, mimt.gov.ir, judiciary.ir, police.ir, cyberpolice.ir, fata.gov.ir, tax.gov.ir, intamedia.ir (timeout).

### Benchmark

- [S50] Gong privacy policy: https://www.gong.io/legal/privacy-policy (read 2026-09-27)
- [S51] Gong terms: https://www.gong.io/legal/terms-and-conditions (read 2026-09-27)
- [S52] Gong DPA: https://www.gong.io/legal/data-processing-addendum (read 2026-09-27)
- [S53] Gong subprocessors: https://www.gong.io/sub-processors (read 2026-09-27)
- [S54] Gong trust page: https://www.gong.io/platform/trust (read 2026-09-27); trust center https://trust.gong.io/ (read 2026-09-27); status https://status.gong.io/ (read 2026-09-27). Failed: https://www.gong.io/.well-known/security.txt (404); https://vdp.gong.io/p/Welcome (JavaScript only).
- [S56] Observe.AI trust, security and responsible AI: https://www.observe.ai/platform/trust-security-responsible-ai (read 2026-09-27)
- [S57] Observe.AI trust: https://www.observe.ai/trust (read 2026-09-27)
- [S58] Observe.AI DPA: https://www.observe.ai/data-processing-addendum (read 2026-09-27)
- [S59] Observe.AI terms: https://www.observe.ai/terms (read 2026-09-27); privacy https://www.observe.ai/privacy-policy (read 2026-09-27). Failed: https://trust.observe.ai/ (JavaScript only); https://www.observe.ai/end-user-license-agreement (401).
- [S60a] CallMiner privacy policy: https://callminer.com/privacy-policy (read 2026-09-27)
- [S60b] CallMiner security: https://callminer.com/our-company-security (read 2026-09-27)
- [S60c] CallMiner blog: https://callminer.com/blog/the-future-of-ai-call-center-automation-in-2025-and-beyond (read 2026-09-27). Failed: trust.callminer.com and status.callminer.com (DNS).
- [S61a] Level AI security: https://thelevel.ai/security (read 2026-09-27)
- [S61b] Level AI DPA: https://thelevel.ai/legal/dpa (read 2026-09-27); subprocessors https://thelevel.ai/legal/subprocessors (read 2026-09-27)
- [S62a] Cresta privacy policy: https://cresta.com/legal/privacy-policy (read 2026-09-27); trust center https://trust.cresta.com/ (read 2026-09-27, summary only; direct download blocked with 403)
- [S62b] Cresta Responsible AI Principles PDF: https://8998024.fs1.hubspotusercontent-na2.net/hubfs/8998024/Cresta-Responsible-AI-Principles.pdf (read 2026-09-27); https://cresta.com/responsible-ai (read 2026-09-27)
- [S63a] Atlassian acceptable use policy: https://www.atlassian.com/legal/acceptable-use-policy (read 2026-09-27)
- [S63b] Atlassian AI transparency: https://www.atlassian.com/trust/ai/transparency (read 2026-09-27)
- [S63c] Atlassian DPA: https://www.atlassian.com/legal/data-processing-addendum (read 2026-09-27); subprocessors https://www.atlassian.com/legal/sub-processors (read 2026-09-27)
- [S63d] Atlassian legal updates and archives: https://www.atlassian.com/legal/legal-updates and https://www.atlassian.com/legal/archives (read 2026-09-27)
- [S63e] Atlassian user notice: https://www.atlassian.com/legal/user-notice (read 2026-09-27)
- [S63f] Atlassian transparency report: https://www.atlassian.com/trust/privacy/transparency-report (read 2026-09-27)
- Also read: https://www.atlassian.com/legal/impressum, https://www.atlassian.com/legal/sla, https://www.atlassian.com/accessibility, https://www.atlassian.com/.well-known/security.txt (all read 2026-09-27)
- [S64a] GitLab AI Transparency Center: https://about.gitlab.com/ai-transparency-center/ (read 2026-09-27)
- [S64b] GitLab subprocessors: https://about.gitlab.com/privacy/subprocessors/ (read 2026-09-27)
- [S64c] GitLab transparency reports: https://handbook.gitlab.com/handbook/legal/privacy/transparency-reports/ (read 2026-09-27)
- Also read: https://gitlab.com/.well-known/security.txt, https://handbook.gitlab.com/handbook/legal/acceptable-use-policy/, https://handbook.gitlab.com/handbook/legal/data-processing-agreement/ (all read 2026-09-27)
- [S65] Liara terms, privacy policy, SLA: https://liara.ir/terms/, https://liara.ir/privacy-policy/, https://liara.ir/sla/ (read 2026-09-27). Failed: all arvancloud.ir pages (JavaScript bot check).

### Repository (at `1b34472`)

- `content/legal/terms.md`, `content/legal/privacy.md`
- `src/lib/site.ts`, `src/lib/content.ts`, `src/lib/locale.tsx`, `src/lib/cart.tsx`, `src/lib/email.ts`, `src/app/layout.tsx`
- `src/app/api/demo/route.ts`, `src/app/api/contact/route.ts`, `src/app/api/book/route.ts`
- `src/data/products.json`, `content/products/modules-analysis.json`, `content/i18n/products.fa.json`, `src/data/categories.json`, `src/app/faq/content.tsx`
- `Homayar/README.md`, `docs/planning/homayar-commercial-blueprint.md`
