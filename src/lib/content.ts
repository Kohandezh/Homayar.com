// Multilingual content for the Homayar marketing site.
// Persian (fa) is the default locale; fa and ar render right-to-left.

export type Locale = "fa" | "en" | "de" | "ar" | "fr" | "es";

export const LOCALES: Locale[] = ["fa", "en", "de", "ar", "fr", "es"];

export const DIR: Record<Locale, "rtl" | "ltr"> = {
  fa: "rtl",
  en: "ltr",
  de: "ltr",
  ar: "rtl",
  fr: "ltr",
  es: "ltr",
};

// Native language names shown in the language switcher.
export const LOCALE_NATIVE: Record<Locale, string> = {
  fa: "فارسی",
  en: "English",
  de: "Deutsch",
  ar: "العربية",
  fr: "Français",
  es: "Español",
};

type Feature = { title: string; body: string };
type Faq = { q: string; a: string };

export type Dict = {
  htmlTitle: string;
  metaDescription: string;
  langName: string; // label shown on the toggle to switch TO the other language
  nav: {
    home: string;
    about: string;
    services: string;
    why: string;
    contact: string;
  };
  phones: string[];
  cta: { demo: string; contact: string };
  hero: {
    title: string;
    paragraphs: string[];
    leadIn: string;
    bullets: string[];
    closing: string;
  };
  customers: {
    title: string;
    subtitle: string;
    logos: string[];
  };
  platform: {
    title: string;
    paragraphs: string[];
  };
  features: {
    title: string;
    items: Feature[];
  };
  process: {
    title: string;
    paragraphs: string[];
  };
  outcomes: {
    title: string;
    body: string;
  };
  industries: {
    title: string;
    items: Feature[];
  };
  finalCta: {
    title: string;
    body: string;
    button: string;
  };
  faq: {
    title: string;
    items: Faq[];
  };
  contact: {
    title: string;
    subtitle: string;
    firstName: string;
    lastName: string;
    mobile: string;
    email: string;
    message: string;
    submit: string;
    success: string;
    error: string;
  };
  footer: {
    brand: string;
    tagline: string;
    description: string;
    contactHeading: string;
    address: string;
    socialHeading: string;
    socials: { label: string; href: string }[];
    copyright: string;
  };
};

