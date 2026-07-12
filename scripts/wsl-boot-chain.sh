#!/usr/bin/env sh
set -eu

# Keep existing machine boot hooks, then start iCloud HME.
if [ -x /mnt/x/fk-web-glm/scripts/fkcodex-wsl-boot.sh ]; then
  /mnt/x/fk-web-glm/scripts/fkcodex-wsl-boot.sh || echo "fkcodex boot failed: $?" >&2
fi

if [ -x /mnt/x/project/icloud-hme/scripts/run-autostart-service.sh ]; then
  /mnt/x/project/icloud-hme/scripts/run-autostart-service.sh --detach || echo "icloud-hme autostart failed: $?" >&2
fi
