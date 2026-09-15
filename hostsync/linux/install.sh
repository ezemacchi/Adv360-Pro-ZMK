#!/bin/bash
# Instalacion de hostsync en Linux. Idempotente: se puede correr de nuevo.
#
#   sudo bash hostsync/linux/install.sh
#   sudo bash hostsync/linux/install.sh --user-units   # + unidad de usuario
#
# No hay ninguna ruta ni usuario cableado: todo se deduce de donde esta este
# script. Si moves el clon de lugar, volve a correr esto.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Correme con sudo: sudo bash hostsync/linux/install.sh" >&2
    exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOSTSYNC_DIR="$(cd "$HERE/.." && pwd)"

if [ ! -f "$HOSTSYNC_DIR/mxswitch.py" ]; then
    echo "No encuentro $HOSTSYNC_DIR/mxswitch.py: corre este script desde el clon." >&2
    exit 1
fi

WANT_USER_UNITS=0
[ "${1:-}" = "--user-units" ] && WANT_USER_UNITS=1

# El usuario real detras del sudo: para instalarle la unidad de usuario y para
# decirle donde mirar. Nunca cableamos un nombre.
TARGET_USER="${SUDO_USER:-}"
TARGET_HOME=""
if [ -n "$TARGET_USER" ]; then
    TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
fi

echo "==> repo detectado: $HOSTSYNC_DIR"

echo "==> regla udev"
# Limpieza de la version mal numerada: 99- corre despues de 70-uaccess.rules
# y por lo tanto no hace nada.
rm -f /etc/udev/rules.d/99-hostsync-hidpp.rules
install -m644 "$HERE/60-hostsync-hidpp.rules" /etc/udev/rules.d/

echo "==> wrapper (con la ruta real del repo adentro)"
# El wrapper trae @HOSTSYNC_DIR@ como marcador; aca queda la ruta de verdad.
sed "s|@HOSTSYNC_DIR@|$HOSTSYNC_DIR|" "$HERE/hostsync-switch" \
    > /usr/local/bin/hostsync-switch.new
chmod 755 /usr/local/bin/hostsync-switch.new
mv /usr/local/bin/hostsync-switch.new /usr/local/bin/hostsync-switch

echo "==> config de keyd"
mkdir -p /etc/keyd
install -m644 "$HERE/keyd.conf" /etc/keyd/hostsync.conf

UNIT_PATH=""
if [ "$WANT_USER_UNITS" -eq 1 ]; then
    if [ -z "$TARGET_HOME" ] || [ ! -d "$TARGET_HOME" ]; then
        echo "==> unidad de usuario: OMITIDA (no pude resolver el HOME de \$SUDO_USER)"
    else
        echo "==> unidad de usuario hostsync-verify.service"
        UNIT_DIR="$TARGET_HOME/.config/systemd/user"
        UNIT_PATH="$UNIT_DIR/hostsync-verify.service"
        mkdir -p "$UNIT_DIR"
        sed "s|@HOSTSYNC_DIR@|$HOSTSYNC_DIR|" "$HERE/hostsync-verify.service" \
            > "$UNIT_PATH"
        chown -R "$TARGET_USER" "$UNIT_DIR"
        echo "    instalada en $UNIT_PATH"
        echo "    habilitala como TU usuario (no como root):"
        echo "      systemctl --user daemon-reload"
        echo "      systemctl --user enable --now hostsync-verify.service"
    fi
fi

echo "==> recargando udev"
udevadm control --reload-rules
udevadm trigger --subsystem-match=hidraw

echo
echo "INSTALADO. keyd NO se habilito todavia, a proposito."
echo
echo "Estado:"
for f in /etc/udev/rules.d/60-hostsync-hidpp.rules \
         /usr/local/bin/hostsync-switch \
         /etc/keyd/hostsync.conf \
         ${UNIT_PATH:-}; do
    [ -e "$f" ] && echo "  ok    $f" || echo "  FALTA $f"
done
echo "  ruta grabada en el wrapper: $(sed -n 's/^    installed="\(.*\)"$/\1/p' \
        /usr/local/bin/hostsync-switch | head -1)"
echo
echo "Permisos del hidraw del mouse:"
for n in /sys/class/hidraw/hidraw*; do
    if grep -q "HID_ID=0005:0000046D" "$n/device/uevent" 2>/dev/null \
    || grep -q "HID_ID=0003:0000046D" "$n/device/uevent" 2>/dev/null; then
        ls -l "/dev/$(basename "$n")"
    fi
done
echo
echo "Si sigue root:root 0600, desconecta y reconecta el mouse."
echo
echo "Proximos pasos:"
echo "  python3 $HOSTSYNC_DIR/mxswitch.py --status     # sin sudo"
echo "  python3 $HOSTSYNC_DIR/mxswitch.py --discover   # armar hosts.toml"
echo "  sudo systemctl enable --now keyd"