const fa: Dict = {
  htmlTitle: "همایار | پلتفرم هوش مکالمه",
  metaDescription:
    "همایار، پلتفرم هوش مکالمه (Conversation Intelligence) از کهن سیستم فردا؛ تحلیل هوشمند تماس‌ها، ایمیل‌ها و چت‌ها برای تبدیل گفت‌وگوهای سازمان شما به بینش کاربردی.",
  langName: "English",
  nav: {
    home: "صفحه اصلی",
    about: "همایار چیست",
    services: "خدمات",
    why: "چرا همایار",
    contact: "تماس با ما",
  },
  phones: ["021-26230054", "021-76222354"],
  cta: { demo: "درخواست دموی رایگان", contact: "تماس با ما" },
  hero: {
    title: "یک مکالمه، یا هزارانش. فرقی نمی‌کند. بینش قدرتمند در انتظار شماست.",
    paragraphs: [
      "سازمان شما هر روز در حال انجام مکالمات ارزشمندی است؛ گنجینه‌ای پنهان از داده که منتظر استخراج هستند. پلتفرم تحلیل هوشمند تماس همایار از کهن سیستم فردا، این گنج را برای شما آشکار می‌سازد.",
      "فرقی ندارد اندازه سازمان شما چقدر است. چه یک تماس برقرار کنید، چه هزاران. سیستم ما به‌راحتی و در هر ابعادی اجرا می‌شود و هر گفت‌وگو (از تماس تلفنی و ایمیل تا چت) را به بینشی کاربردی از نگاه کسب‌وکار شما تبدیل می‌کند.",
    ],
    leadIn: "ما به شما قدرت «درک» می‌دهیم، نه فقط «شنیدن». با همایار، شما:",
    bullets: [
      "نیازها و احساسات پنهان مشتریانتان را با وضوح بی‌سابقه‌ای درک می‌کنید.",
      "عملکرد تیم‌های خود را بر اساس داده‌های عینی و واقعی بهینه می‌سازید.",
      "تصمیمات استراتژیک خود را نه بر اساس حدس و گمان، که بر پایه خروجی‌های تحلیل‌شده همین مکالمات می‌گیرید.",
    ],
    closing:
      "به جمع پیشروترین سازمان‌های ایرانی بپیوندید که با هوش تحلیلی همایار، یک مزیت رقابتی پایدار خلق کرده و تعریف جدیدی از تجربه مشتری نوشته‌اند.",
  },
  customers: {
    title: "مشتریان کهن سیستم فردا",
    subtitle: "افتخار همکاری با شرکت‌های بزرگی مانند:",
    logos: ["hirbodan", "iozhen", "htsc", "hamkaran", "mftplus"],
  },
  platform: {
    title: "همایار، پلتفرم یکپارچه تحلیل هوشمند ارتباطات سازمانی",
    paragraphs: [
      "همایار تنها یک سیستم گزارش‌دهی نیست؛ او «هوش» سازمان شماست. این پلتفرم، به‌عنوان یک هوش مکالمه (Conversation Intelligence) پیشرفته، به‌صورت جامع تمامی کانال‌های ارتباطی کسب‌وکار—از مکالمات صوتی و ایمیل تا چت‌ها، پیام‌رسان‌ها و تیکت‌های پشتیبانی—را پردازش می‌کند و داده‌های خام و بدون ساختار را به بینش‌های کاربردی و قابل اجرا تبدیل می‌نماید.",
      "با همایار، گنجینه پنهان در مکالمات سازمانتان را کشف کنید و تمامی سطوح سازمان را با هوش مصنوعی توانمند سازید.",
    ],
  },
  features: {
    title: "ویژگی‌های همایار: از تحلیل کیفیت تا دستیار هوشمند اپراتور",
    items: [
      {
        title: "دستیار هوشمند و کنترل کیفیت",
        body: "با دستیار هوشمند، اپراتورها را راهنمایی و با کنترل کیفیت خودکار، عملکرد مرکز تماس را بسنجید.",
      },
      {
        title: "مدیریت هوشمند تماس‌ها",
        body: "با توزیع‌کننده هوشمند تماس ما، تماس‌های ورودی را به بهترین شکل ممکن مدیریت و هدایت کنید.",
      },
      {
        title: "ربات‌های پاسخگوی چندکاناله",
        body: "دسترسی به قابلیت‌های پیشرفته و راه‌حل‌های اختصاصی ما، منحصراً از طریق مشاوره تلفنی برای سازمان‌های واجد شرایط میسر است.",
      },
    ],
  },
  process: {
    title:
      "تکنولوژی پیشرفته، فرآیندی ساده: چگونه همایار داده‌ها را به بینش تبدیل می‌کند؟",
    paragraphs: [
      "قبل از هر چیز، تیم متخصص راه‌اندازی و تحلیل کسب‌وکار به سازمان شما می‌آید. این تیم برای درک عمیق فرآیندها، چالش‌ها و اهداف کلیدی شما زمان می‌گذارد. نتیجه این ممیزی تخصصی، پیکربندی کاملاً شخصی‌سازی‌شده‌ی پلتفرم همایار است تا خروجی‌ها دقیقاً با معیارهای موفقیت کسب‌وکار شما همخوانی داشته باشد.",
      "همایار با قابلیت استقرار ابری یا درون‌سازمانی، کاملاً با معماری IT شما سازگار است و امنیت آن در سطح استانداردهای جهانی تضمین می‌شود.",
    ],
  },
  outcomes: {
    title:
      "دستاورد همکاری با همایار: افزایش فروش، بهبود تجربه مشتری و تصمیم‌گیری داده‌محور",
    body: "استفاده از پلتفرم تحلیل هوشمند مکالمات، کنترل کیفیت اپراتورها یا به اختصار تحلیل هوشمند تماس، نتایج ملموس و مستقیمی برای کسب‌وکار شما به همراه دارد. با تحلیل دقیق مکالمات، می‌توانید نیازهای مشتریان را بهتر درک کرده و با ارائه پیشنهادهای مناسب، فروش خود را افزایش دهید. بهبود تجربه مشتری از طریق شناسایی نقاط ضعف در فرآیند پشتیبانی و افزایش رضایت آن‌ها (CSAT)، به وفادارسازی مشتریان منجر می‌شود. همچنین، با خودکارسازی فرآیندهای کنترل کیفیت و شناسایی ناکارآمدی‌ها، هزینه‌های عملیاتی مرکز تماس شما به طور قابل توجهی کاهش می‌یابد. در نهایت، مهم‌ترین دستاورد، توانمندسازی مدیران برای اتخاذ تصمیم‌های استراتژیک بر پایه داده‌های واقعی و قابل اعتماد از قلب ارتباطات سازمان است.",
  },
  industries: {
    title: "همایار، همراه هوشمند کسب‌وکار شما در صنایع مختلف",
    items: [
      {
        title: "مراکز مالی و صنعت بیمه",
        body: "با تحلیل مکالمات، رضایت مشتریان را سنجیده، انطباق با مقررات را تضمین و فرآیندهای فروش را بهینه کنید.",
      },
      {
        title: "فروشگاه‌ها و تجارت الکترونیک",
        body: "با درک نیازهای مشتریان از طریق تماس‌ها و چت‌ها، فروش خود را افزایش و تجربه خرید را بهبود بخشید.",
      },
      {
        title: "مراکز بهداشتی، درمانی و گردشگری",
        body: "کیفیت پاسخگویی به بیماران و مسافران را ارزیابی کرده و با تحلیل بازخوردها، خدمات خود را ارتقا دهید.",
      },
    ],
  },
  finalCta: {
    title: "آماده‌اید تا قدرت واقعی ارتباطات خود را کشف کنید؟",
    body: "اکنون که با پتانسیل پلتفرم تماس هوشمند همایار برای ایجاد تحولی بنیادین در کسب‌وکارتان آشنا شدید، زمان آن است که اولین گام را برای تصمیم‌گیری داده‌محور و ارتقای تجربه مشتری خود بردارید. تیم متخصصان ما در کهن سیستم فردا، که به سازمان‌های برتری چون مرکز تماس ابری همکاران خدمت‌رسانی کرده‌اند، آماده‌اند تا در یک جلسه دموی رایگان، به شما نشان دهند که چگونه همایار می‌تواند به طور خاص به رشد سازمان شما کمک کند. فرصت را برای کشف بینش‌های پنهان در مکالمات خود از دست ندهید و همین امروز برای رزرو دمو یا مشاوره رایگان با ما تماس بگیرید.",
    button: "رزرو دمو یا مشاوره رایگان",
  },
  faq: {
    title: "سوالات متداول درباره همایار",
    items: [
      {
        q: "آیا همایار فقط برای مراکز تماس بزرگ کاربرد دارد؟",
        a: "خیر، همایار برای سازمان‌ها در هر اندازه‌ای طراحی شده است. این پلتفرم به هر کسب‌وکاری که به دنبال درک بهتر مشتریان و بهینه‌سازی ارتباطات خود باشد، کمک می‌کند تا تصمیمات داده‌محور بگیرد.",
      },
      {
        q: "تفاوت اصلی بینش‌های همایار با گزارش‌های معمولی چیست؟",
        a: "گزارش‌های معمول تنها اعداد و آمار خام را نشان می‌دهند؛ اما همایار با درک محتوا و احساس نهفته در مکالمات، «چرایی» پشت داده‌ها را آشکار می‌کند و بینش‌هایی قابل اجرا در اختیار شما می‌گذارد.",
      },
      {
        q: "امنیت داده‌های حساس ما در فرآیند تحلیل چگونه تضمین می‌شود؟",
        a: "همایار با قابلیت استقرار درون‌سازمانی (On-Premise) و رعایت استانداردهای جهانی امنیت اطلاعات، تضمین می‌کند که داده‌های حساس شما هرگز از محیط امن سازمانتان خارج نشود.",
      },
      {
        q: "همایار چگونه با سیستم‌های تلفنی و CRM موجود ما یکپارچه می‌شود؟",
        a: "همایار به‌گونه‌ای طراحی شده که با سیستم‌های تلفنی (VoIP) و CRMهای رایج یکپارچه شود و فرآیند راه‌اندازی به‌صورت کامل توسط تیم متخصص ما انجام می‌گیرد.",
      },
    ],
  },
  contact: {
    title: "با ما در تماس باشید",
    subtitle:
      "فرم زیر را تکمیل کنید تا کارشناسان ما برای یک دموی رایگان با شما تماس بگیرند.",
    firstName: "نام",
    lastName: "نام خانوادگی",
    mobile: "شماره همراه",
    email: "ایمیل",
    message: "پیام",
    submit: "ارسال پیام",
    success: "پیام شما با موفقیت ثبت شد. به‌زودی با شما تماس می‌گیریم.",
    error: "ارسال پیام با خطا مواجه شد. لطفاً دوباره تلاش کنید.",
  },
  footer: {
    brand: "همایار",
    tagline: "همایار | یک پلتفرم هوش مکالمه",
    description:
      "این سیستم با استفاده از هوش مصنوعی، مکالمات صوتی مرکز تماس، ایمیل‌ها، چت‌های آنلاین، پیام‌رسان‌ها و تیکت‌های پشتیبانی را پردازش کرده و داده‌های بدون ساختار را به اطلاعاتی ارزشمند تبدیل می‌کند.",
    contactHeading: "ارتباط با ما",
    address:
      "منطقه بین‌المللی نوآوری ایران (پارک فناوری پردیس)، ساختمان فن‌بازار، طبقه یک، واحد ۱۱۰۴",
    socialHeading: "شبکه‌های اجتماعی",
    socials: [
      { label: "تلگرام", href: "#" },
      { label: "اینستاگرام", href: "#" },
      { label: "یوتیوب", href: "#" },
    ],
    copyright:
      "۱۴۰۴ © تمامی حقوق همایار برای شرکت کهن سیستم فردا محفوظ است و هرگونه کپی‌برداری پیگرد قانونی دارد.",
  },
};

