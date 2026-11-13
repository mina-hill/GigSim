---
id: cs-fs-p10
project_id: fs-p10
type: case_study
persona: fullstack
title: ops console
client_industry: logistics
date: 2023-10
skills:
- admin panel
- TypeScript
- Prisma
synthetic: true
---
# ops console

**Context.** Operations staff edited production data directly in the database.

**What I did**

- Type-safe admin console with audit logging and approvals for risky edits

**Results**

- direct database edits: stopped entirely

Tools: admin panel, TypeScript, Prisma

An excerpt from `src/server/routers/orders.ts`:

```
import { z } from "zod";
import { protectedProcedure, router } from "../trpc";

export const ordersRouter = router({
  updateStatus: protectedProcedure
    .input(z.object({ orderId: z.string().uuid(), status: z.enum(["held", "released"]) }))
    .mutation(async ({ ctx, input }) => {
      await ctx.prisma.auditLog.create({ data: { actor: ctx.user.id, action: "order.status", payload: input } });
      return ctx.prisma.order.update({ where: { id: input.orderId }, data: { status: input.status } });
    }),
```
