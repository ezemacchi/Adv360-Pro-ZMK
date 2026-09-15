# hostsync en Windows

El listener de Windows. Escucha las teclas testigo F13/F14/F15 del Adv360 y
mueve el MX Master 3S al slot correspondiente antes de que el teclado salte de
perfil.

## Estado verificado (2026-09-15)

Instalado y probado end-to-end en una maquina Windows real:

- `hid.enumerate()` SI devuelve `usage_page = 0xFF43` para el MX Master 3S.
  `WindowsTransport.enumerate()` lo encuentra sin ajustes.
- El device index resuelto en runtime fue `0x01` (no `0xFF`; aca el mouse
  aparecio como GATT/HID-over-Bluetooth, clasificado como "usb" por el
  transporte, no como "bluetooth" via campo `BTH` del path). El codigo lo
  resuelve solo probando el orden `1..6` seguido de `0xFF` para ese caso,
  como esta escrito.
- AutoHotkey SI recibe F13/F14/F15 de un teclado Bluetooth sin que otra cosa
  se las coma antes.
- Switch real, ida y vuelta Windows -> Arch -> Windows: teclado y mouse
  volvieron los dos.
- **Bug encontrado y corregido:** `windows/hostsync.ahk` nombraba a su funcion
  `Switch`, que es palabra reservada en AutoHotkey v2 (la sentencia
  switch/case). El script fallaba al cargar con `Error: Expected
  Case/Default`, sin loguear nada — parecia que el testigo nunca llegaba.
  Renombrada a `SwitchHost`.

## Lo que sigue sin verificar

- El comportamiento con Logi Options+ corriendo. Ver "Conflictos" (no se probo
  a proposito: se cerro antes de instalar).
- Que el `wait-ms = 120` del keymap alcance bajo condiciones adversas (mouse
  en suspension profunda, Bluetooth con interferencia). El round-trip funciono
  con el valor tal cual esta.
- El host 3 (`windows-dualboot`): sigue sin bindear a proposito.

## Requisitos

1. **Python 3.11 o superior** (hace falta `tomllib`, que entro en 3.11).
   Marcar "Add python.exe to PATH" al instalar.
2. **hidapi**:
   ```
   pip install hidapi
   ```
   En Windows el backend de hidapi es nativo, no necesita drivers extra.
3. **AutoHotkey v2** (no v1, la sintaxis es incompatible):
   https://www.autohotkey.com/, o por linea de comandos:
   ```
   winget install --id AutoHotkey.AutoHotkey -e
   ```

## Instalacion

1. Clona el repo, o copia la carpeta `hostsync\` completa. El script de Windows
   necesita `mxswitch.py`, `hidpp.py` y `hosts.toml`, que estan un nivel arriba.

2. Comproba que encuentra el mouse:
   ```
   python hostsync\mxswitch.py --status
   ```
   Tiene que imprimir el transporte, el indice de 0x1814, la cantidad de slots
   y en cual esta. Si falla, mira "Diagnostico".

3. Proba un cambio sin ejecutarlo:
   ```
   python hostsync\mxswitch.py --host arch --dry-run
   ```

4. **Cerra Logi Options+ desde el icono de la bandeja** (no alcanza con la
   ventana, ver "Conflictos" abajo), y arranca el listener a mano:
   ```
   hostsync\windows\hostsync.ahk
   ```
   Sin dialogo de error ni ventana significa que cargo bien (no tiene UI:
   corre en background). Apreta una tecla testigo real desde el teclado y
   revisa el log (ver "Diagnostico") para confirmar que llego.

5. Para que arranque solo: `Win+R` -> `shell:startup` -> crea ahi un acceso
   directo a `hostsync.ahk`.

   Si preferis que corra como servicio, el Programador de tareas con
   "Ejecutar tanto si el usuario inicio sesion como si no" **no sirve**: sin
   sesion interactiva AutoHotkey no ve las teclas.

## Conflictos de acceso HID++

Tres programas se pelean por el mismo canal:

| | Que hacer |
|---|---|
| **Logi Options+** | **Cerralo.** Hace polling de 0x1814 y te pisa el cambio de slot. No alcanza con cerrar la ventana: sali desde el icono de la bandeja, y si persiste desactiva `LogiOptionsMgr` del inicio. |
| **OpenLogi** | Cerralo tambien, mismo motivo. |
| **Solaar** | No corre en Windows, no aplica. En Linux SI podes convivir con el. |

Si necesitas Options+ para otra cosa, abrilo, configura, y cerralo. No lo dejes
residente.

## Diagnostico

Log: `%LOCALAPPDATA%\hostsync\mxswitch.log`

Codigos de salida:

| | |
|---|---|
| 0 | ok (cambio, o ya estaba ahi) |
| 2 | hosts.toml ilegible o incoherente |
| 3 | mouse no encontrado |
| 4 | sin permisos sobre el HID |
| 5 | error de protocolo HID++ |
| 6 | la orden se perdio, el mouse no se movio |
| 7 | destino marcado como no conmutable |
| 8 | argumentos invalidos |
| 9 | desalineado entre hosts.toml y el keymap |

**Si sale 3 (no encontrado)**, lo mas probable es el filtro de usage page.
Corre esto para ver que expone el mouse de verdad:

```python
import hid
for d in hid.enumerate(0x046D, 0xB034):
    print(hex(d['usage_page']), hex(d['usage']), d['path'])
```

Busca la interfaz con usage page `0xff43`. Si el mouse esta por receptor Bolt
en vez de Bluetooth, el product id no es `0xB034`: corre `hid.enumerate(0x046D, 0)`
y fijate cual aparece, despues actualiza `product_id` en `hosts.toml`.

## El host 3

`windows-dualboot` esta marcado `switchable = false` en `hosts.toml` porque es
el mismo equipo fisico que `arch` (dual boot, mismo adaptador Bluetooth). Saltar
ahi desde Arch te deja sin teclado ni mouse. El listener lo rechaza con codigo 7.

Esto no aplica a esta maquina Windows, que es un equipo separado
(`WIN-2MQ42904Q9`, adaptador 58:6d:67:a8:23:01) y es el host 1.
