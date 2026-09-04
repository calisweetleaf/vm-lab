# Somnus VM Lab — Cloud Agent base image.
#
# The live runtime is standard-library-only Python (pyproject declares no
# runtime dependencies), so this image only provides stable system tooling:
#   - Python 3.12 with a `python` alias (repo commands invoke `python`, not `python3`)
#   - qemu-img / qemu-system-x86_64 8.2.2 (doctor preflight + P3/P4 physical proofs;
#     the tests reference the Debian-packaged /usr/bin/qemu-img specifically)
#   - packaging tooling (pip/setuptools/wheel) for the installed-wheel smoke lane
#
# Repository source is checked out separately by Cursor, so nothing is COPYed in.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        sudo \
        python3 \
        python3-pip \
        python3-venv \
        python3-setuptools \
        python3-wheel \
        python-is-python3 \
        qemu-utils \
        qemu-system-x86 \
    && rm -rf /var/lib/apt/lists/*

# The default `ubuntu` (uid 1000) account ships in the noble base image. Grant it
# passwordless sudo so the per-boot `start` step can mount tmpfs at /tmp (see below).
RUN usermod -aG sudo ubuntu \
    && printf 'ubuntu ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/90-ubuntu-nopasswd \
    && chmod 0440 /etc/sudoers.d/90-ubuntu-nopasswd

USER ubuntu
WORKDIR /home/ubuntu
