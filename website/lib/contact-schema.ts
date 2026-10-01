import { z } from "zod";

export const contactSchema = z.object({
  name: z.string().trim().min(2).max(120),
  email: z.string().trim().email().max(200),
  company: z.string().trim().min(1).max(160),
  role: z.enum(["", "qa", "supervisor", "operations", "it", "other"]).optional(),
  deployment: z.enum(["saas", "self-hosted", "hybrid", "unsure"]),
  volume: z.enum(["", "lt5k", "5k-20k", "20k-200k", "gt200k"]).optional(),
  message: z.string().trim().max(2000).optional(),
  locale: z.enum(["fa", "en"]).optional(),
});

export type ContactInput = z.infer<typeof contactSchema>;
export type ContactField = keyof ContactInput;