const en: Dict = {
  htmlTitle: "Homayar | Conversation Intelligence Platform",
  metaDescription:
    "Homayar is a Conversation Intelligence platform by Kohan System Farda — intelligent analysis of calls, emails, and chats that turns your organization's conversations into actionable insight.",
  langName: "فارسی",
  nav: {
    home: "Home",
    about: "What is Homayar",
    services: "Services",
    why: "Why Homayar",
    contact: "Contact",
  },
  phones: ["021-26230054", "021-76222354"],
  cta: { demo: "Request a free demo", contact: "Contact us" },
  hero: {
    title:
      "One conversation, or a thousand. It makes no difference. Powerful insight awaits you.",
    paragraphs: [
      "Every day your organization holds valuable conversations — a hidden treasure of data waiting to be mined. Homayar's intelligent call-analysis platform, from Kohan System Farda, reveals that treasure for you.",
      "It doesn't matter how large your organization is. Whether you handle one call or thousands, our system scales effortlessly and turns every conversation — from phone calls and email to chat — into actionable insight from your business's point of view.",
    ],
    leadIn: "We give you the power to “understand,” not just to “hear.” With Homayar, you:",
    bullets: [
      "Understand your customers' hidden needs and emotions with unprecedented clarity.",
      "Optimize your teams' performance based on objective, real-world data.",
      "Make strategic decisions based not on guesswork, but on analyzed output from these very conversations.",
    ],
    closing:
      "Join the most forward-thinking Iranian organizations that have built a lasting competitive advantage with Homayar's analytical intelligence and rewritten the definition of customer experience.",
  },
  customers: {
    title: "Customers of Kohan System Farda",
    subtitle: "Proud to partner with leading companies such as:",
    logos: ["hirbodan", "iozhen", "htsc", "hamkaran", "mftplus"],
  },
  platform: {
    title:
      "Homayar — the unified platform for intelligent analysis of enterprise communications",
    paragraphs: [
      "Homayar is not merely a reporting system; it is the “intelligence” of your organization. As an advanced Conversation Intelligence platform, it comprehensively processes every business communication channel — from voice calls and email to chats, messengers, and support tickets — and transforms raw, unstructured data into practical, actionable insight.",
      "With Homayar, discover the hidden treasure within your organization's conversations and empower every level of your organization with AI.",
    ],
  },
  features: {
    title: "Homayar's features: from quality analysis to an intelligent agent assistant",
    items: [
      {
        title: "Intelligent assistant & quality control",
        body: "Guide your agents with an intelligent assistant and measure your call center's performance with automated quality control.",
      },
      {
        title: "Intelligent call management",
        body: "Manage and route incoming calls in the best possible way with our intelligent call distributor.",
      },
      {
        title: "Multichannel response bots",
        body: "Access to our advanced capabilities and bespoke solutions is available exclusively via phone consultation for qualifying organizations.",
      },
    ],
  },
  process: {
    title:
      "Advanced technology, a simple process: how does Homayar turn data into insight?",
    paragraphs: [
      "First, our specialist onboarding and business-analysis team comes to your organization. They invest the time to deeply understand your processes, challenges, and key goals. The result of this expert audit is a fully customized configuration of the Homayar platform, so its outputs align precisely with your business's success metrics.",
      "With cloud or on-premise deployment, Homayar fits seamlessly into your IT architecture, and its security is guaranteed at world-class standards.",
    ],
  },
  outcomes: {
    title:
      "The payoff of working with Homayar: higher sales, better customer experience, and data-driven decisions",
    body: "Using an intelligent conversation-analysis platform — agent quality control, or simply intelligent call analysis — delivers tangible, direct results for your business. By analyzing conversations precisely, you can better understand customer needs and, by making the right offers, increase your sales. Improving the customer experience by identifying weaknesses in the support process and raising satisfaction (CSAT) leads to greater customer loyalty. By automating quality-control processes and identifying inefficiencies, your call center's operating costs are significantly reduced. Ultimately, the most important payoff is empowering managers to make strategic decisions based on real, reliable data from the heart of the organization's communications.",
  },
  industries: {
    title: "Homayar — your business's intelligent companion across industries",
    items: [
      {
        title: "Financial institutions & insurance",
        body: "Measure customer satisfaction, ensure regulatory compliance, and optimize sales processes through conversation analysis.",
      },
      {
        title: "Retail & e-commerce",
        body: "Understand customer needs through calls and chats to grow sales and improve the shopping experience.",
      },
      {
        title: "Healthcare & tourism",
        body: "Evaluate the quality of responses to patients and travelers, and elevate your services by analyzing feedback.",
      },
    ],
  },
  finalCta: {
    title: "Ready to discover the true power of your communications?",
    body: "Now that you've seen how Homayar's intelligent call platform can fundamentally transform your business, it's time to take the first step toward data-driven decisions and a better customer experience. Our specialists at Kohan System Farda — who have served leading organizations such as the Hamkaran Cloud Contact Center — are ready to show you, in a free demo session, exactly how Homayar can help your organization grow. Don't miss the chance to uncover the insights hidden in your conversations — contact us today to book a free demo or consultation.",
    button: "Book a free demo or consultation",
  },
  faq: {
    title: "Frequently asked questions about Homayar",
    items: [
      {
        q: "Is Homayar only useful for large call centers?",
        a: "No. Homayar is designed for organizations of every size. It helps any business seeking to better understand its customers and optimize its communications to make data-driven decisions.",
      },
      {
        q: "What is the main difference between Homayar's insights and ordinary reports?",
        a: "Conventional reports show only raw numbers and statistics; Homayar understands the content and sentiment within conversations to reveal the “why” behind the data and hand you insights you can act on.",
      },
      {
        q: "How is the security of our sensitive data guaranteed during analysis?",
        a: "With on-premise deployment and adherence to world-class information-security standards, Homayar ensures your sensitive data never leaves your organization's secure environment.",
      },
      {
        q: "How does Homayar integrate with our existing telephony and CRM systems?",
        a: "Homayar is built to integrate with common telephony (VoIP) systems and CRMs, and the entire setup is handled end-to-end by our specialist team.",
      },
    ],
  },
  contact: {
    title: "Get in touch",
    subtitle:
      "Fill out the form below and our specialists will contact you for a free demo.",
    firstName: "First name",
    lastName: "Last name",
    mobile: "Mobile number",
    email: "Email",
    message: "Message",
    submit: "Send message",
    success: "Your message was submitted successfully. We'll be in touch soon.",
    error: "Something went wrong. Please try again.",
  },
  footer: {
    brand: "Homayar",
    tagline: "Homayar | A Conversation Intelligence platform",
    description:
      "Using artificial intelligence, this system processes call-center voice conversations, emails, online chats, messengers, and support tickets, transforming unstructured data into valuable information.",
    contactHeading: "Contact us",
    address:
      "Iran International Innovation Zone (Pardis Technology Park), Fan Bazaar Building, Floor 1, Unit 1104",
    socialHeading: "Social media",
    socials: [
      { label: "Telegram", href: "#" },
      { label: "Instagram", href: "#" },
      { label: "YouTube", href: "#" },
    ],
    copyright:
      "1404 © All rights reserved for Homayar by Kohan System Farda. Any reproduction is subject to legal prosecution.",
  },
};

