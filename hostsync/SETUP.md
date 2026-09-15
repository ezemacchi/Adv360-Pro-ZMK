# hostsync — guia de instalacion desde cero

Esto hace que el teclado y el mouse cambien de computadora juntos, con una sola
tecla. Esta guia asume que no sabes nada del proyecto.

## Lo primero: esto NO es plug and play

Para que esto funcione tenes que:

- **compilar y flashear tu propio firmware del Adv360.** Las macros que mandan
  la tecla testigo viven adentro del firmware; no hay forma de agregarlas desde
  el sistema operativo. Si nunca flasheaste el teclado, ese es el paso mas
  largo de todos.
- **editar el keymap a mano**, adaptando las macros a tu mapa de perfiles.
- **ajustar `hosts.toml`** con los numeros de TU hardware, que casi seguro no
  son los del autor.
- si tu mouse **no es un MX Master 3S**, cambiar el `product_id` (y aceptar que
  el resto del protocolo esta verificado solo contra ese modelo).
- el lado **Windows nunca se ejecuto**. Esta escrito segun la documentacion,
  no probado. Ver `windows/INSTALL.md`.

Si eso te parece demasiado, este proyecto no te sirve todavia.

## Que hace falta

**Hardware**

| | |
|---|---|
| Teclado | Kinesis Advantage360 Professional con firmware **ZMK** (este repo) |
| Mouse | Logitech con HID++ 2.0 y varios slots Easy-Switch. Verificado: **MX Master 3S** (`046d:b034`) por Bluetooth LE directo |
| Hosts | 2 o 3 computadoras, pareadas en perfiles del teclado y slots del mouse |

**Software**

| | |
|---|---|
| Python | ≥ 3.11 (hace falta `tomllib`, que es de la stdlib desde 3.11) |
| Linux | `keyd` (captura F13/F14/F15 debajo de X11 y de Wayland) |
| Linux | acceso a `/dev/hidraw*` del mouse, via la regla udev que instala `install.sh` |
| Windows | AutoHotkey **v2** y `pip install hidapi` |

En Linux **no hace falta** ninguna dependencia de Python: se habla hidraw crudo
a proposito, porque el backend libusb de `hidapi` no ve dispositivos Bluetooth.

## Como funciona, en tres lineas

El Adv360 solo puede liderar: ningun host puede ordenarle cambiar de perfil. Y
el mouse solo acepta ordenes de la maquina a la que esta conectado **en ese
momento**. Por eso la macro del teclado hace, en este orden:

```
1. tecla testigo (F13/F14/F15)  -> al host ACTUAL, que todavia escucha
2. espera (wait-ms)
3. &bt BT_SEL n                 -> recien aca el teclado se va
```

El host actual recibe el testigo, y antes de que el teclado se desconecte manda
el mouse al slot destino por HID++ 0x1814. **La tecla testigo codifica el
destino, no el origen.** Invertir el orden dentro de la macro rompe todo en
silencio.

---

## Paso 1 — clonar y probar que el mouse contesta

```sh
git clone <este repo>
cd Adv360-Pro-ZMK
```

Antes de nada, instala la regla udev, que es lo unico que necesitas para
hablarle al mouse sin `sudo`:

```sh
sudo bash hostsync/linux/install.sh
```

Es idempotente: podes correrlo las veces que quieras. Deduce solo donde esta el
clon, asi que si lo moves de lugar, volve a correrlo.

La regla se aplica **cuando el dispositivo se reconecta**. Si el paso siguiente
falla con codigo 4, desconecta y reconecta el mouse (o reinicia el bluetooth).

```sh
python3 hostsync/mxswitch.py --status
```

Tiene que andar **sin sudo** y mostrar el modelo, la version de HID++, la
cantidad de slots y en cual esta ahora. Si esto funciona, el resto es plomeria.

Si dice "no hay ningun HID 046d:b034 presente", tu mouse tiene otro product id:

```sh
grep HID_ID /sys/class/hidraw/*/device/uevent    # Linux
```

y pone ese valor en `product_id` (paso 3).

## Paso 2 — descubrir tu mapa

Este es el comando que no se puede saltear: nadie puede adivinar que slot del
mouse corresponde a que computadora.

```sh
python3 hostsync/mxswitch.py --discover
```

Es **de solo lectura**: no cambia de slot ni toca nada. Imprime, para cada slot
del mouse, el indice (0-based), el numero de slot (1-based, el que ves en el
boton Easy-Switch), el nombre amigable con el que esa computadora se presento,
su direccion Bluetooth, y cual es el actual. Algo asi:

