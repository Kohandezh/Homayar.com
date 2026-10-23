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
    decorativeMockup: "Simulated Homayar console with sample data",
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
    note: "Homayar analyzes call recordings you are authorized to process. This website loads no third-party scripts, except the Cloudflare Turnstile check on the contact page, and sets no tracking cookies.",
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
      title: "Homayar: post-call review for contact centers",
      description:
        "Homayar transcribes authorized call recordings and gives your team a summary, action items and insights linked to the transcript. QC scoring and coaching are planned. Persian and English, self-hosted, with managed cloud planned.",
    },
    particles: {
      brand: "Homayar",
      slogan: "Every call, an insight.",
    },
    hero: {
      title: "Turn call recordings into reviews you can check against the transcript.",
      headlines: [
        "Turning call recordings",
        "into review reports",
        "with summaries and actions",
        "linked to the transcript",
        "with artificial intelligence.",
        "In Persian and English.",
      ],
      hint: "Scroll: a call becomes a report",
    },
    outputs: {
      eyebrow: "What you get for every call",
      title: "One recording in. A reviewed report out.",
      text: "Each report is built from the transcript of that call, so a supervisor can open the conversation behind any score.",
      items: [
        {
          icon: "gauge",
          title: "Quality control score",
          text: "Planned: empathy, problem solving, clarity, tone, call closing and silence handling, scored per call with an overall result.",
        },
        {
          icon: "alert",
          title: "Problem detection",
          text: "Planned: root causes, system failures, issue severity and problems that keep coming back across calls.",
        },
        {
          icon: "trend",
          title: "Business insight",
          text: "Insights from the call, each with a confidence and the transcript lines behind it. Upsell and churn-risk signals are planned.",
        },
        {
          icon: "book",
          title: "Training package",
          text: "Planned: a coaching package generated from each agent's weak points, with behavioral recommendations.",
        },
      ],
    },
    pipeline: {
      eyebrow: "How it flows",
      title: "From the recording to the console in four steps",
      steps: [
        { title: "Bring the call in", text: "Upload a file. A webhook from your VOIP system is planned." },
        { title: "Transcribe", text: "Speech to text with Whisper, in Persian, English or auto-detected." },
        { title: "Analyze", text: "The analysis stage writes a summary, action items and insights." },
        { title: "Review", text: "Overview, call results and exports. A copilot is planned." },
      ],
      link: "See the full pipeline",
    },
    product: {
      eyebrow: "A working console, not a concept",
      title: "Built for the daily work of a QA team",
      items: [
        { icon: "layout", title: "Dashboard", text: "Recent calls, calls with results, calls in processing and audio used." },
        { icon: "file", title: "Call reports", text: "Summary, action items and insights linked to the transcript lines, plus the full transcript." },
        { icon: "download", title: "Exports", text: "Download a call as TXT, JSON, SRT or VTT. PDF is planned." },
        { icon: "users", title: "Roles", text: "Owner, admin, member and viewer roles, teams with supervisors and agents, and invitations." },
        { icon: "phone", title: "VOIP webhooks", text: "Planned: receivers for Asterisk and FreePBX; agents are created from the extension number." },
        { icon: "bot", title: "AI copilot", text: "Planned: ask questions about your call data in plain language." },
      ],
      link: "Explore the product",
    },
    deployment: {
      eyebrow: "Your data, your choice of where it lives",
      title: "Self-hosted or hybrid today, cloud planned",
      items: [
        { title: "SaaS", text: "Planned: managed by us in a qualified cloud, with tenant isolation enforced in the application and in the database." },
        { title: "Self-hosted", text: "A Docker Compose package on your own servers. No application container has a route to the internet." },
        { title: "Hybrid", text: "Recordings and transcripts stay local. Optional cloud reasoning gets masked text by default, and runs only if you turn it on." },
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
        "Ingest recordings by upload, transcribe with Whisper, get a summary, action items and insights linked to evidence, and review them in the console. QC agents, VOIP webhooks, PDF and an AI copilot are planned.",
    },
    hero: {
      eyebrow: "Platform overview",
      title: "Everything between the recording and the decision",
      text: "Homayar is a post-call analysis engine. It takes a recording, produces a transcript, writes a summary with action items and insights, and shows the result in a console your team can act on.",
    },
    console: {
      eyebrow: "Assemble the console",
      title: "The console builds itself as you scroll",
      text: "Seven panels dock into one window, in the order a recording travels through the product.",
      hint: "Scroll: each panel docks into the window",
      waiting: "Waiting",
      docked: "Docked",
      complete: "Console complete",
      demoEyebrow: "Simulation",
      demoTitle: "Watch a simulated analysis of a sample call",
      demoText: "Waveform, transcript, four planned agents and the report, with sample data.",
    },
    sections: {
      ingest: {
        id: "ingest",
        label: "Ingest · simulated screen",
        title: "Upload a file. A PBX connection is planned.",
        text: "Today a person uploads a file from the console. A webhook that receives each finished call from your VOIP system is planned. Uploads are checked for size, file signature and real audio duration before they are accepted.",
        bullets: [
          "Accepted formats: WAV, MP3, OGG, M4A, AAC, FLAC, MP4 and WEBM.",
          "Planned: webhook receivers for Asterisk and FreePBX with automatic field normalization.",
          "Planned: agents are created on the first call from the extension number.",
          "Planned: every webhook call is verified with a shared secret.",
        ],
      },
      transcription: {
        id: "transcription",
        label: "Transcription · simulated screen",
        title: "Speech to text in Persian and English",
        text: "Transcription runs on Whisper. You can set the spoken language for a recording or let it be detected. Speaker separation is a separate stage. It is not available yet, and the report says so instead of guessing.",
        bullets: [
          "Persian, English or automatic language detection.",
          "Separate stages for preparing the file, speech to text, speaker separation and analysis.",
          "Each stage shows its state: not started, waiting, running, done, partial or failed.",
          "Output from a test provider is labeled as such and never shown as a real analysis.",
        ],
      },
      agents: {
        id: "agents",
        label: "4 agents · planned",
        title: "Planned: four agents, one report",
        text: "Planned. Each agent will read the transcript and write one part of the report. Today the analysis stage writes a summary, action items and insights with evidence.",
        items: [
          { title: "QC Agent", text: "Planned: scores empathy, problem solving, clarity, tone, call closing and silence handling." },
          { title: "Problem Detection", text: "Planned: identifies root causes, system failures, issue severity and repeated problems." },
          { title: "Business Insight", text: "Planned: detects upsell signals, churn-risk signals, revenue opportunities and satisfaction trends." },
          { title: "QC Teacher", text: "Planned: generates training packages from agent weaknesses with behavioral recommendations." },
        ],
      },
      dashboard: {
        id: "dashboard",
        label: "Dashboard · simulated screen",
        title: "Your recent calls on one screen",
        text: "The overview shows recent calls, calls with results, calls in processing and the audio used. Quality trend charts, sentiment distribution, an incidents view and agent comparison are planned.",
        bullets: [
          "Cards for recent calls, calls with results, calls in processing and audio used.",
          "Planned: QC trend charts and sentiment distribution.",
          "Planned: incidents filtered by severity.",
          "Planned: agent comparison charts.",
        ],
      },
      reports: {
        id: "reports",
        label: "Call reports · simulated screen",
        title: "A report you can defend",
        text: "Every call result shows a summary, action items and insights. Each item links to the transcript lines it came from, and the transcript is one tab away. Results can be exported as TXT, JSON, SRT or VTT.",
        bullets: [
          "Planned: score bars for overall QC and each criterion.",
          "Planned: root cause tables with severity.",
          "Action items from the call. The training package is planned.",
          "Transcript view and TXT, JSON, SRT or VTT export. PDF is planned.",
        ],
      },
      copilot: {
        id: "copilot",
        label: "Copilot · planned",
        title: "Planned: ask your call data a question",
        text: "Planned. The copilot will be a chat interface over your call data that answers from stored data, with a graph explorer of agents, calls and issues. Today you can search transcripts and jump to the moment a phrase was said.",
        bullets: [
          "Planned: natural-language questions in Persian or English.",
          "Planned: answers built from stored call data, not free-form guesses.",
          "Planned: interactive graph explorer over agents, calls and issues.",
        ],
      },
      admin: {
        id: "admin",
        label: "Administration · simulated screen",
        title: "Access control for your whole team",
        bullets: [
          "Role-based access: owner, admin, member and viewer, plus team supervisors and agents.",
          "Members join by invitation; roles can be changed and members removed.",
          "Sign-in with e-mail and password, or with Google.",
          "Persian and English interface with automatic RTL layout.",
        ],
      },
    },
  },

  howItWorks: {
    meta: {
      title: "How it works: the Homayar processing pipeline",
      description:
        "Step by step: how a recording is prepared, transcribed, split by speaker where available, summarized with evidence and reviewed, and how the console reports partial or failed stages honestly.",
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
        text: "Size, file signature and real audio duration are verified. The original recording is kept as it is; processing works on a checked copy.",
      },
      {
        stage: "Speech to text",
        title: "Whisper turns speech into a transcript",
        text: "The language is the one you set for the recording, or it is detected. The transcript keeps timestamps so later stages can point back to the conversation.",
      },
      {
        stage: "Separating speakers",
        title: "Planned: turns are assigned to speakers",
        text: "Speaker separation is planned. Today the stage is marked unavailable with the reason, and the rest of the pipeline continues on the text.",
      },
      {
        stage: "Summary and analysis",
        title: "A summary with evidence",
        text: "The analysis stage writes a summary, action items and insights, each linked to the transcript lines behind it. QC scoring, problem detection and coaching are planned. Test-provider output is labeled and never presented as a real analysis.",
      },
      {
        stage: "Review",
        title: "Your team works in the console",
        text: "Overview, call results, transcript, search, share links and exports. Personal-data masking and access rules apply to every view.",
      },
    ],
    rail: { todo: "Not started", done: "Done", running: "Running" },
    snake: {
      hint: "Simulation with sample data. Scroll: the line travels with you",
      checks: ["Size", "Signature", "Duration"],
      agents: ["QC", "Problems", "Insight", "Teacher"],
      tiles: ["Overview", "Call results", "Search"],
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
        "How QA managers, supervisors, contact-center operations leaders and IT or security teams use Homayar's call summaries, evidence links, search and private deployment, and what is planned for each role.",
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
        "qa-managers": "Simulation: planned scores on six criteria",
        supervisors: "Simulation: planned root causes by severity",
        "contact-center-operations": "Simulation: planned queue KPIs",
        "it-security": "Simulation: processing stages with sample data",
      },
    },
    roles: {
      "qa-managers": {
        slug: "qa-managers",
        icon: "gauge",
        name: "QA managers",
        short: "Review every analyzed call with its transcript and evidence. Scoring on fixed criteria is planned.",
        meta: {
          title: "Homayar for QA managers",
          description: "Call summaries, action items and the transcript behind every finding today. Scores on six criteria and coaching packages are planned.",
        },
        hero: {
          title: "Every finding, with the conversation one click away",
          text: "Manual sampling reviews a few calls a week. Homayar writes a summary for every analyzed call and links each point to the transcript, so a disputed result can be checked instead of argued. Scoring on six criteria is planned.",
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
            "A summary, action items and insights for every analyzed call, each linked to the transcript.",
            "The transcript one tab away from the results.",
            "Search across transcripts, with each hit linked to its moment in the call.",
            "Share links and TXT, JSON, SRT or VTT exports to send a call to an agent or a manager.",
          ],
        },
        day: {
          title: "A simulated day with Homayar",
          hint: "Simulation with sample data. Scroll through the day",
          items: [
            { time: "08:30", task: "Open the dashboard", text: "Yesterday's calls are on the overview, with their processing state. Scores on six criteria are planned.", screen: "kpis" },
            { time: "10:00", task: "Check a disputed finding", text: "Each point in the summary links to the transcript lines it came from, so it can be checked against the words. Score bars are planned.", screen: "report" },
            { time: "12:30", task: "Share a report", text: "A share link or an export of the call goes to the agent and their manager. PDF is planned.", screen: "pdf" },
            { time: "15:00", task: "Turn weak points into coaching", text: "Planned: the training package lists behavioral recommendations built from the agent's weak points.", screen: "training" },
            { time: "17:30", task: "Look at the trend", text: "Planned: the QC trend chart shows whether the week moved in the right direction.", screen: "trend" },
          ],
        },
        roadmap: {
          title: "Planned for this role",
          items: ["Custom scorecards per workspace.", "Supervisor review, calibration and appeals workflow.", "Six QC criteria per call and a training package per agent."],
        },
      },
      supervisors: {
        slug: "supervisors",
        icon: "users",
        name: "Supervisors",
        short: "Find the calls that need attention through summaries, action items and search. Coaching packages are planned.",
        meta: {
          title: "Homayar for supervisors",
          description: "Summaries, action items and search across calls today. Incidents by severity, agent comparison and coaching packages are planned.",
        },
        hero: {
          title: "Spend your review time where it matters",
          text: "A supervisor cannot listen to everything. Homayar summarizes each call and lists its action items, with the evidence attached. Incidents by severity and agent comparison are planned.",
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
            "A summary and action items for every analyzed call.",
            "Search across transcripts to find calls that mention the same problem.",
            "Teams with supervisors and agents, so each supervisor sees their team's calls.",
            "Evidence links from every finding to the transcript lines behind it.",
          ],
        },
        day: {
          title: "A simulated day with Homayar",
          hint: "Simulation with sample data. Scroll through the day",
          items: [
            { time: "08:30", task: "Start with the incidents", text: "Planned: the incidents view is filtered by severity, so the few calls worth listening to are at the top.", screen: "incidents" },
            { time: "10:00", task: "Read the root causes", text: "Planned: each detected problem has a root cause and a severity, tied to the call it came from.", screen: "issues" },
            { time: "12:30", task: "Compare the team", text: "Planned: agent comparison charts in the analytics view show who needs time this week.", screen: "compare" },
            { time: "15:00", task: "Coach with a package", text: "Planned: the training package for one agent gives specific behavioral recommendations for the next conversation.", screen: "training" },
            { time: "17:30", task: "Ask the copilot", text: "Planned: one question in plain language returns the issues that came up most this week.", screen: "copilot" },
          ],
        },
        roadmap: {
          title: "Planned for this role",
          items: ["Coaching assignments with follow-up.", "Alerts for high-severity findings.", "Incidents by severity, agent comparison and training packages."],
        },
      },
      "contact-center-operations": {
        slug: "contact-center-operations",
        icon: "trend",
        name: "Contact-center operations",
        short: "Search the whole queue and see what each call was about. Trend views are planned.",
        meta: {
          title: "Homayar for contact-center operations",
          description: "Call summaries, insights, search and usage across the queue today. Quality trends, sentiment, issue tracking and a copilot are planned.",
        },
        hero: {
          title: "Search the whole queue, not one call at a time",
          text: "Operations leaders need the pattern, not the single call. Today Homayar gives you summaries, insights and search across calls. Aggregated quality scores, sentiment and issue trends are planned.",
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
            "An overview of recent calls, calls in processing and audio used.",
            "Insights per call, each with a confidence and its evidence.",
            "Search across call transcripts, with each hit linked to its moment in the call.",
            "Usage per workspace: audio used against the plan limits.",
          ],
        },
        day: {
          title: "A simulated day with Homayar",
          hint: "Simulation with sample data. Scroll through the day",
          items: [
            { time: "08:30", task: "Read the KPI cards", text: "Planned: total calls, average QC score, open issues and calls today, for the period you choose.", screen: "kpis" },
            { time: "10:00", task: "Check the sentiment", text: "Planned: the sentiment distribution shows how the queue felt, not one call.", screen: "sentiment" },
            { time: "12:30", task: "Track recurring issues", text: "Planned: issue tracking groups detected problems with root causes and severity.", screen: "issues" },
            { time: "15:00", task: "Read the business signals", text: "Planned: upsell, churn-risk and satisfaction-trend signals per call.", screen: "signals" },
            { time: "17:30", task: "Ask the data", text: "Planned: the copilot answers from stored call data and explains the result in plain language.", screen: "copilot" },
          ],
        },
        roadmap: {
          title: "Planned for this role",
          items: ["Coverage-adjusted trend statistics.", "Executive briefs built from reviewed findings.", "QC trends, sentiment, issue tracking and a copilot over call data."],
        },
      },
      "it-security": {
        slug: "it-security",
        icon: "shield",
        name: "IT and security",
        short: "Deploy on your servers, control every outbound route and keep every recording under your control. PBX connection and single sign-on are planned.",
        meta: {
          title: "Homayar for IT and security teams",
          description: "Self-hosted Docker Compose package, role-based access, tenant isolation with row-level security and no outbound route from application containers. PBX connection and single sign-on are planned.",
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
            "Sign-in with e-mail and password or Google, and API keys for scripts. PBX webhooks are planned.",
            "JWT sessions, Argon2 password hashing, role-based access, per-route rate limits and PostgreSQL row-level security.",
          ],
        },
        day: {
          title: "A simulated day with Homayar",
          hint: "Simulation with sample data. Scroll through the day",
          items: [
            { time: "08:30", task: "Connect the PBX", text: "Planned: the VOIP webhook receives each finished call. Every call is verified with a shared secret.", screen: "webhook" },
            { time: "10:00", task: "Set up the roles", text: "Members join by invitation and sign in with e-mail or Google. Roles include admin, and teams have supervisors and agents.", screen: "ldap" },
            { time: "12:30", task: "Confirm there is no route out", text: "No application container has a route to the internet. Optional outbound traffic goes only through a deny-by-default proxy.", screen: "egress" },
            { time: "15:00", task: "Check the isolation", text: "Row-level security is forced. A connection with no tenant context sees no tenant rows.", screen: "roles" },
            { time: "17:30", task: "Run the runbook", text: "Health and readiness endpoints, the migration job, drain and rollback steps are documented.", screen: "runbook" },
          ],
        },
        roadmap: {
          title: "Planned for this role",
          items: ["OIDC single sign-on through an identity broker.", "Kubernetes manifests for customers who already run Kubernetes.", "VOIP webhooks for Asterisk and FreePBX, verified with a shared secret."],
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
      eyebrow: "Masking, simulated",
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
          "Each workspace has an AI policy: allowed endpoint kinds, regions and models per stage, and every model passes a license check. Candidates are filtered by the policy before anything else, and a refused candidate is never called and never a fallback.",
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
          "JWT session tokens on signed-in API routes, checked against a live session; passwords hashed with Argon2.",
          "Role-based access with owner, admin, member and viewer roles, plus team supervisors and agents.",
          "Rate limits per route, for example on sign-in, uploads and API keys.",
          "Google sign-in with PKCE and ID-token checks, next to e-mail and password.",
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
      title: "Deployment: self-hosted or hybrid, SaaS planned",
      description:
        "Compare the ways to run Homayar: a self-hosted Docker Compose package with no outbound route, a hybrid with a local data plane and optional cloud analysis on masked text by default, and a planned managed SaaS.",
    },
    hero: {
      eyebrow: "Deployment",
      title: "One product, two places to run it today, a third planned",
      text: "Private operation is a supported profile of the same product, not a fork. Managed SaaS is planned. The same security and evidence rules apply everywhere.",
    },
    flow: {
      source: "Upload",
      app: "Homayar",
      models: "Local models",
      cloud: "Cloud reasoning, masked by default",
      internet: "Internet",
      blocked: "No route",
      allowlist: "Allowlist proxy only",
      optIn: "Opt-in per tenant",
      region: "Planned: cloud region",
    },
    zones: {
      eyebrow: "Three places to run",
      title: "Follow the block",
      text: "The same product moves between three zones. Watch what changes around it.",
      hint: "Scroll: the block travels",
      block: "Homayar",
      items: [
        { name: "Cloud (SaaS)", note: "Planned: cloud region" },
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
        summary: "Planned: we run it for you in a qualified cloud region.",
        points: [
          "Shared PostgreSQL with mandatory workspace scope, application authorization and row-level security.",
          "Planned: a dedicated database, storage and worker deployment for contracts that need stronger operational isolation.",
          "Each workspace policy sets the allowed processing regions, providers and models, and one retention period.",
          "Rate limits per route and a voice-call cap per workspace. Fair scheduling between tenants is planned.",
        ],
        fit: "Teams that want to start fast and accept a qualified cloud region.",
      },
      {
        id: "self-hosted",
        name: "Self-hosted",
        summary: "A Docker Compose package on your own servers.",
        points: [
          "API and console, PostgreSQL, a Redis-compatible broker, S3-compatible object storage, workers, and nginx as the single published origin.",
          "Local speech to text runs on CPU when you install the local AI option. GPU workers and speaker separation are planned. Neo4j is optional.",
          "No application container has a route to the internet. Outbound traffic, if any, passes through a deny-by-default proxy with your allowlist.",
          "Sign-in with e-mail and password, or Google when your network allows it. Keys live in your own secrets storage, as files or variables.",
          "Health and readiness endpoints, migration job, drain and rollback steps documented in the runbook.",
        ],
        fit: "Organizations whose recordings must never leave their network.",
      },
      {
        id: "hybrid",
        name: "Hybrid",
        summary: "A local data plane first, with optional cloud pieces you switch on.",
        points: [
          "Audio, transcripts, identity, search and evidence stay local. The installation keeps working offline.",
          "Planned: an optional management plane receives only approved health, version and license counters, and you can inspect the outgoing payload.",
          "A second mode allows local speech to text and masking, then cloud reasoning that gets masked text by default, opt-in per workspace.",
          "A failed local model never silently falls back to a cloud API.",
        ],
        fit: "Organizations that want private data with selected cloud capacity.",
      },
    ],
    compare: {
      title: "Side by side",
      columns: ["", "SaaS", "Self-hosted", "Hybrid"],
      rows: [
        ["Where recordings live", "Planned: cloud region", "Your servers", "Your servers"],
        ["Who operates it", "Homayar (planned)", "Your IT team, with our package and runbook", "Your IT team; cloud management planned"],
        ["AI providers", "Per-tenant policy", "Local models by default; private endpoints approved by you", "Local first; cloud gets masked text by default, opt-in"],
        ["Identity", "Console accounts", "Console accounts", "Console accounts"],
        ["Network", "Planned: platform firewall and egress proxy", "No outbound route; allowlist proxy optional", "Planned: signed outbound polling, no inbound ports"],
      ],
    },
    runtimeNote: {
      title: "What the on-premise package does not do at runtime",
      items: ["No route for model downloads.", "No telemetry beacons.", "No font or script CDN.", "No mandatory e-mail or SMS provider."],
    },
  },

  about: {
    meta: {
      title: "About Homayar",
      description:
        "Homayar turns recorded customer conversations into reviewable summaries, action items and insights, linked to the original evidence, deployable in your private environment, with managed cloud planned.",
    },
    hero: {
      eyebrow: "About",
      title: "Quality review for Persian customer calls, with the evidence attached",
      text: "Homayar is a Persian-first contact-center quality product. Our promise is simple: turn recorded customer conversations into reviewable quality assessments and recurring operational issues, linked to the original conversation, in the environment you choose.",
    },
    principles: {
      title: "How we build",
      items: [
        { icon: "file", title: "Evidence, not vibes", text: "Every finding sits next to the transcript it came from. A number without a conversation behind it is not a finding." },
        { icon: "check", title: "Truthful states", text: "If a stage cannot run, the console says so. Test-provider output is labeled. We do not fill gaps with defaults." },
        { icon: "shield", title: "Privacy by design", text: "Masking, tenant isolation, explicit retention and durable deletion are part of the core product, not a premium tier." },
        { icon: "globe", title: "Persian first, bilingual always", text: "The interface and transcripts work in Persian and English. RTL is not an afterthought." },
        { icon: "server", title: "Your environment", text: "Self-hosted or hybrid today, managed cloud planned. Private operation is a supported profile of the same product." },
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
      text: "Homayar is an early product. We do not show customer logos or results until a customer has given permission and the numbers have a method behind them.",
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
      minutes: "Monthly call minutes",
      minutesOptions: [
        { value: "", label: "Choose one" },
        { value: "lt10k", label: "Under 10,000 minutes" },
        { value: "10k-50k", label: "10,000 to 50,000 minutes" },
        { value: "50k-250k", label: "50,000 to 250,000 minutes" },
        { value: "250k-1m", label: "250,000 to 1,000,000 minutes" },
        { value: "gt1m", label: "Over 1,000,000 minutes" },
      ],
      phone: "Phone",
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
        phone: "Use only digits, spaces, +, - and brackets, up to 32 characters.",
        minutes: "Choose a value from the list.",
      },
      successTitle: "Request received",
      successText: "Thank you. We have your request and will reply to the e-mail address you gave.",
      referenceLabel: "Your reference number",
      failedTitle: "The request could not be sent",
      failedText: "Please try again in a moment.",
      retry: "Try again",
      turnstileFailed: "The security check did not pass. Please complete it again, then send the form.",
      turnstileWaiting: "Waiting for the security check to finish. Your request is sent as soon as it does.",
      turnstileLoadFailed: "The security check could not load. Check your connection or ad blocker, then reload the page.",
      privacyNote: "We get only what you type here. Cloudflare Turnstile, run by Cloudflare, checks that you are a person. Your request is stored with Cloudflare and e-mailed to the Homayar team only so we can answer you. We delete the stored copy after 180 days and never add you to a marketing list.",
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
      { name: "SaaS", text: "Planned. We run it." },
      { name: "Self-hosted", text: "You run it." },
    ],
    plans: [
      { name: "Monthly", text: "Planned: pay month by month.", amount: "To be announced" },
      { name: "Yearly", text: "Planned: commit for a year.", amount: "To be announced" },
      { name: "Enterprise", text: "Planned: contract terms, private deployment and support.", amount: "On request" },
    ],
    cta: "Ask for a written proposal",
  },

  roi: {
    meta: {
      title: "Review time calculator",
      description: "Type your own numbers and count the review hours per month, today and with Homayar. Every formula is on the page, so you can check the result by hand.",
    },
    hero: {
      eyebrow: "Review time calculator",
      title: "Count your review hours",
      text: "Type your own numbers. Every formula is shown, so you can check the result by hand.",
    },
    sample: "The values below are sample data, not measurements. Every value is your own estimate. Change them to your numbers.",
    fields: {
      agents: { label: "Agents whose calls are reviewed", hint: "", unit: "people" },
      calls: { label: "Calls reviewed per agent each month", hint: "", unit: "calls" },
      minutesToday: { label: "Minutes to review one call today", hint: "By listening to the recording.", unit: "minutes" },
      minutesHomayar: { label: "Minutes to review one call with Homayar", hint: "By reading the transcript and the summary. This is your own estimate.", unit: "minutes" },
      rate: { label: "Cost of one reviewer hour", hint: "In your own currency.", unit: "per hour" },
    },
    errors: {
      whole: "Enter a whole number from 0 to 100,000.",
      minutes: "Enter a number from 0 to 600.",
      rate: "Enter a number from 0 to 1,000,000,000.",
    },
    result: {
      title: "The result",
      hoursUnit: "hours",
      today: "Today",
      withHomayar: "With Homayar",
      gap: "Saved",
      costUnit: "in your currency",
      none: "With these numbers there is no review time saved.",
      invalid: "Fix the marked field to see the result.",
    },
    formulas: {
      title: "The formulas",
      text: "Each line uses the numbers above. You can check every line with a calculator.",
      items: {
        T: "Reviewed calls per month",
        H1: "Review hours per month today",
        H2: "Review hours per month with Homayar",
        S: "Review hours saved per month",
        C: "Cost of the saved hours per month, at your own rate",
      },
      minutes: "60 is the number of minutes in one hour.",
      rounding: "Hours and cost are rounded to one decimal place.",
    },
    notes: {
      title: "What this page counts",
      items: [
        "Only review hours. The cost line is the saved hours times your own hourly rate. Homayar does not promise this amount.",
        "The values filled in when the page opens are sample data. They are not measurements from any team.",
        "Minutes to review one call with Homayar is your own estimate.",
      ],
    },
    product: {
      title: "What a reviewer gets from Homayar",
      text: "For each call recording you upload, Homayar writes a transcript and a summary, with action items and insights linked to the transcript.",
    },
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
    label: "Simulation",
    sample: "Sample data",
    sampleNote: "A simulation of planned features with a made-up call. Names and numbers are invented. Nothing here is real customer data.",
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
      qc: { name: "Quality Control", agent: "QC Agent · planned", note: "Six criteria and an overall score." },
      problems: {
        name: "Problem Detection",
        agent: "Problem Detection · planned",
        severity: "Severity",
        items: [
          { label: "Delivery status not updated", severity: "High" },
          { label: "Agent could not see the order history", severity: "Medium" },
        ],
      },
      insights: {
        name: "Business Insight",
        agent: "Business Insight · planned",
        items: [
          { label: "Churn Risk", value: "Medium" },
          { label: "Upsell", value: "No" },
          { label: "Satisfaction trend", value: "Falling" },
        ],
      },
      teacher: {
        name: "Training Package",
        agent: "QC Teacher · planned",
        items: [
          "Acknowledge a repeat contact before checking the system.",
          "Give a concrete follow-up time instead of 'soon'.",
          "Close the call by confirming what happens next.",
        ],
      },
    },
    ready: "Report ready",
    exportPdf: "Export",
  },

  story: {
    eyebrow: "Planned: the four agents",
    title: "One transcript. Four readings.",
    text: "A simulation with sample data. Each planned agent reads the same transcript and writes one part of the report.",
    surfaces: {
      eyebrow: "Simulated console",
      title: "A simulated console with sample data",
      text: "These screens use sample data. QC scores, incidents, analytics, the copilot and the graph are planned, not in the console today.",
    },
    flow: {
      eyebrow: "Where the data goes",
      title: "The recording stays where you decide",
      text: "Self-hosted and hybrid today; managed cloud is planned. In the self-hosted package no application container has a route to the internet.",
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
      { label: "Compare deployments", meta: "Self-hosted, hybrid, SaaS planned", path: "/deployment", kbd: "C" },
    ],
  },

  theme: { toDark: "Switch to dark mode", toLight: "Switch to light mode" },

  loader: { loading: "Loading" },

  scene: {
    warp: {
      eyebrow: "Space to space",
      title: "The call ends. The analysis space opens.",
      text: "Keep scrolling. You are entering the network that reads the conversation.",
    },
    brain: {
      eyebrow: "Inside the analysis",
      title: "The core reads every line of the call",
      text: "A simulation with a sample call. Each line lights up the part of the network that reads it. At the end the report comes to you.",
      conversation: "Sample conversation",
      analyzing: "Analyzing the conversation",
      ready: "Report ready",
      sample: "Simulation: sample data, invented names and numbers",
    },
  },

  mock: {
    nav: ["Overview", "Calls", "Incidents", "Analytics", "Copilot", "Graph", "Settings"],
    workspace: "Sample queue",
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
    exportPdf: "Export",
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
    phoneTitle: "Sample calls",
    phoneItems: [
      { title: "Ext. 104 · Inbound", meta: "06:12 · QC 82" },
      { title: "Ext. 118 · Inbound", meta: "03:48 · QC 71" },
      { title: "Ext. 104 · Outbound", meta: "08:05 · QC 88" },
    ],
    roles: ["Admin", "Supervisor", "Agent"],
    compare: ["Ext. 104", "Ext. 118", "Ext. 121"],
    runbook: ["Health", "Readiness", "Migration job", "Drain", "Rollback"],
    rls: "Row-level security: forced",
    webhookTitle: "VOIP webhook · planned",
    phoneTabs: ["Overview", "Calls", "Incidents", "Copilot"],
    agent: "Agent",
    duration: "Duration",
    language: "Language",
    languageValue: "Persian",
  },
};

export type Dictionary = typeof en;
export default en;