const de: Dict = {
  "htmlTitle": "Homayar | Conversation-Intelligence-Plattform",
  "metaDescription": "Homayar ist eine Conversation-Intelligence-Plattform von Kohan System Farda – mit intelligenter Analyse von Anrufen, E-Mails und Chats, die die Gespräche Ihres Unternehmens in umsetzbare Erkenntnisse verwandelt.",
  "langName": "Deutsch",
  "nav": {
    "home": "Startseite",
    "about": "Was ist Homayar",
    "services": "Leistungen",
    "why": "Warum Homayar",
    "contact": "Kontakt"
  },
  "phones": [
    "021-26230054",
    "021-76222354"
  ],
  "cta": {
    "demo": "Kostenlose Demo anfordern",
    "contact": "Kontaktieren Sie uns"
  },
  "hero": {
    "title": "Ein Gespräch oder tausend – das macht keinen Unterschied. Aussagekräftige Erkenntnisse warten auf Sie.",
    "paragraphs": [
      "Tag für Tag führt Ihr Unternehmen wertvolle Gespräche – ein verborgener Datenschatz, der nur darauf wartet, gehoben zu werden. Homayars intelligente Plattform zur Anrufanalyse von Kohan System Farda macht diesen Schatz für Sie sichtbar.",
      "Dabei spielt es keine Rolle, wie groß Ihr Unternehmen ist. Ob Sie ein Gespräch oder Tausende abwickeln – unser System skaliert mühelos und verwandelt jedes Gespräch, vom Telefonanruf über E-Mail bis hin zum Chat, in umsetzbare Erkenntnisse aus der Perspektive Ihres Unternehmens."
    ],
    "leadIn": "Wir geben Ihnen die Fähigkeit zu „verstehen“, nicht nur zu „hören“. Mit Homayar können Sie:",
    "bullets": [
      "die verborgenen Bedürfnisse und Emotionen Ihrer Kunden mit beispielloser Klarheit erfassen.",
      "die Leistung Ihrer Teams auf Basis objektiver, realer Daten optimieren.",
      "strategische Entscheidungen nicht auf Vermutungen stützen, sondern auf den analysierten Ergebnissen eben dieser Gespräche."
    ],
    "closing": "Schließen Sie sich den zukunftsorientiertesten iranischen Unternehmen an, die sich mit der analytischen Intelligenz von Homayar einen nachhaltigen Wettbewerbsvorteil verschafft und den Begriff der Kundenerfahrung neu definiert haben."
  },
  "customers": {
    "title": "Kunden von Kohan System Farda",
    "subtitle": "Wir sind stolz darauf, mit führenden Unternehmen zusammenzuarbeiten, darunter:",
    "logos": [
      "hirbodan",
      "iozhen",
      "htsc",
      "hamkaran",
      "mftplus"
    ]
  },
  "platform": {
    "title": "Homayar – die einheitliche Plattform für die intelligente Analyse der Unternehmenskommunikation",
    "paragraphs": [
      "Homayar ist nicht bloß ein Reporting-System; es ist die „Intelligenz“ Ihres Unternehmens. Als fortschrittliche Conversation-Intelligence-Plattform verarbeitet sie umfassend jeden Kommunikationskanal Ihres Unternehmens – von Sprachanrufen und E-Mails über Chats und Messenger bis hin zu Support-Tickets – und verwandelt rohe, unstrukturierte Daten in praxisnahe, umsetzbare Erkenntnisse.",
      "Heben Sie mit Homayar den verborgenen Schatz in den Gesprächen Ihres Unternehmens und befähigen Sie jede Ebene Ihrer Organisation mit künstlicher Intelligenz."
    ]
  },
  "features": {
    "title": "Die Funktionen von Homayar: von der Qualitätsanalyse bis zum intelligenten Agenten-Assistenten",
    "items": [
      {
        "title": "Intelligenter Assistent & Qualitätssicherung",
        "body": "Unterstützen Sie Ihre Agenten mit einem intelligenten Assistenten und messen Sie die Leistung Ihres Callcenters mit automatisierter Qualitätssicherung."
      },
      {
        "title": "Intelligentes Anrufmanagement",
        "body": "Verwalten und leiten Sie eingehende Anrufe mit unserem intelligenten Anrufverteiler auf bestmögliche Weise."
      },
      {
        "title": "Multichannel-Antwortbots",
        "body": "Der Zugang zu unseren fortschrittlichen Funktionen und maßgeschneiderten Lösungen steht ausschließlich qualifizierten Unternehmen über eine telefonische Beratung offen."
      }
    ]
  },
  "process": {
    "title": "Fortschrittliche Technologie, einfacher Prozess: Wie verwandelt Homayar Daten in Erkenntnisse?",
    "paragraphs": [
      "Zunächst kommt unser spezialisiertes Team für Onboarding und Geschäftsanalyse zu Ihnen ins Unternehmen. Es nimmt sich die Zeit, Ihre Prozesse, Herausforderungen und zentralen Ziele genau zu verstehen. Das Ergebnis dieses fachkundigen Audits ist eine vollständig maßgeschneiderte Konfiguration der Homayar-Plattform, sodass ihre Ergebnisse exakt mit den Erfolgskennzahlen Ihres Unternehmens übereinstimmen.",
      "Mit Cloud- oder On-Premise-Bereitstellung fügt sich Homayar nahtlos in Ihre IT-Architektur ein, und seine Sicherheit wird nach internationalen Spitzenstandards gewährleistet."
    ]
  },
  "outcomes": {
    "title": "Der Nutzen der Zusammenarbeit mit Homayar: höhere Umsätze, bessere Kundenerfahrung und datengestützte Entscheidungen",
    "body": "Der Einsatz einer intelligenten Plattform zur Gesprächsanalyse – sei es zur Qualitätssicherung von Agenten oder einfach zur intelligenten Anrufanalyse – liefert greifbare, unmittelbare Ergebnisse für Ihr Unternehmen. Durch die präzise Analyse von Gesprächen verstehen Sie die Bedürfnisse Ihrer Kunden besser und können mit den passenden Angeboten Ihren Umsatz steigern. Die Verbesserung der Kundenerfahrung durch die Identifikation von Schwachstellen im Support-Prozess und die Steigerung der Zufriedenheit (CSAT) führt zu größerer Kundentreue. Durch die Automatisierung von Qualitätssicherungsprozessen und das Aufdecken von Ineffizienzen senken Sie zudem die Betriebskosten Ihres Callcenters erheblich. Der wichtigste Nutzen ist letztlich die Befähigung Ihrer Führungskräfte, strategische Entscheidungen auf Basis echter, verlässlicher Daten aus dem Herzen der Unternehmenskommunikation zu treffen."
  },
  "industries": {
    "title": "Homayar – der intelligente Begleiter Ihres Unternehmens in den unterschiedlichsten Branchen",
    "items": [
      {
        "title": "Finanzinstitute & Versicherungen",
        "body": "Messen Sie die Kundenzufriedenheit, gewährleisten Sie die Einhaltung gesetzlicher Vorgaben und optimieren Sie Vertriebsprozesse durch die Analyse von Gesprächen."
      },
      {
        "title": "Einzelhandel & E-Commerce",
        "body": "Verstehen Sie die Bedürfnisse Ihrer Kunden über Anrufe und Chats, um Ihren Umsatz zu steigern und das Einkaufserlebnis zu verbessern."
      },
      {
        "title": "Gesundheitswesen & Tourismus",
        "body": "Bewerten Sie die Qualität der Betreuung von Patienten und Reisenden und heben Sie Ihre Services durch die Analyse von Feedback auf ein neues Niveau."
      }
    ]
  },
  "finalCta": {
    "title": "Bereit, die wahre Kraft Ihrer Kommunikation zu entdecken?",
    "body": "Nachdem Sie nun gesehen haben, wie die intelligente Anrufplattform von Homayar Ihr Unternehmen grundlegend verändern kann, ist es an der Zeit, den ersten Schritt hin zu datengestützten Entscheidungen und einer besseren Kundenerfahrung zu gehen. Unsere Expertinnen und Experten bei Kohan System Farda – die bereits führende Organisationen wie das Hamkaran Cloud Contact Center betreut haben – zeigen Ihnen in einer kostenlosen Demo-Sitzung gerne, wie genau Homayar das Wachstum Ihres Unternehmens fördern kann. Verpassen Sie nicht die Gelegenheit, die in Ihren Gesprächen verborgenen Erkenntnisse zu erschließen – kontaktieren Sie uns noch heute, um eine kostenlose Demo oder Beratung zu vereinbaren.",
    "button": "Kostenlose Demo oder Beratung vereinbaren"
  },
  "faq": {
    "title": "Häufig gestellte Fragen zu Homayar",
    "items": [
      {
        "q": "Ist Homayar nur für große Callcenter sinnvoll?",
        "a": "Nein. Homayar ist für Unternehmen jeder Größe konzipiert. Es unterstützt jedes Unternehmen, das seine Kunden besser verstehen und seine Kommunikation optimieren möchte, dabei, datengestützte Entscheidungen zu treffen."
      },
      {
        "q": "Was ist der wesentliche Unterschied zwischen den Erkenntnissen von Homayar und herkömmlichen Berichten?",
        "a": "Herkömmliche Berichte zeigen nur rohe Zahlen und Statistiken; Homayar hingegen erfasst Inhalt und Stimmung innerhalb der Gespräche, deckt das „Warum“ hinter den Daten auf und liefert Ihnen Erkenntnisse, auf die Sie unmittelbar reagieren können."
      },
      {
        "q": "Wie wird die Sicherheit unserer sensiblen Daten während der Analyse gewährleistet?",
        "a": "Mit der On-Premise-Bereitstellung und der Einhaltung internationaler Standards der Informationssicherheit stellt Homayar sicher, dass Ihre sensiblen Daten niemals die geschützte Umgebung Ihres Unternehmens verlassen."
      },
      {
        "q": "Wie lässt sich Homayar in unsere bestehenden Telefonie- und CRM-Systeme integrieren?",
        "a": "Homayar ist darauf ausgelegt, sich in gängige Telefonie-Systeme (VoIP) und CRMs zu integrieren, und die gesamte Einrichtung wird durchgängig von unserem Fachteam übernommen."
      }
    ]
  },
  "contact": {
    "title": "Nehmen Sie Kontakt auf",
    "subtitle": "Füllen Sie das untenstehende Formular aus, und unsere Expertinnen und Experten melden sich bei Ihnen für eine kostenlose Demo.",
    "firstName": "Vorname",
    "lastName": "Nachname",
    "mobile": "Mobilnummer",
    "email": "E-Mail",
    "message": "Nachricht",
    "submit": "Nachricht senden",
    "success": "Ihre Nachricht wurde erfolgreich übermittelt. Wir melden uns in Kürze bei Ihnen.",
    "error": "Es ist ein Fehler aufgetreten. Bitte versuchen Sie es erneut."
  },
  "footer": {
    "brand": "Homayar",
    "tagline": "Homayar | Eine Conversation-Intelligence-Plattform",
    "description": "Mithilfe künstlicher Intelligenz verarbeitet dieses System Sprachgespräche im Callcenter, E-Mails, Online-Chats, Messenger und Support-Tickets und verwandelt unstrukturierte Daten in wertvolle Informationen.",
    "contactHeading": "Kontaktieren Sie uns",
    "address": "Iran International Innovation Zone (Pardis Technology Park), Fan-Bazaar-Gebäude, 1. Etage, Einheit 1104",
    "socialHeading": "Soziale Medien",
    "socials": [
      {
        "label": "Telegram",
        "href": "#"
      },
      {
        "label": "Instagram",
        "href": "#"
      },
      {
        "label": "YouTube",
        "href": "#"
      }
    ],
    "copyright": "1404 © Alle Rechte an Homayar vorbehalten für Kohan System Farda. Jegliche Vervielfältigung wird rechtlich verfolgt."
  }
};

