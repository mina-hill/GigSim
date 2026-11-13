import { createAdapter } from "@socket.io/redis-adapter";
import { createClient } from "redis";
import { Server } from "socket.io";

const io = new Server(4000, { cors: { origin: process.env.WEB_ORIGIN } });
const pub = createClient({ url: process.env.REDIS_URL });
const sub = pub.duplicate();
await Promise.all([pub.connect(), sub.connect()]);
io.adapter(createAdapter(pub, sub));

io.on("connection", (socket) => {
  socket.on("join", (conversationId: string) => socket.join(conversationId));
  socket.on("typing", (conversationId: string) => socket.to(conversationId).emit("typing"));
});
