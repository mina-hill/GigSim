import Stripe from "stripe";
import { prisma } from "@/lib/prisma";

const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!);

export async function POST(req: Request) {
  const sig = req.headers.get("stripe-signature")!;
  const event = stripe.webhooks.constructEvent(await req.text(), sig, process.env.STRIPE_WEBHOOK_SECRET!);
  // Idempotency: Stripe retries webhooks, so remember every event we processed.
  const seen = await prisma.processedEvent.findUnique({ where: { id: event.id } });
  if (seen) return new Response("duplicate", { status: 200 });
  if (event.type === "customer.subscription.updated") {
    const sub = event.data.object as Stripe.Subscription;
    await prisma.account.update({ where: { stripeCustomerId: String(sub.customer) }, data: { plan: sub.items.data[0].price.lookup_key } });
  }
  await prisma.processedEvent.create({ data: { id: event.id } });
  return new Response("ok");
}