const ar: Dict = {
  "htmlTitle": "هومايار | منصة ذكاء المحادثات",
  "metaDescription": "هومايار هي منصة ذكاء المحادثات (Conversation Intelligence) من شركة كهن سيستم فردا — تحليل ذكي للمكالمات ورسائل البريد الإلكتروني والمحادثات يحوّل محادثات مؤسستك إلى رؤى قابلة للتنفيذ.",
  "langName": "العربية",
  "nav": {
    "home": "الرئيسية",
    "about": "ما هي هومايار",
    "services": "الخدمات",
    "why": "لماذا هومايار",
    "contact": "تواصل معنا"
  },
  "phones": [
    "021-26230054",
    "021-76222354"
  ],
  "cta": {
    "demo": "اطلب عرضاً توضيحياً مجانياً",
    "contact": "تواصل معنا"
  },
  "hero": {
    "title": "محادثة واحدة أو ألف محادثة، لا فرق؛ رؤى قوية في انتظارك.",
    "paragraphs": [
      "تُجري مؤسستك كل يوم محادثات قيّمة — كنزٌ خفيٌّ من البيانات في انتظار استخراجه. منصة تحليل المكالمات الذكية من هومايار، التابعة لشركة كهن سيستم فردا، تكشف لك هذا الكنز.",
      "لا يهم حجم مؤسستك. سواء كنت تتعامل مع مكالمة واحدة أو آلاف المكالمات، يتوسّع نظامنا بسلاسة تامة ويحوّل كل محادثة — من المكالمات الهاتفية والبريد الإلكتروني إلى المحادثات النصية — إلى رؤى قابلة للتنفيذ من منظور أعمالك."
    ],
    "leadIn": "نمنحك القدرة على «الفهم»، لا مجرد «السماع». مع هومايار، أنت:",
    "bullets": [
      "تفهم احتياجات عملائك ومشاعرهم الخفية بوضوحٍ غير مسبوق.",
      "تُحسّن أداء فِرَقك استناداً إلى بيانات موضوعية وواقعية.",
      "تتخذ قراراتك الاستراتيجية لا بناءً على التخمين، بل بناءً على مُخرجاتٍ مُحلَّلة من هذه المحادثات نفسها."
    ],
    "closing": "انضمّ إلى أكثر المؤسسات الإيرانية تطلّعاً نحو المستقبل، التي بنت ميزةً تنافسيةً دائمة بفضل الذكاء التحليلي من هومايار وأعادت تعريف تجربة العميل."
  },
  "customers": {
    "title": "عملاء كهن سيستم فردا",
    "subtitle": "نفخر بالشراكة مع شركات رائدة مثل:",
    "logos": [
      "hirbodan",
      "iozhen",
      "htsc",
      "hamkaran",
      "mftplus"
    ]
  },
  "platform": {
    "title": "هومايار — المنصة الموحَّدة للتحليل الذكي لاتصالات المؤسسات",
    "paragraphs": [
      "هومايار ليست مجرد نظام لإعداد التقارير؛ بل هي «ذكاء» مؤسستك. وبصفتها منصة ذكاء محادثات (Conversation Intelligence) متقدّمة، تُعالج بشكل شامل كل قنوات الاتصال في أعمالك — من المكالمات الصوتية والبريد الإلكتروني إلى المحادثات النصية وتطبيقات المراسلة وتذاكر الدعم — وتحوّل البيانات الخام غير المُنظَّمة إلى رؤى عملية قابلة للتنفيذ.",
      "مع هومايار، اكتشف الكنز الخفي داخل محادثات مؤسستك ومكّن كل مستوى من مستويات مؤسستك بالذكاء الاصطناعي."
    ]
  },
  "features": {
    "title": "مزايا هومايار: من تحليل الجودة إلى مساعد ذكي لوكيل مركز الاتصال",
    "items": [
      {
        "title": "المساعد الذكي وضبط الجودة",
        "body": "وجِّه وكلاء مركز الاتصال لديك عبر مساعد ذكي، وقِس أداء مركز الاتصال من خلال ضبط جودة آلي."
      },
      {
        "title": "الإدارة الذكية للمكالمات",
        "body": "أدِر المكالمات الواردة ووجِّهها بأفضل طريقة ممكنة بفضل موزِّع المكالمات الذكي لدينا."
      },
      {
        "title": "روبوتات الرد متعددة القنوات",
        "body": "يتوفّر الوصول إلى قدراتنا المتقدمة وحلولنا المُخصَّصة حصرياً عبر استشارة هاتفية للمؤسسات المؤهَّلة."
      }
    ]
  },
  "process": {
    "title": "تقنية متقدمة، عملية بسيطة: كيف تحوّل هومايار البيانات إلى رؤى؟",
    "paragraphs": [
      "في البداية، يحضر إلى مؤسستك فريقنا المتخصص في الإعداد وتحليل الأعمال. يخصّص هذا الفريق الوقت اللازم لفهم عملياتك وتحدياتك وأهدافك الرئيسية فهماً عميقاً. ونتيجة هذا التدقيق المتخصص هي تهيئة مُخصَّصة بالكامل لمنصة هومايار، بحيث تتوافق مُخرجاتها تماماً مع معايير نجاح أعمالك.",
      "بفضل إمكانية النشر السحابي أو الداخلي (On-Premise)، تتكامل هومايار بسلاسة مع بنيتك التقنية، ويُضمَن أمانها وفق معايير عالمية المستوى."
    ]
  },
  "outcomes": {
    "title": "ثمار العمل مع هومايار: مبيعات أعلى، وتجربة عملاء أفضل، وقرارات قائمة على البيانات",
    "body": "إنّ استخدام منصة تحليل محادثات ذكية — سواء لمراقبة جودة أداء الوكلاء أو ببساطة للتحليل الذكي للمكالمات — يحقّق نتائج ملموسة ومباشرة لأعمالك. فمن خلال تحليل المحادثات بدقة، يمكنك فهم احتياجات العملاء على نحوٍ أفضل، وزيادة مبيعاتك عبر تقديم العروض المناسبة. كما يؤدي تحسين تجربة العميل من خلال تحديد نقاط الضعف في عملية الدعم ورفع مستوى الرضا (CSAT) إلى تعزيز ولاء العملاء. وبفضل أتمتة عمليات ضبط الجودة وتحديد أوجه القصور، تنخفض التكاليف التشغيلية لمركز الاتصال لديك انخفاضاً ملحوظاً. وفي النهاية، تتمثّل أهم الثمار في تمكين المديرين من اتخاذ قرارات استراتيجية مبنية على بيانات حقيقية وموثوقة من صميم اتصالات المؤسسة."
  },
  "industries": {
    "title": "هومايار — رفيق أعمالك الذكي عبر مختلف القطاعات",
    "items": [
      {
        "title": "المؤسسات المالية وقطاع التأمين",
        "body": "قِس رضا العملاء، واضمن الامتثال للوائح التنظيمية، وحسِّن عمليات المبيعات من خلال تحليل المحادثات."
      },
      {
        "title": "المتاجر والتجارة الإلكترونية",
        "body": "افهم احتياجات العملاء عبر المكالمات والمحادثات النصية لزيادة المبيعات وتحسين تجربة التسوق."
      },
      {
        "title": "الرعاية الصحية والسياحة",
        "body": "قيِّم جودة الاستجابة للمرضى والمسافرين، وارتقِ بخدماتك من خلال تحليل الملاحظات."
      }
    ]
  },
  "finalCta": {
    "title": "هل أنت مستعدّ لاكتشاف القوة الحقيقية لاتصالاتك؟",
    "body": "الآن وقد رأيت كيف يمكن لمنصة المكالمات الذكية من هومايار أن تُحدث تحوّلاً جذرياً في أعمالك، حان الوقت لاتخاذ الخطوة الأولى نحو قرارات قائمة على البيانات وتجربة عملاء أفضل. إنّ خبراءنا في كهن سيستم فردا — الذين خدموا مؤسسات رائدة مثل مركز الاتصال السحابي «همكاران» — على استعداد ليُروك، في جلسة عرض توضيحي مجانية، كيف يمكن لهومايار تحديداً أن تساعد مؤسستك على النمو. لا تُفوّت فرصة الكشف عن الرؤى الخفية في محادثاتك — تواصل معنا اليوم لحجز عرض توضيحي أو استشارة مجانية.",
    "button": "احجز عرضاً توضيحياً أو استشارة مجانية"
  },
  "faq": {
    "title": "الأسئلة الشائعة حول هومايار",
    "items": [
      {
        "q": "هل هومايار مفيدة لمراكز الاتصال الكبيرة فقط؟",
        "a": "لا. صُمِّمت هومايار للمؤسسات بمختلف أحجامها. فهي تساعد أيّ نشاط تجاري يسعى إلى فهم عملائه على نحوٍ أفضل وتحسين اتصالاته على اتخاذ قرارات قائمة على البيانات."
      },
      {
        "q": "ما الفرق الجوهري بين رؤى هومايار والتقارير التقليدية؟",
        "a": "تُظهر التقارير التقليدية أرقاماً وإحصاءات خام فحسب؛ أمّا هومايار فتفهم المحتوى والمشاعر الكامنة داخل المحادثات لتكشف «السبب» وراء البيانات وتمنحك رؤى قابلة للتنفيذ."
      },
      {
        "q": "كيف يُضمَن أمان بياناتنا الحسّاسة أثناء عملية التحليل؟",
        "a": "بفضل إمكانية النشر الداخلي (On-Premise) والالتزام بمعايير أمن المعلومات عالمية المستوى، تضمن هومايار ألّا تغادر بياناتك الحسّاسة بيئة مؤسستك الآمنة أبداً."
      },
      {
        "q": "كيف تتكامل هومايار مع أنظمة الهاتف وأنظمة إدارة علاقات العملاء (CRM) الحالية لدينا؟",
        "a": "صُمِّمت هومايار لتتكامل مع أنظمة الهاتف الشائعة (VoIP) وأنظمة إدارة علاقات العملاء (CRM)، ويتولّى فريقنا المتخصص عملية الإعداد بالكامل من البداية إلى النهاية."
      }
    ]
  },
  "contact": {
    "title": "تواصل معنا",
    "subtitle": "املأ النموذج أدناه وسيتواصل معك خبراؤنا لتقديم عرض توضيحي مجاني.",
    "firstName": "الاسم الأول",
    "lastName": "اسم العائلة",
    "mobile": "رقم الهاتف المحمول",
    "email": "البريد الإلكتروني",
    "message": "الرسالة",
    "submit": "إرسال الرسالة",
    "success": "تمّ إرسال رسالتك بنجاح. سنتواصل معك قريباً.",
    "error": "حدث خطأٌ ما. يُرجى المحاولة مرة أخرى."
  },
  "footer": {
    "brand": "هومايار",
    "tagline": "هومايار | منصة ذكاء المحادثات",
    "description": "باستخدام الذكاء الاصطناعي، يُعالج هذا النظام المكالمات الصوتية لمركز الاتصال ورسائل البريد الإلكتروني والمحادثات النصية عبر الإنترنت وتطبيقات المراسلة وتذاكر الدعم، محوّلاً البيانات غير المُنظَّمة إلى معلومات قيّمة.",
    "contactHeading": "تواصل معنا",
    "address": "المنطقة الدولية للابتكار في إيران (مجمّع برديس للتقنية)، مبنى فن بازار، الطابق الأول، الوحدة ١١٠٤",
    "socialHeading": "وسائل التواصل الاجتماعي",
    "socials": [
      {
        "label": "تيليجرام",
        "href": "#"
      },
      {
        "label": "إنستغرام",
        "href": "#"
      },
      {
        "label": "يوتيوب",
        "href": "#"
      }
    ],
    "copyright": "١٤٠٤ © جميع الحقوق محفوظة لهومايار لدى شركة كهن سيستم فردا، وأيُّ نسخٍ غير مصرَّح به يُعرّض مرتكبه للملاحقة القانونية."
  }
};

