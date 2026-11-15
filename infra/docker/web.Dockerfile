# web: Next.js frontend
FROM node:22-alpine AS build
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web/ ./
ARG GIGBID_API_URL=http://api:8000
ENV GIGBID_API_URL=$GIGBID_API_URL
RUN mkdir -p public && npm run build

FROM node:22-alpine
WORKDIR /web
ENV NODE_ENV=production HOSTNAME=0.0.0.0
COPY --from=build /web/.next/standalone ./
COPY --from=build /web/.next/static ./.next/static
COPY --from=build /web/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
