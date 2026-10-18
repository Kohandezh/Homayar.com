import type { ErrorCodeMessages } from "@/lib/error-messages";
import type { LocationKind } from "@/types/locations";

/** Every locations page text, in the console's two languages (HOM-191). */
export const LOCATIONS_COPY = {
  en: {
    title: "Service locations",
    intro: "Branches, dealers and service centers of this workspace.",
    addLocation: "Add location",

    kind: "Kind",
    status: "Status",
    city: "City",
    allKinds: "All kinds",
    allStatuses: "All statuses",
    active: "Active",
    inactive: "Inactive",
    kinds: {
      branch: "Branch",
      dealer: "Dealer",
      service_center: "Service center",
    },

    tableLabel: "Service locations",
    tableScrollLabel: "Service locations, scrolls horizontally",
    columnName: "Name",
    columnKind: "Kind",
    columnCity: "City",
    columnPhone: "Phone",
    columnStatus: "Status",
    columnActions: "Actions",

    edit: "Edit",
    editLocation: (name: string) => `Edit ${name}`,
    remove: "Delete",
    removeLocation: (name: string) => `Delete ${name}`,
    loading: "Loading locations",
    loadFailed: "The locations could not be loaded.",
    noneYet: "No locations yet.",
    addFirst: "Add the first branch, dealer or service center.",
    noMatch: "No location matches these filters.",

    createHeading: "Add location",
    editHeading: "Edit location",
    fields: {
      kind: "Kind",
      name: "Name",
      code: "Code",
      province: "Province",
      city: "City",
      district: "District",
      street: "Street",
      postalCode: "Postal code",
      address: "Address",
      latitude: "Latitude",
      longitude: "Longitude",
      phone: "Phone",
      serviceTags: "Service tags",
      active: "Active",
    },
    tagsHelp: "Separate tags with commas.",
    save: "Save",
    cancel: "Cancel",
    close: "Close",
    saved: "Location saved",
    saveFailed: "The location could not be saved.",
    noChanges: "No changes to save.",

    savedWithWarnings: (name: string) => `Saved “${name}” with warnings`,
    dismiss: "Dismiss",

    deleteHeading: "Delete location?",
    deleteTail: "will be removed. This cannot be undone.",
    deleteConfirm: "Delete",
    deleted: "Location deleted",
    deleteFailed: "The location could not be deleted.",

    validation: {
      name_required: "Enter a name.",
      name_too_long: "Use at most 200 characters.",
      code_required: "Enter a code.",
      code_invalid: "Use letters, digits, dot, dash or underscore (at most 64).",
      coordinates_partial: "Enter both latitude and longitude, or neither.",
      latitude_invalid: "Latitude must be a number between -90 and 90.",
      longitude_invalid: "Longitude must be a number between -180 and 180.",
      postal_invalid: "Enter a 10-digit postal code.",
      phone_too_long: "Use at most 32 characters.",
      too_many_tags: "Use at most 50 tags.",
    },

    warnings: {
      coordinates_outside_iran: "The coordinates are outside Iran.",
      city_not_in_gazetteer: "The city is not in the place list. Check the spelling.",
      province_not_in_gazetteer: "The province is not in the place list. Check the spelling.",
      city_province_mismatch: "The city is not in this province.",
      city_ambiguous: "More than one city has this name. Add the province.",
    },
  },
  fa: {
    title: "مکان‌های خدمات",
    intro: "شعبه‌ها، نمایندگی‌ها و مرکزهای خدمات این فضای کاری.",
    addLocation: "افزودن مکان",

    kind: "نوع",
    status: "وضعیت",
    city: "شهر",
    allKinds: "همه نوع‌ها",
    allStatuses: "همه وضعیت‌ها",
    active: "فعال",
    inactive: "غیرفعال",
    kinds: {
      branch: "شعبه",
      dealer: "نمایندگی",
      service_center: "مرکز خدمات",
    },

    tableLabel: "مکان‌های خدمات",
    tableScrollLabel: "مکان‌های خدمات، با پیمایش افقی",
    columnName: "نام",
    columnKind: "نوع",
    columnCity: "شهر",
    columnPhone: "تلفن",
    columnStatus: "وضعیت",
    columnActions: "عملیات",

    edit: "ویرایش",
    editLocation: (name: string) => `ویرایش ${name}`,
    remove: "حذف",
    removeLocation: (name: string) => `حذف ${name}`,
    loading: "در حال دریافت مکان‌ها",
    loadFailed: "فهرست مکان‌ها بارگیری نشد.",
    noneYet: "هنوز مکانی ساخته نشده است.",
    addFirst: "اولین شعبه، نمایندگی یا مرکز خدمات را اضافه کنید.",
    noMatch: "هیچ مکانی با این فیلترها پیدا نشد.",

    createHeading: "افزودن مکان",
    editHeading: "ویرایش مکان",
    fields: {
      kind: "نوع",
      name: "نام",
      code: "کد",
      province: "استان",
      city: "شهر",
      district: "محله",
      street: "خیابان",
      postalCode: "کد پستی",
      address: "نشانی",
      latitude: "عرض جغرافیایی",
      longitude: "طول جغرافیایی",
      phone: "تلفن",
      serviceTags: "خدمات",
      active: "فعال",
    },
    tagsHelp: "برچسب‌ها را با ویرگول جدا کنید.",
    save: "ذخیره",
    cancel: "انصراف",
    close: "بستن",
    saved: "مکان ذخیره شد",
    saveFailed: "مکان ذخیره نشد.",
    noChanges: "تغییری برای ذخیره نیست.",

    savedWithWarnings: (name: string) => `«${name}» با هشدار ذخیره شد`,
    dismiss: "بستن",

    deleteHeading: "مکان حذف شود؟",
    deleteTail: "حذف می‌شود. این کار بازگشت‌پذیر نیست.",
    deleteConfirm: "حذف",
    deleted: "مکان حذف شد",
    deleteFailed: "مکان حذف نشد.",

    validation: {
      name_required: "نام را وارد کنید.",
      name_too_long: "حداکثر ۲۰۰ نویسه وارد کنید.",
      code_required: "کد را وارد کنید.",
      code_invalid: "از حرف، رقم، نقطه، خط تیره یا زیرخط استفاده کنید (حداکثر ۶۴).",
      coordinates_partial: "عرض و طول جغرافیایی را هر دو وارد کنید یا هیچ‌کدام را.",
      latitude_invalid: "عرض جغرافیایی باید عددی بین ‎-۹۰ و ۹۰ باشد.",
      longitude_invalid: "طول جغرافیایی باید عددی بین ‎-۱۸۰ و ۱۸۰ باشد.",
      postal_invalid: "یک کد پستی ۱۰ رقمی وارد کنید.",
      phone_too_long: "حداکثر ۳۲ نویسه وارد کنید.",
      too_many_tags: "حداکثر ۵۰ برچسب وارد کنید.",
    },

    warnings: {
      coordinates_outside_iran: "مختصات بیرون از ایران است.",
      city_not_in_gazetteer: "شهر در فهرست مکان‌ها نیست. املای آن را بررسی کنید.",
      province_not_in_gazetteer: "استان در فهرست مکان‌ها نیست. املای آن را بررسی کنید.",
      city_province_mismatch: "این شهر در این استان نیست.",
      city_ambiguous: "بیش از یک شهر با این نام وجود دارد. استان را اضافه کنید.",
    },
  },
} as const;

