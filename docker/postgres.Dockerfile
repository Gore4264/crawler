# Postgres image bundling pgvector + pgmq.
#
# Tembo's published `quay.io/tembo/pg16-pgmq` image ships with pgmq but
# not pgvector. Adding pgvector via Debian's apt repo is faster and more
# reproducible than rebuilding either extension from source.
#
# Built once (~30s with the package cache), then reused via docker-compose.

FROM quay.io/tembo/pg16-pgmq:latest

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-16-pgvector \
    && rm -rf /var/lib/apt/lists/*

USER postgres
