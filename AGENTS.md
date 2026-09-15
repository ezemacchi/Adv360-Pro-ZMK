# Adv360-Pro-ZMK — instrucciones para agentes

Fork de Kinesis del firmware ZMK para el Kinesis Advantage360 Pro, con
personalizaciones propias.

## EMPEZÁ ACÁ

Este repo contiene, además del firmware, **`hostsync/`**: un sistema que hace
que el teclado y el mouse (Logitech MX Master 3S) cambien de computadora en
simultáneo.

**Antes de tocar `hostsync/`, `config/macros.dtsi`, `config/adv360.keymap` o
`config/boards/arm/adv360/adv360_left_defconfig`, leé
[`hostsync/AGENTS.md`](hostsync/AGENTS.md) completo.** No es opcional ni es
documentación de cortesía: tiene el estado verificado del hardware, las trampas
del protocolo HID++ y el problema del descriptor cacheado, que son cosas que ya
costaron horas y que no se deducen mirando el código.

Para instalar desde cero, [`hostsync/SETUP.md`](hostsync/SETUP.md).

## Reglas de oro

1. **No flashees el teclado.** Compilar (`make`) sí; flashear lo hace el
   usuario, con el teclado en la mano.
2. **No dispares switches reales sin avisar.** `mxswitch.py --host X` mueve el
   mouse a otra computadora físicamente. Si esa máquina está apagada, el
   usuario se queda sin mouse hasta traerlo con el botón Easy-Switch. Usá
   `--status`, `--list` y `--dry-run`, que son inocuos.
3. **No toques `settings-reset.uf2`.** Borra los emparejamientos Bluetooth del
   teclado y obliga a re-parear los tres perfiles.
4. **El mapa perfil-BT ↔ slot-del-mouse se edita SOLO en `hostsync/hosts.toml`.**
   Después de cualquier cambio: `python3 hostsync/mxswitch.py --verify-keymap`.
5. **Si las teclas testigo no llegan, es el descriptor cacheado del host, no el
   firmware.** No reflashees buscando arreglarlo. Ver `hostsync/AGENTS.md`.

## Build

```sh
make        # ambas mitades
make left   # solo la izquierda
```

Usa Docker o Podman. Los `.uf2` quedan en `firmware/`. El procedimiento de
flasheo está en el `README.md`.

## Cuidado al mergear upstream

`config/boards/arm/adv360/adv360_left_defconfig` tiene
`CONFIG_ZMK_HID_KEYBOARD_EXTENDED_REPORT=y`, cambiado respecto del upstream.
Con NKRO activado y ese flag en `n`, **F13–F24 no se transmiten** y las teclas
testigo no llegan nunca al host, sin ningún error visible. `--verify-keymap` lo
vigila.
