# Nix with flakes. (Earlier base images shipped Determinate Nix; as of the
# 2026-07-16 relaunch the base has an empty /nix — so install it here.)
# Determinate's installer needs no sudo and --init none skips the systemd
# daemon that has no place in a container. build-users-group is cleared so
# builds run as the calling user; sandbox is off because the container can't
# set up build-sandbox user namespaces unprivileged.
RUN rm -rf /nix \
    && curl --proto '=https' --tlsv1.2 -sSf -L https://install.determinate.systems/nix \
        -o /tmp/nix-installer.sh \
    && sh /tmp/nix-installer.sh install linux \
        --init none \
        --no-confirm \
        --extra-conf "experimental-features = nix-command flakes" \
        --extra-conf "build-users-group =" \
        --extra-conf "sandbox = false" \
    && rm /tmp/nix-installer.sh

# Any runtime uid can then manage profiles/DB/gcroots without a daemon. Nix
# re-chmods per-user dirs to 0755 every call and EPERMs if it doesn't own them,
# so delete them here; the runtime user recreates and owns them on first `nix`.
# The `default` profile symlink points INTO per-user (default ->
# per-user/root/profile), so resolve it to its store path FIRST and re-link
# after the delete — otherwise `default` dangles and nix is off PATH.
RUN target="$(readlink -f /nix/var/nix/profiles/default)" \
    && chmod -R go+rwX /nix \
    && rm -rf /nix/var/nix/profiles/per-user /nix/var/nix/gcroots/per-user \
    && ln -sfn "$target" /nix/var/nix/profiles/default
ENV PATH=/nix/var/nix/profiles/default/bin:$PATH

# The Determinate installer wipes /home/claude during a fresh install (the
# 2026-07-16 base ships an empty /nix, so this now runs for real). At build
# time nothing recreates it — the base image's entrypoint only re-runs
# `useradd` at container startup — so recreate it here, world-accessible:
# the runtime uid follows the host user (1000 on Linux, 501 on macOS), so a
# hardcoded chown locks the actual user out after a host change.
RUN mkdir -p /home/claude && chmod 777 /home/claude

# Bazel 7.7.1's bundled JDK 21.0.5 SIGILLs on Armv9 SVE (M-series host,
# JDK-8345296); disable SVE for the bazel server JVM. Cap heap and
# parallelism too — the 4GB container OOM-kills the server during the
# initial parallel torch/CUDA wheel downloads otherwise.
RUN printf '%s\n' \
    'startup --host_jvm_args=-XX:UseSVE=0' \
    'startup --host_jvm_args=-Xmx1g' \
    'common --loading_phase_threads=4' \
    'build --jobs=4' \
    >> /home/claude/.bazelrc \
    && chmod 666 /home/claude/.bazelrc

# 2026-07-29: passwordless sudo so `sudo apt-get install ...` works at
# runtime. The rule matches ALL users because the runtime user is created
# at container start with a host-mapped UID (no fixed name to grant).
RUN apt-get update && apt-get install -y --no-install-recommends \
        sudo \
    && rm -rf /var/lib/apt/lists/* \
    && echo 'ALL ALL=(ALL:ALL) NOPASSWD:ALL' > /etc/sudoers.d/anyuser-nopasswd \
    && chmod 0440 /etc/sudoers.d/anyuser-nopasswd

# 2026-07-29: apt-install helper — `apt-install <pkg>...` installs via
# sudo apt-get AND appends a dated RUN block to this Dockerfile, so
# runtime installs survive the next image rebuild. Blocks append below
# this line, keeping the layers above cached.
COPY apt-install /usr/local/bin/apt-install
RUN chmod 0755 /usr/local/bin/apt-install

# 2026-07-29: openssh-client for git push over ssh (recorded for rebuild;
# this session uses a nix-built openssh instead since sudo isn't live yet).
RUN apt-get update && apt-get install -y --no-install-recommends \
        openssh-client \
    && rm -rf /var/lib/apt/lists/*
