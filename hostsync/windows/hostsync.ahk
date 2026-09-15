#Requires AutoHotkey v2.0
#SingleInstance Force
;
; hostsync - listener de Windows.
;
; Escucha F13/F14/F15 y le manda al MX Master 3S el cambio de slot ANTES de
; que el teclado se vaya a otro perfil.
;
; Toda la logica vive en mxswitch.py, el mismo script que corre en Linux. Este
; archivo es solo el listener: no conoce ningun numero de slot ni de perfil.
; El mapa esta en hosts.toml y en ningun otro lado.
;
; NO VERIFICADO CONTRA HARDWARE. Ver INSTALL.md.

; --- configuracion -----------------------------------------------------------
; Ajustar si clonaste el repo en otro lado.
HOSTSYNC_DIR := A_ScriptDir . "\.."
PYTHON := "pythonw.exe"          ; pythonw = sin ventana de consola

LOGDIR := EnvGet("LOCALAPPDATA") . "\hostsync"
DirCreate(LOGDIR)

; --- deduplicacion -----------------------------------------------------------
; El keymap manda cada tecla testigo DOS VECES a proposito (el Adv360 duerme a
; los 30s y el primer report al despertar se puede perder). mxswitch.py tambien
; deduplica por su cuenta; esto le evita el arranque de un segundo proceso.
global LastKey := ""
global LastTime := 0
DEDUP_MS := 600

Switch(witness) {
    global LastKey, LastTime, DEDUP_MS, HOSTSYNC_DIR, PYTHON
    now := A_TickCount
    if (witness = LastKey && now - LastTime < DEDUP_MS) {
        return
    }
    LastKey := witness
    LastTime := now

    script := HOSTSYNC_DIR . "\mxswitch.py"
    try {
        Run(Format('{1} "{2}" --witness {3}', PYTHON, script, witness), , "Hide")
    } catch as e {
        TrayTip("hostsync", "No pude ejecutar mxswitch.py: " . e.Message, 1)
    }
}

; --- las teclas testigo ------------------------------------------------------
; Se consumen aca: no se reenvian a las aplicaciones.
F13:: Switch("f13")
F14:: Switch("f14")
F15:: Switch("f15")

; Salida de emergencia: Ctrl+Alt+F12 mata el listener.
^!F12:: ExitApp()
