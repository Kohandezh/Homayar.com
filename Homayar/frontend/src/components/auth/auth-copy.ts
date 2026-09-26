import { apiErrorInfo, minutesUntil } from "@/lib/api-errors";
import { useI18n, type Lang } from "@/lib/i18n-context";
import { formatNumber } from "@/lib/format";

export interface AuthCopy {
  layout: {
    tagline: string;
    privateWorkspace: string;
    homeLabel: string;
    website: string;
    switchLanguage: string;
    switchLanguageShort: string;
  };
  common: {
    email: string;
    workEmail: string;
    password: string;
    showPassword: string;
    hidePassword: string;
    google: string;
    orEmail: string;
    passwordHint: string;
    passwordsMismatch: string;
    backToSignIn: string;
    continueToSignIn: string;
    contactSupport: string;
    loading: string;
  };
  login: {
    title: string;
    description: string;
    submit: string;
    forgot: string;
    phone: string;
    newHere: string;
    createAccount: string;
    sessionExpired: string;
    googleFailed: string;
    googleLinkRequired: string;
    failed: string;
  };
  register: {
    title: string;
    description: string;
    fullName: string;
    fullNamePlaceholder: string;
    confirm: string;
    submit: string;
    haveAccount: string;
    signIn: string;
    created: (email: string) => string;
    createdNext: string;
    failed: string;
    accountExists: string;
  };
  verify: {
    title: string;
    description: string;
    token: string;
    tokenPlaceholder: string;
    submit: string;
    verified: string;
    continueWorkspace: string;
    invalid: string;
    failed: string;
  };
  forgot: {
    title: string;
    description: string;
    submit: string;
    accepted: (email: string) => string;
    remembered: string;
    failed: string;
  };
  reset: {
    title: string;
    description: string;
    token: string;
    tokenPlaceholder: string;
    newPassword: string;
    confirm: string;
    submit: string;
    complete: string;
    signInNew: string;
    invalid: string;
    failed: string;
  };
  phone: {
    title: string;
    description: string;
    label: string;
    hint: string;
    submit: string;
    unavailableTitle: string;
    unavailableBody: string;
    invalidPhone: string;
    useEmail: string;
    failed: string;
    deliveryFailed: string;
  };
  otp: {
    title: string;
    description: (phone: string) => string;
    label: string;
    submit: string;
    expiresIn: (clock: string) => string;
    expired: string;
    resend: string;
    resendIn: (clock: string) => string;
    newCodeSent: string;
    invalid: string;
    noChallenge: string;
    requestCode: string;
    failed: string;
  };
  errors: {
    network: string;
    unavailable: string;
    rateLimited: (minutes: string) => string;
    invalidCredentials: string;
    invalidEmail: string;
  };
}

