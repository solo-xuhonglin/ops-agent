# ===== 构建阶段：在镜像内 npm install && build =====
FROM node:22-alpine AS build
WORKDIR /app
# 国内镜像源（服务器在腾讯云时可加速；其他环境可注释）
RUN npm config set registry https://registry.npmmirror.com
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build

# ===== 运行阶段：nginx 托管 dist =====
FROM nginx:1.27-alpine
RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