const fr: Dict = {
  "htmlTitle": "Homayar | Plateforme d'intelligence conversationnelle (Conversation Intelligence)",
  "metaDescription": "Homayar est une plateforme d'intelligence conversationnelle (Conversation Intelligence) proposée par Kohan System Farda : l'analyse intelligente des appels, des e-mails et des conversations qui transforme les échanges de votre organisation en informations exploitables.",
  "langName": "Français",
  "nav": {
    "home": "Accueil",
    "about": "Qu'est-ce que Homayar",
    "services": "Services",
    "why": "Pourquoi Homayar",
    "contact": "Contact"
  },
  "phones": [
    "021-26230054",
    "021-76222354"
  ],
  "cta": {
    "demo": "Demander une démo gratuite",
    "contact": "Contactez-nous"
  },
  "hero": {
    "title": "Une conversation, ou un millier : aucune différence. Une analyse puissante vous attend.",
    "paragraphs": [
      "Chaque jour, votre organisation mène des conversations précieuses, un trésor de données caché qui ne demande qu'à être exploité. La plateforme d'analyse intelligente des appels Homayar, signée Kohan System Farda, révèle ce trésor pour vous.",
      "Peu importe la taille de votre organisation. Que vous traitiez un seul appel ou des milliers, notre système monte en charge sans effort et transforme chaque conversation, des appels téléphoniques et e-mails jusqu'aux messageries, en informations exploitables, du point de vue de votre activité."
    ],
    "leadIn": "Nous vous donnons le pouvoir de « comprendre », et pas seulement d'« entendre ». Avec Homayar, vous :",
    "bullets": [
      "comprenez les besoins et les émotions cachés de vos clients avec une clarté sans précédent ;",
      "optimisez la performance de vos équipes sur la base de données objectives, ancrées dans la réalité ;",
      "prenez vos décisions stratégiques non pas au jugé, mais à partir des résultats analysés issus de ces conversations mêmes."
    ],
    "closing": "Rejoignez les organisations iraniennes les plus avant-gardistes, qui ont bâti un avantage concurrentiel durable grâce à l'intelligence analytique de Homayar et redéfini l'expérience client."
  },
  "customers": {
    "title": "Les clients de Kohan System Farda",
    "subtitle": "Fiers de collaborer avec des entreprises de premier plan telles que :",
    "logos": [
      "hirbodan",
      "iozhen",
      "htsc",
      "hamkaran",
      "mftplus"
    ]
  },
  "platform": {
    "title": "Homayar, la plateforme unifiée d'analyse intelligente des communications d'entreprise",
    "paragraphs": [
      "Homayar n'est pas un simple système de reporting : c'est l'« intelligence » de votre organisation. En tant que plateforme avancée d'intelligence conversationnelle (Conversation Intelligence), elle traite de façon globale chaque canal de communication de l'entreprise, des appels vocaux et e-mails jusqu'aux conversations, messageries et tickets de support, et transforme des données brutes et non structurées en informations concrètes et exploitables.",
      "Avec Homayar, découvrez le trésor caché au cœur des conversations de votre organisation et donnez à chacun de ses niveaux la puissance de l'intelligence artificielle."
    ]
  },
  "features": {
    "title": "Les fonctionnalités de Homayar : de l'analyse de la qualité à l'assistant intelligent pour vos agents",
    "items": [
      {
        "title": "Assistant intelligent et contrôle qualité",
        "body": "Guidez vos agents grâce à un assistant intelligent et mesurez la performance de votre centre d'appels avec un contrôle qualité automatisé."
      },
      {
        "title": "Gestion intelligente des appels",
        "body": "Gérez et acheminez les appels entrants de la meilleure façon possible grâce à notre distributeur d'appels intelligent."
      },
      {
        "title": "Robots de réponse multicanaux",
        "body": "L'accès à nos fonctionnalités avancées et à nos solutions sur mesure est réservé exclusivement aux organisations éligibles, sur consultation téléphonique."
      }
    ]
  },
  "process": {
    "title": "Une technologie de pointe, un processus simple : comment Homayar transforme-t-il les données en informations exploitables ?",
    "paragraphs": [
      "Tout d'abord, notre équipe spécialisée en déploiement et en analyse métier intervient au sein de votre organisation. Elle prend le temps de comprendre en profondeur vos processus, vos enjeux et vos objectifs clés. Le résultat de cet audit d'experts est une configuration entièrement personnalisée de la plateforme Homayar, afin que ses résultats correspondent précisément à vos indicateurs de réussite.",
      "Disponible en déploiement cloud ou sur site (On-Premise), Homayar s'intègre parfaitement à votre architecture informatique, et sa sécurité est garantie selon les standards les plus exigeants au niveau mondial."
    ]
  },
  "outcomes": {
    "title": "Les bénéfices d'une collaboration avec Homayar : davantage de ventes, une meilleure expérience client et des décisions fondées sur les données",
    "body": "L'utilisation d'une plateforme d'analyse intelligente des conversations, qu'il s'agisse du contrôle qualité des agents ou, plus simplement, de l'analyse intelligente des appels, apporte des résultats tangibles et directs pour votre activité. En analysant précisément les conversations, vous comprenez mieux les besoins de vos clients et, en formulant les bonnes offres, vous augmentez vos ventes. L'amélioration de l'expérience client, par l'identification des points faibles du processus de support et l'accroissement de la satisfaction (CSAT), favorise une plus grande fidélité de la clientèle. En automatisant les processus de contrôle qualité et en repérant les inefficacités, vous réduisez de façon significative les coûts d'exploitation de votre centre d'appels. Enfin, le bénéfice le plus important reste de permettre aux dirigeants de prendre des décisions stratégiques fondées sur des données réelles et fiables, issues du cœur même des communications de l'organisation."
  },
  "industries": {
    "title": "Homayar, le partenaire intelligent de votre activité dans tous les secteurs",
    "items": [
      {
        "title": "Établissements financiers et secteur de l'assurance",
        "body": "Mesurez la satisfaction client, garantissez la conformité réglementaire et optimisez vos processus de vente grâce à l'analyse des conversations."
      },
      {
        "title": "Commerce de détail et e-commerce",
        "body": "Comprenez les besoins de vos clients à travers les appels et les conversations pour développer vos ventes et améliorer l'expérience d'achat."
      },
      {
        "title": "Santé et tourisme",
        "body": "Évaluez la qualité des réponses apportées aux patients et aux voyageurs, et rehaussez vos services grâce à l'analyse des retours."
      }
    ]
  },
  "finalCta": {
    "title": "Prêt à découvrir la véritable puissance de vos communications ?",
    "body": "Maintenant que vous avez vu comment la plateforme d'appels intelligents Homayar peut transformer en profondeur votre activité, il est temps de franchir le premier pas vers des décisions fondées sur les données et une meilleure expérience client. Nos spécialistes chez Kohan System Farda, qui ont accompagné des organisations de premier plan telles que le centre de contact cloud Hamkaran, sont prêts à vous montrer, lors d'une session de démonstration gratuite, comment Homayar peut concrètement contribuer à la croissance de votre organisation. Ne manquez pas l'occasion de révéler les informations cachées dans vos conversations : contactez-nous dès aujourd'hui pour réserver une démo ou une consultation gratuite.",
    "button": "Réserver une démo ou une consultation gratuite"
  },
  "faq": {
    "title": "Questions fréquentes sur Homayar",
    "items": [
      {
        "q": "Homayar n'est-il utile que pour les grands centres d'appels ?",
        "a": "Non. Homayar est conçu pour des organisations de toutes tailles. Il aide toute entreprise souhaitant mieux comprendre ses clients et optimiser ses communications à prendre des décisions fondées sur les données."
      },
      {
        "q": "Quelle est la principale différence entre les analyses de Homayar et les rapports classiques ?",
        "a": "Les rapports classiques ne montrent que des chiffres et des statistiques bruts ; Homayar, lui, comprend le contenu et le ressenti au sein des conversations pour révéler le « pourquoi » derrière les données et vous fournir des informations sur lesquelles vous pouvez agir."
      },
      {
        "q": "Comment la sécurité de nos données sensibles est-elle garantie lors de l'analyse ?",
        "a": "Grâce à un déploiement sur site (On-Premise) et au respect des standards mondiaux en matière de sécurité de l'information, Homayar garantit que vos données sensibles ne quittent jamais l'environnement sécurisé de votre organisation."
      },
      {
        "q": "Comment Homayar s'intègre-t-il à nos systèmes de téléphonie et CRM existants ?",
        "a": "Homayar est conçu pour s'intégrer aux systèmes de téléphonie (VoIP) courants et aux CRM, et l'ensemble de la mise en place est pris en charge de bout en bout par notre équipe de spécialistes."
      }
    ]
  },
  "contact": {
    "title": "Contactez-nous",
    "subtitle": "Remplissez le formulaire ci-dessous et nos spécialistes vous contacteront pour une démo gratuite.",
    "firstName": "Prénom",
    "lastName": "Nom",
    "mobile": "Numéro de mobile",
    "email": "E-mail",
    "message": "Message",
    "submit": "Envoyer le message",
    "success": "Votre message a bien été envoyé. Nous vous contacterons très prochainement.",
    "error": "Une erreur s'est produite. Veuillez réessayer."
  },
  "footer": {
    "brand": "Homayar",
    "tagline": "Homayar | Une plateforme d'intelligence conversationnelle (Conversation Intelligence)",
    "description": "Grâce à l'intelligence artificielle, ce système traite les conversations vocales du centre d'appels, les e-mails, les conversations en ligne, les messageries et les tickets de support, transformant des données non structurées en informations précieuses.",
    "contactHeading": "Contactez-nous",
    "address": "Zone internationale d'innovation d'Iran (Parc technologique de Pardis), bâtiment Fan Bazaar, 1er étage, unité 1104",
    "socialHeading": "Réseaux sociaux",
    "socials": [
      {
        "label": "Telegram",
        "href": "#"
      },
      {
        "label": "Instagram",
        "href": "#"
      },
      {
        "label": "YouTube",
        "href": "#"
      }
    ],
    "copyright": "1404 © Tous droits réservés à Homayar par Kohan System Farda. Toute reproduction est passible de poursuites judiciaires."
  }
};

