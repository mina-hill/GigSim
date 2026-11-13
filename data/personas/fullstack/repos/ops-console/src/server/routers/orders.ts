import { z } from "zod";
import { protectedProcedure, router } from "../trpc";

export const ordersRouter = router({
  updateStatus: protectedProcedure
    .input(z.object({ orderId: z.string().uuid(), status: z.enum(["held", "released"]) }))
    .mutation(async ({ ctx, input }) => {
      await ctx.prisma.auditLog.create({ data: { actor: ctx.user.id, action: "order.status", payload: input } });
      return ctx.prisma.order.update({ where: { id: input.orderId }, data: { status: input.status } });
    }),
});
