# syntax=docker/dockerfile:1
#
# Two stages: build the venv where pip and a C toolchain are allowed to exist,
# copy only the finished venv into a slim runtime image that has neither. The
# same split pyproject.toml's own dev/runtime distinction makes, applied to the
# image instead of to `pip install`.
#
# Base image pinned to the exact patch version .python-version and pyproject's
# requires-python name (3.11), not a floating `3.11-slim` tag: acryl-datahub is
# pinned exact throughout this project for the same reason (see pyproject.toml),
# and an image that drifts to whatever 3.11.x Docker Hub serves this week is the
# one place that discipline would quietly stop applying.

FROM python:3.11.14-slim AS builder

WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# Only what pip needs to resolve and install the package: pyproject.toml (which
# names README.md as the long description, so it must be present too), then the
# source. Copied in that order so an unrelated code change does not bust the
# dependency-resolution layer.
COPY pyproject.toml README.md ./
COPY janus/ janus/

# agent: the LangGraph human-approval gate (`scan --review`) and `janus
# gate`'s policy engine, which imports agent/pipeline.py.
# mcp: janus-mcp, the conversational fourth trigger.
# Neither an LLM provider nor acryl-datahub's own extras are included: detection,
# the gate, and every MCP tool run with no LLM configured (deterministic template
# prose, or none), which is the judge's out-of-the-box path documented in
# README.md. Rebuild with --build-arg JANUS_EXTRAS=agent,mcp,anthropic (or
# openai, or google) to bake a provider in instead of installing it at runtime.
#
# No BuildKit cache mount: portability across build environments (a judge's
# machine, a CI runner, a plain `docker build` with no buildx installed) matters
# more here than a faster rebuild, and --no-cache-dir already keeps the layer
# from carrying pip's own download cache into the image.
ARG JANUS_EXTRAS=agent,mcp
RUN pip install --no-cache-dir ".[${JANUS_EXTRAS}]"

FROM python:3.11.14-slim AS runtime

# Runs as an unprivileged user with no login shell and no home-directory secrets:
# nothing here needs root, and a container that could still run as root if the
# image were ever `docker exec -u root`'d is one honest privilege drop away from
# not being a hardening measure at all.
RUN groupadd --system janus \
    && useradd --system --gid janus --create-home --shell /usr/sbin/nologin janus

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER janus
WORKDIR /home/janus

# No CMD default beyond --help: this image runs four different console scripts
# (janus, janus-mcp, janus-seed, janus-scenario), and a
# default that silently ran one of them on a bare `docker run` would either scan
# nothing (no --table/--model) or seed a graph nobody asked for. Every real
# invocation names its command explicitly; see README.md and docker-compose.yml.
ENTRYPOINT ["janus"]
CMD ["--help"]
