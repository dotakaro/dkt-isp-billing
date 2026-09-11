FROM freeradius/freeradius-server:3.2.7
USER root
RUN if command -v apt-get >/dev/null 2>&1; then \
      apt-get update \
      && apt-get install -y --no-install-recommends freeradius-postgresql \
      && rm -rf /var/lib/apt/lists/*; \
    elif command -v apk >/dev/null 2>&1; then \
      apk add --no-cache freeradius-postgresql || apk add --no-cache freeradius-sql; \
    fi
