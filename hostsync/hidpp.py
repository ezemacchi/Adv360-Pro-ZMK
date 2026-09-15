"""
Capa HID++ 2.0 para el MX Master 3S, con backends de Linux y Windows.

Verificado contra el hardware el 2026-09-15 (BLE directo, HID++ 4.5):
  - La interfaz expone SOLO el report long 0x11 (20 bytes). No hay short 0x10.
    Casi todos los ejemplos de 0x1814 dando vueltas usan short y fallan mudos.
  - Por BLE directo el device index es 0xFF. Por receptor Bolt es 1..6.
    Se detecta en runtime, no se asume.
  - 0x1814 vivia en el indice 0x0a, pero eso es de ESTE modelo y ESTE firmware:
    siempre se resuelve via IRoot/GetFeature.
"""
import os
import sys
import glob
import time
import logging

log = logging.getLogger("hostsync.hidpp")

LONG_REPORT_ID = 0x11
LONG_LEN = 20
IROOT_INDEX = 0x00
IROOT_GET_FEATURE = 0x00        # ojo: fn0 es GetFeature, fn1 es el ping
IROOT_GET_PROTOCOL = 0x01
FEAT_CHANGE_HOST = 0x1814
FEAT_HOSTS_INFO = 0x1815
CHANGE_HOST_GET_INFO = 0x00
CHANGE_HOST_SET_HOST = 0x01

# 0x1815 HOSTS_INFO. Los layouts de abajo estan verificados contra el
# MX Master 3S; ver los comentarios de cada metodo, que documentan las
# trampas (capabilities de 2 bytes, MAC invertida, nameLen en el byte 4).
HOSTS_INFO_GET_INFO = 0x00
HOSTS_INFO_GET_DESCRIPTOR = 0x01
HOSTS_INFO_GET_ADDRESS = 0x02
HOSTS_INFO_GET_NAME = 0x03

HOST_STATUS = {0: "no emparejado", 1: "emparejado", 2: "conectado"}
BUS_TYPE = {0: "btclassic", 1: "ble", 2: "receptor", 3: "usb"}

DEV_INDEX_BLE = 0xFF
DEV_INDEX_RECEIVER = tuple(range(1, 7))

HIDPP_ERRORS = {
    0x01: "Unknown", 0x02: "InvalidArgument", 0x03: "OutOfRange",
    0x04: "HWError", 0x05: "LogitechInternal", 0x06: "InvalidFeatureIndex",
    0x07: "InvalidFunctionId", 0x08: "Busy", 0x09: "Unsupported",
}


class DeviceNotFound(Exception):
    pass


class ProtocolError(Exception):
    pass


class DeviceGone(Exception):
    """El dispositivo desaparecio. Tras un setCurrentHost esto es EXITO."""


# --------------------------------------------------------------------------
# Backends de transporte
# --------------------------------------------------------------------------

class LinuxTransport:
    """hidraw crudo. No usamos hidapi en Linux a proposito: su backend libusb
    no ve dispositivos Bluetooth, y el serial que expone es la MAC, que en
    este mouse cambia por slot."""

    def __init__(self, path):
        self.path = path
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)

    @staticmethod
    def enumerate(vendor_id, product_id, name_hint=None):
        found = []
        for node in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
            try:
                raw = open(os.path.join(node, "device", "uevent")).read()
            except OSError:
                continue
            info = dict(l.split("=", 1) for l in raw.splitlines() if "=" in l)
            parts = info.get("HID_ID", "").split(":")
            if len(parts) != 3:
                continue
            try:
                bus, vid, pid = int(parts[0], 16), int(parts[1], 16), int(parts[2], 16)
            except ValueError:
                continue
            if vid != vendor_id or pid != product_id:
                continue
            found.append({
                "path": "/dev/" + os.path.basename(node),
                "name": info.get("HID_NAME", ""),
                "address": info.get("HID_UNIQ", ""),
                "transport": "bluetooth" if bus == 0x0005 else "usb",
            })
        if name_hint and len(found) > 1:
            narrowed = [f for f in found if name_hint.lower() in f["name"].lower()]
            if narrowed:
                found = narrowed
        return found

    def write(self, data):
        try:
            os.write(self.fd, data)
        except OSError as exc:
            raise DeviceGone(str(exc)) from exc

    def read(self, timeout):
        import select
        try:
            ready, _, _ = select.select([self.fd], [], [], timeout)
        except OSError as exc:
            raise DeviceGone(str(exc)) from exc
        if not ready:
            return None
        try:
            return os.read(self.fd, 64)
        except BlockingIOError:
            return None
        except OSError as exc:
            raise DeviceGone(str(exc)) from exc

    def alive(self):
        return os.path.exists(self.path)

    def drain(self):
        """Tira todo lo que haya encolado. Sin esto, la primera lectura puede
        devolver un report viejo (notificacion asincrona o respuesta de una
        corrida anterior) y hacer fallar la primera llamada."""
        while True:
            try:
                if not os.read(self.fd, 64):
                    return
            except (BlockingIOError, OSError):
                return

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass


