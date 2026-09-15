# hostsync

Switch coordinado de teclado + mouse entre computadoras.

El Adv360 (ZMK) solo puede liderar: no hay forma de que un host le ordene
cambiar de perfil. Y el MX Master 3S solo acepta ordenes de la maquina a la que
esta conectado en ese momento. De ahi el diseño:

```
  tecla de macro en el Adv360
        |
        +--1--> tecla testigo (F13/F14/F15) al host ACTUAL, que todavia escucha
        |          |
        |          +--> keyd / AutoHotkey --> mxswitch.py --> HID++ 0x1814
        |                                          |
        |                                          +--> el mouse salta de slot
        |
        +--2--> espera (wait-ms, CALIBRAR)
        |
        +--3--> &bt BT_SEL n  --> recien aca el teclado se va
```

La tecla testigo codifica el **destino**, no el origen.

## El mapa

Todo sale de `hosts.toml`. Es el unico lugar donde se editan estos numeros.
Para armar el tuyo, `mxswitch.py --discover` lee los slots reales del mouse.
El mapa de abajo es el de esta maquina, a modo de ejemplo.

| host | teclado | mouse | testigo | |
|---|---|---|---|---|
| 1 `windows` | `BT_SEL 0` | slot 1 | F13 | maquina separada, `WIN-2MQ42904Q9` |
| 2 `arch` | `BT_SEL 1` | slot 2 | F14 | este equipo |
| 3 `windows-dualboot` | `BT_SEL 2` | slot 3 | F15 | **no conmutable**, ver abajo |

Verificado contra el hardware el 2026-09-15: HID++ 4.5, 0x1814 en el indice
0x0a, 3 slots, Bluetooth LE directo.

### Por que el host 3 no se puede conmutar

`windows-dualboot` es el **mismo equipo fisico** que `arch` (mismo adaptador,
`08:5b:d6:34:dc:5c`). Dual boot: nunca corren los dos a la vez. Saltar ahi desde
Arch mandaria mouse y teclado a un sistema apagado y te dejaria sin perifericos,
sin vuelta atras por software. Salida: boton Easy-Switch del mouse y botones de
perfil del teclado.

Por eso `switchable = false` y por eso la macro `hostsync_windows_dualboot`
existe en el keymap pero **no esta bindeada a ninguna tecla**. El dia que el
host 3 sea un equipo de verdad, se bindea y se saca la bandera.

## El problema de los tres archivos

El mapa vive en tres lugares y no hay forma de que sea uno solo: ZMK compila a
devicetree dentro del firmware y no puede leer un TOML en runtime.

Lo que si se puede es detectar el desalineamiento:

```
python3 mxswitch.py --verify-keymap
```

Compara `hosts.toml` con `config/macros.dtsi` y avisa si se separaron: perfiles
BT que no coinciden, teclas testigo cambiadas, macros faltantes, o el testigo
mandado una sola vez en vez de dos. Corre solo al iniciar sesion
(`hostsync-verify.service`) y notifica. Los scripts de Linux y de Windows leen
`hosts.toml` en runtime, asi que esos dos nunca se desalinean.

## Instalacion

Guia completa, desde cero y para alguien que no conoce el proyecto:
**[SETUP.md](SETUP.md)**. Resumen:

```sh
sudo pacman -S keyd
sudo bash hostsync/linux/install.sh          # udev + wrapper + keyd.conf

python3 hostsync/mxswitch.py --status        # tiene que andar SIN sudo
python3 hostsync/mxswitch.py --discover      # que hay en cada slot del mouse

cp hostsync/hosts.example.toml hostsync/hosts.toml   # y completalo
python3 hostsync/mxswitch.py --verify-keymap

sudo systemctl enable --now keyd
```

Ojo: hace falta compilar y flashear tu propio firmware. Las macros que mandan
la tecla testigo viven adentro del firmware; no hay forma de agregarlas desde
el sistema operativo. Para Windows, ver `windows/INSTALL.md` (no probado).

## Calibracion

El unico numero que hay que ajustar a mano es el `wait-ms` de las macros del
keymap (arranca en 120). Es el margen entre que el host recibe el testigo y el
teclado se desconecta.

Muy corto: el teclado se va antes de que el listener termine, el mouse se queda.
Muy largo: se siente lento.

Procedimiento: apreta la macro, y si el mouse no se movio, subilo de a 40 y
reflashea. El log (`~/.local/state/hostsync/mxswitch.log`) te dice si el script
llego a correr, que es como distinguir "el testigo no llego" de "el testigo
llego pero el mouse no obedecio".

## Convivencia con otro software HID++

| | |
|---|---|
| **Solaar** (Linux) | Podes convivir. No toma el dispositivo en exclusiva; segui usandolo para bateria y DPI. |
| **Logi Options+** (Windows) | **Cerralo.** Hace polling de 0x1814 y pisa el cambio de slot. |
| **OpenLogi** | Cerralo, mismo motivo. |

Hoy en esta maquina no corre ninguno.

## Detalles del protocolo que cuestan caro descubrir

- **Solo reports long (0x11), 20 bytes.** El descriptor del MX Master 3S por BLE
  no expone el report short 0x10. Casi todos los ejemplos de 0x1814 dando vueltas
  usan short y fallan sin decir nada.
- **IRoot fn0 es GetFeature, fn1 es GetProtocolVersion.** Invertirlos devuelve la
  version del protocolo donde esperabas un indice de feature, y el error recien
  aparece un paso despues, como `INVALID_FUNCTION_ID`.
- **Device index 0xFF por BLE directo**, 1..6 detras de un receptor Bolt. Se
  detecta probando, no se asume.
- **La MAC del mouse cambia por slot.** Verificado: al mover de slot 3 a slot 2
  paso de `dd:47:18:39:0d:71` a `dd:47:18:39:0d:74`. Identificar por serial se
  rompe justo cuando el mouse hace lo unico que nos importa: por eso el match es
  por VID/PID + nombre.
- **El exito de setCurrentHost se confirma por ausencia.** Si el switch funciona,
  el mouse se desconecta al instante y la respuesta nunca llega. Esperar una
  respuesta y tratar el timeout como error reportaria fallo justo cuando todo
  salio bien. `wait_until_gone()` invierte la logica: si el mouse sigue
  respondiendo y `getHostInfo` devuelve el host viejo, la orden se perdio
  (tipico con el mouse en suspension profunda) y se reintenta.
