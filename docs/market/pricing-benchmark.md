# Homayar public pricing benchmark

Last reviewed: 2026-08-18

Homayar uses explicit list prices rather than a formula derived from internal
catalog values. The public price position is based on these official benchmarks:

| Product | Public benchmark | Pricing basis |
| --- | ---: | --- |
| Google Cloud Customer Experience Insights | $0.015-$0.025/minute | Voice conversation analysis |
| Google Cloud Quality AI | $50/agent/month | Up to 2,000 evaluated conversations per agent |
| Avoma Organization + Conversation Intelligence | $58/recorder/month | Annual base plan plus CI add-on |
| Avoma Enterprise + Conversation Intelligence | $68/recorder/month | Annual base plan plus CI add-on, minimum 10 seats |
| Fireflies Business | $19/seat/month | Annual plan; advanced AI usage also uses credits |
| MeetGeek Business | $17/user/month | Annual meeting-assistant plan |

Sources:

- [Google Cloud Conversational Insights pricing](https://cloud.google.com/contact-center/insights/pricing)
- [Avoma pricing](https://www.avoma.com/pricing)
- [Fireflies pricing](https://fireflies.ai/pricing)
- [MeetGeek pricing](https://meetgeek.ai/pricing)

## Positioning decision

- Starter remains $499/month for up to 2,000 analyzed calls. This competes with
  contact-center QA pricing while including transcription, speaker separation,
  issue detection, scorecards, and team reporting in one plan.
- Professional remains $1,499/month for up to 10,000 analyzed calls. This is a
  volume-based alternative to per-seat pricing and keeps advanced workflows,
  integrations, and governance in one predictable tier.
- Enterprise and workload-dependent products remain quote-based because
  infrastructure, residency, integration, and throughput materially change cost.
- Add-ons retain their explicit catalog list prices. Public code must not apply an
  arbitrary multiplier or divisor to them.

## Rial display rate

The showcase uses 1 USD = 1,890,000 IRR, matching the 2026-08-04 open-market
sell rate of 189,000 toman published by Bonbast. It is a display reference, not
an invoice promise. Contract documents must record their own agreed exchange rate.

Source: [Bonbast exchange-rate history](https://www.bon-bast.com/history)
