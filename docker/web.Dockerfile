FROM node:22-alpine AS dev
WORKDIR /app
ENV npm_config_cache=/tmp/.npm \
    npm_config_update_notifier=false
COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
# Source is bind-mounted in docker-compose.yml; this copy keeps the image usable on its own.
COPY . .
CMD ["npm", "run", "dev"]
