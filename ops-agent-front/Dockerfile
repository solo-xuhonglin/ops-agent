# 前端：基于已 build 的 dist 目录，用 nginx 托管
FROM nginx:1.27-alpine

# 清除默认配置
RUN rm -f /etc/nginx/conf.d/default.conf

# 拷贝自定义配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 拷贝前端构建产物
COPY dist /usr/share/nginx/html

EXPOSE 80
