---
id: cs-fs-p04
project_id: fs-p04
type: case_study
persona: fullstack
title: buyer-seller messaging
client_industry: marketplace
date: 2024-02
skills:
- WebSockets
- real-time messaging
- React
- Redis
synthetic: true
---
# buyer-seller messaging

For a marketplace client (2023-11 to 2024-02). Buyers and sellers left the platform to talk on other apps.

Real-time messaging with read receipts and typing indicators. Horizontal scaling through Redis pub/sub.

Outcome: deals closed on-platform: up 25%.

An excerpt from `server/index.ts`:

```
import { createAdapter } from "@socket.io/redis-adapter";
import { createClient } from "redis";
import { Server } from "socket.io";

const io = new Server(4000, { cors: { origin: process.env.WEB_ORIGIN } });
const pub = createClient({ url: process.env.REDIS_URL });
const sub = pub.duplicate();
await Promise.all([pub.connect(), sub.connect()]);
io.adapter(createAdapter(pub, sub));

```
