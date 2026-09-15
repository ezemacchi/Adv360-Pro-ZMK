# hostsync — notas operativas para agentes

Esto **no** es una guia de instalacion. Para instalar, leé `SETUP.md`. Acá estan
las reglas de oro, las trampas del protocolo, qué está verificado y qué no, y
cómo diagnosticar.

**Fecha del relevamiento: 2026-09-15.** Todo lo marcado como "verificado" se
comprobó contra el hardware real ese día. Si leés esto mucho después, corré
`mxswitch.py --status` antes de confiar en los números.

Mapa de archivos:

| | |
|---|---|
| `SETUP.md` | instalación desde cero, para un tercero |
| `README.md` | qué es y por qué el diseño es así |
| `hosts.toml` | **la config viva del usuario.** No la edites sin que te lo pidan |
| `hosts.example.toml` | plantilla; sus valores `999`/`cambiame` se detectan y rechazan |
| `mxswitch.py` | CLI y lógica |
| `hidpp.py` | protocolo HID++ y transportes (Linux hidraw / Windows hidapi) |
| `linux/`, `windows/` | instalación por plataforma |

---

## Reglas de oro

1. **No dispares un switch real sin permiso explícito.** `mxswitch.py --host X`
   mueve el mouse del usuario a otra computadora **de verdad**. Si esa máquina
   está apagada, se queda sin mouse hasta que lo traiga con el botón físico
   Easy-Switch. Comandos inocuos, todos de solo lectura sobre el mouse:
   `--status`, `--list`, `--discover`, `--dry-run`, `--verify-keymap`.
2. **No flashees el teclado.** El usuario lo hace. Podés compilar (`make`),
   nunca flashear. Y no corras `make` si te avisaron que hay un flasheo en curso.
3. **No toques `settings-reset.uf2`.** Borra los emparejamientos Bluetooth del
   teclado y obliga a re-parear los tres perfiles.
4. **El mapa se edita en `hosts.toml` y en ningún otro lado.** Si cambiás un
   número ahí, corré `mxswitch.py --verify-keymap` antes de dar nada por hecho.
5. **No reemplaces los `&bt BT_SEL n` sueltos de la capa Mod** por macros
   hostsync. Son la salida de emergencia del usuario.

---

## La arquitectura, y por qué es así

El Adv360 (ZMK) **solo puede liderar**: no existe ningún servicio que le permita
a un host ordenarle cambiar de perfil. El MX Master 3S **solo acepta órdenes de
la máquina a la que está conectado en ese momento**. De ahí:

```
macro del teclado
   1. tecla testigo (F13/F14/F15) -> al host ACTUAL, que todavía escucha
   2. espera (wait-ms, calibrable)
   3. &bt BT_SEL n  -> recién acá el teclado se va
```

**La tecla codifica el destino, no el origen.** Invertir el orden dentro de la
macro rompe todo en silencio: el testigo llegaría al host destino, que no tiene
idea de qué pasó.

---

## Estado verificado del hardware

| | |
|---|---|
| Mouse | Logitech MX Master 3S, `046d:b034` |
| Transporte | Bluetooth LE directo (adaptador Intel AX200). **Sin receptor Bolt.** |
| HID++ | 4.5 |
| Reports | **Solo long `0x11`** (20 bytes). No existe el short `0x10`. |
| Device index | `0xFF` (BLE directo). Detrás de un Bolt sería `1..6`. |
| `0x1814` CHANGE_HOST | índice `0x0a` |
| `0x1815` HOSTS_INFO | índice `0x0b` |
| Slots | 3 |

Los dos índices de feature se resuelven **en runtime** vía IRoot/GetFeature.
Los números de arriba son de este modelo y este firmware: no los cablees.

### El mapa de esta máquina

Confirmado el 2026-09-15 con `mxswitch.py --discover`:

| host | teclado | mouse | testigo | |
|---|---|---|---|---|
| 1 `windows` | `BT_SEL 0` | slot 1 (idx 0) | F13 | máquina separada, `WIN-2MQ42904Q9`, adaptador `58:6d:67:a8:23:01` |
| 2 `arch` | `BT_SEL 1` | slot 2 (idx 1) | F14 | esta máquina, `cachyos-x8664`, adaptador `08:5b:d6:34:dc:5c` |
| 3 `windows-dualboot` | `BT_SEL 2` | slot 3 (idx 2) | F15 | **no conmutable** |

**El host 3 es el mismo equipo físico que el host 2** (dual boot, mismo
adaptador; `--discover` lo delata porque la dirección se repite). Nunca corren a
la vez. Saltar ahí desde Arch deja al usuario sin teclado ni mouse, sin vuelta
atrás por software. Por eso:

