{
  description = "signal-ai-bot dev shell and runtime environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
      in
      {
        # M4 will add pkgs.playwright-driver.browsers here; the pip `playwright`
        # pin in requirements.in must match nixpkgs' playwright-driver version —
        # bump them together. The systemd unit then exports
        # PLAYWRIGHT_BROWSERS_PATH and PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS.
        devShells.default = pkgs.mkShell {
          packages = with pkgs; [
            bazelisk
            gallery-dl
          ];
        };
      });
}