class WindowsTransport:
    """hidapi. En Windows cada interfaz HID es un device propio: hay que elegir
    la del usage page 0xFF43 (vendor Logitech), no la primera que aparezca.

    NO VERIFICADO CONTRA HARDWARE: escrito sin acceso a la maquina Windows."""

    USAGE_PAGE = 0xFF43

    def __init__(self, path):
        import hid
        self.dev = hid.device()
        self.dev.open_path(path)
        self.dev.set_nonblocking(1)
        self._alive = True

    @staticmethod
    def enumerate(vendor_id, product_id, name_hint=None):
        import hid
        found = []
        for d in hid.enumerate(vendor_id, product_id):
            if d.get("usage_page") != WindowsTransport.USAGE_PAGE:
                continue
            found.append({
                "path": d["path"],
                "name": d.get("product_string") or "",
                "address": d.get("serial_number") or "",
                "transport": "bluetooth" if "BTH" in str(d["path"]).upper() else "usb",
            })
        return found

    def write(self, data):
        try:
            if self.dev.write(data) < 0:
                raise DeviceGone("write fallo")
        except OSError as exc:
            self._alive = False
            raise DeviceGone(str(exc)) from exc

    def read(self, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                data = self.dev.read(64, timeout_ms=50)
            except OSError as exc:
                self._alive = False
                raise DeviceGone(str(exc)) from exc
            if data:
                return bytes(data)
        return None

    def alive(self):
        return self._alive

    def drain(self):
        while True:
            try:
                if not self.dev.read(64, timeout_ms=0):
                    return
            except OSError:
                return

    def close(self):
        try:
            self.dev.close()
        except Exception:
            pass


def get_transport_class():
    return WindowsTransport if sys.platform.startswith("win") else LinuxTransport


# --------------------------------------------------------------------------
# Protocolo
# --------------------------------------------------------------------------

class HidppDevice:
    def __init__(self, transport, device_index):
        self.t = transport
        self.device_index = device_index
        self._sw_id = 0
        self._feature_cache = {}

    # -- descubrimiento ----------------------------------------------------

    @classmethod
    def open(cls, vendor_id, product_id, name_hint=None):
        cls_t = get_transport_class()
        candidates = cls_t.enumerate(vendor_id, product_id, name_hint)
        if not candidates:
            raise DeviceNotFound(
                f"no hay ningun HID {vendor_id:04x}:{product_id:04x} presente")
        last = None
        denied = []
        for cand in candidates:
            log.debug("probando %s (%s, %s)", cand["path"], cand["name"], cand["transport"])
            try:
                transport = cls_t(cand["path"])
            except PermissionError as exc:
                # No lo tapamos como "no encontrado": el diagnostico correcto
                # es que falta la regla udev, no que el mouse no este.
                denied.append(f"{cand['path']}: {exc}")
                log.debug("sin permisos sobre %s: %s", cand["path"], exc)
                continue
            except OSError as exc:
                last = exc
                log.debug("no se pudo abrir %s: %s", cand["path"], exc)
                continue
            # El device index depende del transporte: 0xFF por BLE directo,
            # 1..6 detras de un receptor Bolt. Se prueba, no se asume.
            order = ([DEV_INDEX_BLE] + list(DEV_INDEX_RECEIVER)
                     if cand["transport"] == "bluetooth"
                     else list(DEV_INDEX_RECEIVER) + [DEV_INDEX_BLE])
            for idx in order:
                dev = cls(transport, idx)
                try:
                    major, minor = dev.ping()
                except (ProtocolError, DeviceGone, TimeoutError):
                    continue
                dev.info = cand
                dev.protocol = (major, minor)
                log.info("mouse en %s via %s, device_index=0x%02x, HID++ %d.%d",
                         cand["path"], cand["transport"], idx, major, minor)
                return dev
            transport.close()
            last = f"{cand['path']} no respondio HID++ en ningun device index"
        if denied and last is None:
            raise PermissionError(
                "sin permisos sobre el hidraw del mouse: " + "; ".join(denied))
        raise DeviceNotFound(str(last) if last else "no responde HID++")

    # -- primitivas --------------------------------------------------------

    def _next_sw_id(self):
        self._sw_id = (self._sw_id % 15) + 1
        return self._sw_id

    def call(self, feature_index, function_id, params=b"", timeout=1.0):
        sw_id = self._next_sw_id()
        header = bytes([LONG_REPORT_ID, self.device_index,
                        feature_index, (function_id << 4) | sw_id])
        packet = header + params
        packet += b"\x00" * (LONG_LEN - len(packet))
        self.t.write(packet)

        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError(
                    f"sin respuesta (feature 0x{feature_index:02x} fn {function_id})")
            resp = self.t.read(remaining)
            if not resp or len(resp) < 7:
                continue
            if resp[0] != LONG_REPORT_ID or resp[1] != self.device_index:
                continue
            # Error: feature index 0xFF y eco de la peticion en bytes 4-5.
            if resp[2] == 0xFF and resp[4] == feature_index:
                code = resp[6]
                raise ProtocolError(
                    f"HID++ error 0x{code:02x} ({HIDPP_ERRORS.get(code, 'desconocido')}) "
                    f"en feature 0x{feature_index:02x} fn {function_id}")
            # Cualquier otra cosa es una notificacion asincrona: descartar.
            if resp[2] != feature_index or resp[3] != ((function_id << 4) | sw_id):
                continue
            return resp[4:]

    def ping(self):
        r = self.call(IROOT_INDEX, IROOT_GET_PROTOCOL, b"\x00\x00\xAF", timeout=0.6)
        if r[2] != 0xAF:
            raise ProtocolError("el ping no devolvio el eco esperado")
        return r[0], r[1]

    def feature_index(self, feature_id):
        """Resuelve el indice en runtime. Varia por modelo y por firmware."""
        if feature_id in self._feature_cache:
            return self._feature_cache[feature_id]
        r = self.call(IROOT_INDEX, IROOT_GET_FEATURE,
                      bytes([feature_id >> 8, feature_id & 0xFF]))
        index = r[0]
        if index == 0:
            raise ProtocolError(f"el dispositivo no soporta la feature 0x{feature_id:04x}")
        self._feature_cache[feature_id] = index
        log.debug("feature 0x%04x -> indice 0x%02x", feature_id, index)
        return index

    # -- 0x1814 CHANGE_HOST ------------------------------------------------

    def get_host_info(self):
        """Devuelve (cantidad_de_hosts, host_actual_0based)."""
        idx = self.feature_index(FEAT_CHANGE_HOST)
        r = self.call(idx, CHANGE_HOST_GET_INFO)
        return r[0], r[1]

    def set_host(self, slot):
        """Manda el cambio de host.

        No espera respuesta a proposito: si el comando funciona, el mouse se
        desconecta de este equipo AL INSTANTE y la respuesta nunca llega.
        Esperarla y tratar el timeout como error reportaria fallo justo cuando
        todo salio bien. La confirmacion se hace afuera, en wait_until_gone().
        """
        idx = self.feature_index(FEAT_CHANGE_HOST)
        sw_id = self._next_sw_id()
        packet = bytes([LONG_REPORT_ID, self.device_index, idx,
                        (CHANGE_HOST_SET_HOST << 4) | sw_id, slot])
        packet += b"\x00" * (LONG_LEN - len(packet))
        log.debug("setCurrentHost -> slot %d (idx feature 0x%02x)", slot, idx)
        self.t.write(packet)

    # -- 0x1815 HOSTS_INFO -------------------------------------------------

    def drain(self):
        """Vacia la cola de lectura del transporte, si el backend sabe."""
        drain = getattr(self.t, "drain", None)
        if drain:
            drain()

    def get_hosts_info(self):
        """Devuelve (cantidad_de_hosts, host_actual_0based).

        TRAMPA: el layout es [caps_hi, caps_lo, numHosts, currentHost]. Las
        capabilities son DOS bytes; leerlas como uno corre todo un lugar y
        devuelve cantidades de host imposibles (leimos "8 hosts" en un mouse
        de 3).
        """
        idx = self.feature_index(FEAT_HOSTS_INFO)
        r = self.call(idx, HOSTS_INFO_GET_INFO)
        return r[2], r[3]

    def get_host_descriptor(self, host_index):
        """[hostIndex, status, busType, numPages, nameLen, nameMaxLen].

        TRAMPA: el largo del nombre es el byte 4, NO el 3 (el 3 es numPages).
        """
        idx = self.feature_index(FEAT_HOSTS_INFO)
        r = self.call(idx, HOSTS_INFO_GET_DESCRIPTOR, bytes([host_index]))
        return {
            "index": r[0],
            "status": r[1],
            "bus_type": r[2],
            "num_pages": r[3],
            "name_len": r[4],
            "name_max_len": r[5],
        }

    def get_host_address(self, host_index):
        """Direccion Bluetooth del host, como string.

        TRAMPA: la MAC esta en los bytes 3..8 y viene EN ORDEN INVERTIDO.
        """
        idx = self.feature_index(FEAT_HOSTS_INFO)
        r = self.call(idx, HOSTS_INFO_GET_ADDRESS, bytes([host_index]))
        mac = bytes(r[3:9])[::-1]
        if not any(mac):
            return ""
        return ":".join(f"{b:02x}" for b in mac)

    def get_host_name(self, host_index, name_len=None):
        """Nombre amigable del host, pedido por chunks.

        fn3 devuelve [hostIndex, byteIndex, chars...], asi que hay que iterar
        subiendo byteIndex hasta juntar name_len.

        TRAMPA: la PRIMERA llamada a fn3 de una sesion devuelve vacio de forma
        consistente (parece un paquete obsoleto en la cola). De ahi el drain()
        y el reintento.
        """
        if name_len is None:
            name_len = self.get_host_descriptor(host_index)["name_len"]
        if not name_len:
            return ""
        for attempt in (1, 2):
            chunks = bytearray()
            while len(chunks) < name_len:
                idx = self.feature_index(FEAT_HOSTS_INFO)
                r = self.call(idx, HOSTS_INFO_GET_NAME,
                              bytes([host_index, len(chunks)]))
                piece = bytes(r[2:])
                # El report viene siempre padeado a 20 bytes: cortar en el
                # primer NUL, o se cuela basura en el nombre.
                nul = piece.find(b"\x00")
                if nul >= 0:
                    piece = piece[:nul]
                if not piece:
                    break
                chunks += piece
            name = bytes(chunks[:name_len]).decode("utf-8", "replace").strip()
            if name:
                return name
            log.debug("nombre vacio para el host %d (intento %d): drenando",
                      host_index, attempt)
            self.drain()
        return ""

    def enumerate_hosts(self):
        """Mapa completo de los slots del mouse. Solo lectura."""
        # La primera fn3 sale vacia si hay algo viejo en la cola.
        self.drain()
        total, current = self.get_hosts_info()
        hosts = []
        for i in range(total):
            try:
                desc = self.get_host_descriptor(i)
            except (ProtocolError, TimeoutError) as exc:
                log.debug("descriptor del host %d fallo: %s", i, exc)
                desc = {"index": i, "status": None, "bus_type": None,
                        "num_pages": 0, "name_len": 0, "name_max_len": 0}
            try:
                address = self.get_host_address(i)
            except (ProtocolError, TimeoutError) as exc:
                log.debug("direccion del host %d fallo: %s", i, exc)
                address = ""
            try:
                name = self.get_host_name(i, desc["name_len"])
            except (ProtocolError, TimeoutError) as exc:
                log.debug("nombre del host %d fallo: %s", i, exc)
                name = ""
            hosts.append({
                "index": i,
                "slot": i + 1,
                "name": name,
                "address": address,
                "status": desc["status"],
                "bus_type": desc["bus_type"],
                "current": i == current,
            })
        return total, current, hosts

    def wait_until_gone(self, timeout):
        """Confirmacion del switch.

        Contraintuitivo pero es la unica señal confiable: si el mouse se fue a
        otro host, deja de hablarnos. Si en cambio sigue respondiendo y
        getHostInfo devuelve el host viejo, el comando se perdio (tipico con el
        mouse en suspension profunda) y hay que reintentar.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.t.alive():
                return True
            time.sleep(0.1)
            try:
                _, current = self.get_host_info()
            except (DeviceGone, TimeoutError, OSError):
                return True
            except ProtocolError:
                continue
            else:
                self._last_seen_host = current
        return False

    def close(self):
        self.t.close()
