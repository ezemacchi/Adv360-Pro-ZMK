#!/usr/bin/env python3
"""
mxswitch - mueve el MX Master 3S al slot que corresponde al host destino.

Lee TODO el mapa de hosts.toml. No hay ningun numero de slot ni de perfil
cableado en este archivo, a proposito.

Uso:
    mxswitch.py --host arch          # por nombre
    mxswitch.py --host 2             # por id
    mxswitch.py --witness f14        # por tecla testigo (lo que usa keyd)
    mxswitch.py --status             # que ve el mouse ahora mismo
    mxswitch.py --discover           # que hay en cada slot (para armar hosts.toml)
    mxswitch.py --verify-keymap      # hosts.toml vs config/macros.dtsi
    mxswitch.py --list

Codigos de salida:
    0  ok (cambio, o ya estaba en el destino)
    2  error de configuracion (hosts.toml ilegible o incoherente)
    3  mouse no encontrado
    4  sin permisos sobre el hidraw (falta la regla udev)
    5  error de protocolo HID++
    6  la orden se perdio: el mouse no se movio (suspension profunda)
    7  el destino esta marcado como no conmutable
    8  argumentos invalidos / host desconocido
    9  destino duplicado o desalineado entre archivos
"""
import argparse
import json
import logging
import os
import re
import sys
import time
import tomllib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hidpp import (HidppDevice, DeviceNotFound, ProtocolError, DeviceGone,
                   FEAT_CHANGE_HOST, FEAT_HOSTS_INFO, HOST_STATUS, BUS_TYPE)

EX_OK, EX_CONFIG, EX_NODEV, EX_PERM = 0, 2, 3, 4
EX_PROTO, EX_LOST, EX_NOSWITCH, EX_USAGE, EX_MISMATCH = 5, 6, 7, 8, 9

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("HOSTSYNC_CONFIG", os.path.join(HERE, "hosts.toml"))
REPO_ROOT = os.path.dirname(HERE)
MACROS_PATH = os.path.join(REPO_ROOT, "config", "macros.dtsi")
KEYMAP_JSON_PATH = os.path.join(REPO_ROOT, "config", "keymap.json")
DEFCONFIG_PATH = os.path.join(REPO_ROOT, "config", "boards", "arm", "adv360",
                              "adv360_left_defconfig")

log = logging.getLogger("hostsync")


def setup_logging(verbose):
    if sys.platform.startswith("win"):
        logdir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                              "hostsync")
    else:
        # keyd ejecuta esto como ROOT. Si nos guiaramos por HOME, los switches
        # reales irian a /root/.local/state/ y las corridas manuales del usuario
        # a la suya: dos logs distintos, y el que mires nunca tiene lo que
        # buscas. Ya nos paso. Cuando el wrapper nos dice de quien es la sesion
        # (HOSTSYNC_USER), escribimos SIEMPRE en el directorio de ese usuario.
        owner_uid = owner_gid = None
        home = os.path.expanduser("~")
        if os.geteuid() == 0 and os.environ.get("HOSTSYNC_USER"):
            try:
                import pwd
                ent = pwd.getpwnam(os.environ["HOSTSYNC_USER"])
                home, owner_uid, owner_gid = ent.pw_dir, ent.pw_uid, ent.pw_gid
            except (ImportError, KeyError):
                pass
        base = os.environ.get("XDG_STATE_HOME") if owner_uid is None else None
        logdir = os.path.join(base or os.path.join(home, ".local", "state"),
                              "hostsync")
        if os.geteuid() == 0 and not os.access(os.path.dirname(logdir), os.W_OK):
            logdir = "/var/log/hostsync"
    try:
        os.makedirs(logdir, exist_ok=True)
        logfile = os.path.join(logdir, "mxswitch.log")
        handlers = [logging.FileHandler(logfile)]
        # Que el usuario pueda leer lo que escribio root, y que root pueda
        # seguir escribiendo lo que creo el usuario.
        if not sys.platform.startswith("win") and os.geteuid() == 0 \
                and owner_uid is not None:
            for path in (logdir, logfile):
                try:
                    os.chown(path, owner_uid, owner_gid)
                except OSError:
                    pass
            try:
                os.chmod(logfile, 0o664)
            except OSError:
                pass
    except OSError:
        handlers = []
    handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers, force=True)


# --------------------------------------------------------------------------
# Configuracion
# --------------------------------------------------------------------------

class ConfigError(Exception):
    pass


