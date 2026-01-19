FROM node:20-alpine AS deps
WORKDIR /app
COPY ui/package*.json ./
RUN npm ci

FROM node:20-alpine AS build
WORKDIR /app
ARG API_PROXY_TARGET
ARG LOCALSTACK_PROXY_TARGET
ENV API_PROXY_TARGET=${API_PROXY_TARGET}
ENV LOCALSTACK_PROXY_TARGET=${LOCALSTACK_PROXY_TARGET}
COPY --from=deps /app/node_modules ./node_modules
COPY ui/ .
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
EXPOSE 3000
CMD ["node", "server.js"]
