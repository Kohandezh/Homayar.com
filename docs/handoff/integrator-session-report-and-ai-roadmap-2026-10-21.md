# گزارش کامل session Integrator و نقشهٔ راه AI

۲۰۲۶-۱۰-۲۱ · Homayar · Integrator برای Lane C و Lane D

**وضعیت: INFRASTRUCTURE FROZEN: NO · NOT PRODUCTION READY**

---

## بخش ۱ — مأموریت session (خلاصهٔ prompt اصلی)

این session بر اساس «HOMAYAR — INFRASTRUCTURE FREEZE MASTER PROMPT» کار کرد. نقشش integrator اصلی backend بود.

### Laneها

| Lane | موضوع | تیکت‌ها |
|---|---|---|
| Lane C | backup، restore و recovery | HOM-153, HOM-116, HOM-117, HOM-118 |
| Lane D | audit، observability و release | HOM-113, HOM-114, HOM-115, HOM-126, HOM-28, HOM-29 |

### ترتیب اولویت

1. سبز نگه داشتن main
2. گزارش و Linear
3. backup و restore
4. audit و observability
5. build تکرارپذیر و manifest
6. امنیت استقرار (HOM-33 ← HOM-34 ← HOM-35 ← HOM-196)
7. Infrastructure Freeze Gate
8. و فقط بعد از آن، کار محصول

### قانون merge برای هر PR

1. بررسی diff
2. تست‌های متمرکز
3. بازبینی migration
4. بازبینی امنیت و حریم خصوصی
5. بازبینی مستقل
6. merge فقط اگر قابل قبول باشد
7. به‌روزرسانی Linear
8. به‌روزرسانی گزارش

### قوانین ثابت

- کار frontend ممنوع.
- RLS و حریم خصوصی تضعیف نمی‌شوند.
- هیچ تستی حذف یا skip نمی‌شود.
- force-push ممنوع.
- هیچ secret ای چاپ یا rotate نمی‌شود.
- deploy به production ممنوع.
- providerهای پولی صدا زده نمی‌شوند.
- migration بدون بازبینی merge نمی‌شود.
- Odoo، Hamkaran و voice زنده قبل از freeze پیاده نمی‌شوند.
- آمادگی production فقط از روی CI ادعا نمی‌شود.
- در commitها فقط این trailer می‌آید: `Co-Authored-By: Mohammad Kohandezh`.

### دستورهای بعدی مالک

- «merge on local evidence»: وقتی Actions به‌خاطر billing قطع بود، merge با شواهد محلی مجاز شد.
- ادامه تا وقتی همهٔ کار Lane C و Lane D merge شود و sessionها قابل حذف باشند.
- کار به‌صورت موازی و با agent بیشتر، برای سرعت.

---

## بخش ۲ — آنچه اتفاق افتاد (به ترتیب زمان)

### ۱۷ تا ۱۸ اکتبر

- **main قرمز بود.** علت یک false positive در تست بود. با #39 رفع شد.
- **Lane C — #42 (backup و restore).**
  - سه دور بازبینی مستقل انجام شد: blockerهای B1 تا B9، بعد C1 تا C4.
  - gate محلی: ۲۸۸۸ تست پاس شد.
  - merge شد.
