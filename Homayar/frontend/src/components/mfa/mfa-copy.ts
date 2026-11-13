import { formatNumber } from "@/lib/format";
import type { Lang } from "@/lib/i18n-context";

/**
 * Every string of the MFA screens (HOM-241), in both console languages. The
 * English strings of the acceptance criteria are used exactly as written;
 * the Persian of the headings and main controls comes from the SPEC, the
 * rest is plain Persian written for this screen.
 */
export interface MfaCopy {
  setup: {
    title: string;
    intro: string;
    start: string;
    startAgain: string;
    qrLabel: string;
    setupKey: string;
    copyKey: string;
    copied: string;
    copyFailed: string;
    codeField: string;
    confirm: string;
  };
  verify: {
    title: string;
    intro: string;
    submit: string;
    useRecovery: string;
    useAuthenticator: string;
    codeField: string;
    recoveryField: string;
  };
  codes: {
    heading: string;
    body: string;
    copyCodes: string;
    copied: string;
    copyFailed: string;
    savedLabel: string;
    continueLabel: string;
    usedRecovery: (remaining: string) => string;
    makeNew: string;
  };
  account: {
    title: string;
    intro: string;
    twoStep: string;
    on: string;
    off: string;
    requiredForRole: string;
    codesLeft: (count: string) => string;
    sessionNotVerified: string;
    verifyNow: string;
    setupLink: string;
    newApp: string;
    dialogTitle: string;
    dialogBody: string;
    cancel: string;
    dialogConfirm: string;
    loading: string;
    errorFallback: string;
    retry: string;
  };
  common: {
    signOut: string;
    checking: string;
  };
  errors: {
    emptyCode: string;
    emptyRecovery: string;
    codeInvalid: string;
    recoveryInvalid: string;
    rateLimited: (minutes: string) => string;
    rateLimitedLater: string;
    reauthentication: string;
    signInAgain: string;
    expired: string;
    unavailable: string;
    generic: string;
  };
}

