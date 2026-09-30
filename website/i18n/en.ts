const en = {
  locale: "en",
  brand: "Homayar",
  tagline: "Post-call voice intelligence",
  titleSuffix: "Homayar",

  common: {
    skipToContent: "Skip to main content",
    login: "Log in",
    start: "Start",
    requestDemo: "Request a demo",
    seeHowItWorks: "See how it works",
    seeProduct: "See the product",
    talkToUs: "Talk to us",
    learnMore: "Learn more",
    comingSoon: "Coming soon",
    inProgress: "Being prepared",
    menu: "Open menu",
    closeMenu: "Close menu",
    switchLanguage: "Switch to Persian",
    switchLanguageShort: "فارسی",
    onThisPage: "On this page",
    readMore: "Read more",
    backHome: "Back to home",
    decorativeMockup: "Illustration of the Homayar console",
  },

  nav: {
    home: "Home",
    product: "Product",
    howItWorks: "How it works",
    solutions: "Solutions",
    security: "Security",
    deployment: "Deployment",
    pricing: "Pricing",
    about: "About",
    contact: "Contact",
    transparency: "Transparency",
    privacy: "Privacy",
    terms: "Terms",
    gdpr: "GDPR",
  },

  footer: {
    product: "Product",
    trust: "Trust",
    company: "Company",
    legal: "Legal",
    note: "Homayar analyzes call recordings you are authorized to process. This website loads no third-party scripts and sets no tracking cookies.",
    rights: "Homayar. All rights reserved.",
  },

  ctaBand: {
    title: "See a real call report, not a slide deck.",
    text: "Tell us about your queue, your recording setup and where the data must stay. We will walk you through the console with a sample call and scope a pilot.",
    primary: "Request a demo",
    secondary: "How it works",
  },

  notFound: {
    title: "Page not found",
    text: "The address may be wrong, or the page has moved.",
    home: "Go to the home page",
    contact: "Contact us",
  },

  home: {
    meta: {
      title: "Homayar: post-call quality review for contact centers",
      description:
        "Homayar transcribes authorized call recordings, runs four analysis agents and gives supervisors scored reports, detected problems and coaching points. Persian and English, cloud or self-hosted.",
    },
    particles: {
      brand: "Homayar",
      slogan: "Every call, an insight.",
      hint: "Scroll: the points spell it out",
    },
    hero: {
      eyebrow: "Post-call analysis for contact centers",
      title: "Turn call recordings into quality reviews you can trust.",
      text: "Homayar transcribes authorized call recordings, runs four analysis agents and gives supervisors a scored report, detected problems and coaching points. Persian and English. In the cloud or fully on your own servers.",
      headlines: [
        "Turning calls",
        "into audio files",
        "for review",
        "of response quality",
        "uncovering your organization's problems",
        "with artificial intelligence.",
        "Multilingual support.",
      ],
      primary: "Request a demo",
      secondary: "See the product",
      hint: "Scroll: a call becomes a report",
      facts: ["4 analysis agents", "Persian and English", "Self-hosted option", "Personal-data masking"],
    },
    outputs: {
      eyebrow: "What you get for every call",
      title: "One recording in. Four reviewed outputs out.",
      text: "Each report is built from the transcript of that call, so a supervisor can open the conversation behind any score.",
      items: [
        {
          icon: "gauge",
          title: "Quality control score",
          text: "Empathy, problem solving, clarity, tone, call closing and silence handling, scored per call with an overall result.",
        },
        {
          icon: "alert",
          title: "Problem detection",
          text: "Root causes, system failures, issue severity and problems that keep coming back across calls.",
        },
        {
          icon: "trend",
          title: "Business insight",
          text: "Upsell signals, cancellation and churn-risk signals, satisfaction trend and revenue opportunities the customer mentioned.",
        },
        {
          icon: "book",
          title: "Training package",
          text: "A coaching package generated from each agent's weak points, with behavioral recommendations.",
        },
      ],
    },
    pipeline: {
      eyebrow: "How it flows",
      title: "From the PBX to the dashboard in four steps",
      steps: [
        { title: "Bring the call in", text: "Upload a file or let your VOIP system post it through a webhook." },
        { title: "Transcribe", text: "Speech to text with Whisper, in Persian, English or auto-detected." },
        { title: "Analyze", text: "Four agents read the transcript and write their part of the report." },
        { title: "Review", text: "Dashboard, call report, PDF export and a copilot that answers questions." },
      ],
      link: "See the full pipeline",
    },
    product: {
      eyebrow: "A working console, not a concept",
      title: "Built for the daily work of a QA team",
      items: [
        { icon: "layout", title: "Dashboard", text: "KPI cards, quality trend charts, sentiment distribution and issue tracking." },
        { icon: "file", title: "Call reports", text: "Score bars, root cause tables, business insights, recommended actions and the full transcript." },
        { icon: "download", title: "PDF export", text: "Generate and download a report for a call." },
        { icon: "users", title: "Roles", text: "Admin, supervisor and agent roles with user management." },
        { icon: "phone", title: "VOIP webhooks", text: "Receivers for Asterisk, 3CX and FreePBX; agents are created from the extension number." },
        { icon: "bot", title: "AI copilot", text: "Ask questions about your call data in plain language." },
      ],
      link: "Explore the product",
    },
    deployment: {
      eyebrow: "Your data, your choice of where it lives",
      title: "Cloud, self-hosted or hybrid",
      items: [
        { title: "SaaS", text: "Managed by us in a qualified cloud, with tenant isolation enforced in the application and in the database." },
        { title: "Self-hosted", text: "A Docker Compose package on your Linux servers. No application container has a route to the internet." },
        { title: "Hybrid", text: "Recordings and transcripts stay local. Optional cloud reasoning runs only on masked text and only if you turn it on." },
      ],
      link: "Compare deployment options",
    },
    trust: {
      eyebrow: "Privacy is a product feature",
      title: "Designed so the recording never leaves your control",
      items: [
        { icon: "eyeOff", title: "Personal-data masking", text: "Phone numbers, e-mails, national IDs, card and IBAN numbers are replaced in the transcript. The limits are stated, not hidden." },
        { icon: "layers", title: "Tenant isolation", text: "Workspace checks in the application plus PostgreSQL row-level security under a non-bypass role." },
        { icon: "trash", title: "Deletion that purges", text: "A delete denies access at once, then a durable purge removes transcripts, analyses and media bytes." },
        { icon: "shield", title: "No hidden cloud fallback", text: "If a local model fails, the stage is reported as unavailable. Nothing is quietly sent elsewhere." },
      ],
      link: "Read the security page",
    },
  },

  product: {
    meta: {
      title: "Product: the Homayar analysis platform",
      description:
        "Ingest recordings by upload or VOIP webhook, transcribe with Whisper, run four analysis agents, and review results in a dashboard, call reports, PDF exports and an AI copilot.",
    },
    hero: {
      eyebrow: "Platform overview",
      title: "Everything between the recording and the decision",
      text: "Homayar is a post-call analysis engine. It takes a recording, produces a transcript, runs four agents over it and turns the result into reports and dashboards your team can act on.",
    },
    console: {
      eyebrow: "Assemble the console",
      title: "The console builds itself as you scroll",
      text: "Seven panels dock into one window, in the order a recording travels through the product.",
      hint: "Scroll: each panel docks into the window",
      waiting: "Waiting",
      docked: "Docked",
      complete: "Console complete",
      demoEyebrow: "Live demo",
      demoTitle: "Watch a sample call being analyzed",
      demoText: "Waveform, transcript, four agents and the report, with sample data.",
    },
    sections: {
      ingest: {
        id: "ingest",
        label: "Ingest",
        title: "Upload a file, or connect the PBX",
        text: "Recordings arrive in two ways. A person uploads a file from the console, or your VOIP system posts each finished call to a webhook. Uploads are checked for size, file signature and real audio duration before they are accepted.",
        bullets: [
          "Accepted formats: WAV, MP3, OGG, M4A, AAC, FLAC, MP4 and WEBM.",
          "Webhook receivers for Asterisk, 3CX and FreePBX with automatic field normalization.",
          "Agents are created on the first call from the extension number.",
          "Every webhook call is verified with a shared secret.",
        ],
      },
      transcription: {
        id: "transcription",
        label: "Transcription",
        title: "Speech to text in Persian and English",
        text: "Transcription runs on Whisper. You can set the spoken language for a recording or let it be detected. Speaker separation is a separate stage. When it is not available for a recording, the report says so instead of guessing.",
        bullets: [
          "Persian, English or automatic language detection.",
          "Separate stages for preparing the file, speech to text, speaker separation and analysis.",
          "Each stage shows its state: not started, waiting, running, done, partial or failed.",
          "Output from a test provider is labeled as such and never shown as a real analysis.",
        ],
      },
      agents: {
        id: "agents",
        label: "The 4 agents",
        title: "Four agents, one report",
        text: "Each agent reads the transcript and writes one part of the report. All prompts are bilingual, so Persian calls get the same depth of analysis as English ones.",
        items: [
          { title: "QC Agent", text: "Scores empathy, problem solving, clarity, tone, call closing and silence handling." },
          { title: "Problem Detection", text: "Identifies root causes, system failures, issue severity and repeated problems." },
          { title: "Business Insight", text: "Detects upsell signals, churn-risk signals, revenue opportunities and satisfaction trends." },
          { title: "QC Teacher", text: "Generates training packages from agent weaknesses with behavioral recommendations." },
        ],
      },
      dashboard: {
        id: "dashboard",
        label: "Dashboard",
        title: "The whole queue on one screen",
        text: "The dashboard opens with KPI cards for the period, followed by quality trend charts, sentiment distribution and issue tracking. An incidents view filters problems by severity, and the analytics view compares agents side by side.",
        bullets: [
          "KPI cards such as total calls, average QC score, open issues, calls today and average duration.",
          "QC trend charts and sentiment distribution.",
          "Incidents filtered by severity.",
          "Agent comparison charts.",
        ],
      },
      reports: {
        id: "reports",
        label: "Call reports",
        title: "A report you can defend",
        text: "Every call report shows score bars per criterion, a table of root causes, the business insights and the recommended actions, with the transcript on the same page. A PDF of the report can be generated and downloaded.",
        bullets: [
          "Score bars for overall QC and each criterion.",
          "Root cause tables with severity.",
          "Recommended actions and the training package.",
          "Transcript view and PDF export.",
        ],
      },
      copilot: {
        id: "copilot",
        label: "Copilot",
        title: "Ask your call data a question",
        text: "The copilot is a chat interface over your call data. It maps a question to a predefined query, runs it, and explains the result in plain language. A graph explorer lets you browse the relationships between agents, calls and issues.",
        bullets: [
          "Natural-language questions in Persian or English.",
          "Answers built from stored call data, not free-form guesses.",
          "Interactive graph explorer over agents, calls and issues.",
        ],
      },
      admin: {
        id: "admin",
        label: "Administration",
        title: "Fits into the systems you already run",
        bullets: [
          "Role-based access: admin, supervisor and agent.",
          "User management with create, edit and remove.",
          "LDAP and Active Directory sync for authentication.",
          "Persian and English interface with automatic RTL layout.",
        ],
      },
    },
  },

  howItWorks: {
    meta: {
      title: "How it works: the Homayar processing pipeline",
      description:
        "Step by step: how a recording is prepared, transcribed, split by speaker, analyzed by four agents and reviewed, and how the console reports partial or failed stages honestly.",
    },
    hero: {
      eyebrow: "Pipeline",
      title: "What happens to a recording, step by step",
      text: "Processing runs on the server. You can leave the page and come back; every stage reports its own state.",
    },
    steps: [
      {
        stage: "Preparing the recording",
        title: "The file is checked before anything else",
        text: "Size, file signature and real audio duration are verified. The original recording is kept as it is; a working copy is made for the models.",
      },
      {
        stage: "Speech to text",
        title: "Whisper turns speech into a transcript",
        text: "The language is the one you set for the recording, or it is detected. The transcript keeps timestamps so later stages can point back to the conversation.",
      },
      {
        stage: "Separating speakers",
        title: "Turns are assigned to speakers",
        text: "When speaker separation is not available for a recording, the stage is marked unavailable with the reason, and the rest of the pipeline continues on the text.",
      },
      {
        stage: "Summary and analysis",
        title: "Four agents write the report",
        text: "QC scoring, problem detection, business insight and the training package are produced from the transcript. Test-provider output is labeled and never presented as a real analysis.",
      },
      {
        stage: "Review",
        title: "Your team works in the console",
        text: "Dashboard, call report, transcript, PDF export and the copilot. Personal-data masking and access rules apply to every view.",
      },
    ],
    rail: { todo: "Not started", done: "Done", running: "Running" },
    snake: {
      hint: "Scroll: the line travels with you",
      checks: ["Size", "Signature", "Duration"],
      agents: ["QC", "Problems", "Insight", "Teacher"],
      tiles: ["Dashboard", "Call report", "Copilot"],
      providers: [
        { name: "Local Whisper", allowed: true },
        { name: "Local analysis model", allowed: true },
        { name: "Cloud provider outside the policy", allowed: false },
      ],
      allowed: "Allowed",
      refused: "Refused",
      passing: "The line is here",
    },
    states: {
      eyebrow: "Truthful states",
      title: "A missing stage is shown, not filled in",
      text: "Each stage of a job carries an explicit state. If a stage stops, the report shows why, in the language of the console.",
      items: [
        { label: "Not started", text: "The stage has not been reached yet." },
        { label: "Waiting", text: "The recording is in the processing queue." },
        { label: "Running", text: "The stage is being processed right now." },
        { label: "Partial", text: "Processing finished with gaps; the stages show what is missing." },
        { label: "Unavailable", text: "The stage could not run, for example when speaker separation is not available for this recording." },
        { label: "Failed", text: "The last attempt failed with a named reason. Automatic retries are bounded and visible." },
      ],
    },
    policy: {
      eyebrow: "Where the AI runs",
      title: "Every stage checks the workspace policy first",
      text: "Each workspace has an AI policy: which providers, regions and models may be used for speech to text, speaker separation and analysis. The policy is checked before a stage runs. A provider the policy does not allow is never called and never used as a fallback.",
      link: "Read how routing and egress work",
    },
  },

  solutions: {
    meta: {
      title: "Solutions by role: QA, supervisors, operations, IT",
      description:
        "How QA managers, supervisors, contact-center operations leaders and IT or security teams use Homayar's scored reports, problem detection, dashboards and private deployment.",
    },
    hero: {
      eyebrow: "By role",
      title: "The same call, seen from four seats",
      text: "Homayar serves the people who score calls, the people who coach agents, the people who own the numbers and the people who approve where data goes.",
    },
    ring: {
      eyebrow: "Four seats, one call",
      hint: "Scroll: the ring turns and the report re-filters",
      seat: "Seat",
      sees: "What this role sees",
      open: "Open the page",
      views: {
        "qa-managers": "Six criteria scored, transcript one click away",
        supervisors: "Incidents by severity and open issues",
        "contact-center-operations": "Queue KPIs, quality trend and sentiment",
        "it-security": "Stages, roles and where the data lives",
      },
    },
    roles: {
      "qa-managers": {
        slug: "qa-managers",
        icon: "gauge",
        name: "QA managers",
        short: "Score every analyzed call on the same criteria and open the transcript behind any number.",
        meta: {
          title: "Homayar for QA managers",
          description: "Consistent quality scores on six criteria, root causes, coaching packages and the transcript behind every score. What exists today and what is coming.",
        },
        hero: {
          title: "Consistent scores, with the conversation one click away",
          text: "Manual sampling reviews a few calls a week. Homayar scores every analyzed call on the same six criteria and keeps the transcript next to the score, so a disputed result can be checked instead of argued.",
        },
        needs: {
          title: "What a QA manager needs",
          items: [
            "The same criteria applied to every call.",
            "Evidence for each score that an agent can read too.",
            "A way to turn weak points into coaching, not just a number.",
          ],
        },
        gets: {
          title: "What Homayar does today",
          items: [
            "Six QC criteria per call: empathy, problem solving, clarity, tone, call closing and silence handling, plus an overall score.",
            "The transcript on the same page as the report.",
            "A training package per agent generated from the weak points.",
            "PDF export of a call report to share with an agent or a manager.",
          ],
        },
        day: {
          title: "A day with Homayar",
          hint: "Scroll: travel through the day",
          items: [
            { time: "08:30", task: "Open the dashboard", text: "Yesterday's calls are scored on the same six criteria. The average QC score and open issues are on the first screen.", screen: "kpis" },
            { time: "10:00", task: "Check a disputed score", text: "The call report shows the score bars with the transcript on the same page, so the score can be checked against the words.", screen: "report" },
            { time: "12:30", task: "Share a report", text: "A PDF of the call report goes to the agent and their manager.", screen: "pdf" },
            { time: "15:00", task: "Turn weak points into coaching", text: "The training package lists behavioral recommendations built from the agent's weak points.", screen: "training" },
            { time: "17:30", task: "Look at the trend", text: "The QC trend chart shows whether the week moved in the right direction.", screen: "trend" },
          ],
        },
        roadmap: {
          title: "Coming soon",
          items: ["Custom scorecards per workspace.", "Supervisor review, calibration and appeals workflow."],
        },
      },
      supervisors: {
        slug: "supervisors",
        icon: "users",
        name: "Supervisors",
        short: "Find the few calls that need attention today and give each agent specific coaching.",
        meta: {
          title: "Homayar for supervisors",
          description: "Incidents by severity, open issues, agent comparison and coaching packages, so supervisors spend their time on the calls that matter.",
        },
        hero: {
          title: "Spend your review time where it matters",
          text: "A supervisor cannot listen to everything. Homayar surfaces incidents by severity, tracks open issues and compares agents, so the next coaching conversation is specific.",
        },
        needs: {
          title: "What a supervisor needs",
          items: [
            "A short list of calls worth listening to.",
            "Coaching points per agent, tied to real calls.",
            "Progress over time, not a one-off audit.",
          ],
        },
        gets: {
          title: "What Homayar does today",
          items: [
            "An incidents view filtered by severity.",
            "Open issues and repeated problems detected across calls.",
            "Agent comparison charts in the analytics view.",
            "A training package per agent with behavioral recommendations.",
          ],
        },
        day: {
          title: "A day with Homayar",
          hint: "Scroll: travel through the day",
          items: [
            { time: "08:30", task: "Start with the incidents", text: "The incidents view is filtered by severity, so the few calls worth listening to are at the top.", screen: "incidents" },
            { time: "10:00", task: "Read the root causes", text: "Each detected problem has a root cause and a severity, tied to the call it came from.", screen: "issues" },
            { time: "12:30", task: "Compare the team", text: "Agent comparison charts in the analytics view show who needs time this week.", screen: "compare" },
            { time: "15:00", task: "Coach with a package", text: "The training package for one agent gives specific behavioral recommendations for the next conversation.", screen: "training" },
            { time: "17:30", task: "Ask the copilot", text: "One question in plain language returns the issues that came up most this week.", screen: "copilot" },
          ],
        },
        roadmap: {
          title: "Coming soon",
          items: ["Coaching assignments with follow-up.", "Alerts for high-severity findings."],
        },
      },
      "contact-center-operations": {
        slug: "contact-center-operations",
        icon: "trend",
        name: "Contact-center operations",
        short: "See recurring service failures and satisfaction trends across the whole queue.",
        meta: {
          title: "Homayar for contact-center operations",
          description: "Quality trends, sentiment distribution, issue tracking and business signals across the queue, with a copilot that answers questions in plain language.",
        },
        hero: {
          title: "Recurring problems, visible before the monthly report",
          text: "Operations leaders need the pattern, not the single call. Homayar aggregates quality scores, sentiment and detected issues across calls and lets you ask the data a question.",
        },
        needs: {
          title: "What operations needs",
          items: [
            "Which problems keep coming back, and how often.",
            "Whether quality and satisfaction are moving in the right direction.",
            "Answers without waiting for an analyst.",
          ],
        },
        gets: {
          title: "What Homayar does today",
          items: [
            "Dashboard KPI cards with QC trend charts and sentiment distribution.",
            "Issue tracking with root causes and severity.",
            "Business insight signals: upsell, churn-risk and satisfaction trend per call.",
            "A copilot that answers natural-language questions from stored call data.",
          ],
        },
        day: {
          title: "A day with Homayar",
          hint: "Scroll: travel through the day",
          items: [
            { time: "08:30", task: "Read the KPI cards", text: "Total calls, average QC score, open issues and calls today, for the period you choose.", screen: "kpis" },
            { time: "10:00", task: "Check the sentiment", text: "The sentiment distribution shows how the queue felt, not one call.", screen: "sentiment" },
            { time: "12:30", task: "Track recurring issues", text: "Issue tracking groups detected problems with root causes and severity.", screen: "issues" },
            { time: "15:00", task: "Read the business signals", text: "Upsell, churn-risk and satisfaction-trend signals per call.", screen: "signals" },
            { time: "17:30", task: "Ask the data", text: "The copilot answers from stored call data and explains the result in plain language.", screen: "copilot" },
          ],
        },
        roadmap: {
          title: "Coming soon",
          items: ["Coverage-adjusted trend statistics.", "Executive briefs built from reviewed findings."],
        },
      },
      "it-security": {
        slug: "it-security",
        icon: "shield",
        name: "IT and security",
        short: "Deploy on your servers, connect the PBX and the directory, and keep every recording under your control.",
        meta: {
          title: "Homayar for IT and security teams",
          description: "Self-hosted Docker Compose package, VOIP webhooks, LDAP and Active Directory sync, role-based access, tenant isolation and no outbound route from application containers.",
        },
        hero: {
          title: "A bounded stack you can install, back up and audit",
          text: "IT approves the integration; security approves the data flow. Homayar is built to answer both without a slide of promises.",
        },
        needs: {
          title: "What IT and security need",
          items: [
            "A deployment that does not require the internet.",
            "Integration with the PBX and the directory you already run.",
            "Proof of isolation, deletion and access control.",
          ],
        },
        gets: {
          title: "What Homayar does today",
          items: [
            "Self-hosted Docker Compose package with PostgreSQL, a Redis-compatible broker, S3-compatible object storage, API, workers and the console behind one nginx origin.",
            "No application container has a route out; optional outbound traffic goes through a deny-by-default proxy with an exact allowlist.",
            "VOIP webhooks with a shared secret; LDAP and Active Directory sync.",
            "JWT authentication, bcrypt password hashing, role-based access, rate limiting and PostgreSQL row-level security.",
          ],
        },
        day: {
          title: "A day with Homayar",
          hint: "Scroll: travel through the day",
          items: [
            { time: "08:30", task: "Connect the PBX", text: "The VOIP webhook receives each finished call. Every call is verified with a shared secret.", screen: "webhook" },
            { time: "10:00", task: "Sync the directory", text: "Users authenticate through LDAP and Active Directory sync. Roles are admin, supervisor and agent.", screen: "ldap" },
            { time: "12:30", task: "Confirm there is no route out", text: "No application container has a route to the internet. Optional outbound traffic goes only through a deny-by-default proxy.", screen: "egress" },
            { time: "15:00", task: "Check the isolation", text: "Row-level security is forced. A connection with no tenant context sees no tenant rows.", screen: "roles" },
            { time: "17:30", task: "Run the runbook", text: "Health and readiness endpoints, the migration job, drain and rollback steps are documented.", screen: "runbook" },
          ],
        },
        roadmap: {
          title: "Coming soon",
          items: ["OIDC single sign-on through an identity broker.", "Kubernetes manifests for customers who already run Kubernetes."],
        },
      },
    },
    cta: "Talk to us about your role",
  },

  security: {
    meta: {
      title: "Security and privacy at Homayar",
      description:
        "Personal-data masking with stated limits, tenant isolation with PostgreSQL row-level security, explicit retention and durable deletion, and self-hosted deployment with no outbound route. No certifications are claimed.",
    },
    hero: {
      eyebrow: "Security and privacy",
      title: "What we built, and what we do not claim",
      text: "Call recordings are among the most sensitive data a company holds. This page describes the controls that exist in the product today, as they are described in our architecture decisions, and lists what we do not claim.",
    },
    layers: {
      eyebrow: "Through the layers",
      title: "Four layers between a recording and anyone who should not see it",
      hint: "Scroll: pass through each layer",
      items: [
        { icon: "server", title: "Network wall", text: "In the self-hosted package no application container has a route to the internet." },
        { icon: "layers", title: "Tenant isolation", text: "The application's authorization matrix and PostgreSQL row-level security, enforced twice." },
        { icon: "eyeOff", title: "Personal-data masking", text: "Phone numbers, e-mails, IDs and card numbers become stable placeholders in the transcript." },
        { icon: "trash", title: "Retention and deletion", text: "Explicit retention modes and a tombstone-first deletion that removes the bytes." },
      ],
    },
    maskDemo: {
      eyebrow: "Masking, live",
      title: "Watch a phone number disappear from the transcript",
      original: "Original transcript",
      masked: "Masked version",
      who: "Customer",
      before: "You can reach me at 0912 345 6789 after five.",
      after: "You can reach me at [PHONE_1] after five.",
      badge: "1 phone number masked",
      hint: "Scroll: the number dissolves",
    },
    sections: {
      redaction: {
        id: "redaction",
        title: "Personal-data masking in transcripts",
        text: "A workspace can turn on masking. Personal data in the transcript is replaced by stable placeholders such as [PHONE_1], and analysis, search and exports then use the masked version.",
        detected: {
          title: "What is detected",
          items: [
            "E-mail addresses.",
            "Phone numbers: Iranian mobile and landline in every common spelling, and international numbers with a + or 00 prefix.",
            "Iranian national IDs, checked by their check digit.",
            "Bank card numbers, checked with the Luhn rule.",
            "IBANs, checked with the mod-97 rule.",
            "Iranian postal codes, only near a postal-code keyword.",
          ],
        },
        limits: {
          title: "What is not detected, stated plainly",
          items: [
            "Names, addresses, dates of birth and license plates.",
            "Numbers written in words, or split across transcript segments.",
            "The audio recording itself is not masked. Masking applies to text.",
          ],
        },
        controls: {
          title: "How originals are protected",
          items: [
            "Masked versions are immutable. A change in policy creates a new version instead of rewriting one.",
            "Viewing an original transcript needs a separate owner-only permission and is written to the audit log.",
            "Each replaced value is stored only as AES-256-GCM ciphertext, with the keys kept outside the database. Without a key nothing original is kept.",
            "Revealing a single placeholder needs a reason and is audited without the value.",
          ],
        },
      },
      isolation: {
        id: "isolation",
        title: "Tenant isolation",
        text: "Isolation is enforced twice: by the application's authorization matrix and by PostgreSQL row-level security.",
        items: [
          "Every query runs under a database role that cannot bypass row-level security. Production start-up refuses a superuser or bypass role.",
          "Policies are forced, so even the table owner is subject to them. A connection with no tenant context sees no tenant rows.",
          "Tenant context is bound per transaction from the validated session, job or share token, so it cannot survive connection-pool reuse.",
          "The backend test suite runs as the restricted role and includes cross-tenant read, write and re-homing tests.",
        ],
      },
      retention: {
        id: "retention",
        title: "Retention and deletion",
        text: "Retention has three explicit modes, and deletion is designed to actually remove data, not only hide it.",
        items: [
          "Retention modes: finite (1 to 3650 days after ingestion), indefinite (only an explicit deletion removes data) or immediate (eligible as soon as processing ends). Ambiguous values are refused, never guessed.",
          "A legal hold overrides retention. Retention never removes a held conversation.",
          "Deletion is tombstone-first: access is denied at once, share links are revoked, and a durable purge then removes transcripts, analyses, evidence, summaries and media bytes in retried attempts.",
          "What remains is a non-content receipt: ids, counters, timestamps and an error code. Titles and file names are emptied.",
          "Tombstones are journaled outside the database so a restored backup re-applies deletions.",
          "A presigned download URL issued before a deletion stays valid until its own short expiry or until the bytes are removed. We say this instead of promising instant revocation.",
        ],
      },
      egress: {
        id: "egress",
        title: "Self-hosted: no route to the internet",
        text: "In the on-premise package, denial is enforced at the application layer and at the network layer.",
        items: [
          "No application container has a route out. Application networks are internal; only nginx publishes a port.",
          "Optional outbound HTTP(S) goes only through a deny-by-default proxy that forwards to the exact host and port lines of an allowlist you control. The air-gapped default runs no proxy at all.",
          "At the application layer, a networked AI adapter can only be called under a permit granted for that exact policy-approved endpoint. Without it the request is refused before any connection.",
          "Cloud-metadata addresses, link-local, loopback and private addresses are refused for cloud endpoints. Private endpoints on your LAN are approved explicitly.",
        ],
      },
      routing: {
        id: "routing",
        title: "AI providers and routing",
        text: "Which model sees your data is a policy decision recorded with every run.",
        items: [
          "Each workspace has an AI policy: allowed endpoint kinds, regions, models and licenses per stage. Candidates are filtered by the policy before anything else, and a refused candidate is never called and never a fallback.",
          "If speech to text is refused, the attempt fails with the reason and nothing is sent. If analysis is refused, the stage is reported unavailable and the transcript is kept.",
          "A cloud analysis endpoint receives the transcript masked with every supported class by default, even when the workspace shows originals.",
          "Permission for text never covers audio. Cloud speech recognition needs its own stage policy.",
          "A failed local model is retried on the same endpoint. Cloud credentials present on the server are never used for a local-only workspace.",
        ],
      },
      access: {
        id: "access",
        title: "Access and authentication",
        items: [
          "JWT authentication on all API routes; passwords hashed with bcrypt.",
          "Role-based access with admin, supervisor and agent roles.",
          "Rate limiting (60 requests per minute by default) and webhook secret verification.",
          "LDAP and Active Directory sync for user authentication.",
          "A global exception handler so stack traces are never returned to a client.",
        ],
      },
      notClaimed: {
        id: "not-claimed",
        title: "What we do not claim",
        text: "We publish tested behavior only.",
        items: [
          "No ISO 27001 or SOC 2 certification or attestation is claimed.",
          "No penetration-test report is published.",
          "No uptime SLA is published.",
          "Masking is not anonymization. Sensitive facts can remain in the text of a conversation.",
        ],
      },
    },
  },

  deployment: {
    meta: {
      title: "Deployment: SaaS, self-hosted or hybrid",
      description:
        "Compare the three ways to run Homayar: managed SaaS in a qualified cloud, a self-hosted Docker Compose package with no outbound route, or a hybrid with a local data plane.",
    },
    hero: {
      eyebrow: "Deployment",
      title: "One product, three places to run it",
      text: "Private operation is a supported profile of the same product, not a fork. The same security and evidence rules apply everywhere.",
    },
    flow: {
      source: "PBX or upload",
      app: "Homayar",
      models: "Local models",
      cloud: "Cloud reasoning on masked text",
      internet: "Internet",
      blocked: "No route",
      allowlist: "Allowlist proxy only",
      optIn: "Opt-in per tenant",
      region: "Qualified cloud region",
    },
    zones: {
      eyebrow: "Three places to run",
      title: "Follow the block",
      text: "The same product moves between three zones. Watch what changes around it.",
      hint: "Scroll: the block travels",
      block: "Homayar",
      items: [
        { name: "Cloud (SaaS)", note: "Qualified cloud region" },
        { name: "Your server", note: "A wall closes behind it: no route out" },
        { name: "Hybrid", note: "It splits: local data plane, optional cloud pieces" },
      ],
      wall: "No route",
      halves: ["Local data plane", "Optional cloud pieces"],
      compareHint: "Scroll: the table builds itself",
    },
    options: [
      {
        id: "saas",
        name: "SaaS",
        summary: "We run it for you in a qualified cloud region.",
        points: [
          "Shared PostgreSQL with mandatory workspace scope, application authorization and row-level security.",
          "A dedicated database, storage and worker deployment for contracts that need stronger operational isolation.",
          "Each tenant policy sets allowed processing regions, providers, models and retention by data type.",
          "Per-tenant rate limits and fair scheduling so one large tenant cannot starve another.",
        ],
        fit: "Teams that want to start fast and accept a qualified cloud region.",
      },
      {
        id: "self-hosted",
        name: "Self-hosted",
        summary: "A Docker Compose package on your own Linux x86-64 servers.",
        points: [
          "API and console, PostgreSQL, a Redis-compatible broker, S3-compatible object storage, workers, and nginx as the single published origin.",
          "GPU workers for speech to text and speaker separation, and local model workers, run as part of the same package. Neo4j is optional.",
          "No application container has a route to the internet. Outbound traffic, if any, passes through a deny-by-default proxy with your allowlist.",
          "Authentication against your directory through LDAP and Active Directory sync. Keys live in your own secrets storage.",
          "Health and readiness endpoints, migration job, drain and rollback steps documented in the runbook.",
        ],
        fit: "Organizations whose recordings must never leave their network.",
      },
      {
        id: "hybrid",
        name: "Hybrid",
        summary: "A local data plane first, with optional cloud pieces you switch on.",
        points: [
          "Audio, transcripts, identity, search and QC evidence stay local. The installation keeps working offline.",
          "An optional management plane receives only approved health, version and license counters, and you can inspect the outgoing payload.",
          "A second mode allows local speech to text and masking followed by cloud reasoning on the masked text, opt-in per tenant and data class.",
          "A failed local model never silently falls back to a cloud API.",
        ],
        fit: "Organizations that want private data with selected cloud capacity.",
      },
    ],
    compare: {
      title: "Side by side",
      columns: ["", "SaaS", "Self-hosted", "Hybrid"],
      rows: [
        ["Where recordings live", "Qualified cloud region", "Your servers", "Your servers"],
        ["Who operates it", "Homayar", "Your IT team, with our package and runbook", "Your IT team, optional cloud management"],
        ["AI providers", "Per-tenant policy", "Local models by default; private endpoints approved by you", "Local first; cloud only on masked text, opt-in"],
        ["Identity", "Console accounts", "LDAP and Active Directory sync", "LDAP and Active Directory sync"],
        ["Network", "Platform firewall and egress proxy", "No outbound route; allowlist proxy optional", "Signed outbound polling preferred over inbound ports"],
      ],
    },
    runtimeNote: {
      title: "What the on-premise package does not do at runtime",
      items: ["No model downloads.", "No telemetry beacons.", "No font or script CDN.", "No mandatory e-mail or SMS provider."],
    },
  },

  about: {
    meta: {
      title: "About Homayar",
      description:
        "Homayar turns recorded customer conversations into reviewable quality assessments and recurring operational issues, linked to the original evidence, deployable in your private environment or a qualified cloud.",
    },
    hero: {
      eyebrow: "About",
      title: "Quality review for Persian customer calls, with the evidence attached",
      text: "Homayar is a Persian-first contact-center quality product. Our promise is simple: turn recorded customer conversations into reviewable quality assessments and recurring operational issues, linked to the original conversation, in the environment you choose.",
    },
    principles: {
      title: "How we build",
      items: [
        { icon: "file", title: "Evidence, not vibes", text: "Every score and finding sits next to the transcript it came from. A number without a conversation behind it is not a finding." },
        { icon: "check", title: "Truthful states", text: "If a stage cannot run, the console says so. Test-provider output is labeled. We do not fill gaps with defaults." },
        { icon: "shield", title: "Privacy by design", text: "Masking, tenant isolation, explicit retention and durable deletion are part of the core product, not a premium tier." },
        { icon: "globe", title: "Persian first, bilingual always", text: "Prompts, interface and reports work in Persian and English. RTL is not an afterthought." },
        { icon: "server", title: "Your environment", text: "Cloud, self-hosted or hybrid. Private operation is a supported profile of the same product." },
        { icon: "scale", title: "Claims we can show", text: "We do not publish customer names, percentages or certifications we cannot back with evidence." },
      ],
    },
    who: {
      title: "Who it is for",
      text: "Contact centers and service operations that record calls in Persian or English and want their QA team, supervisors and operations leaders to work from the same reviewed data. The first buyer is usually the contact-center or CX leader; the daily users are QA managers and supervisors; IT and security approve the integration.",
    },
    howWeWork: {
      title: "How we work with new customers",
      steps: [
        { title: "Discovery", text: "Your queue, recording access, current QA process and where data must stay." },
        { title: "Sample and baseline", text: "An authorized sample of calls and your current review time, so results can be measured against something real." },
        { title: "Measured pilot", text: "A scoped pilot with agreed success criteria. Failures are reported alongside successes." },
        { title: "Rollout", text: "Training, acceptance and a support handoff." },
      ],
    },
    constellation: { eyebrow: "Six principles", hint: "Scroll: the links are drawn" },
    stepper: { hint: "Scroll: step by step", step: "Step", of: "of" },
    status: {
      title: "Where we are",
      text: "Homayar is an early product working with design partners. We do not show customer logos or results until a customer has given permission and the numbers have a method behind them.",
    },
  },

  contact: {
    meta: {
      title: "Contact and demo request",
      description: "Request a demo of Homayar. Tell us about your queue, your recording setup and where the data must stay, and we will scope a pilot with you.",
    },
    hero: {
      eyebrow: "Contact",
      title: "Request a demo",
      text: "Tell us a little about your operation. We reply by e-mail and propose a short call.",
    },
    form: {
      name: "Full name",
      email: "Work e-mail",
      company: "Company",
      role: "Your role",
      roleOptions: [
        { value: "", label: "Choose one" },
        { value: "qa", label: "QA manager" },
        { value: "supervisor", label: "Supervisor or team lead" },
        { value: "operations", label: "Contact-center or CX operations" },
        { value: "it", label: "IT or security" },
        { value: "other", label: "Other" },
      ],
      deployment: "Where should the data stay?",
      deploymentOptions: [
        { value: "", label: "Choose one" },
        { value: "saas", label: "SaaS is fine" },
        { value: "self-hosted", label: "Self-hosted on our servers" },
        { value: "hybrid", label: "Hybrid" },
        { value: "unsure", label: "Not sure yet" },
      ],
      volume: "Monthly call volume",
      volumeOptions: [
        { value: "", label: "Choose one" },
        { value: "lt5k", label: "Under 5,000 calls" },
        { value: "5k-20k", label: "5,000 to 20,000 calls" },
        { value: "20k-200k", label: "20,000 to 200,000 calls" },
        { value: "gt200k", label: "Over 200,000 calls" },
      ],
      message: "Anything else we should know?",
      messageHint: "Optional. For example your PBX, languages spoken on your calls, or a deadline.",
      required: "Required",
      optional: "Optional",
      submit: "Send request",
      sending: "Sending",
      errorsTitle: "Please fix the following",
      errors: {
        name: "Enter your full name.",
        email: "Enter a valid work e-mail address.",
        company: "Enter your company name.",
        deployment: "Choose where the data should stay.",
      },
      successTitle: "Request received",
      successText: "Thank you. We will reply to the e-mail address you gave. Nothing else is stored on this website.",
      failedTitle: "The request could not be sent",
      failedText: "Please try again in a moment.",
      retry: "Try again",
      privacyNote: "This form sends only what you type here. It sets no cookies and stores nothing in your browser.",
    },
    aside: {
      title: "What happens next",
      steps: [
        "We reply by e-mail, usually within a few business days.",
        "A short call to understand your queue, recording access and data constraints.",
        "A walkthrough of the console with a sample call, including how partial results look.",
        "If it fits, a scoped pilot proposal with agreed success criteria.",
      ],
      linksTitle: "Useful before the call",
    },
  },

  pricing: {
    meta: {
      title: "Pricing",
      description: "Homayar pricing is being prepared. This page will list plans and amounts once the commercial model is final. Nothing on it is an offer.",
    },
    hero: {
      eyebrow: "Pricing",
      title: "Pricing is being prepared",
      text: "We are finalizing the commercial model and payment options. Until then this page shows the plan structure only, without amounts. Nothing on this page is an offer.",
    },
    notice: "Amounts, billing units and terms will be published here once they are final. If you need numbers for a budget today, contact us and we will send a written proposal.",
    deployments: [
      { name: "SaaS", text: "We run it." },
      { name: "Self-hosted", text: "You run it." },
    ],
    plans: [
      { name: "Monthly", text: "Pay month by month.", amount: "To be announced" },
      { name: "Yearly", text: "Commit for a year.", amount: "To be announced" },
      { name: "Enterprise", text: "Contract terms, private deployment and support.", amount: "On request" },
    ],
    cta: "Ask for a written proposal",
  },

  legal: {
    privacy: {
      meta: { title: "Privacy policy", description: "The Homayar privacy policy is being prepared with legal review and is not yet published." },
      title: "Privacy policy",
      notice: "This document is being prepared with legal review and is not yet in force. It will describe what data this website and the Homayar product process, on what basis, for how long, and who to contact. Until it is published, please contact us for the current data handling terms.",
    },
    terms: {
      meta: { title: "Terms of service", description: "The Homayar terms of service are being prepared with legal review and are not yet published." },
      title: "Terms of service",
      notice: "This document is being prepared with legal review and is not yet in force. No terms on this page are binding. Contact us with questions.",
    },
    gdpr: {
      meta: { title: "GDPR statement", description: "The Homayar GDPR statement is being prepared and is not yet published." },
      title: "GDPR statement",
      notice: "Our statement on the EU General Data Protection Regulation is being prepared. It will describe roles, lawful bases, data subject requests and international transfers once reviewed. Nothing on this page is a compliance claim.",
    },
  },

  transparency: {
    meta: { title: "Transparency report", description: "The Homayar transparency report is being prepared and is not yet published." },
    title: "Transparency report",
    notice: "This report is being prepared. It will describe how processing works, which subprocessors are involved in each deployment option, and how requests about data are handled. It is not yet published and contains no commitments.",
    backLink: "Read the security page in the meantime",
  },

  demo: {
    label: "Live demo",
    sample: "Sample data",
    sampleNote: "A made-up call with invented names and numbers. Nothing here is real customer data.",
    play: "Play the demo",
    pause: "Pause the demo",
    replay: "Replay the demo",
    reducedNote: "Motion is reduced on this device, so the finished report is shown.",
    transcriptLang: "Transcript language",
    call: "Ext. 104 · Inbound · 06:12",
    recording: "call-104-inbound.wav",
    states: { waiting: "Waiting", running: "Running", done: "Done" },
    speakers: { customer: "Customer", agent: "Agent" },
    transcript: {
      fa: [
        { who: "customer", text: "سفارشم بعد از نه روز هنوز «در حال ارسال» است." },
        { who: "agent", text: "بابت این موضوع متأسفم. همین حالا وضعیت مرسوله را بررسی می‌کنم." },
        { who: "customer", text: "هفتهٔ پیش هم تماس گرفتم و کسی پیگیری نکرد." },
        { who: "agent", text: "یادداشت را می‌بینم. وضعیت ارسال از طرف ما به‌روز نشده بود. امروز پیگیری می‌کنم و پیش از ساعت شش با شما تماس می‌گیرم." },
        { who: "customer", text: "باشد. لطفاً این بار حتماً." },
      ],
      en: [
        { who: "customer", text: "My order still shows as in transit after nine days." },
        { who: "agent", text: "I am sorry about that. Let me check the shipment now." },
        { who: "customer", text: "I called last week too and nobody followed up." },
        { who: "agent", text: "I see the note. The delivery status was not updated on our side. I will escalate it today and call you back before six." },
        { who: "customer", text: "Okay. Please make sure this time." },
      ],
    },
    report: "Call Report",
    agents: {
      qc: { name: "Quality Control", agent: "QC Agent", note: "Six criteria and an overall score." },
      problems: {
        name: "Problem Detection",
        agent: "Problem Detection",
        severity: "Severity",
        items: [
          { label: "Delivery status not updated", severity: "High" },
          { label: "Agent could not see the order history", severity: "Medium" },
        ],
      },
      insights: {
        name: "Business Insight",
        agent: "Business Insight",
        items: [
          { label: "Churn Risk", value: "Medium" },
          { label: "Upsell", value: "No" },
          { label: "Satisfaction trend", value: "Falling" },
        ],
      },
      teacher: {
        name: "Training Package",
        agent: "QC Teacher",
        items: [
          "Acknowledge a repeat contact before checking the system.",
          "Give a concrete follow-up time instead of 'soon'.",
          "Close the call by confirming what happens next.",
        ],
      },
    },
    ready: "Report ready",
    exportPdf: "Export PDF",
  },

  story: {
    eyebrow: "The four agents",
    title: "One transcript. Four readings.",
    text: "Each agent reads the same transcript and writes one part of the report. Scroll to watch them work.",
    surfaces: {
      eyebrow: "Real surfaces",
      title: "The console your team opens every morning",
      text: "Dashboard, incidents, analytics, copilot and graph. These are the screens that exist today, drawn from the console itself.",
    },
    flow: {
      eyebrow: "Where the data goes",
      title: "The recording stays where you decide",
      text: "Three ways to run the same product. In the self-hosted package no application container has a route to the internet.",
    },
    trust: {
      eyebrow: "Built in, not bolted on",
      title: "Privacy is a product feature",
    },
  },

  palette: {
    eyebrow: "Next step",
    title: "Ask for a real call report",
    placeholder: "Ask Homayar…",
    hint: "Pick an action",
    actions: [
      { label: "Request a demo", meta: "We reply by e-mail, then a short call", path: "/contact", kbd: "D" },
      { label: "See the pipeline", meta: "Step by step, with truthful states", path: "/how-it-works", kbd: "P" },
      { label: "Read the security page", meta: "Masking, isolation, deletion, egress", path: "/security", kbd: "S" },
      { label: "Compare deployments", meta: "SaaS, self-hosted, hybrid", path: "/deployment", kbd: "C" },
    ],
  },

  theme: { toDark: "Switch to dark mode", toLight: "Switch to light mode" },

  loader: { loading: "Loading" },

  scene: {
    flight: { hint: "Scroll to fly in" },
    warp: {
      eyebrow: "Space to space",
      title: "The call ends. The analysis space opens.",
      text: "Keep scrolling. You are entering the network that reads the conversation.",
    },
    brain: {
      eyebrow: "Inside the analysis",
      title: "The core reads every line as it is said",
      text: "A sample call. Each line lights up the part of the network that reads it. At the end the report comes to you.",
      conversation: "Sample conversation",
      analyzing: "Analyzing the conversation",
      ready: "Report ready",
      sample: "Sample data, invented names and numbers",
    },
    journey: { hint: "Scroll to travel", step: "Agent" },
    depth: { hint: "Scroll: each surface comes to you" },
  },

  mock: {
    nav: ["Overview", "Calls", "Incidents", "Analytics", "Copilot", "Graph", "Settings"],
    workspace: "Support queue",
    kpis: [
      { label: "Total Calls", value: "1,284" },
      { label: "Avg QC Score", value: "78", unit: "/ 100" },
      { label: "Open Issues", value: "37" },
      { label: "Calls Today", value: "96" },
    ],
    trend: "QC trend",
    sentiment: "Sentiment distribution",
    sentimentLabels: ["Positive", "Neutral", "Negative"],
    report: "Call Report",
    qc: "Quality Control",
    problems: "Problem Detection",
    insights: "Business Insight",
    transcript: "Transcript",
    actions: "Recommended Actions",
    exportPdf: "Export PDF",
    scores: [
      { label: "Overall QC", value: 82 },
      { label: "Empathy", value: 88 },
      { label: "Problem Solving", value: 74 },
      { label: "Clarity", value: 91 },
      { label: "Call Closing", value: 79 },
      { label: "Silence Handling", value: 70 },
      { label: "Tone", value: 86 },
    ],
    rootCauses: [
      { label: "Delivery status not updated", severity: "High" },
      { label: "Agent could not see the order history", severity: "Medium" },
    ],
    severity: "Severity",
    transcriptLines: [
      { who: "Customer", text: "My order still shows as in transit after nine days." },
      { who: "Agent", text: "I am sorry about that. Let me check the shipment now." },
      { who: "Customer", text: "I called last week too and nobody followed up." },
    ],
    stages: ["Preparing the recording", "Speech to text", "Separating speakers", "Summary and analysis"],
    stageStates: ["Done", "Done", "Unavailable", "Running"],
    copilotQuestion: "Which issues came up most this week?",
    copilotAnswer: "Delivery status delays appeared in 21 calls, followed by refund timing in 14.",
    upload: "Upload a recording",
    uploadFormats: "WAV, MP3, OGG, M4A, AAC, FLAC, MP4, WEBM",
    phoneTitle: "Calls",
    phoneItems: [
      { title: "Ext. 104 · Inbound", meta: "06:12 · QC 82" },
      { title: "Ext. 118 · Inbound", meta: "03:48 · QC 71" },
      { title: "Ext. 104 · Outbound", meta: "08:05 · QC 88" },
    ],
    roles: ["Admin", "Supervisor", "Agent"],
    compare: ["Ext. 104", "Ext. 118", "Ext. 121"],
    runbook: ["Health", "Readiness", "Migration job", "Drain", "Rollback"],
    rls: "Row-level security: forced",
    webhookTitle: "VOIP webhook",
    phoneTabs: ["Overview", "Calls", "Incidents", "Copilot"],
    agent: "Agent",
    duration: "Duration",
    language: "Language",
    languageValue: "Persian",
  },
};

export type Dictionary = typeof en;
export default en;
