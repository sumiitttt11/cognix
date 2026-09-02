# =====================================================================
# Cognix, as a container.
#
# There is no build stage and no pip install, because there is nothing to
# build and nothing to install: the server is the standard library, and the
# front end is the files in app/ exactly as they are edited. React and htm
# are vendored under app/vendor/. So this image is the base plus the source,
# and `docker build` takes about as long as the copy does.
#
# What the base image does not have is a shell's worth of tools, which is
# the point of -slim here: fewer things in the image than the one process
# needs is fewer things to keep patched.
# =====================================================================
FROM python:3.12-slim

# Unbuffered so Cloud Logging sees a line when it is written rather than when
# the buffer fills; no .pyc because the filesystem is read-only in production
# and a failed write on every import is noise.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=random

# Not root. The process opens a socket and reads files it does not own, and
# it never writes to disk, so it has no reason to be able to.
RUN useradd --system --create-home --uid 10001 cognix
WORKDIR /app

# Copied by name rather than `COPY . .`, so a file that appears in the tree
# later does not silently end up in a public image. .dockerignore is the
# second half of this — it keeps .env out even if this list grows.
COPY --chown=cognix:cognix serve.py            ./serve.py
COPY --chown=cognix:cognix server/             ./server/
COPY --chown=cognix:cognix app/                ./app/

USER cognix

# Cloud Run sets PORT and expects the process to answer on it; serve.py reads
# it (unprefixed, as the platform sends it) and binds 0.0.0.0 when K_SERVICE
# is present. 8080 is the default for both, and this line is documentation
# for anyone running the image by hand.
ENV PORT=8080
EXPOSE 8080

# No entrypoint script. One process, in the foreground, handling SIGTERM
# itself — which is what Cloud Run sends before it takes the instance away.
CMD ["python", "serve.py"]
