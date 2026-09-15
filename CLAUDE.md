# Adv360-Pro-ZMK — notas del repositorio

Fork de Kinesis del firmware ZMK para el Kinesis Advantage360 Pro, con
personalizaciones propias.

## hostsync

Este repo contiene, además del firmware, **`hostsync/`**: un sistema que hace que
el teclado y el mouse (Logitech MX Master 3S) cambien de computadora en
simultáneo.

**Si vas a tocar `hostsync/`, `config/macros.dtsi`, `config/adv360.keymap` o
`config/boards/arm/adv360/adv360_left_defconfig`, leé primero
[`hostsync/AGENTS.md`](hostsync/AGENTS.md).** Tiene el contexto operativo, el
estado verificado del hardware, y las trampas del protocolo HID++ que ya nos
costaron horas.

Lo mínimo que hay que saber:

- **No flashear el teclado.** Compilar (`make`) sí; flashear lo hace el usuario.
- **No disparar switches reales** sin avisar: `mxswitch.py --host X` mueve el
  mouse a otra computadora de verdad. Usá `--status` y `--dry-run`.
- **No tocar `settings-reset.uf2`**: borra los emparejamientos Bluetooth.
- El mapa perfil-BT ↔ slot-del-mouse se edita **solo** en `hostsync/hosts.toml`.
  Después de cualquier cambio: `python3 hostsync/mxswitch.py --verify-keymap`.

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
testigo de hostsync no llegan nunca al host, sin ningún error visible.
`--verify-keymap` lo vigila.