export const MFA_COPY: Record<Lang, MfaCopy> = {
  en: {
    setup: {
      title: "Set up two-step verification",
      intro:
        "An authenticator app on your phone shows a new six-digit code every 30 seconds. Start the setup, scan the code with the app, and enter one of its codes to confirm.",
      start: "Start setup",
      startAgain: "Start again",
      qrLabel: "QR code for your authenticator app",
      setupKey: "Setup key",
      copyKey: "Copy setup key",
      copied: "Copied",
      copyFailed: "Copy failed. Select the text and copy it by hand.",
      codeField: "Six-digit code",
      confirm: "Confirm",
    },
    verify: {
      title: "Verify it's you",
      intro: "Enter a code from your authenticator app, or use a recovery code.",
      submit: "Verify",
      useRecovery: "Use a recovery code",
      useAuthenticator: "Use authenticator code",
      codeField: "Six-digit code",
      recoveryField: "Recovery code",
    },
    codes: {
      heading: "Save your recovery codes",
      body: "Each code works once. You can use one if you lose your phone. This is the only time they are shown.",
      copyCodes: "Copy codes",
      copied: "Copied",
      copyFailed: "Copy failed. Select the codes and copy them by hand.",
      savedLabel: "I saved my recovery codes",
      continueLabel: "Continue",
      usedRecovery: (remaining) => `You used a recovery code. ${remaining} recovery codes left.`,
      makeNew: "Make new recovery codes",
    },
    account: {
      title: "Account security",
      intro: "Your sign-in security, including two-step verification.",
      twoStep: "Two-step verification",
      on: "On",
      off: "Off",
      requiredForRole: "Required for your role",
      codesLeft: (count) => `${count} recovery codes left`,
      sessionNotVerified: "This session is not verified yet.",
      verifyNow: "Verify now",
      setupLink: "Set up two-step verification",
      newApp: "Use a new authenticator app",
      dialogTitle: "Make new recovery codes?",
      dialogBody: "Your old recovery codes will stop working.",
      cancel: "Cancel",
      dialogConfirm: "Make new codes",
      loading: "Loading",
      errorFallback: "Could not load your security status.",
      retry: "Try again",
    },
    common: {
      signOut: "Sign out",
      checking: "Checking two-step verification",
    },
    errors: {
      emptyCode: "Enter the six-digit code from your app.",
      emptyRecovery: "Enter one of your recovery codes.",
      codeInvalid: "That code is not correct. Try the newest code from your app.",
      recoveryInvalid: "That code is not correct.",
      rateLimited: (minutes) => `Too many attempts. Try again in ${minutes} minutes.`,
      rateLimitedLater: "Too many attempts. Try again later.",
      reauthentication: "For your safety, sign in again to set up two-step verification.",
      signInAgain: "Sign in again",
      expired: "This setup expired. Start again.",
      unavailable: "Two-step verification is not available on this server. Ask your administrator.",
      generic: "The request failed.",
    },
  },
  fa: {
    setup: {
      title: "راه‌اندازی ورود دومرحله‌ای",
      intro:
        "برنامهٔ احراز هویت روی گوشی شما هر ۳۰ ثانیه یک کد شش‌رقمی تازه نشان می‌دهد. راه‌اندازی را شروع کنید، کد را با برنامه اسکن کنید و یکی از کدهای آن را برای تأیید وارد کنید.",
      start: "شروع راه‌اندازی",
      startAgain: "شروع دوباره",
      qrLabel: "کد QR برای برنامهٔ احراز هویت",
      setupKey: "کلید راه‌اندازی",
      copyKey: "کپی کلید راه‌اندازی",
      copied: "کپی شد",
      copyFailed: "کپی نشد. متن را انتخاب کنید و خودتان کپی کنید.",
      codeField: "کد شش‌رقمی",
      confirm: "تأیید",
    },
    verify: {
      title: "هویت خود را تأیید کنید",
      intro: "کدی از برنامهٔ احراز هویت خود وارد کنید یا از کد بازیابی استفاده کنید.",
      submit: "بررسی کد",
      useRecovery: "استفاده از کد بازیابی",
      useAuthenticator: "استفاده از کد برنامه",
      codeField: "کد شش‌رقمی",
      recoveryField: "کد بازیابی",
    },
    codes: {
      heading: "کدهای بازیابی را نگه دارید",
      body: "هر کد فقط یک بار کار می‌کند. اگر گوشی خود را گم کنید، می‌توانید از یکی از آن‌ها استفاده کنید. این کدها فقط همین یک بار نمایش داده می‌شوند.",
      copyCodes: "کپی کدها",
      copied: "کپی شد",
      copyFailed: "کپی نشد. کدها را انتخاب کنید و خودتان کپی کنید.",
      savedLabel: "کدهای بازیابی را ذخیره کردم",
      continueLabel: "ادامه",
      usedRecovery: (remaining) => `شما از یک کد بازیابی استفاده کردید. ${remaining} کد بازیابی باقی مانده است.`,
      makeNew: "ساخت کدهای بازیابی تازه",
    },
    account: {
      title: "امنیت حساب",
      intro: "امنیت ورود شما، شامل ورود دومرحله‌ای.",
      twoStep: "ورود دومرحله‌ای",
      on: "فعال",
      off: "غیرفعال",
      requiredForRole: "برای نقش شما لازم است",
      codesLeft: (count) => `${count} کد بازیابی باقی مانده است`,
      sessionNotVerified: "این نشست هنوز تأیید نشده است.",
      verifyNow: "همین حالا تأیید کنید",
      setupLink: "راه‌اندازی ورود دومرحله‌ای",
      newApp: "استفاده از برنامهٔ احراز هویت تازه",
      dialogTitle: "ساخت کدهای بازیابی تازه؟",
      dialogBody: "کدهای بازیابی پیشین شما از کار می‌افتند.",
      cancel: "انصراف",
      dialogConfirm: "ساخت کدهای تازه",
      loading: "در حال بارگذاری",
      errorFallback: "وضعیت امنیتی شما دریافت نشد.",
      retry: "تلاش دوباره",
    },
    common: {
      signOut: "خروج",
      checking: "در حال بررسی ورود دومرحله‌ای",
    },
    errors: {
      emptyCode: "کد شش‌رقمی را از برنامهٔ خود وارد کنید.",
      emptyRecovery: "یکی از کدهای بازیابی خود را وارد کنید.",
      codeInvalid: "آن کد درست نیست. تازه‌ترین کد برنامهٔ خود را امتحان کنید.",
      recoveryInvalid: "آن کد درست نیست.",
      rateLimited: (minutes) => `تلاش‌ها زیاد بوده است؛ ${minutes} دقیقهٔ دیگر دوباره تلاش کنید.`,
      rateLimitedLater: "تلاش‌ها زیاد بوده است؛ بعداً دوباره تلاش کنید.",
      reauthentication: "برای امنیت شما، برای راه‌اندازی ورود دومرحله‌ای دوباره وارد شوید.",
      signInAgain: "دوباره وارد شوید",
      expired: "این راه‌اندازی منقضی شد. دوباره شروع کنید.",
      unavailable: "ورود دومرحله‌ای روی این سرور در دسترس نیست. با مدیر سامانهٔ خود تماس بگیرید.",
      generic: "درخواست انجام نشد.",
    },
  },
};

/** The remaining-count text with locale digits: "9" in English, "۹" in Persian. */
export function remainingCodesText(count: number, lang: Lang): string {
  return formatNumber(count, lang);
}
