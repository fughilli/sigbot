# Nix with flakes. Determinate's installer needs no sudo and --init none skips
# the systemd daemon that has no place in a container. build-users-group is
# cleared so builds run as the calling user (no nixbld group); sandbox is off
# because the container can't set up build-sandbox user namespaces unprivileged.
RUN curl --proto '=https' --tlsv1.2 -sSf -L https://install.determinate.systems/nix \
        -o /tmp/nix-installer.sh \
    && sh /tmp/nix-installer.sh install linux \
        --init none \
        --no-confirm \
        --extra-conf "experimental-features = nix-command flakes" \
        --extra-conf "build-users-group =" \
        --extra-conf "sandbox = false" \
    && rm /tmp/nix-installer.sh
ENV PATH=/nix/var/nix/profiles/default/bin:$PATH

# Any runtime uid can then manage profiles/DB/gcroots without a daemon. Nix
# re-chmods per-user dirs to 0755 every call and EPERMs if it doesn't own them,
# so delete them here; the runtime user recreates and owns them on first `nix`.
RUN chmod -R go+rwX /nix \
    && rm -rf /nix/var/nix/profiles/per-user /nix/var/nix/gcroots/per-user
