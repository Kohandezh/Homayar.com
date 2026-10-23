import { z } from "zod";

// Digits (Latin, Persian or Arabic-Indic), spaces, +, -, ( and ).
const phonePattern = /^[0-9۰-۹٠-٩ +\-()]*$/;

export const minutesValues = ["", "lt10k", "10k-50k", "50k-250k", "250k-1m", "gt1m"] as const;

export const contactSchema = z.object({
  name: z.string().trim().min(2).max(120),
  email: z.string().trim().email().max(200),
  company: z.string().trim().min(1).max(160),
  role: z.enum(["", "qa", "supervisor", "operations", "it", "other"]).optional(),
  deployment: z.enum(["saas", "self-hosted", "hybrid", "unsure"]),
  minutes: z.enum(minutesValues).optional(),
  phone: z.string().trim().max(32).regex(phonePattern).optional(),
  message: z.string().trim().max(2000).optional(),
  locale: z.enum(["fa", "en"]).optional(),
});

export type ContactInput = z.infer<typeof contactSchema>;
export type ContactField = keyof ContactInput;
