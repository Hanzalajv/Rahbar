# Stage 1: Build & verify static assets
FROM alpine:3.21 AS builder

WORKDIR /src
COPY index.html nginx.conf ./
RUN test -f index.html && test -f nginx.conf

# Stage 2: Minimal non-root nginx runtime
FROM nginxinc/nginx-unprivileged:1.27-alpine

USER 101

COPY --from=builder --chown=101:101 /src/index.html /usr/share/nginx/html/index.html
COPY --from=builder --chown=101:101 /src/nginx.conf /etc/nginx/nginx.conf

EXPOSE 8080

CMD ["nginx", "-g", "daemon off;"]
