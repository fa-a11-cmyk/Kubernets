FROM busybox:1.36 AS assets
WORKDIR /site
COPY src/ .

FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime
COPY --from=assets /site/ /usr/share/nginx/html/
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080