```
idx  slot     nombre                direccion           estado        bus
0    1        WIN-2MQ42904Q9        58:6d:67:a8:23:01   emparejado    tipo 4
1    2     -> cachyos-x8664         08:5b:d6:34:dc:5c   emparejado    tipo 4
2    3        cachyos-x8664         08:5b:d6:34:dc:5c   emparejado    tipo 4
```

Despues de la tabla te imprime bloques `[[hosts]]` listos para pegar.

**Que mirar:**

- Si un slot sale **sin nombre**, esa computadora nunca se pareo ahi. Pareala
  primero y volve a correr `--discover`.
- Si dos slots tienen la **misma direccion**, son el mismo equipo fisico (tipico
  dual boot). Ese es un caso peligroso: ver "El caso del dual boot".

## Paso 3 — escribir tu `hosts.toml`

```sh
cp hostsync/hosts.example.toml hostsync/hosts.toml
```

La plantilla trae `999` y `cambiame` a proposito: si intentas usarla sin tocar,
`mxswitch.py` se niega con un mensaje que te manda de vuelta a `--discover`. No
hay forma de creer que instalaste algo que funciona cuando no.

Pega los bloques que te dio `--discover` y ajusta:

| campo | que es |
|---|---|
| `id` | numero libre y unico; sirve para `--host 2` |
| `name` | identificador corto, unico, minusculas. **Tiene que coincidir con la macro del keymap**: `arch` -> `hostsync_arch` (los guiones pasan a `_`) |
| `bt_profile` | 0-based: el `n` de `&bt BT_SEL n` del **teclado** |
| `mouse_slot` | 0-based: slot 1 del Easy-Switch = `0` |
| `witness` | `f13` / `f14` / `f15`, una por host |
| `switchable` | `false` para bloquear un destino peligroso |
| `this_host` | marca el host donde corre este listener |

`bt_profile` y `mouse_slot` **no tienen por que coincidir**: son dos numeraciones
independientes (una la elegis vos en el keymap, la otra la fijo el orden en que
pareaste el mouse).

Comproba:

```sh
python3 hostsync/mxswitch.py --list
```

## Paso 4 — adaptar las macros del keymap

Aca hay que editar archivos del firmware. Mira `config/macros.dtsi`: hay una
macro por host, con esta forma (una por cada `[[hosts]]` de tu TOML):

```
hostsync_arch: hostsync_arch {
    compatible = "zmk,behavior-macro";
    #binding-cells = <0>;
    wait-ms = <120>;
    bindings
        = <&kp F14>          // testigo: DOS VECES a proposito
        , <&kp F14>
        , <&bt BT_SEL 1>;    // recien aca el teclado se va
};
```

Reglas que no se pueden romper:

1. **El nombre de la macro es `hostsync_` + el `name` del TOML**, con los
   guiones convertidos en `_`.
2. **El testigo va primero, el `BT_SEL` ultimo.** Al reves, el testigo llega a
   la maquina destino, que no tiene idea de que paso.
3. **El testigo se manda DOS VECES.** El Adv360 duerme a los 30 segundos y el
   primer report al despertar se puede perder. El listener deduplica.
4. Bindea cada macro a una tecla en la capa que uses (`config/adv360.keymap`),
   **salvo** las que marcaste `switchable = false`.
5. **No reemplaces los `&bt BT_SEL n` sueltos de la capa Mod.** Son tu salida de
   emergencia si el sistema falla.

Hay un guard mas, que cuesta horas descubrir a mano: en
`config/boards/arm/adv360/adv360_left_defconfig` hace falta

```
CONFIG_ZMK_HID_KEYBOARD_EXTENDED_REPORT=y
```

Con `NKRO=y` y `EXTENDED_REPORT=n` (que es como viene el fork) **F13–F24 no se
transmiten**: el teclado salta de perfil y el testigo no llega nunca, sin error,
sin log, sin ninguna pista.

Chequea todo junto:

```sh
python3 hostsync/mxswitch.py --verify-keymap
```

Compara tu `hosts.toml` contra `config/macros.dtsi`, `config/keymap.json` y el
defconfig. Sale con codigo 9 si algo no coincide y te dice exactamente que.

> Sobre `config/keymap.json`: es el archivo del configurador grafico de Kinesis.
> El build **no** lo usa, pero si abris el editor web y exportas, regenera
> `adv360.keymap` desde ahi y se lleva puestas las macros. Mientras edites el
> `.keymap` a mano, este aviso es esperado.

Despues: compila y flashea tu firmware (ver el README principal del repo). Hasta
que no flashees, F13/F14/F15 no existen y **el sistema entero esta inerte**.

## Paso 5 — instalacion en Linux

