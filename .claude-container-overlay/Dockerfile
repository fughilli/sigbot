# The base image already ships Determinate Nix (flakes enabled, store made
# world-writable, per-user profile dirs deleted so the runtime uid owns them
# on first use). One gap: the `default` profile symlink dangles until a
# per-user profile exists, so `nix` is not on PATH. Repair by linking the
# profile straight at the Determinate Nix store path.
RUN mkdir -p /nix/var/nix/profiles/per-user/root \
    && ln -sfn "$(ls -d /nix/store/*-determinate-nix-[0-9]* | head -1)" \
        /nix/var/nix/profiles/per-user/root/profile \
    && chmod -R go+rwX /nix/var/nix/profiles/per-user
ENV PATH=/nix/var/nix/profiles/default/bin:$PATH