export const AUTH_COPY: Record<Lang, AuthCopy> = {
  en: {
    layout: {
      tagline: "Voice intelligence",
      privateWorkspace: "Private workspace",
      homeLabel: "Homayar sign in",
      website: "Homayar website",
      switchLanguage: "تغییر به فارسی",
      switchLanguageShort: "FA",
    },
    common: {
      email: "Email address",
      workEmail: "Work email",
      password: "Password",
      showPassword: "Show password",
      hidePassword: "Hide password",
      google: "Continue with Google",
      orEmail: "or use email",
      passwordHint: "At least 10 characters, including a letter and a number.",
      passwordsMismatch: "The passwords do not match.",
      backToSignIn: "Back to sign in",
      continueToSignIn: "Continue to sign in",
      contactSupport: "Contact support",
      loading: "Loading…",
    },
    login: {
      title: "Welcome back",
      description: "Sign in to your Homayar workspace.",
      submit: "Sign in",
      forgot: "Forgot password?",
      phone: "Sign in with a phone code",
      newHere: "New to Homayar?",
      createAccount: "Create an account",
      sessionExpired: "Your session ended. Sign in again to continue.",
      googleFailed: "Google sign-in could not be completed. Try again or use your email.",
      googleLinkRequired:
        "This Google account's email already belongs to a Homayar account. Sign in with that account's existing method.",
      failed: "Sign-in failed. Try again.",
    },
    register: {
      title: "Create your account",
      description: "Register with your work email. A personal workspace is created for you.",
      fullName: "Full name",
      fullNamePlaceholder: "Your name",
      confirm: "Confirm password",
      submit: "Create account",
      haveAccount: "Already have an account?",
      signIn: "Sign in",
      created: (email) => `Account created for ${email}.`,
      createdNext:
        "Check your inbox for the verification link (valid for 24 hours). You can sign in now; uploading recordings needs a verified email. If it does not arrive, sign in and request a new link from the notice at the top of the console.",
      failed: "The account could not be created. Try again.",
      accountExists: "An account already exists for this email. Sign in or reset its password.",
    },
    verify: {
      title: "Verify your email",
      description: "Confirm the link from your verification email.",
      token: "Verification token",
      tokenPlaceholder: "Paste the token from the email",
      submit: "Verify email",
      verified: "Your email is verified.",
      continueWorkspace: "Continue to your workspace",
      invalid: "This link is invalid, already used, or older than 24 hours.",
      failed: "The email could not be verified. Try again.",
    },
    forgot: {
      title: "Reset your password",
      description: "Enter your account email and we will send a reset link.",
      submit: "Send reset link",
      accepted: (email) => `If an account exists for ${email}, a reset link is on its way.`,
      remembered: "Remembered it?",
      failed: "The reset request could not be sent. Try again.",
    },
    reset: {
      title: "Choose a new password",
      description: "Set a new password for your Homayar account.",
      token: "Reset token",
      tokenPlaceholder: "Paste the token from the email",
      newPassword: "New password",
      confirm: "Confirm new password",
      submit: "Change password",
      complete: "Your password was changed and every session was signed out.",
      signInNew: "Sign in with the new password",
      invalid: "This reset link is invalid, already used, or expired. Request a new one.",
      failed: "The password could not be changed. Try again.",
    },
    phone: {
      title: "Sign in by phone",
      description: "We send a one-time code by SMS to your Iranian mobile number.",
      label: "Mobile number",
      hint: "Iranian mobile number, for example 0912 123 4567.",
      submit: "Send code",
      unavailableTitle: "Phone sign-in is not available",
      unavailableBody: "SMS sign-in is not enabled for this service. Sign in with your email and password.",
      invalidPhone: "Enter a valid Iranian mobile number.",
      useEmail: "Use email instead?",
      failed: "Phone sign-in is not available right now. Use your email and password.",
      deliveryFailed: "The code could not be sent. Try again in a minute, or use your email and password.",
    },
    otp: {
      title: "Enter your code",
      description: (phone) => `Enter the 6-digit code sent to ${phone}.`,
      label: "One-time code",
      submit: "Verify code",
      expiresIn: (clock) => `The code expires in ${clock}.`,
      expired: "This code has expired. Request a new one.",
      resend: "Send a new code",
      resendIn: (clock) => `You can request a new code in ${clock}.`,
      newCodeSent: "A new code was sent. Earlier codes no longer work.",
      invalid: "The code is wrong, expired, or replaced by a newer code.",
      noChallenge: "Request a code first.",
      requestCode: "Request a code",
      failed: "The code could not be verified. Try again.",
    },
    errors: {
      network: "Could not reach the server. Check your connection and try again.",
      unavailable: "The service is temporarily unavailable. Try again shortly.",
      rateLimited: (minutes) => `Too many attempts. Try again in ${minutes} min.`,
      invalidCredentials: "The email or password is incorrect.",
      invalidEmail: "Enter a valid email address.",
    },
  },
  fa: {
    layout: {
      tagline: "هوشمندی گفت‌وگو",
      privateWorkspace: "فضای کاری خصوصی",
      homeLabel: "ورود به همایار",
      website: "وب‌سایت همایار",
      switchLanguage: "Switch to English",
      switchLanguageShort: "EN",
    },
    common: {
      email: "نشانی ایمیل",
      workEmail: "ایمیل کاری",
      password: "گذرواژه",
      showPassword: "نمایش گذرواژه",
      hidePassword: "پنهان کردن گذرواژه",
      google: "ادامه با حساب Google",
      orEmail: "یا با ایمیل",
      passwordHint: "دست‌کم ۱۰ نویسه، شامل یک حرف و یک عدد.",
      passwordsMismatch: "گذرواژه و تکرار آن یکسان نیستند.",
      backToSignIn: "بازگشت به ورود",
      continueToSignIn: "ادامه و ورود",
      contactSupport: "تماس با پشتیبانی",
      loading: "در حال بارگذاری…",
    },
    login: {
      title: "ورود به همایار",
      description: "به فضای کاری همایار خود وارد شوید.",
      submit: "ورود",
      forgot: "گذرواژه را فراموش کرده‌اید؟",
      phone: "ورود با کد پیامکی",
      newHere: "حساب ندارید؟",
      createAccount: "ساخت حساب",
      sessionExpired: "نشست شما به پایان رسید. برای ادامه دوباره وارد شوید.",
      googleFailed: "ورود با Google کامل نشد. دوباره امتحان کنید یا با ایمیل وارد شوید.",
      googleLinkRequired:
        "ایمیل این حساب Google قبلاً برای یک حساب همایار ثبت شده است. با روش ورود فعلی همان حساب وارد شوید.",
      failed: "ورود انجام نشد. دوباره امتحان کنید.",
    },
    register: {
      title: "ساخت حساب همایار",
      description: "با ایمیل کاری ثبت‌نام کنید؛ یک فضای کاری شخصی برایتان ساخته می‌شود.",
      fullName: "نام و نام خانوادگی",
      fullNamePlaceholder: "نام شما",
      confirm: "تکرار گذرواژه",
      submit: "ساخت حساب",
      haveAccount: "حساب دارید؟",
      signIn: "وارد شوید",
      created: (email) => `حساب برای ${email} ساخته شد.`,
      createdNext:
        "پیوند تأیید را در صندوق ایمیل خود پیدا کنید (اعتبار: ۲۴ ساعت). همین حالا می‌توانید وارد شوید، اما بارگذاری فایل صوتی به ایمیل تأییدشده نیاز دارد. اگر ایمیل نرسید، وارد شوید و از اعلان بالای کنسول پیوند تازه درخواست کنید.",
      failed: "حساب ساخته نشد. دوباره امتحان کنید.",
      accountExists: "برای این ایمیل قبلاً حساب ساخته شده است. وارد شوید یا گذرواژه را بازیابی کنید.",
    },
    verify: {
      title: "تأیید ایمیل",
      description: "پیوند ایمیل تأیید را اینجا تأیید کنید.",
      token: "کد تأیید",
      tokenPlaceholder: "کد درج‌شده در ایمیل را وارد کنید",
      submit: "تأیید ایمیل",
      verified: "ایمیل شما تأیید شد.",
      continueWorkspace: "ادامه در فضای کاری",
      invalid: "این پیوند نامعتبر است، قبلاً استفاده شده یا بیش از ۲۴ ساعت از ارسال آن گذشته است.",
      failed: "ایمیل تأیید نشد. دوباره امتحان کنید.",
    },
    forgot: {
      title: "بازیابی گذرواژه",
      description: "ایمیل حساب خود را وارد کنید تا پیوند بازیابی فرستاده شود.",
      submit: "ارسال پیوند بازیابی",
      accepted: (email) => `اگر حسابی با نشانی ${email} وجود داشته باشد، پیوند بازیابی برای آن فرستاده می‌شود.`,
      remembered: "گذرواژه یادتان آمد؟",
      failed: "درخواست بازیابی فرستاده نشد. دوباره امتحان کنید.",
    },
    reset: {
      title: "انتخاب گذرواژهٔ جدید",
      description: "برای حساب همایار خود گذرواژهٔ تازه‌ای تعیین کنید.",
      token: "کد بازیابی",
      tokenPlaceholder: "کد درج‌شده در ایمیل را وارد کنید",
      newPassword: "گذرواژهٔ جدید",
      confirm: "تکرار گذرواژهٔ جدید",
      submit: "تغییر گذرواژه",
      complete: "گذرواژه تغییر کرد و همهٔ نشست‌های حساب بسته شدند.",
      signInNew: "ورود با گذرواژهٔ جدید",
      invalid: "این پیوند بازیابی نامعتبر، استفاده‌شده یا منقضی است. پیوند تازه درخواست کنید.",
      failed: "گذرواژه تغییر نکرد. دوباره امتحان کنید.",
    },
    phone: {
      title: "ورود با شمارهٔ موبایل",
      description: "یک کد یک‌بارمصرف با پیامک به شمارهٔ موبایل ایرانی شما فرستاده می‌شود.",
      label: "شمارهٔ موبایل",
      hint: "شمارهٔ موبایل ایران، مثلاً ۰۹۱۲ ۱۲۳ ۴۵۶۷.",
      submit: "ارسال کد",
      unavailableTitle: "ورود با موبایل در دسترس نیست",
      unavailableBody: "ورود پیامکی برای این سرویس فعال نشده است. با ایمیل و گذرواژه وارد شوید.",
      invalidPhone: "شمارهٔ موبایل ایرانی معتبر وارد کنید.",
      useEmail: "ورود با ایمیل؟",
      failed: "ورود با موبایل فعلاً در دسترس نیست. با ایمیل و گذرواژه وارد شوید.",
      deliveryFailed: "کد ارسال نشد. یک دقیقهٔ دیگر دوباره تلاش کنید یا با ایمیل و گذرواژه وارد شوید.",
    },
    otp: {
      title: "کد را وارد کنید",
      description: (phone) => `کد ۶ رقمی ارسال‌شده به ${phone} را وارد کنید.`,
      label: "کد یک‌بارمصرف",
      submit: "تأیید کد",
      expiresIn: (clock) => `اعتبار کد: ${clock}`,
      expired: "اعتبار این کد تمام شده است. کد تازه بگیرید.",
      resend: "ارسال کد تازه",
      resendIn: (clock) => `ارسال کد تازه تا ${clock} دیگر ممکن می‌شود.`,
      newCodeSent: "کد تازه فرستاده شد. کدهای قبلی دیگر معتبر نیستند.",
      invalid: "کد نادرست است، منقضی شده یا کد تازه‌تری جای آن را گرفته است.",
      noChallenge: "ابتدا کد درخواست کنید.",
      requestCode: "درخواست کد",
      failed: "کد تأیید نشد. دوباره امتحان کنید.",
    },
    errors: {
      network: "ارتباط با سرور برقرار نشد. اتصال خود را بررسی کنید و دوباره امتحان کنید.",
      unavailable: "سرویس موقتاً در دسترس نیست. کمی بعد دوباره امتحان کنید.",
      rateLimited: (minutes) => `تعداد تلاش‌ها بیش از حد مجاز است. ${minutes} دقیقهٔ دیگر دوباره امتحان کنید.`,
      invalidCredentials: "ایمیل یا گذرواژه نادرست است.",
      invalidEmail: "نشانی ایمیل معتبر وارد کنید.",
    },
  },
};