```sh
sudo pacman -S keyd            # o el equivalente de tu distro

# udev + wrapper + config de keyd, con la ruta real de tu clon adentro
sudo bash hostsync/linux/install.sh

# verificacion al iniciar sesion (opcional, recomendado)
sudo bash hostsync/linux/install.sh --user-units
systemctl --user daemon-reload
systemctl --user enable --now hostsync-verify.service

# recien cuando --status funcione sin sudo:
sudo systemctl enable --now keyd
```

`install.sh` imprime al final el estado de cada archivo y los permisos del
hidraw del mouse. **Si moves el clon de directorio, volve a correrlo**: la ruta
queda grabada adentro del wrapper.

`/etc/keyd/hostsync.conf` filtra por el id del Adv360 (`1d50:615e`) para que un
F13 de otro teclado no mueva el mouse. Si tu teclado reporta otro id:

```sh
sudo keyd monitor
```

Apreta cualquier tecla: la primera columna de cada linea es el `vid:pid` que va
en `[ids]`. No uses `*`: agarraria todos los teclados.

Prueba final, sin riesgo:

```sh
python3 hostsync/mxswitch.py --host <nombre> --dry-run
```

## Paso 6 — Windows

`windows/INSTALL.md` tiene el procedimiento completo (AutoHotkey v2,
`pip install hidapi`, arranque via `shell:startup`).

**Esa parte nunca se ejecuto ni se probo.** Esta escrita segun la
documentacion del protocolo. El punto de verdad es el mismo:
`python hostsync\mxswitch.py --status`. Si eso anda, el resto es plomeria.

---

## El caso del dual boot

Si `--discover` te muestra dos slots con la misma direccion de adaptador, son
el mismo equipo fisico y **nunca corren a la vez**. Saltar de uno al otro manda
teclado y mouse a un sistema apagado y te deja sin perifericos, sin vuelta atras
por software.

Dos protecciones, y hacen falta las dos:

1. `switchable = false` en `hosts.toml`. Protege al **mouse**: el listener
   rechaza con codigo 7.
2. **No bindear la macro a ninguna tecla.** Esta es la unica que protege al
   **teclado**: ningun flag del TOML puede impedir que el firmware salte.

## Calibracion

El unico numero que se ajusta a mano es el `wait-ms` de las macros (arranca en
120). Es el margen entre que el host recibe el testigo y el teclado se va.

- **Muy corto**: el teclado se desconecta antes de que el listener termine y el
  mouse se queda.
- **Muy largo**: se siente lento.

Apreta la macro y mira el log:

```sh
tail -f ~/.local/state/hostsync/mxswitch.log
```

- **No aparece ninguna linea** → el testigo no llego. Subi el `wait-ms` de a 40
  y reflashea, o revisa `EXTENDED_REPORT`.
- **Aparece y dice codigo 6** → el testigo llego, el mouse no obedecio (estaba
  dormido). Ahi se tocan `retries` / `retry_delay_ms` en `hosts.toml`.

## Convivencia con otro software HID++

| | |
|---|---|
| **Logi Options+** (Windows) | **Cerralo.** Hace polling de 0x1814 y pisa el cambio de slot. No alcanza con cerrar la ventana: sali desde la bandeja |
| **OpenLogi** | Cerralo, mismo motivo |
| **Solaar** (Linux) | Podes convivir. No toma el dispositivo en exclusiva |

## Salida de emergencia

Si quedas sin perifericos en la maquina equivocada:

- **Mouse**: boton Easy-Switch abajo, elegi el slot a mano.
- **Teclado**: `BT_SEL n` en la fila 1 de la capa Mod.

## Si las teclas testigo no llegan

Sintoma: el teclado tipea normal, cambia de perfil, pero el log no registra
nada al apretar la macro y el mouse no se mueve.

**Casi siempre es el descriptor HID cacheado por el host**, no un problema del
firmware ni de la instalacion. Pasa cada vez que cambia
`CONFIG_ZMK_HID_KEYBOARD_EXTENDED_REPORT`, porque eso altera el report
descriptor y los hosts Bluetooth lo tienen memorizado del emparejamiento
anterior.

El diagnostico y el arreglo, para Linux y para Windows, estan en
[`AGENTS.md`](AGENTS.md), seccion "EL problema que mas tiempo cuesta".

No reflashees buscando arreglarlo: el firmware esta bien, el host esta
desactualizado.

## Codigos de salida

`0` ok · `2` config · `3` mouse no encontrado · `4` permisos · `5` protocolo ·
`6` la orden se perdio · `7` destino no conmutable · `8` argumentos ·
`9` desalineado
