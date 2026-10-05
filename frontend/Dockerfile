# ===== hr-interview-simulator — Web 静态容器（agent 家族 web 段 5302；容器内 :80，host 双层）=====
# 多阶段：node 构建 Vite 产物 → nginx:alpine 静态服务。
# 前端代码不写绝对地址（同源相对 /api），prod 由 VPS vhost 把 /api/ 反代到 api 容器
# （5402），无需 VITE_* 烘焙——比家族 SPA 仓少一个 build-time env 环节。
FROM node:22-alpine AS builder
WORKDIR /app

# 硬约束：npm 依赖一律走 npmmirror（suite root CLAUDE.md §2）
RUN npm config set registry https://registry.npmmirror.com

COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .
RUN npm run build

# ---------- Stage 2: runtime ----------
FROM nginx:alpine AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1

CMD ["nginx", "-g", "daemon off;"]
