# 前端：依赖与构建在宿主机完成（npm install && npm run build → dist/）
# 本镜像仅拷贝已构建产物，构建秒级、依赖可缓存于宿主机 node_modules
FROM nginx:1.27-alpine
RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY dist /usr/share/nginx/html
EXPOSE 80