interface ValidationDetail {
  field?: unknown;
}

function invalidField(details: unknown): string | null {
  if (!Array.isArray(details)) return null;
  const field = (details as ValidationDetail[]).find((item) => typeof item?.field === "string")?.field;
  return typeof field === "string" ? field : null;
}

/**
 * The user-facing message for a failed auth request, chosen by error code.
 * Server messages are English-only, so they are never shown directly.
 */
export function authErrorText(error: unknown, copy: AuthCopy, lang: Lang, fallback: string): string {
  const info = apiErrorInfo(error);
  if (info.network) return copy.errors.network;
  switch (info.code) {
    case "invalid_credentials":
      return copy.errors.invalidCredentials;
    case "rate_limited":
    case "otp_cooldown":
      return copy.errors.rateLimited(formatNumber(minutesUntil(info.retryAfterSeconds ?? 60), lang));
    case "rate_limiter_unavailable":
      return copy.errors.unavailable;
    case "account_exists":
      return copy.register.accountExists;
    case "invalid_verification_token":
      return copy.verify.invalid;
    case "invalid_reset_token":
      return copy.reset.invalid;
    case "invalid_phone":
      return copy.phone.invalidPhone;
    case "invalid_otp":
      return copy.otp.invalid;
    case "validation_error": {
      const field = invalidField(info.details) ?? "";
      if (field.includes("password")) return copy.common.passwordHint;
      if (field.includes("email")) return copy.errors.invalidEmail;
      if (field.includes("phone")) return copy.phone.invalidPhone;
      if (field.includes("code")) return copy.otp.invalid;
      return fallback;
    }
  }
  if (info.status !== null && info.status >= 500) return copy.errors.unavailable;
  return fallback;
}

export function useAuthCopy() {
  const { lang } = useI18n();
  return { copy: AUTH_COPY[lang], lang };
}