def load_config(path=CONFIG_PATH):
    try:
        with open(path, "rb") as fh:
            cfg = tomllib.load(fh)
    except FileNotFoundError:
        raise ConfigError(
            f"no existe {path}. Copia la plantilla y completala:\n"
            f"  cp {os.path.join(HERE, 'hosts.example.toml')} {path}\n"
            f"  python3 {os.path.join(HERE, 'mxswitch.py')} --discover")
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} no parsea: {exc}")

    hosts = cfg.get("hosts") or []
    if not hosts:
        raise ConfigError(f"{path} no define ningun [[hosts]]")

    # Primero la plantilla: los valores de ejemplo estan repetidos a proposito,
    # asi que el chequeo de unicidad de abajo dispararia antes y taparia la
    # causa real ("999 repetido" en vez de "no completaste hosts.toml").
    stale = template_problems({"hosts": hosts})
    if stale:
        raise ConfigError(
            f"{path} sigue siendo la plantilla sin completar:\n  - "
            + "\n  - ".join(stale))

    # Chequeos de coherencia. Sin esto, el desalineamiento es silencioso, que
    # es exactamente el modo de falla que este archivo existe para evitar.
    seen = {"id": {}, "name": {}, "bt_profile": {}, "mouse_slot": {}, "witness": {}}
    for h in hosts:
        for field in ("id", "name", "bt_profile", "mouse_slot", "witness"):
            if field not in h:
                raise ConfigError(f"host {h.get('name', '?')}: falta '{field}'")
            value = h[field]
            if isinstance(value, str):
                value = value.lower()
                h[field] = value
            if value in seen[field]:
                raise ConfigError(
                    f"'{field}' = {value!r} esta repetido: "
                    f"hosts {seen[field][value]!r} y {h['name']!r}")
            seen[field][value] = h["name"]
        h.setdefault("switchable", True)
        h.setdefault("label", h["name"])
    cfg["hosts"] = hosts
    cfg.setdefault("timing", {})
    cfg.setdefault("notify", {})
    return cfg


# hosts.example.toml usa estos valores justamente para que se noten. Si
# alguien copia la plantilla y la usa sin tocar, el error tiene que ser
# explicito y no un "slot 999 no existe" tres capas mas abajo.
TEMPLATE_SLOT = 999
TEMPLATE_NAME = "cambiame"


def template_problems(cfg):
    """Devuelve la lista de razones por las que esta config sigue siendo la
    plantilla sin completar."""
    problems = []
    for h in cfg.get("hosts", []):
        if h.get("mouse_slot") == TEMPLATE_SLOT:
            problems.append(
                f"host '{h.get('name')}': mouse_slot = {TEMPLATE_SLOT} es el "
                f"valor de ejemplo de hosts.example.toml")
        if h.get("bt_profile") == TEMPLATE_SLOT:
            problems.append(
                f"host '{h.get('name')}': bt_profile = {TEMPLATE_SLOT} es el "
                f"valor de ejemplo de hosts.example.toml")
        if str(h.get("name", "")).startswith(TEMPLATE_NAME):
            problems.append(
                f"host '{h.get('name')}': el nombre sigue siendo el de la "
                f"plantilla")
    if problems:
        problems.append(
            "Corre 'mxswitch.py --discover' para ver los slots reales del "
            "mouse y completar hosts.toml. Ver SETUP.md.")
    return problems


def find_host(cfg, *, name=None, witness=None):
    for h in cfg["hosts"]:
        if name is not None:
            if str(h["id"]) == str(name) or h["name"] == str(name).lower():
                return h
        if witness is not None and h["witness"] == str(witness).lower():
            return h
    return None


def this_host(cfg):
    for h in cfg["hosts"]:
        if h.get("this_host"):
            return h
    return None


# --------------------------------------------------------------------------
# Notificaciones
# --------------------------------------------------------------------------

def notify_success(cfg, host, slot):
    """Notificacion de switch exitoso.

    Apagada por defecto, y no es pereza: se dispara DESPUES de confirmar el
    switch, o sea cuando el mouse ya se fue y vos ya estas mirando la otra
    pantalla. Aparece en la maquina que estas abandonando, asi que en la
    practica no la ve nadie. Las utiles son las de fallo, porque en ese caso
    te quedas en esa maquina.
    """
    if not cfg.get("notify", {}).get("on_success", False):
        return
    notify(cfg, "Mouse -> " + host["label"], f"slot {slot + 1}")