- `switchable = false` en `hosts.toml` (el listener rechaza con código 7), y
- la macro `hostsync_windows_dualboot` **existe pero no está bindeada a ninguna
  tecla**. El flag protege al mouse, pero no puede impedir que el teclado salte
  igual: no bindearla es la única protección real.

El slot 3 del mouse todavía tiene el pareo de `arch` (se pisó el viejo "DESK").
Cuando el dual boot vuelva a existir hay que parearlo de cero.

---

## Trampas del protocolo que cuestan horas

Todas descubiertas a los golpes en este hardware:

- **IRoot fn0 es `GetFeature`, fn1 es `GetProtocolVersion`.** Invertirlos
  devuelve la versión del protocolo donde esperabas un índice de feature, y el
  error recién aparece un paso después como `INVALID_FUNCTION_ID`, apuntando al
  lugar equivocado.
- **Solo reports long.** El descriptor del MX Master 3S por BLE no expone el
  report short `0x10`. Los ejemplos de 0x1814 que circulan usan short y fallan
  sin decir nada.
- **Las respuestas de error** vienen con feature index `0xFF`, el eco de la
  petición en los bytes 4–5 y el código en el byte 6.
- **El éxito de `setCurrentHost` se confirma por ausencia.** Si el switch
  funciona, el mouse se desconecta al instante y la respuesta nunca llega.
  Esperar respuesta y tratar el timeout como error reporta fallo justo cuando
  todo salió bien. `wait_until_gone()` invierte la lógica.
- **La MAC del mouse cambia por slot.** Verificado: al mover de slot 3 a slot 2
  pasó de `dd:47:18:39:0d:71` a `dd:47:18:39:0d:74`. **Nunca identifiques el
  dispositivo por serial/MAC** — se rompe justo cuando el mouse hace lo único
  que importa. El match es por VID/PID + nombre.

### Layouts de `0x1815` HOSTS_INFO (verificados)

Cada uno tiene su trampa. Están implementados en `hidpp.py` y los usa
`--discover`:

| fn | qué | layout |
|---|---|---|
| 0 | `getHostsInfo` | `[caps_hi, caps_lo, numHosts, currentHost]` |
| 1 | `getHostDescriptor(hostIndex)` | `[hostIndex, status, busType, numPages, nameLen, nameMaxLen]` |
| 2 | `getHostAddress(hostIndex)` | MAC en los bytes 3..8 |
| 3 | `getHostFriendlyName(hostIndex, byteIndex)` | `[hostIndex, byteIndex, chars...]` |

- **fn0: las capabilities son DOS bytes.** Leerlo como uno corre todo un lugar
  y devuelve cantidades imposibles (leímos "8 hosts" con un mouse de 3).
- **fn1: el largo del nombre es el byte 4, NO el 3** (el 3 es `numPages`).
- **fn2: la MAC viene EN ORDEN INVERTIDO.** Hay que darla vuelta.
- **fn3: hay que pedir por chunks** subiendo `byteIndex` hasta juntar `nameLen`,
  y cortar en el primer NUL (el report viene padeado a 20 bytes).
- **fn3: la PRIMERA llamada de una sesión devuelve vacío**, de forma
  consistente, mientras que la misma llamada hecha después funciona. Parece un
  paquete obsoleto en la cola de lectura. Solución: drenar la cola antes de
  empezar (`transport.drain()`) y reintentar una vez si el nombre vuelve vacío
  con `nameLen > 0`. **Prueba de que está arreglado: el slot 1 tiene que mostrar
  `WIN-...` (14 caracteres), no vacío.**
- El `busType` de este mouse devuelve `4`, que no está en las tablas públicas.
  Se muestra como `tipo 4` y no se usa para nada.

---

## El problema de los archivos duplicados

El mapa vive en varios lugares porque **ZMK compila a devicetree dentro del
firmware y no puede leer un TOML en runtime**. No hay forma de que sea uno solo.
Lo que sí se puede es detectar la deriva:

```sh
python3 hostsync/mxswitch.py --verify-keymap
```

Compara `hosts.toml` contra:

1. `config/macros.dtsi` — perfiles BT, teclas testigo, y que el testigo se mande
   **dos veces** (el Adv360 duerme a los 30s y el primer report al despertar se
   puede perder).
2. `config/keymap.json` — el archivo del configurador gráfico de Kinesis. El
   build **no** lo usa, pero si alguien abre el editor web y exporta, regenera
   `adv360.keymap` desde ahí y se lleva puestas las macros. **Hoy avisa siempre**
   y es el único problema esperado: `--verify-keymap` sale con código 9 con
   exactamente ese aviso y ninguno más. Si aparece otro, algo se rompió.
3. `config/boards/arm/adv360/adv360_left_defconfig` — que
   `CONFIG_ZMK_HID_KEYBOARD_EXTENDED_REPORT=y`.

