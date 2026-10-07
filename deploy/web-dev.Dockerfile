FROM node:24-bookworm-slim

WORKDIR /app
COPY web/package.json web/package-lock.json ./
RUN npm ci \
    && npx playwright install --with-deps chromium

EXPOSE 5173

CMD ["sh", "-c", "npm ci && exec npm run dev -- --host 0.0.0.0 --port 5173 --strictPort"]