const es: Dict = {
  "htmlTitle": "Homayar | Plataforma de Inteligencia Conversacional",
  "metaDescription": "Homayar es una plataforma de Inteligencia Conversacional (Conversation Intelligence) de Kohan System Farda: análisis inteligente de llamadas, correos electrónicos y chats que convierte las conversaciones de tu organización en información práctica y accionable.",
  "langName": "فارسی",
  "nav": {
    "home": "Inicio",
    "about": "Qué es Homayar",
    "services": "Servicios",
    "why": "Por qué Homayar",
    "contact": "Contacto"
  },
  "phones": [
    "021-26230054",
    "021-76222354"
  ],
  "cta": {
    "demo": "Solicita una demo gratuita",
    "contact": "Contáctanos"
  },
  "hero": {
    "title": "Una conversación, o mil. No hay diferencia. Un conocimiento poderoso te espera.",
    "paragraphs": [
      "Cada día tu organización mantiene conversaciones valiosas: un tesoro oculto de datos que espera ser extraído. La plataforma de análisis inteligente de llamadas de Homayar, de Kohan System Farda, revela ese tesoro para ti.",
      "No importa el tamaño de tu organización. Ya gestiones una llamada o miles, nuestro sistema escala sin esfuerzo y convierte cada conversación —desde llamadas telefónicas y correos electrónicos hasta chats— en información accionable desde la perspectiva de tu negocio."
    ],
    "leadIn": "Te damos el poder de «comprender», no solo de «escuchar». Con Homayar, tú:",
    "bullets": [
      "Comprendes las necesidades y emociones ocultas de tus clientes con una claridad sin precedentes.",
      "Optimizas el rendimiento de tus equipos a partir de datos objetivos y reales.",
      "Tomas decisiones estratégicas no basadas en conjeturas, sino en los resultados analizados de estas mismas conversaciones."
    ],
    "closing": "Únete a las organizaciones iraníes más innovadoras que han construido una ventaja competitiva duradera con la inteligencia analítica de Homayar y han reescrito la definición de la experiencia del cliente."
  },
  "customers": {
    "title": "Clientes de Kohan System Farda",
    "subtitle": "Orgullosos de colaborar con empresas líderes como:",
    "logos": [
      "hirbodan",
      "iozhen",
      "htsc",
      "hamkaran",
      "mftplus"
    ]
  },
  "platform": {
    "title": "Homayar: la plataforma unificada para el análisis inteligente de las comunicaciones empresariales",
    "paragraphs": [
      "Homayar no es un simple sistema de informes; es la «inteligencia» de tu organización. Como plataforma avanzada de Inteligencia Conversacional (Conversation Intelligence), procesa de forma integral cada canal de comunicación del negocio —desde llamadas de voz y correos electrónicos hasta chats, mensajería y tickets de soporte— y transforma los datos brutos y no estructurados en información práctica y accionable.",
      "Con Homayar, descubre el tesoro oculto en las conversaciones de tu organización y potencia con inteligencia artificial todos los niveles de tu empresa."
    ]
  },
  "features": {
    "title": "Las funciones de Homayar: del análisis de calidad a un asistente inteligente para el agente",
    "items": [
      {
        "title": "Asistente inteligente y control de calidad",
        "body": "Guía a tus agentes con un asistente inteligente y mide el rendimiento de tu centro de contacto con un control de calidad automatizado."
      },
      {
        "title": "Gestión inteligente de llamadas",
        "body": "Gestiona y enruta las llamadas entrantes de la mejor manera posible con nuestro distribuidor inteligente de llamadas."
      },
      {
        "title": "Bots de respuesta multicanal",
        "body": "El acceso a nuestras capacidades avanzadas y soluciones a medida está disponible exclusivamente mediante consulta telefónica para las organizaciones que cumplan los requisitos."
      }
    ]
  },
  "process": {
    "title": "Tecnología avanzada, un proceso sencillo: ¿cómo convierte Homayar los datos en información?",
    "paragraphs": [
      "Primero, nuestro equipo especializado de implementación y análisis de negocio visita tu organización. Dedican el tiempo necesario para comprender en profundidad tus procesos, desafíos y objetivos clave. El resultado de esta auditoría experta es una configuración totalmente personalizada de la plataforma Homayar, de modo que sus resultados se alineen con precisión con los indicadores de éxito de tu negocio.",
      "Con despliegue en la nube o en local (on-premise), Homayar se integra a la perfección en tu arquitectura de TI y su seguridad está garantizada conforme a estándares de clase mundial."
    ]
  },
  "outcomes": {
    "title": "Los beneficios de trabajar con Homayar: más ventas, mejor experiencia del cliente y decisiones basadas en datos",
    "body": "Utilizar una plataforma inteligente de análisis de conversaciones —control de calidad de los agentes o, simplemente, análisis inteligente de llamadas— aporta resultados tangibles y directos a tu negocio. Al analizar las conversaciones con precisión, puedes comprender mejor las necesidades de los clientes y, al hacer las ofertas adecuadas, aumentar tus ventas. Mejorar la experiencia del cliente identificando las debilidades del proceso de soporte y elevando la satisfacción (CSAT) conduce a una mayor fidelización. Al automatizar los procesos de control de calidad e identificar las ineficiencias, los costes operativos de tu centro de contacto se reducen de forma notable. En última instancia, el resultado más importante es capacitar a los directivos para tomar decisiones estratégicas a partir de datos reales y fiables procedentes del corazón de las comunicaciones de la organización."
  },
  "industries": {
    "title": "Homayar: el aliado inteligente de tu negocio en todos los sectores",
    "items": [
      {
        "title": "Instituciones financieras y seguros",
        "body": "Mide la satisfacción del cliente, garantiza el cumplimiento normativo y optimiza los procesos de venta mediante el análisis de conversaciones."
      },
      {
        "title": "Comercio minorista y comercio electrónico",
        "body": "Comprende las necesidades de los clientes a través de llamadas y chats para aumentar las ventas y mejorar la experiencia de compra."
      },
      {
        "title": "Sanidad y turismo",
        "body": "Evalúa la calidad de la atención a pacientes y viajeros y eleva tus servicios analizando los comentarios recibidos."
      }
    ]
  },
  "finalCta": {
    "title": "¿Listo para descubrir el verdadero poder de tus comunicaciones?",
    "body": "Ahora que has visto cómo la plataforma inteligente de llamadas de Homayar puede transformar tu negocio de raíz, es el momento de dar el primer paso hacia decisiones basadas en datos y una mejor experiencia del cliente. Nuestros especialistas en Kohan System Farda —que han prestado servicio a organizaciones líderes como el Centro de Contacto en la Nube de Hamkaran— están listos para mostrarte, en una sesión de demo gratuita, exactamente cómo Homayar puede ayudar a crecer a tu organización. No pierdas la oportunidad de descubrir la información oculta en tus conversaciones: contáctanos hoy mismo para reservar una demo o consulta gratuita.",
    "button": "Reserva una demo o consulta gratuita"
  },
  "faq": {
    "title": "Preguntas frecuentes sobre Homayar",
    "items": [
      {
        "q": "¿Homayar solo resulta útil para grandes centros de contacto?",
        "a": "No. Homayar está diseñado para organizaciones de cualquier tamaño. Ayuda a cualquier negocio que busque comprender mejor a sus clientes y optimizar sus comunicaciones a tomar decisiones basadas en datos."
      },
      {
        "q": "¿Cuál es la principal diferencia entre la información de Homayar y los informes convencionales?",
        "a": "Los informes convencionales muestran solo cifras y estadísticas en bruto; Homayar comprende el contenido y el sentimiento dentro de las conversaciones para revelar el «porqué» detrás de los datos y ofrecerte información sobre la que puedes actuar."
      },
      {
        "q": "¿Cómo se garantiza la seguridad de nuestros datos sensibles durante el análisis?",
        "a": "Con el despliegue en local (on-premise) y el cumplimiento de estándares de seguridad de la información de clase mundial, Homayar garantiza que tus datos sensibles nunca salgan del entorno seguro de tu organización."
      },
      {
        "q": "¿Cómo se integra Homayar con nuestros sistemas de telefonía y CRM actuales?",
        "a": "Homayar está concebido para integrarse con los sistemas de telefonía (VoIP) y los CRM más habituales, y nuestro equipo de especialistas se encarga de toda la implementación de principio a fin."
      }
    ]
  },
  "contact": {
    "title": "Ponte en contacto",
    "subtitle": "Completa el siguiente formulario y nuestros especialistas se pondrán en contacto contigo para una demo gratuita.",
    "firstName": "Nombre",
    "lastName": "Apellidos",
    "mobile": "Número de móvil",
    "email": "Correo electrónico",
    "message": "Mensaje",
    "submit": "Enviar mensaje",
    "success": "Tu mensaje se ha enviado correctamente. Nos pondremos en contacto contigo en breve.",
    "error": "Algo salió mal. Por favor, inténtalo de nuevo."
  },
  "footer": {
    "brand": "Homayar",
    "tagline": "Homayar | Una plataforma de Inteligencia Conversacional",
    "description": "Mediante inteligencia artificial, este sistema procesa las conversaciones de voz del centro de contacto, los correos electrónicos, los chats en línea, la mensajería y los tickets de soporte, transformando los datos no estructurados en información valiosa.",
    "contactHeading": "Contáctanos",
    "address": "Zona Internacional de Innovación de Irán (Parque Tecnológico de Pardis), Edificio Fan Bazaar, Planta 1, Unidad 1104",
    "socialHeading": "Redes sociales",
    "socials": [
      {
        "label": "Telegram",
        "href": "#"
      },
      {
        "label": "Instagram",
        "href": "#"
      },
      {
        "label": "YouTube",
        "href": "#"
      }
    ],
    "copyright": "1404 © Todos los derechos reservados de Homayar para Kohan System Farda. Cualquier reproducción será objeto de acciones legales."
  }
};

export const CONTENT: Record<Locale, Dict> = { fa, en, de, ar, fr, es };
