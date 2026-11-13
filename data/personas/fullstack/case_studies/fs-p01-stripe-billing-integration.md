---
id: cs-fs-p01
project_id: fs-p01
type: case_study
persona: fullstack
title: Stripe billing integration
client_industry: retail
date: 2025-05
skills:
- Stripe
- Next.js
- webhooks
- PostgreSQL
synthetic: true
---
# Stripe billing integration

For a retail client (2025-03 to 2025-05). The team billed customers by sending invoices manually each month.

Stripe Checkout and Billing with three plans, trials and proration. Webhook handler storing processed event ids so retries never double-apply. Billing page with invoices, card updates and cancellation.

Outcome: manual invoicing: eliminated for 1,200 customers; duplicate webhook side effects after launch: 0.

An excerpt from `src/app/api/stripe/webhook/route.ts`:

```
import Stripe from "stripe";
import { prisma } from "@/lib/prisma";

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!);

export async function POST(req: Request) {
  const sig = req.headers.get("stripe-signature")!;
  const event = stripe.webhooks.constructEvent(await req.text(), sig, process.env.STRIPE_WEBHOOK_SECRET!);
  // Idempotency: Stripe retries webhooks, so remember every event we processed.
  const seen = await prisma.processedEvent.findUnique({ where: { id: event.id } });
```