def notify(cfg, title, body, urgency="normal"):
    if not cfg.get("notify", {}).get("enabled", True):
        return
    ncfg = cfg.get("notify", {})
    timeout = str(ncfg.get("timeout_ms", 2000))
    # Por la especificacion de freedesktop, urgency=critical significa "requiere
    # que el usuario la reconozca": el timeout se IGNORA y la notificacion se
    # queda hasta que la cierren a mano. KDE lo implementa al pie de la letra.
    # Verificado en Plasma 6.7.4: la normal se va sola, la critical no.
    # Por defecto no usamos critical para que todo respete timeout_ms.
    if urgency == "critical" and not ncfg.get("use_critical", False):
        urgency = "normal"
    if sys.platform.startswith("win"):
        return
    try:
        import subprocess
        cmd = ["notify-send", "-a", "hostsync", "-t", timeout,
               "-u", urgency, title, body]
        env = dict(os.environ)
        # keyd corre como root: sin esto la notificacion no llega a tu sesion.
        if os.geteuid() == 0:
            uid = os.environ.get("HOSTSYNC_UID")
            user = os.environ.get("HOSTSYNC_USER")
            if uid and user:
                env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path=/run/user/{uid}/bus"
                cmd = ["runuser", "-u", user, "--"] + cmd
            else:
                log.debug("root sin HOSTSYNC_UID/USER: no puedo notificar")
                return
        subprocess.run(cmd, env=env, timeout=3,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        log.debug("notify-send fallo: %s", exc)


# --------------------------------------------------------------------------
# Deduplicacion
# --------------------------------------------------------------------------

def dedup_state_path():
    if sys.platform.startswith("win"):
        base = os.environ.get("TEMP", ".")
    else:
        base = f"/run/user/{os.getuid()}" if os.path.isdir(
            f"/run/user/{os.getuid()}") else "/tmp"
    return os.path.join(base, "hostsync-last.json")


def is_duplicate(cfg, target_name):
    """El keymap manda la tecla testigo dos veces (el ADV360 duerme a los 30s
    y el primer report al despertar se puede perder). La segunda no debe
    disparar un segundo switch."""
    window = cfg["timing"].get("dedup_window_ms", 600) / 1000.0
    path = dedup_state_path()
    now = time.time()
    try:
        with open(path) as fh:
            last = json.load(fh)
        if last.get("target") == target_name and now - last.get("ts", 0) < window:
            return True
    except (OSError, ValueError):
        pass
    try:
        with open(path, "w") as fh:
            json.dump({"target": target_name, "ts": now}, fh)
    except OSError:
        pass
    return False


# --------------------------------------------------------------------------
# Acciones
# --------------------------------------------------------------------------

def open_mouse(cfg):
    m = cfg["mouse"]
    return HidppDevice.open(m["vendor_id"], m["product_id"], m.get("name_hint"))


def cmd_status(cfg):
    dev = open_mouse(cfg)
    try:
        total, current = dev.get_host_info()
        idx = dev.feature_index(FEAT_CHANGE_HOST)
        print(f"dispositivo : {dev.info['path']}  ({dev.info['name']})")
        print(f"transporte  : {dev.info['transport']}  addr={dev.info['address']}")
        print(f"HID++       : {dev.protocol[0]}.{dev.protocol[1]}  "
              f"device_index=0x{dev.device_index:02x}")
        print(f"0x1814      : indice 0x{idx:02x}")
        print(f"slots       : {total}")
        print(f"slot actual : {current} (0-based) -> slot {current + 1}")
        host = next((h for h in cfg["hosts"] if h["mouse_slot"] == current), None)
        print(f"host actual : {host['label'] if host else 'NO MAPEADO en hosts.toml'}")
        if total != len(cfg["hosts"]):
            print(f"\n!! hosts.toml define {len(cfg['hosts'])} hosts pero el mouse "
                  f"reporta {total} slots")
            return EX_MISMATCH
    finally:
        dev.close()
    return EX_OK


def cmd_list(cfg):
    for h in cfg["hosts"]:
        flags = []
        if h.get("this_host"):
            flags.append("este equipo")
        if not h["switchable"]:
            flags.append(f"NO CONMUTABLE ({h.get('same_machine_as', 'ver hosts.toml')})")
        print(f"{h['id']}  {h['name']:<18} BT_SEL {h['bt_profile']}  "
              f"slot {h['mouse_slot'] + 1}  {h['witness']:<4} "
              f"{'  '.join(flags)}")
    return EX_OK


def cmd_discover(cfg):
    """Lee el mapa REAL del mouse via 0x1815 y sugiere el hosts.toml.

    Es de solo lectura: no cambia de slot ni toca nada. Es el primer comando
    que corre alguien que arranca de cero, porque sin esto no hay forma de
    saber que slot del mouse corresponde a que computadora.
    """
    dev = open_mouse(cfg)
    try:
        idx = dev.feature_index(FEAT_HOSTS_INFO)
        total, current, hosts = dev.enumerate_hosts()
        print(f"dispositivo : {dev.info['path']}  ({dev.info['name']})")
        print(f"transporte  : {dev.info['transport']}  "
              f"vendor_id=0x{cfg['mouse']['vendor_id']:04X} "
              f"product_id=0x{cfg['mouse']['product_id']:04X}")
        print(f"HID++       : {dev.protocol[0]}.{dev.protocol[1]}  "
              f"device_index=0x{dev.device_index:02x}")
        print(f"0x1815      : indice 0x{idx:02x}")
        print(f"slots       : {total}\n")

        print(f"{'idx':<5}{'slot':<6}{'':<3}{'nombre':<22}"
              f"{'direccion':<20}{'estado':<14}bus")
        print("-" * 78)
        for h in hosts:
            mark = "->" if h["current"] else "  "
            status = HOST_STATUS.get(h["status"], f"0x{h['status']:02x}"
                                     if h["status"] is not None else "?")
            bus = BUS_TYPE.get(h["bus_type"], f"tipo {h['bus_type']}")
            print(f"{h['index']:<5}{h['slot']:<6}{mark:<3}"
                  f"{h['name'] or '(sin nombre)':<22}"
                  f"{h['address'] or '(sin direccion)':<20}{status:<14}{bus}")
        print("\n-> = slot en el que esta el mouse AHORA")

        print("\nPara hosts.toml (ajusta name/label/bt_profile/witness a tu "
              "teclado):\n")
        # El mismo equipo puede aparecer en dos slots (dual boot: mismo
        # adaptador, mismo nombre). hosts.toml exige nombres unicos, asi que
        # la sugerencia los desambigua en vez de generar un TOML invalido.
        used = {}
        for h in hosts:
            guess = (h["name"] or f"host{h['slot']}").lower().replace(" ", "-")
            used[guess] = used.get(guess, 0) + 1
            if used[guess] > 1:
                guess = f"{guess}-{used[guess]}"
            print("[[hosts]]")
            print(f"id         = {h['slot']}")
            print(f'name       = "{guess}"')
            print(f'label      = "{h["name"] or guess}"')
            print(f"bt_profile = {h['index']}"
                  f"                      # &bt BT_SEL {h['index']} en el keymap")
            print(f"mouse_slot = {h['index']}"
                  f"                      # slot {h['slot']} del Easy-Switch")
            print(f'witness    = "f{12 + h["slot"]}"')
            print("switchable = true")
            if h["current"]:
                print("this_host  = true"
                      "                   # el mouse esta aca ahora; "
                      "confirma que sea ESTA maquina")
            if h["address"]:
                print(f"# direccion del adaptador: {h['address']}")
            print()
        print("Ojo: bt_profile es el perfil del TECLADO y no tiene por que "
              "coincidir con el slot del mouse.")
        dupes = [a for a in {x["address"] for x in hosts if x["address"]}
                 if sum(1 for x in hosts if x["address"] == a) > 1]
        if dupes:
            print("Ojo: hay slots con la MISMA direccion de adaptador "
                  f"({', '.join(dupes)}): probablemente sean el mismo equipo "
                  "fisico (dual boot). Marca uno con switchable = false y "
                  "same_machine_as, y NO bindees su macro.")
        print("Verifica el resultado con: mxswitch.py --list && "
              "mxswitch.py --verify-keymap")
    finally:
        dev.close()
    return EX_OK


def cmd_switch(cfg, host, dry_run=False):
    if not host["switchable"]:
        reason = host.get("same_machine_as")
        msg = (f"{host['label']} es el mismo equipo fisico que "
               f"'{reason}': nunca corren a la vez" if reason
               else f"{host['label']} esta marcado como no conmutable")
        log.warning("switch rechazado: %s", msg)
        notify(cfg, "Switch bloqueado", msg, urgency="critical")
        return EX_NOSWITCH

    if is_duplicate(cfg, host["name"]):
        log.info("tecla testigo repetida para %s dentro de la ventana de "
                 "dedup: ignorada", host["name"])
        return EX_OK

    target = host["mouse_slot"]
    delay = cfg["timing"].get("pre_switch_delay_ms", 0) / 1000.0
    if delay:
        time.sleep(delay)

    dev = open_mouse(cfg)
    try:
        total, current = dev.get_host_info()
        log.info("mouse en slot %d de %d; destino slot %d (%s)",
                 current + 1, total, target + 1, host["label"])

        if target >= total:
            log.error("hosts.toml pide el slot %d pero el mouse solo tiene %d",
                      target + 1, total)
            notify(cfg, "hostsync: configuracion invalida",
                   f"slot {target + 1} no existe (el mouse tiene {total})",
                   urgency="critical")
            return EX_MISMATCH

        if current == target:
            log.info("ya esta en el destino, no hago nada")
            return EX_OK

        if dry_run:
            print(f"[dry-run] mandaria setCurrentHost({target}) "
                  f"= slot {target + 1} ({host['label']})")
            return EX_OK

        retries = cfg["timing"].get("retries", 2)
        verify_timeout = cfg["timing"].get("verify_timeout_ms", 2500) / 1000.0
        retry_delay = cfg["timing"].get("retry_delay_ms", 400) / 1000.0

        for attempt in range(1, retries + 2):
            try:
                dev.set_host(target)
            except DeviceGone:
                # Se fue mientras escribiamos: eso es exito.
                log.info("el mouse se desconecto al mandar la orden (ok)")
                notify_success(cfg, host, target)
                return EX_OK

            if dev.wait_until_gone(verify_timeout):
                log.info("switch confirmado a %s (slot %d) en el intento %d",
                         host["label"], target + 1, attempt)
                notify_success(cfg, host, target)
                return EX_OK

            log.warning("intento %d: el mouse sigue conectado, la orden se "
                        "perdio (suspension profunda?)", attempt)
            if attempt <= retries:
                time.sleep(retry_delay)

        log.error("el mouse no se movio despues de %d intentos", retries + 1)
        notify(cfg, "hostsync: el switch fallo",
               f"El mouse sigue en el slot {current + 1}. "
               f"Usa el boton Easy-Switch.", urgency="critical")
        return EX_LOST
    finally:
        dev.close()


# --------------------------------------------------------------------------
# Verificacion contra el keymap
# --------------------------------------------------------------------------

def macro_name(host_name):
    """Los labels de devicetree no admiten guiones."""
    return "hostsync_" + host_name.replace("-", "_")


MACRO_RE = re.compile(
    r"hostsync_(?P<name>\w+)\s*:\s*hostsync_\w+\s*\{(?P<body>.*?)\}\s*;",
    re.DOTALL)
WITNESS_RE = re.compile(r"&kp\s+(F1[345])\b", re.IGNORECASE)
BTSEL_RE = re.compile(r"&bt\s+BT_SEL\s+(\d+)")


def cmd_verify_keymap(cfg, macros_path=MACROS_PATH, quiet=False):
    """ZMK compila a devicetree y no puede leer hosts.toml, asi que el mapa
    vive por duplicado. Esto compara los dos y avisa si se separaron."""
    problems = list(template_problems(cfg))
    try:
        source = open(macros_path).read()
    except OSError as exc:
        problems.append(f"no pude leer {macros_path}: {exc}")
        source = ""

    found = {}
    for m in MACRO_RE.finditer(source):
        body = m.group("body")
        witness = WITNESS_RE.search(body)
        btsel = BTSEL_RE.search(body)
        found[m.group("name").lower()] = {
            "witness": witness.group(1).lower() if witness else None,
            "bt_profile": int(btsel.group(1)) if btsel else None,
            "witness_count": len(WITNESS_RE.findall(body)),
        }

    for h in cfg["hosts"]:
        label = macro_name(h["name"])
        macro = found.get(label[len("hostsync_"):])
        if macro is None:
            problems.append(
                f"host '{h['name']}': no hay macro {label} en el keymap")
            continue
        if macro["witness"] != h["witness"]:
            problems.append(
                f"host '{h['name']}': hosts.toml dice testigo {h['witness']} "
                f"pero el keymap manda {macro['witness']}")
        if macro["bt_profile"] != h["bt_profile"]:
            problems.append(
                f"host '{h['name']}': hosts.toml dice BT_SEL {h['bt_profile']} "
                f"pero el keymap hace BT_SEL {macro['bt_profile']}")
        if macro["witness_count"] < 2:
            problems.append(
                f"host '{h['name']}': el keymap manda la tecla testigo "
                f"{macro['witness_count']} vez; deberian ser 2 (sleep de 30s)")

    for name in found:
        if not any(macro_name(h["name"]) == "hostsync_" + name for h in cfg["hosts"]):
            problems.append(f"el keymap tiene hostsync_{name} pero hosts.toml no "
                            f"define ese host")

    # config/keymap.json es el archivo que consume el configurador grafico de
    # Kinesis. El build NO lo usa (el Makefile compila desde adv360.keymap),
    # pero si alguien abre el editor web y exporta, regenera adv360.keymap
    # DESDE este json y se lleva puestas las macros sin avisar.
    if found and os.path.exists(KEYMAP_JSON_PATH):
        try:
            if "hostsync" not in open(KEYMAP_JSON_PATH).read():
                problems.append(
                    "config/keymap.json no conoce las macros hostsync: si usas el "
                    "configurador grafico de Kinesis te va a pisar adv360.keymap. "
                    "Edita el .keymap a mano, o regenera el json despues de tocarlo")
        except OSError as exc:
            log.debug("no pude leer keymap.json: %s", exc)

    # Sin esto, las teclas testigo no salen del teclado y el switch falla mudo.
    try:
        dc = open(DEFCONFIG_PATH).read()
        nkro = "CONFIG_ZMK_HID_REPORT_TYPE_NKRO=y" in dc
        ext = re.search(r"^CONFIG_ZMK_HID_KEYBOARD_EXTENDED_REPORT=(\w)",
                        dc, re.MULTILINE)
        if nkro and (not ext or ext.group(1) != "y"):
            problems.append(
                "adv360_left_defconfig tiene NKRO=y con "
                "EXTENDED_REPORT != y: F13-F24 NO se transmiten y las teclas "
                "testigo nunca van a llegar al host")
    except OSError as exc:
        log.debug("no pude leer el defconfig: %s", exc)

    if problems:
        for p in problems:
            log.error("desalineado: %s", p)
            if not quiet:
                print("  !! " + p, file=sys.stderr)
        notify(cfg, "hostsync: keymap desalineado",
               f"{len(problems)} problema(s). Ver el log.", urgency="critical")
        return EX_MISMATCH

    if not quiet:
        print(f"OK: hosts.toml y {os.path.basename(macros_path)} coinciden "
              f"({len(cfg['hosts'])} hosts)")
    log.info("verificacion del keymap OK")
    return EX_OK


# --------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(description="Cambia el host del MX Master 3S")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--host", help="nombre o id del host destino")
    g.add_argument("--witness", help="tecla testigo (f13/f14/f15)")
    g.add_argument("--status", action="store_true")
    g.add_argument("--discover", action="store_true",
                   help="lee los slots del mouse (0x1815) y sugiere hosts.toml")
    g.add_argument("--list", action="store_true")
    g.add_argument("--verify-keymap", action="store_true")
    p.add_argument("--config", default=CONFIG_PATH)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    setup_logging(args.verbose)

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        log.error("configuracion invalida: %s", exc)
        return EX_CONFIG

    try:
        if args.status:
            return cmd_status(cfg)
        if args.discover:
            return cmd_discover(cfg)
        if args.list:
            return cmd_list(cfg)
        if args.verify_keymap:
            return cmd_verify_keymap(cfg)

        host = (find_host(cfg, name=args.host) if args.host
                else find_host(cfg, witness=args.witness))
        if host is None:
            log.error("host desconocido: %s", args.host or args.witness)
            return EX_USAGE
        return cmd_switch(cfg, host, dry_run=args.dry_run)

    except DeviceNotFound as exc:
        log.error("mouse no encontrado: %s", exc)
        notify(cfg, "hostsync: mouse no encontrado", str(exc), urgency="critical")
        return EX_NODEV
    except PermissionError as exc:
        log.error("sin permisos sobre el hidraw (falta la regla udev?): %s", exc)
        return EX_PERM
    except ProtocolError as exc:
        log.error("error de protocolo HID++: %s", exc)
        return EX_PROTO
    except DeviceGone as exc:
        log.error("el dispositivo desaparecio inesperadamente: %s", exc)
        return EX_NODEV


if __name__ == "__main__":
    sys.exit(main())
