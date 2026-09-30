"""Avvio. Decifra formula e dati, poi server o build."""

from __future__ import annotations

import os
import sys

import vault


def main() -> None:
    vault.install_core()
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        import build_cdn_bundle

        skip = os.environ.get("PORCINI_SKIP_METEO", "") in ("1", "true", "yes")
        build_cdn_bundle.build(skip_meteo=skip)
        return
    import server

    server.main()


if __name__ == "__main__":
    main()