export type LocationsCopy = (typeof LOCATIONS_COPY)["en" | "fa"];

/** `branch` -> "Branch" / "شعبه"; the label shown in tables, filters and forms. */
export function locationKindLabel(kind: LocationKind, lang: "fa" | "en") {
  return LOCATIONS_COPY[lang].kinds[kind];
}

/** Error codes of backend/routers/v1/locations.py and locations/service.py. */
export const LOCATION_ERROR_CODES: ErrorCodeMessages = {
  code_taken: {
    fa: "مکان دیگری از قبل از این کد استفاده می‌کند.",
    en: "Another location already uses this code.",
  },
  invalid_address: {
    fa: "نشانی نامعتبر است. کد پستی و مختصات را بررسی کنید.",
    en: "The address is invalid. Check the postal code and the coordinates.",
  },
  invalid_opening_hours: {
    fa: "ساعات کاری ذخیره‌شده نادرست است.",
    en: "The saved opening hours are malformed.",
  },
  validation_error: {
    fa: "برخی فیلدها نامعتبر هستند.",
    en: "Some fields are invalid.",
  },
  no_changes: {
    fa: "تغییری برای ذخیره نیست.",
    en: "No changes to save.",
  },
  location_not_found: {
    fa: "این مکان دیگر وجود ندارد.",
    en: "This location no longer exists.",
  },
  permission_denied: {
    fa: "شما اجازهٔ تغییر مکان‌ها را ندارید.",
    en: "You do not have permission to change locations.",
  },
};

/** A warning code from a save, or the code itself when the server adds one. */
export function locationWarningText(code: string, lang: "fa" | "en") {
  const labels = LOCATIONS_COPY[lang].warnings;
  return Object.hasOwn(labels, code) ? labels[code as keyof typeof labels] : code;
}
