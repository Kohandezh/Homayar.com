const en = {
  locale: "en",
  brand: "Homayar",
  tagline: "Post-call analysis",
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
    comingSoon: "Planned",
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
    rights: "Homayar. All rights reserved.",
  },

  ctaBand: {
    title: "See a real call report, not a slide deck.",
    text: "Tell us about your queue, how you record calls and where the data must stay. We show you the console with a sample call and plan a pilot together.",
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
      title: "Homayar: post-call analysis for contact centers",
      description:
        "Your supervisors can't listen to every call. Homayar uses AI to turn each recording into a full transcript, a summary and action items, with every point linked to the line it came from. Persian and English, on your own servers.",
    },
    particles: {
      brand: "Homayar",
      slogan: "Every call, an insight.",
    },
    hero: {
      title: "Know what happened on every call, and what needs follow-up.",
      headlines: [
        "Can't get to every call?",
        "Homayar can.",
        "Summaries and action items.",
        "Backed by the transcript.",
        "With AI, on your own servers.",
        "In Persian and English.",
      ],
      hint: "From call to report",
    },
    outputs: {
      eyebrow: "What you get for every call",
      title: "Hand over the recording. Get the report back.",
      text: "Every report comes from that call's transcript, so a supervisor can open the exact line behind any point.",
      items: [
        {
          icon: "gauge",
          title: "A quality score for every call",
          text: "Planned: every call is scored on six criteria, from empathy to silence handling, and every agent is judged by the same ones.",
        },
        {
          icon: "alert",
          title: "Root causes",
          text: "Planned: see the root cause of each complaint, how serious it is, and which problems keep coming back in other calls.",
        },
        {
          icon: "trend",
          title: "Insights from every call",
          text: "Insights drawn from the call itself, each with a confidence level and the lines that back it up. Upsell and churn-risk signals are planned.",
        },
        {
          icon: "book",
          title: "Coaching for each agent",
          text: "Planned: each agent gets clear coaching tips, based on their own weak points.",
        },
      ],
    },
    pipeline: {
      eyebrow: "Four steps",
      title: "From recording to console in four steps",
      steps: [
        { title: "Send the file", text: "Upload the call recording. Direct intake from your phone system is planned." },
        { title: "Full transcript", text: "The conversation becomes text in Persian or English, and the language can be detected for you." },
        { title: "Summary and action items", text: "You get a summary, action items and insights, each linked to the line it came from." },
        { title: "Review", text: "Read the result in the console, search it or send it to a colleague. The copilot is planned." },
      ],
      link: "Step by step",
    },
    product: {
      eyebrow: "A working console, not a concept",
      title: "Made for your QA team's everyday work",
      items: [
        { icon: "layout", title: "Your calls at a glance", text: "See which calls are ready, which are still processing, and how many minutes of audio have been analyzed so far, on one screen." },
        { icon: "file", title: "Reports you can check", text: "A summary, action items and insights, each linked to the line it came from, with the full transcript one tab away." },
        { icon: "download", title: "Share links and exports", text: "Send a call to an agent or their manager with a share link, or download it as TXT, JSON, SRT or VTT. PDF export is planned." },
        { icon: "users", title: "Access by role", text: "Everyone gets the access their role allows: owner, admin, member or viewer. Teams have supervisors and agents, and you invite your own colleagues." },
        { icon: "phone", title: "Phone system connection", text: "Planned: calls arrive straight from Asterisk and FreePBX, and each extension number becomes an agent in Homayar." },
        { icon: "bot", title: "Ask your call data", text: "Planned: ask about your calls in plain words and get an answer." },
      ],
      link: "All features",
    },
    deployment: {
      eyebrow: "Your data, where you want it",
      title: "Self-hosted or hybrid today; managed cloud planned",
      items: [
        { title: "Managed cloud", text: "Planned: we run Homayar for you in the cloud, with each organization's data kept apart in the application and in the database." },
        { title: "Self-hosted", text: "A Docker Compose package on your own servers. No application container can reach the internet." },
        { title: "Hybrid", text: "Recordings and transcripts stay with you. Cloud analysis runs only if you turn it on, and by default it gets text with personal data masked." },
      ],
      modes: ["We run it", "You run it", "You run it, plus cloud"],
      link: "Find the right setup",
    },
    trust: {
      eyebrow: "From day one",
      title: "Built so the recording stays under your control",
      items: [
        { icon: "eyeOff", title: "Personal-data masking", text: "Phone numbers, e-mails, national IDs, card numbers and IBANs are replaced with placeholders in the transcript. Where there are limits, we say so." },
        { icon: "layers", title: "Each organization kept apart", text: "The application checks each workspace's access, and PostgreSQL row-level security blocks access to another organization's data." },
        { icon: "trash", title: "Delete really means delete", text: "When you delete a call, access ends at once. Then its transcript, analyses and audio file are purged for good." },
        { icon: "shield", title: "No hidden trips to the cloud", text: "If a local model fails, that step is marked unavailable. No data is quietly sent anywhere else." },
      ],
      link: "Security details",
    },
  },

  product: {
    meta: {
      title: "Product: a transcript, summary and action items for every call",
      description:
        "Upload a call recording and get a full transcript, a summary, action items and insights, each linked to the line it came from. Read, search and share them in the console. QC scoring, phone system connection, PDF export and the copilot are planned.",
    },
    hero: {
      eyebrow: "The Homayar console",
      title: "From the call recording to your supervisor's decision",
      text: "Hand over the call recording. Homayar prepares the full transcript, a summary, action items and insights, and your team reads and follows up in the console.",
    },
    console: {
      eyebrow: "The console",
      title: "Each part of the console does one job for your team",
      text: "Seven parts, in the order a call moves through Homayar.",
      hint: "Seven parts, in order",
      waiting: "Waiting",
      docked: "Ready",
      complete: "Console ready",
      demoEyebrow: "Simulation",
      demoTitle: "A sample call, from recording to report",
      demoText: "A simulation with sample data: the transcript, the planned parts of the report and the finished result.",
    },
    sections: {
      ingest: {
        id: "ingest",
        label: "Upload · simulated screen",
        title: "Upload a file. A phone system connection is planned.",
        text: "Today you upload a file from the console. Automatic intake of each finished call from your phone system is planned. Every file is checked for size, file signature and real audio duration before it is accepted.",
        bullets: [
          "Accepted formats: WAV, MP3, OGG, M4A, AAC, FLAC, MP4 and WEBM.",
          "Planned: calls arrive straight from Asterisk and FreePBX, with each call's details normalized for you.",
          "Planned: each extension's agent is set up in Homayar on its first call.",
          "Planned: every call sent by your phone system is verified with a shared secret.",
        ],
      },
      transcription: {
        id: "transcription",
        label: "Transcription · simulated screen",
        title: "A full transcript of each call, in Persian or English",
        text: "Set the spoken language for each recording, or let it be detected. Speaker separation is not available yet, and the report says so plainly instead of guessing.",
        bullets: [
          "Persian, English or automatic language detection.",
          "Each stage runs on its own: preparing the file, speech to text, speaker separation and analysis.",
          "Each stage shows its state: not started, waiting, running, done, partial or failed.",
          "Output from a test provider is labeled as such and never shown as a real analysis.",
        ],
      },
      agents: {
        id: "agents",
        label: "4 parts · planned",
        title: "Planned: one call, four answers",
        text: "Planned: each call report gets four new parts, from the quality score to coaching. Today every call already comes with a summary, action items and insights, with evidence.",
        items: [
          { title: "Quality score", text: "Planned: each call is scored on empathy, problem solving, clarity, tone, call closing and silence handling." },
          { title: "Root causes", text: "Planned: see the root cause of each complaint, how serious it is, where a system failed and which problems repeat." },
          { title: "Business insight", text: "Planned: see which customers are ready to buy more, which may leave, and how satisfaction changes over time." },
          { title: "Coaching", text: "Planned: each agent gets clear coaching tips, based on their own weak points." },
        ],
      },
      dashboard: {
        id: "dashboard",
        label: "Overview · simulated screen",
        title: "Your recent calls on one screen",
        text: "The overview shows which calls are ready, which are still processing, and how many minutes of audio have been analyzed so far. QC trend charts, sentiment distribution, an incidents view and agent comparison are planned.",
        bullets: [
          "You get a card each for recent calls, calls with results, calls in processing and audio used.",
          "Planned: see quality trends and customer sentiment in charts.",
          "Planned: filter incidents by severity.",
          "Planned: compare agents in a chart.",
        ],
      },
      reports: {
        id: "reports",
        label: "Call reports · simulated screen",
        title: "A report you can defend",
        text: "For each call you see a summary, action items and insights, each linked to the line it came from, with the full transcript one tab away. Export the result as TXT, JSON, SRT or VTT.",
        bullets: [
          "Planned: see the overall score and each criterion on a bar.",
          "Planned: see each root cause with its severity in a table.",
          "See the action items from the call. Coaching is planned.",
          "Read the transcript and export it as TXT, JSON, SRT or VTT. PDF export is planned.",
        ],
      },
      copilot: {
        id: "copilot",
        label: "Copilot · planned",
        title: "Planned: ask your call data a question",
        text: "Planned: the copilot answers your questions about calls from stored data and shows how agents, calls and problems connect on a graph. Today you can already search transcripts and jump to the moment a phrase was said.",
        bullets: [
          "Planned: ask in plain words, in Persian or English.",
          "Planned: answers come from your own call data, not from guesses.",
          "Planned: see how agents, calls and problems connect on a graph.",
        ],
      },
      admin: {
        id: "admin",
        label: "Administration · simulated screen",
        title: "Access that fits each person's role",
        bullets: [
          "Four workspace roles: owner, admin, member or viewer; and in each team, supervisors and agents.",
          "Invite colleagues, then change their role or remove them whenever you need.",
          "Log in with e-mail and password, or with your Google account.",
          "The console works in Persian and English, with automatic right-to-left layout for Persian.",
        ],
      },
    },
  },

  howItWorks: {
    meta: {
      title: "How it works: from recording to report, step by step",
      description:
        "Step by step: how a recording is checked, transcribed and summarized with evidence, and how your team works with it in the console. Every stage shows its true state, even when it is partial or failed.",
    },
    hero: {
      eyebrow: "The steps",
      title: "From recording to report",
      text: "No need to wait on the page: processing runs on the server, and when you come back, every stage shows its own state.",
    },
    steps: [
      {
        stage: "Preparing the recording",
        title: "The file is checked before anything else",
        text: "Size, file signature and real audio duration are checked. Your original recording stays untouched; Homayar works on a checked copy.",
      },
      {
        stage: "Speech to text",
        title: "A full transcript, with the time of every line",
        text: "You set the language for each recording, or it is detected. The time of every line stays in the transcript, so each point in the report goes back to its moment in the call.",
      },
      {
        stage: "Separating speakers",
        title: "Planned: telling the customer and the agent apart",
        text: "Speaker separation is planned. Today this stage shows as unavailable, with the reason, and the rest of the work continues on the text.",
      },
      {
        stage: "Summary and analysis",
        title: "A summary and action items, with evidence",
        text: "You get a summary, action items and insights, each linked to the line it came from. QC scoring, root causes and coaching are planned. Test-provider output is labeled and never passed off as a real analysis.",
      },
      {
        stage: "Review",
        title: "Your team works in the console",
        text: "Your team reads each call's result and transcript, searches, and shares through share links or exports. Access rules apply everywhere, and personal-data masking, when it is on, applies to every view.",
      },
    ],
    rail: { todo: "Not started", done: "Done", running: "Running" },
    snake: {
      hint: "Simulation with sample data: one call, from recording to report",
      checks: ["Size", "Signature", "Duration"],
      agents: ["QC", "Problems", "Insight", "Coaching"],
      tiles: ["Overview", "Call results", "Search"],
      providers: [
        { name: "Local Whisper", allowed: true },
        { name: "Local analysis model", allowed: true },
        { name: "Cloud provider outside the policy", allowed: false },
      ],
      allowed: "Allowed",
      refused: "Refused",
      passing: "Current step",
    },
    states: {
      eyebrow: "Per-stage status",
      title: "If something is missing, you see it",
      text: "Every stage has a clear state. If a stage stops, the report shows why, in the language of the console.",
      items: [
        { label: "Not started", text: "The stage has not been reached yet." },
        { label: "Waiting", text: "The recording is waiting its turn." },
        { label: "Running", text: "The stage is being processed right now." },
        { label: "Partial", text: "Processing finished with gaps; the stages show what is missing." },
        { label: "Unavailable", text: "The stage could not run, for example when speaker separation is not available for this recording." },
        { label: "Failed", text: "The last attempt failed for a named reason. Automatic retries are limited, and you can see each one." },
      ],
    },
    policy: {
      eyebrow: "Where the AI runs",
      title: "AI runs only where you allow it",
      text: "Each workspace has its own AI policy: which providers, regions and models may be used for speech to text, speaker separation and analysis. The policy is checked before every stage. A provider it does not allow is never used, not even as a fallback.",
      link: "Egress control in detail",
    },
  },

  solutions: {
    meta: {
      title: "Solutions by role: QA, supervisors, operations, IT",
      description:
        "What Homayar does for QA, supervisors, operations and IT: a summary of every call, each point linked to the transcript, search across calls and installation on your own servers. For each role, we also say what is planned.",
    },
    hero: {
      eyebrow: "By role",
      title: "One call, four points of view",
      text: "Each role wants something different from a call: QA wants fair reviews, supervisors want the calls that matter, operations wants patterns and IT wants to know where the data lives. Here is what Homayar gives each of them today, and what is planned.",
    },
    ring: {
      eyebrow: "Four roles, one call",
      hint: "Simulation: each role, its own view",
      seat: "Role",
      sees: "What each one gets",
      open: "More details",
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
        short: "Review each call with its summary and transcript, and trace every point to its line. Scoring is planned.",
        meta: {
          title: "Homayar for QA managers",
          description: "Get a summary and action items for every call, and check each finding against the line it came from. Quality scores on six criteria and coaching are planned.",
        },
        hero: {
          title: "Every finding, one click from the transcript",
          text: "Manual sampling covers only a few calls a week. Homayar summarizes every analyzed call and links each point to the transcript, so a disputed result gets checked, not argued. Scoring on six criteria is planned.",
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
          title: "What you get today",
          items: [
            "A summary, action items and insights for every call, each linked to the line it came from.",
            "The full transcript, one tab away from the results.",
            "Search across transcripts; each hit takes you to that moment in the call.",
            "Send a call to an agent or their manager with a share link, or download it as TXT, JSON, SRT or VTT.",
          ],
        },
        day: {
          title: "A simulated day with Homayar",
          hint: "Simulation with sample data",
          items: [
            { time: "08:30", task: "Yesterday's calls", text: "The overview shows which of yesterday's calls are ready and which are still processing. Quality scores are planned.", screen: "kpis" },
            { time: "10:00", task: "Check a disputed finding", text: "Each point in the summary links to the line it came from, so you check it against what the agent and the customer actually said. Score bars are planned.", screen: "report" },
            { time: "12:30", task: "Share a report", text: "Send the call to the agent and their manager with a share link or an export. PDF export is planned.", screen: "pdf" },
            { time: "15:00", task: "Turn weak points into coaching", text: "Planned: each agent gets clear coaching tips, based on their own weak points.", screen: "training" },
            { time: "17:30", task: "The week's trend", text: "Planned: the quality trend chart shows whether call quality got better or worse this week.", screen: "trend" },
          ],
        },
        roadmap: {
          title: "On this role's roadmap",
          items: ["Your own scorecard for each workspace.", "Supervisor review, reviewer calibration and agent appeals.", "A quality score on six criteria for every call, and coaching for each agent."],
        },
      },
      supervisors: {
        slug: "supervisors",
        icon: "users",
        name: "Supervisors",
        short: "Find the calls that need follow-up through summaries, action items and search. Coaching is planned.",
        meta: {
          title: "Homayar for supervisors",
          description: "Can't get to every call? Read each call's summary and action items, and find the calls that matter with search. Incidents by severity, agent comparison and coaching are planned.",
        },
        hero: {
          title: "Spend your review time where it matters",
          text: "No supervisor can listen to every call. Homayar summarizes each one and lists its action items, each with its evidence in the transcript. Incidents by severity and agent comparison are planned.",
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
          title: "What you get today",
          items: [
            "A summary and action items for every analyzed call.",
            "Search the transcripts to find calls that mention the same problem.",
            "Teams have supervisors and agents, and each supervisor sees their own team's calls.",
            "Every finding links to the line of the call it came from.",
          ],
        },
        day: {
          title: "A simulated day with Homayar",
          hint: "Simulation with sample data",
          items: [
            { time: "08:30", task: "Start with the incidents", text: "Planned: incidents are sorted by severity, so the few calls worth hearing come first.", screen: "incidents" },
            { time: "10:00", task: "Read the root causes", text: "Planned: see the root cause and severity of each problem, one click from its call.", screen: "issues" },
            { time: "12:30", task: "Compare the team", text: "Planned: agent comparison charts show who needs your time this week.", screen: "compare" },
            { time: "15:00", task: "Coach an agent", text: "Planned: each agent gets specific tips for their next call.", screen: "training" },
            { time: "17:30", task: "Ask the copilot", text: "Planned: ask one plain question and learn which issues came up most this week.", screen: "copilot" },
          ],
        },
        roadmap: {
          title: "On this role's roadmap",
          items: ["Coaching assignments with follow-up.", "Alerts for high-severity findings.", "Incidents by severity, agent comparison and coaching."],
        },
      },
      "contact-center-operations": {
        slug: "contact-center-operations",
        icon: "trend",
        name: "Contact-center operations",
        short: "Search all your calls and know what each one was about. Trend charts are planned.",
        meta: {
          title: "Homayar for contact-center operations",
          description: "See each call's summary and insights, search across all calls and track usage per workspace in one place. Quality trends, customer sentiment, issue tracking and a copilot are planned.",
        },
        hero: {
          title: "Search every call at once, not one at a time",
          text: "Operations leaders need the pattern, not a single call. Today Homayar gives you each call's summary and insights, and search across all of them. Aggregated quality scores, customer sentiment and issue trends are planned.",
        },
        needs: {
          title: "What an operations leader needs",
          items: [
            "Which problems keep coming back, and how often.",
            "Whether quality and satisfaction are getting better or worse.",
            "Fast answers, without waiting for an analyst.",
          ],
        },
        gets: {
          title: "What you get today",
          items: [
            "One screen with recent calls, calls still processing and the audio analyzed so far.",
            "Insights from each call, each with a confidence level and the lines that back it up.",
            "Search across transcripts; each hit takes you to that moment in the call.",
            "Usage per workspace: how much audio is analyzed and how much is left in your plan.",
          ],
        },
        day: {
          title: "A simulated day with Homayar",
          hint: "Simulation with sample data",
          items: [
            { time: "08:30", task: "KPIs at a glance", text: "Planned: total calls, average quality score, open issues and today's calls, for any period you choose.", screen: "kpis" },
            { time: "10:00", task: "How customers feel", text: "Planned: the sentiment chart shows how customers felt across all calls, not just one.", screen: "sentiment" },
            { time: "12:30", task: "Track recurring issues", text: "Planned: similar problems are grouped together, each with its root cause and severity.", screen: "issues" },
            { time: "15:00", task: "Upsell and churn signals", text: "Planned: upsell chances, churn risk and the satisfaction trend for each call.", screen: "signals" },
            { time: "17:30", task: "Ask the data", text: "Planned: ask your question; the copilot answers from your call data and explains it in plain words.", screen: "copilot" },
          ],
        },
        roadmap: {
          title: "On this role's roadmap",
          items: ["Trend statistics adjusted for how many calls were analyzed.", "Short briefs for executives, built from reviewed findings.", "Quality trends, customer sentiment, issue tracking and a copilot for your call data."],
        },
      },
      "it-security": {
        slug: "it-security",
        icon: "shield",
        name: "IT and security",
        short: "Install Homayar on your own servers, control every outbound route and keep recordings inside your organization. PBX connection and single sign-on are planned.",
        meta: {
          title: "Homayar for IT and security teams",
          description: "Install Homayar on your own servers with Docker Compose. Each organization's data is kept apart with PostgreSQL row-level security, and no application container can reach the internet. PBX connection and single sign-on are planned.",
        },
        hero: {
          title: "A compact stack you install, back up and audit yourself",
          text: "IT signs off on the integration, security signs off on the data flow. Homayar gives both a clear answer: real configuration, not slides and promises.",
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
          title: "What you get today",
          items: [
            "One Docker Compose package for your own servers: PostgreSQL, a Redis-compatible broker, S3-compatible object storage, the API, the workers and the console, all behind one nginx origin.",
            "No application container has a route out. If you need outbound traffic, it goes only through an allowlist proxy; everything is closed except the destinations you allow.",
            "Log in with e-mail and password or Google, and API keys for scripts. PBX webhooks are planned.",
            "JWT sessions, Argon2 password hashing, role-based access, per-route rate limits and PostgreSQL row-level security.",
          ],
        },
        day: {
          title: "A simulated day with Homayar",
          hint: "Simulation with sample data",
          items: [
            { time: "08:30", task: "Connect the PBX", text: "Planned: a VOIP webhook receives each finished call from your PBX; a shared secret verifies every request.", screen: "webhook" },
            { time: "10:00", task: "Set up the roles", text: "Colleagues join by invitation and log in with e-mail or Google. Roles: owner, admin, member or viewer; teams have supervisors and agents.", screen: "ldap" },
            { time: "12:30", task: "Confirm there is no route out", text: "No application container has a route to the internet. Outbound traffic, if you need it, goes only through an allowlist proxy.", screen: "egress" },
            { time: "15:00", task: "Check the isolation", text: "Row-level security (RLS) is forced. A connection with no organization set sees no rows.", screen: "roles" },
            { time: "17:30", task: "Run the runbook", text: "Health and readiness checks, the migration job, drain and rollback are all documented.", screen: "runbook" },
          ],
        },
        roadmap: {
          title: "On this role's roadmap",
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
        "You can mask personal data in transcripts, and we state the limits. Each organization's data is kept apart, deletion really deletes and the self-hosted package has no route to the internet. We claim no certifications.",
    },
    hero: {
      eyebrow: "Security and privacy",
      title: "What we built, and what we do not claim",
      text: "Call recordings are among the most sensitive data any company holds. Here is how Homayar protects them today, and what we do not claim.",
    },
    layers: {
      eyebrow: "Layered protection",
      title: "Four layers between your recording and anyone who should not hear it",
      hint: "All four are in the product today",
      items: [
        { icon: "server", title: "Network wall", text: "In the self-hosted package, no application container can reach the internet." },
        { icon: "layers", title: "Org isolation", text: "Two separate locks: access checks in the application and row-level security in PostgreSQL." },
        { icon: "eyeOff", title: "Personal-data masking", text: "Phone numbers, e-mails, IDs and card numbers are replaced with fixed placeholders in the transcript." },
        { icon: "trash", title: "Retention and deletion", text: "You set how long data stays. Deletion blocks access, then erases it." },
      ],
    },
    maskDemo: {
      eyebrow: "Masking, simulated",
      title: "With masking on, phone numbers are hidden in transcripts",
      original: "Original transcript",
      masked: "Masked version",
      who: "Customer",
      before: "You can reach me at 0912 345 6789 after five.",
      after: "You can reach me at [PHONE_1] after five.",
      badge: "1 phone number masked",
      hint: "Made-up number, sample data",
    },
    sections: {
      redaction: {
        id: "redaction",
        title: "Personal-data masking in transcripts",
        text: "You turn masking on per workspace. From then on, personal data in the transcript is replaced with a stable placeholder such as [PHONE_1], and analysis, search and exports use the masked version.",
        detected: {
          title: "What it finds",
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
          title: "What it does not find",
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
        title: "Org isolation",
        text: "Each organization's data is protected twice: once by the application's authorization checks and once by PostgreSQL row-level security.",
        items: [
          "Every query runs under a database role that cannot bypass row-level security. Production start-up refuses a superuser or bypass role.",
          "Policies are forced, so even the table owner is subject to them. A connection with no organization context sees no organization rows.",
          "The organization for each transaction comes from the validated session, job or share token, so it cannot carry over when a pooled connection is reused.",
          "The backend tests run as the same restricted role and include cross-organization read, write and re-homing tests.",
        ],
      },
      retention: {
        id: "retention",
        title: "Retention and deletion",
        text: "You get three clear retention modes, and deletion actually removes data instead of only hiding it.",
        items: [
          "Retention modes: finite (1 to 3650 days after ingestion), indefinite (only an explicit deletion removes data) or immediate (eligible as soon as processing ends). Ambiguous values are refused, never guessed.",
          "A legal hold overrides retention. Retention never removes a held conversation.",
          "Deletion happens in two steps: access ends at once and share links are revoked; then transcripts, analyses, evidence, summaries and media files are purged, with retries if a step fails.",
          "What remains is a non-content receipt: ids, counters, timestamps and an error code. Titles and file names are emptied.",
          "Tombstones are journaled outside the database so a restored backup re-applies deletions.",
          "A presigned download URL issued before a deletion stays valid until its own short expiry or until the bytes are removed. We say this instead of promising instant revocation.",
        ],
      },
      egress: {
        id: "egress",
        title: "Self-hosted: no route to the internet",
        text: "In the self-hosted package, data is stopped from leaving twice: once in the application and once in the network.",
        items: [
          "No application container has a route out. Application networks are internal; only nginx publishes a port.",
          "Optional outbound HTTP(S) goes only through a deny-by-default proxy that forwards to the exact host and port lines of an allowlist you control. The air-gapped default runs no proxy at all.",
          "At the application layer, a networked AI adapter can only be called under a permit granted for that exact policy-approved endpoint. Without it the request is refused before any connection.",
          "Cloud-metadata addresses, link-local, loopback and private addresses are refused for cloud endpoints. Private endpoints on your LAN are approved explicitly.",
        ],
      },
      routing: {
        id: "routing",
        title: "Which AI sees your data",
        text: "Your workspace's AI policy decides, and the decision is recorded with every run.",
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
          "JWT session tokens on API routes for logged-in users, checked against a live session; passwords hashed with Argon2.",
          "Role-based access with owner, admin, member and viewer roles, plus team supervisors and agents.",
          "Rate limits per route, for example on login, uploads and API keys.",
          "Google sign-in with PKCE and ID-token checks, next to e-mail and password.",
          "A global exception handler so stack traces are never returned to a client.",
        ],
      },
      notClaimed: {
        id: "not-claimed",
        title: "What we do not claim",
        text: "We only state what we have tested.",
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
      title: "Deployment: self-hosted or hybrid; managed cloud planned",
      description:
        "Run Homayar on your own servers with no route to the internet, or choose hybrid: your data stays local, and cloud analysis is added only if you want it, with masked text by default. Managed cloud is planned.",
    },
    hero: {
      eyebrow: "Deployment",
      title: "You decide where Homayar runs",
      text: "Today on your own servers or as a hybrid; managed cloud is planned. All three are one product, with the same security and evidence rules, not separate versions.",
    },
    flow: {
      source: "Upload",
      app: "Homayar",
      models: "Local models",
      cloud: "Cloud analysis, masked by default",
      internet: "Internet",
      blocked: "No route",
      allowlist: "Allowlist proxy only",
      optIn: "Opt-in per org",
      region: "Planned: cloud region",
    },
    zones: {
      eyebrow: "Three places to run",
      title: "One product",
      text: "The product is the same; only where data lives and how it can leave changes.",
      hint: "Cloud, self-hosted, hybrid",
      block: "Homayar",
      items: [
        { name: "Managed cloud", note: "Planned: cloud region" },
        { name: "Your server", note: "No route to the internet" },
        { name: "Hybrid", note: "Data stays with you; cloud analysis only if you want it" },
      ],
      wall: "No route",
      halves: ["Local data", "Optional cloud"],
      compareHint: "Three ways to run it",
    },
    options: [
      {
        id: "saas",
        name: "Managed cloud",
        summary: "Planned: we run it for you in a qualified cloud region.",
        points: [
          "Each organization's data stays apart in a shared PostgreSQL: every request is limited to its own workspace, the application checks access, and row-level security applies.",
          "Planned: if your contract needs stronger isolation, we set up a separate database, storage and processing servers for you.",
          "Each workspace policy sets where data is processed, with which providers and models, and how long it is kept.",
          "Rate limits apply per route, and a voice-call cap per workspace. Fair scheduling between organizations is planned.",
        ],
        fit: "Teams that want to start fast and accept a qualified cloud region.",
      },
      {
        id: "self-hosted",
        name: "Self-hosted",
        summary: "A Docker Compose package on your own servers.",
        points: [
          "Everything is in the package: API and console, PostgreSQL, a Redis-compatible broker, S3-compatible object storage, processing services, and nginx as the only entry point.",
          "If you install the local AI option, speech to text runs on the server's own CPU. GPU processing and speaker separation are planned. Neo4j is optional.",
          "No application container can reach the internet. If outbound traffic is needed, it passes only through a deny-by-default proxy with your allowlist.",
          "Log in with e-mail and password, or with Google sign-in when your network allows it. Keys stay in your own secrets storage, as files or variables.",
          "The runbook covers it all: health and readiness checks, the database migration job, drain and rollback.",
        ],
        fit: "Organizations whose recordings must never leave their network.",
      },
      {
        id: "hybrid",
        name: "Hybrid",
        summary: "Your data stays with you, and you switch on cloud pieces only if you want them.",
        points: [
          "Audio, transcripts, user accounts, search and evidence stay local, and Homayar keeps working offline.",
          "Planned: an optional management service receives only approved health, version and license counters, and you see exactly what is sent.",
          "In the second mode, speech to text and personal-data masking run on your own server, then cloud analysis runs on masked text by default, only if that workspace opts in.",
          "If a local model fails, the work is never quietly handed to a cloud service.",
        ],
        fit: "Organizations that keep their data in-house and use the cloud only where they need it.",
      },
    ],
    compare: {
      title: "Side by side",
      columns: ["", "Managed cloud", "Self-hosted", "Hybrid"],
      rows: [
        ["Where recordings live", "Planned: cloud region", "Your servers", "Your servers"],
        ["Who operates it", "Homayar (planned)", "Your IT team, with our package and runbook", "Your IT team; cloud management planned"],
        ["AI providers", "Per-organization policy", "Local models by default; private endpoints approved by you", "Local first; cloud gets masked text by default, opt-in"],
        ["User accounts", "Console accounts", "Console accounts", "Console accounts"],
        ["Network", "Planned: platform firewall and egress proxy", "No outbound route; allowlist proxy optional", "Planned: signed outbound polling, no inbound ports"],
      ],
    },
    runtimeNote: {
      title: "What the self-hosted package never does",
      items: ["No route for model downloads.", "No telemetry beacons.", "No font or script CDN.", "No mandatory e-mail or SMS provider."],
    },
  },

  about: {
    meta: {
      title: "About Homayar",
      description:
        "Homayar turns recorded customer calls into transcripts, summaries, action items and insights, each linked to the original evidence. On your own servers or hybrid; managed cloud is planned.",
    },
    hero: {
      eyebrow: "About",
      title: "Quality review for Persian customer calls, with the evidence attached",
      text: "Homayar is built for Persian-speaking contact centers. Our promise is simple: turn recorded customer calls into quality reviews you can check and repeat problems you can act on, each linked to the original call, in the environment you choose.",
    },
    principles: {
      title: "How we build",
      items: [
        { icon: "file", title: "Evidence, not guesses", text: "Every finding sits next to the line of the call it came from. A number with no call behind it is not a finding." },
        { icon: "check", title: "Honest states", text: "If a stage cannot run, the console says so. Test-provider output is labeled. We do not fill gaps with defaults." },
        { icon: "shield", title: "Privacy from day one", text: "Personal-data masking, keeping each organization's data apart, a clear retention period and real deletion are in the product itself, not in a premium tier." },
        { icon: "globe", title: "Persian first, bilingual always", text: "The console and the transcripts work in Persian and English, and right-to-left layout was there from day one." },
        { icon: "server", title: "Your environment", text: "Self-hosted or hybrid today; managed cloud is planned. The self-hosted version is the same product, not a separate one." },
        { icon: "scale", title: "Claims we can show", text: "We do not publish customer names, percentages or certifications we cannot back with evidence." },
      ],
    },
    who: {
      title: "Who it is for",
      text: "For contact centers and service teams that record calls in Persian or English and want their QA team, supervisors and operations leaders to work from the same checked data. The buyer is usually the head of the contact center or customer experience; QA managers and supervisors use it every day; IT and security approve the integration.",
    },
    howWeWork: {
      title: "How we work with new customers",
      steps: [
        { title: "Discovery", text: "We go through your queue, recording access, current QA process and where the data must stay." },
        { title: "Sample and baseline", text: "We take an authorized sample of your calls and your current review time, so results are measured against something real." },
        { title: "Measured pilot", text: "A pilot with a clear scope and success criteria we agree on. We report what did not work next to what did." },
        { title: "Rollout", text: "Training for your team, final acceptance and a handover to support." },
      ],
    },
    constellation: { eyebrow: "Six principles", hint: "Six rules we build Homayar by" },
    stepper: { hint: "Four steps to rollout", step: "Step", of: "of" },
    status: {
      title: "Where we are",
      text: "Homayar is an early product. We do not show customer logos or results until a customer has given permission and the numbers have a method behind them.",
    },
  },

  contact: {
    meta: {
      title: "Contact and demo request",
      description: "See Homayar with a sample call. Tell us about your calls, your recording setup and where the data must stay, and we will scope a pilot with you.",
    },
    hero: {
      eyebrow: "Contact",
      title: "Request a demo",
      text: "Tell us a little about your contact center. We reply by e-mail and set up a short call.",
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
        { value: "saas", label: "Managed cloud is fine" },
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
      messageHint: "Optional. For example your PBX, the languages of your calls, or a date you need it by.",
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
      successText: "Thank you. We will reply to the e-mail address you gave.",
      referenceLabel: "Your reference number",
      failedTitle: "The request could not be sent",
      failedText: "Please try again in a moment.",
      retry: "Try again",
      turnstileFailed: "The security check did not pass. Please complete it again, then send the form.",
      turnstileWaiting: "Waiting for the security check to finish. Your request is sent as soon as it does.",
      turnstileLoadFailed: "The security check could not load. Check your connection or ad blocker, then reload the page.",
      turnstileError: "The security check ran into a problem. Press Try again to run it again, then your request is sent.",
      privacyNote: "We get only what you type here. Cloudflare Turnstile, run by Cloudflare, checks that you are a person. Your request is stored with Cloudflare and e-mailed to the Homayar team only so we can answer you. We delete the stored copy after 180 days and never add you to a marketing list.",
    },
    aside: {
      title: "What happens next",
      steps: [
        "We reply by e-mail, usually within a few business days.",
        "On a short call, we ask about your calls, your access to recordings and your data constraints.",
        "We show you the Homayar console with a sample call, including how a partial result looks.",
        "If Homayar fits, we propose a pilot with a clear scope and success criteria we agree on.",
      ],
      linksTitle: "Useful before the call",
    },
  },

  pricing: {
    meta: {
      title: "Pricing",
      description: "Homayar pricing is not final yet. Plans and amounts will appear here once the commercial model is set. Until then, ask us for a written proposal. Nothing on this page is an offer.",
    },
    hero: {
      eyebrow: "Pricing",
      title: "Get a written proposal for your budget",
      text: "We are finalizing the commercial model and payment options. Until then this page shows the plan structure only, without amounts. Nothing on this page is an offer.",
    },
    notice: "Amounts, billing units and terms will be published here once they are final. If you need numbers for a budget today, contact us and we will send a written proposal.",
    deployments: [
      { name: "Managed cloud", text: "Planned. We run it." },
      { name: "Self-hosted", text: "You run it." },
    ],
    plans: [
      { name: "Monthly", text: "Planned: pay month by month.", amount: "Not announced yet" },
      { name: "Yearly", text: "Planned: commit for a year.", amount: "Not announced yet" },
      { name: "Enterprise", text: "Planned: contract terms, private deployment and support.", amount: "On request" },
    ],
    cta: "Ask for a written proposal",
  },

  roi: {
    meta: {
      title: "Review time calculator",
      description: "Use your own numbers to work out how many hours your QA team spends reviewing calls each month, today and with Homayar. Every formula is on the page, so you can check the result yourself.",
    },
    hero: {
      eyebrow: "Review time calculator",
      title: "How many hours does call review cost your team?",
      text: "Type your own numbers. Every formula is on screen, so you can check the result with a calculator.",
    },
    sample: "The numbers below are sample data, not measurements. Replace them with your own estimates.",
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
      text: "Each line uses the numbers above, and you can check it with a calculator.",
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
      text: "For each recording you upload, you get the full transcript and a summary, with action items and insights each linked to a line of the call. Your reviewer reads instead of listening.",
    },
  },

  legal: {
    privacy: {
      meta: { title: "Privacy policy", description: "The Homayar privacy policy is being prepared with legal review and is not yet published." },
      title: "Privacy policy",
      notice: "This document is being prepared with legal review and is not yet in force. It will describe what data this website and the Homayar product process, on what basis, for how long, and who to contact. Until it is published, please contact us for the current data handling terms.",
      site: {
        title: "What holds today",
        text: "Homayar analyzes only the call recordings you are authorized to process. This website loads no third-party scripts except the Cloudflare Turnstile check on the contact page, and it sets no tracking cookies. A request you send through the contact form is stored; the note next to the form says where and for how long.",
      },
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
    sampleNote: "A simulation of planned features with a made-up call. Names and numbers are invented, and no real customer data is shown.",
    play: "Play the demo",
    pause: "Pause the demo",
    replay: "Replay the demo",
    reducedNote: "Motion is reduced on this device, so you see the finished report at once.",
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
    report: "Call report",
    agents: {
      qc: { name: "Scores", agent: "QC scoring · planned", note: "Six criteria and an overall score." },
      problems: {
        name: "Problems in this call",
        agent: "Root causes · planned",
        severity: "Severity",
        items: [
          { label: "Delivery status not updated", severity: "High" },
          { label: "Agent could not see the order history", severity: "Medium" },
        ],
      },
      insights: {
        name: "Business insight",
        agent: "Insights · partly planned",
        items: [
          { label: "Churn risk", value: "Medium" },
          { label: "Upsell", value: "No" },
          { label: "Satisfaction trend", value: "Falling" },
        ],
      },
      teacher: {
        name: "Coaching",
        agent: "Coaching · planned",
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
    eyebrow: "Planned: the full report",
    title: "One call. Four answers.",
    text: "Four questions every supervisor asks: how did the agent do, what went wrong, how does the customer feel, what should we coach? A simulation with sample data; apart from per-call insights, everything here is planned.",
    surfaces: {
      eyebrow: "Simulated console",
      title: "Your team's recent calls on one screen",
      text: "These screens are a simulation with sample data. QC scores, incidents, analytics, the copilot and the graph are planned and not in the console yet.",
    },
    flow: {
      eyebrow: "Deployment",
      title: "You decide where your data lives",
      text: "Install Homayar on your own servers, or choose hybrid. Managed cloud is planned.",
    },
    trust: {
      eyebrow: "From day one",
      title: "Your customers' privacy, built into the product",
    },
  },

  palette: {
    eyebrow: "Next step",
    title: "See a real call report",
    placeholder: "Go to a page…",
    hint: "Pick one",
    actions: [
      { label: "Request a demo", meta: "We reply by e-mail and set up a short call", path: "/contact", kbd: "D" },
      { label: "How Homayar works", meta: "Step by step, from recording to report", path: "/how-it-works", kbd: "P" },
      { label: "Data security", meta: "Masking, isolation, deletion and egress control", path: "/security", kbd: "S" },
      { label: "Choose your setup", meta: "Self-hosted or hybrid; managed cloud planned", path: "/deployment", kbd: "C" },
    ],
  },

  theme: { toDark: "Switch to dark mode", toLight: "Switch to light mode" },

  loader: { loading: "Loading" },

  scene: {
    warp: {
      eyebrow: "Read, not replay",
      title: "Conversations analyzed after the call ends",
      text: "Get the summary and the action items, and decide without hearing the whole call.",
    },
    brain: {
      eyebrow: "A sample call",
      title: "From the customer's words to your report",
      text: "A simulation with sample data: a customer whose order is nine days late. The quality scores, root causes and coaching tip are planned.",
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
      { label: "Total calls", value: "1,284" },
      { label: "Avg QC score", value: "78", unit: "/ 100" },
      { label: "Open issues", value: "37" },
      { label: "Calls today", value: "96" },
    ],
    trend: "QC trend",
    sentiment: "Sentiment distribution",
    sentimentLabels: ["Positive", "Neutral", "Negative"],
    report: "Call report",
    qc: "Quality score",
    problems: "Root causes",
    insights: "Business insight",
    transcript: "Transcript",
    actions: "Recommended actions",
    exportPdf: "Export",
    scores: [
      { label: "Overall QC", value: 82 },
      { label: "Empathy", value: 88 },
      { label: "Problem solving", value: 74 },
      { label: "Clarity", value: 91 },
      { label: "Call closing", value: 79 },
      { label: "Silence handling", value: 70 },
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
    webhookTitle: "Phone system · planned",
    phoneTabs: ["Overview", "Calls", "Incidents", "Copilot"],
    agent: "Agent",
    duration: "Duration",
    language: "Language",
    languageValue: "Persian",
  },
};

export type Dictionary = typeof en;
export default en;