### Sobre el punto 3, que es el más traicionero

El fork viene con `CONFIG_ZMK_HID_REPORT_TYPE_NKRO=y` y `EXTENDED_REPORT=n`.
**Con esa combinación F13–F24 no se transmiten.** El teclado saltaría de perfil
sin que el testigo llegue nunca: sin error, sin log, sin ninguna pista. Ya está
corregido y el guard lo vigila. Si alguien hace merge de upstream, revisá que no
se haya revertido.

---

## Estado de la instalación en esta máquina (Arch / CachyOS)

| | |
|---|---|
| `/etc/udev/rules.d/60-hostsync-hidpp.rules` | instalado; `--status` anda sin sudo |
| `/usr/local/bin/hostsync-switch` | instalado (**ojo**: la versión de `linux/` cambió, ver abajo) |
| `/etc/keyd/hostsync.conf` | mapea f13/f14/f15, solo del `1d50:615e` |
| `keyd.service` | enabled + running |
| `hostsync-verify.service` (user) | chequeo al iniciar sesión |

**Si tocás `linux/hostsync-switch` o `linux/hostsync-verify.service`, avisale al
usuario que tiene que reinstalar** (`sudo bash hostsync/linux/install.sh`): lo
instalado en `/usr/local/bin` y en `~/.config/systemd/user` es una copia, no un
symlink. Los dos archivos del repo son **plantillas** con el marcador
`@HOSTSYNC_DIR@`, que `install.sh` reemplaza por la ruta real del clon.

**El número de la regla udev importa.** Tiene que ser `< 70`: el tag `uaccess`
lo procesa `70-uaccess.rules`, así que una regla `99-` etiqueta el dispositivo
después de que el procesador ya pasó, y el hidraw se queda `root:root 0600` sin
ningún error visible. Esto ya nos pasó.

**Lo que falta en Arch:** el firmware con las macros. Hasta que el usuario
flashee, F13/F14/F15 no existen y el sistema entero está inerte.

---

## Qué NO está verificado

Sé honesto sobre esto con el usuario:

- **Todo `windows/`.** Ni una línea ejecutada. Nunca lo presentes como probado.
  Lo primero que va a fallar ahí es `WindowsTransport.enumerate()`, que filtra
  por `usage_page == 0xFF43`; si en Windows viene distinto, sale con código 3.
  Diagnóstico: `python -c "import hid; print(hid.enumerate(0x046D, 0xB034))"`.
  Y si el mouse estuviera por receptor Bolt en vez de BLE, el product id no es
  `0xb034`.
- **Un switch real.** Nunca se ejecutó un `setCurrentHost` — habría movido el
  mouse del usuario a otra máquina en mitad de la sesión. La ruta de
  confirmación por desconexión y los reintentos están escritos según el
  protocolo, pero no ejercitados.
- **El `wait-ms = 120`** de las macros. Es el único número a calibrar.
- **keyd end-to-end.** El daemon corre y agarró el teclado (verificado con
  `keyd monitor`), pero nunca recibió un F13 real porque el firmware no los
  emite todavía.
- **`install.sh` con `--user-units`** y la resolución de ruta del wrapper en una
  máquina limpia. La sintaxis está chequeada (`bash -n`); la instalación real no
  se volvió a correr para no pisar lo que ya funciona.

---

## Comandos útiles

```sh
python3 hostsync/mxswitch.py --status         # inocuo
python3 hostsync/mxswitch.py --discover       # inocuo: slots reales del mouse
python3 hostsync/mxswitch.py --list           # el mapa segun hosts.toml
python3 hostsync/mxswitch.py --verify-keymap  # deriva entre archivos
python3 hostsync/mxswitch.py --host windows --dry-run
sudo keyd monitor                             # ¿llegan los testigos? ¿qué id tiene el teclado?
tail -f ~/.local/state/hostsync/mxswitch.log
```

Códigos de salida: 0 ok · 2 config · 3 no encontrado · 4 permisos · 5 protocolo
· 6 orden perdida · 7 no conmutable · 8 argumentos · 9 desalineado.

### Cómo distinguir las dos fallas que se confunden

Al apretar la macro, mirando el log:

- **No aparece ninguna línea** → el testigo no llegó. Subí el `wait-ms`, o
  revisá `EXTENDED_REPORT`.
- **Aparece y dice código 6** → el testigo llegó, el mouse no obedeció (dormido).
  Ahí se tocan `retries` / `retry_delay_ms`.

---

## Salida de emergencia

Si el usuario queda sin periféricos en la máquina equivocada:

- **Mouse:** botón Easy-Switch abajo, elige el slot a mano.
- **Teclado:** `BT_SEL n` en la fila 1 de la capa Mod.