- **GitHub Actions به‌خاطر مشکل billing از کار افتاد.** مالک «merge on local evidence» را تصویب کرد.
- **HOM-210 را خودم ساختم و رفع کردم (#60).** sweepها فقط N workspace اول را می‌دیدند. حالا همهٔ workspaceها به‌صورت چرخشی پیمایش می‌شوند.
- **#78:** allowlist برای fingerprint کلید backup در secret scan.
- **#67:** journal دیگر متن exception را لاگ نمی‌کند.

### ۱۸ تا ۱۹ اکتبر (Actions دوباره کار کرد)

**#43 — observability**
- چند دور بازبینی شد.
- یک باگ پنهان هم پیدا و رفع شد: `fileConfig` در Alembic loggerها را غیرفعال می‌کرد و همین main را قرمز کرده بود.

**#76 و #92 — restore drill (HOM-118)**
- اجرای اول روی نسخهٔ قدیمی بود.
- re-run روی main دو مشکل در cleanup داشت:
  - یک volume ‏۴۲۹ MB با backup رمزشده باقی می‌ماند.
  - اگر Docker در cleanup خطا می‌داد، فایل‌های secret پاک نمی‌شدند.
- هر دو رفع شدند. نتیجهٔ نهایی:
  - RTO ‏۱۱۶ ثانیه برای scope drill‌شده
  - RPO اثبات نشده؛ تصمیم D03 هنوز باز است.

**#77 و #93 — S3 off-site**
- بازبینی‌ها این مشکل‌ها را پیدا کردند:
  - اگر prune قطع می‌شد، نسخه‌های رمزشده برای همیشه off-site می‌ماندند.
  - کمبود یک permission به‌اشتباه «lock» حساب می‌شد.
  - backfill می‌توانست یک کپی خالی را verified علامت بزند.
  - prune هر شب با تاریخ lock گذشته fail می‌شد.
- همه رفع شدند.

**#47 و #95 — alertها**
- اطمینان از اینکه هر alert به metric واقعی وصل است.

**#50 — release manifest**
- اصلاح مستند: manifest «امضا» نیست، فقط checksum است.

**#55 — رویدادهای audit (HOM-114)**
- بازبینی دید که سه event type که main هنوز می‌نویسد حذف شده بود. اگر merge می‌شد:
  - صدای ضبط‌شده بعد از پردازش حذف می‌شد، ولی دیتابیس هنوز آن را موجود نشان می‌داد.
  - نصب عضو workspace همیشه خطای 500 می‌داد.
- یک فایل settings محلی هم به‌اشتباه commit شده بود.
- همه رفع شدند. tests flaky (UUID حاوی رقم شبیه شماره تلفن) به‌صورت محدودیت مستند ثبت شد.

**#66 — حریم خصوصی لاگ**
- **CI دو بار hang کرد.** علت: `datetime.utcnow()` در worker‌های Celery یک warning تولید می‌کرد، لاگ‌شدن آن warning باز warning می‌ساخت، و این حلقه worker را می‌کشت. یعنی یک باگ واقعی production هم بود.
- **تست وابسته به ترتیب.** علت: `alembic.ini` سطح root logger را بالا می‌برد.
- هر دو از ریشه رفع شدند.

**#69 و #70 — append-only بودن audit و export امن (HOM-115)**
- یک مشکل امنیتی رفع شد: نقش reader که از قبل وجود داشت، می‌توانست login کند.
- #70 قبل از تمام شدن CI و با شواهد محلی merge شد. به Sina اطلاع داده شد.

### تصمیم‌های مالک در این session

| # | تصمیم |
|---|---|
| ۱ | re-baseline شدن fingerprintهای `auth.py`: ‏`login`، `refresh`، `logout` و `otp_request` |
| ۲ | اگر audit ورود موفق نوشته نشود، ورود رد می‌شود (fail-closed) |
| ۳ | gate بررسی audit قدیمی قبل از اولین export |
| ۴ | D15: ‏Object Lock در حالت GOVERNANCE |

### نتیجه

- **۱۶ PR merge شد:** #42, #43, #47, #50, #55, #60, #66, #67, #69, #70, #76, #77, #78, #92, #93, #95
- **sessionها:** همهٔ sessionهای Lane C و Lane D حذف شدند. کار push نشده‌ای باقی نمانده است.

### باقی‌مانده تا freeze

1. چک کردن CI روی main بعد از #70
2. gate یکپارچه روی main
3. HOM-33 ← HOM-34 ← HOM-35 ← HOM-196
4. Freeze Gate (۱۷ معیار)

---

## بخش ۳ — ترتیب پیشنهادی featureهای AI (بر اساس AppVision)

AppVision در §9.2 گام ۲ مسیر را مشخص کرده است: «مسیر کامل post-call: گفتار و diarization واقعی، ارزیابی نماینده، بررسی شواهد».

بنابراین قانون این است: **اول اندازه‌گیری، بعد مدل، بعد تحلیل، بعد Copilot.**

### فاز ۰ — پایهٔ ارزیابی (اجباری، P0)

1. **F80** — corpus طلایی فارسی و benchmark هر slice
2. **F81 و N06** — gate رگرسیون AI و promote مدل/prompt، به‌همراه حلقهٔ feedback به evaluation
3. **N15 و F20** — rollback مدل و prompt، و registry

دلیل: بدون این‌ها هیچ ادعایی دربارهٔ کیفیت مدل مجاز نیست (AppVision §4).

### فاز ۱ — گفتار و متن (حدود ۲۸ تا ۳۹ lane-day)

4. **F26 و N04** — نرمال‌سازی صدا، VAD و کیفیت داده
5. **F27** — STT تولیدی فارسی و انگلیسی
6. **F28** — workerهای GPU پایدار و batch محدود
7. **F25** — تشخیص گوینده بر اساس کانال
8. **F30 و F31** — diarization تک‌کاناله و چندنفره، و تشخیص هم‌پوشانی
9. **F33** — هم‌ترازی زمانی و confidence کالیبره‌شده
10. **F45** — اصلاح transcript و گوینده توسط انسان

### فاز ۲ — لایهٔ تحلیل تماس (حدود ۱۹ تا ۲۵ lane-day)

11. **F34** — سکوت، نسبت صحبت، هم‌صحبتی و سرعت گفتار (قطعی، بدون LLM)
12. **F42** — قوانین کلمه کلیدی، اسکریپت و انطباق، همراه با شواهد
13. **F36** — قصد، موضوع، موجودیت‌ها و وضعیت حل مسئله
14. **F41** — scorecard وزنی و نسخه‌دار
15. **F37** — خلاصه، تصمیم‌ها و لحظه‌های کلیدی
16. **F38** — action item، مسئول و مهلت

### فاز ۳ — بازبینی و coaching (حدود ۱۵ تا ۱۹ lane-day)

17. **F43** — نمونه‌گیری خودکار برای بازبینی
18. **F44** — بازبینی سرپرست و override امتیاز
19. **F46** — کالیبراسیون و اعتراض
20. **F47** — تکلیف coaching و پیشرفت
21. **F49 و F61** — روند شکایت‌ها و مشکلات، و داشبورد نقش‌محور

### فاز ۴ — جست‌وجو، Copilot و اقدام (حدود ۲۲ تا ۲۹ lane-day، همراه با F63 PBX)

22. **F57** — جست‌وجوی lexical و semantic با scope
23. **F58** — Copilot تحلیلی، فقط با شواهد و citation
24. **F59 و N07** — هشدار بعد از تماس، digest و صف بازبینی
25. **F60** — اقدام CRM، تیکت یا coaching، فقط با تأیید انسان
26. **F74** — بستهٔ air-gapped ‏(STT، LLM و embedding محلی) برای on-prem

### بعد از v1 (expansion؛ هنوز مجوز ندارند)

| feature | موضوع |
|---|---|
| F39 | sentiment فارسی |
| F55 | اعتراض‌های فروش |
| F62 | brief مدیریتی |
| F89 | دستیار زنده حین تماس |
| F29 | code-switching |
| F94 | ماسک PII صوتی |

F40 (امتیازدهی احساس یا شخصیت از روی صدا) عمداً از scope امتیازدهی کارکنان خارج شده است.

### جمع تخمین

- **فاز ۱ تا ۴:** حدود ۸۴ تا ۱۱۲ lane-day
- **با ۳ lane موازی:** حدود ۶ تا ۸ هفته
- فاز ۰ به این عدد اضافه می‌شود، ولی می‌تواند با فاز ۱ موازی پیش برود.
- این تخمین شرط دارد: GPU و corpus طلایی در دسترس باشند.

### پیشنهاد من

بعد از freeze، اول F80 و F27 را موازی شروع کنید. corpus طلایی برای انتخاب و پذیرش مدل STT لازم است. بدون آن، هر کار تحلیلی روی transcriptی ساخته می‌شود که کیفیتش اندازه‌گیری نشده است.
