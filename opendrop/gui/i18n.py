"""Tiny translation table for the desktop app (English and French)."""

import locale
import os

STRINGS = {
    "app_title": ("OpenDrop", "OpenDrop"),
    "tab_send": ("Send", "Envoyer"),
    "tab_inbox": ("Received", "Reçus"),
    "tab_settings": ("Settings", "Réglages"),
    "receiving_on": ("Visible to others", "Visible par les autres"),
    "receiving_off": ("Not receiving", "Réception désactivée"),
    "devices": ("Nearby devices", "Appareils à proximité"),
    "devices_hint": (
        "Drop files on a device to send them, or select a device and use the buttons.",
        "Déposez des fichiers sur un appareil pour les envoyer, ou sélectionnez-le et "
        "utilisez les boutons.",
    ),
    "searching": ("Searching for devices…", "Recherche d'appareils…"),
    "send_files": ("Send files…", "Envoyer des fichiers…"),
    "send_folder": ("Send a folder…", "Envoyer un dossier…"),
    "send_link": ("Send a link…", "Envoyer un lien…"),
    "add_manual": ("Add by address…", "Ajouter par adresse…"),
    "refresh": ("Refresh", "Actualiser"),
    "transfers": ("Transfers", "Transferts"),
    "pick_device": ("Select a device first.", "Sélectionnez d'abord un appareil."),
    "link_prompt": ("Link to send:", "Lien à envoyer :"),
    "address_prompt": (
        "IP address and port of the receiver (e.g. 192.168.1.20:8771):",
        "Adresse IP et port du destinataire (ex. 192.168.1.20:8771) :",
    ),
    "address_failed": ("Could not reach {0}: {1}", "Impossible de joindre {0} : {1}"),
    "state_asking": ("Waiting for {0} to accept…", "En attente de l'accord de {0}…"),
    "state_sending": ("Sending to {0}…", "Envoi vers {0}…"),
    "state_done": ("Sent to {0}", "Envoyé à {0}"),
    "state_declined": ("{0} declined", "{0} a refusé"),
    "state_failed": ("Failed: {1}", "Échec : {1}"),
    "inbox_empty": ("Nothing received yet.", "Rien reçu pour l'instant."),
    "open_folder": ("Open folder", "Ouvrir le dossier"),
    "clear_list": ("Clear list", "Vider la liste"),
    "from": ("from {0}", "de {0}"),
    "links_saved": ("link(s)", "lien(s)"),
    "settings_name": ("Name shown to others", "Nom affiché aux autres"),
    "settings_interface": ("Network interface", "Interface réseau"),
    "settings_folder": ("Save received files in", "Enregistrer les fichiers reçus dans"),
    "settings_browse": ("Browse…", "Parcourir…"),
    "settings_auto": (
        "Accept transfers without asking",
        "Accepter les transferts sans demander",
    ),
    "settings_tray": (
        "Keep running in the system tray when the window is closed",
        "Rester dans la barre des tâches quand la fenêtre est fermée",
    ),
    "settings_save": ("Apply", "Appliquer"),
    "settings_saved": ("Settings applied.", "Réglages appliqués."),
    "settings_android": (
        "Android: Pixel and Galaxy phones with Quick Share ↔ AirDrop support can "
        "exchange with OpenDrop when Quick Share is set to “Everyone”. Like "
        "iPhones, they need AWDL (macOS, or Linux with OWL).",
        "Android : les Pixel et Galaxy compatibles Quick Share ↔ AirDrop peuvent "
        "échanger avec OpenDrop si Quick Share est réglé sur « Tout le monde ». "
        "Comme les iPhone, ils ont besoin d'AWDL (macOS, ou Linux avec OWL).",
    ),
    "settings_awdl": (
        "To exchange with iPhones and Macs, use awdl0 (macOS, or Linux with OWL). "
        "Other interfaces work between OpenDrop computers.",
        "Pour échanger avec des iPhone et des Mac, utilisez awdl0 (macOS, ou Linux "
        "avec OWL). Les autres interfaces fonctionnent entre ordinateurs OpenDrop.",
    ),
    "request_title": ("Incoming transfer", "Transfert entrant"),
    "request_text": ("<b>{0}</b> wants to send you:", "<b>{0}</b> veut vous envoyer :"),
    "accept": ("Accept", "Accepter"),
    "decline": ("Decline", "Refuser"),
    "request_timeout": ("Declines automatically in {0} s", "Refus automatique dans {0} s"),
    "received_notify": ("Received from {0}", "Reçu de {0}"),
    "tray_show": ("Show OpenDrop", "Afficher OpenDrop"),
    "tray_receive": ("Visible to others", "Visible par les autres"),
    "tray_quit": ("Quit", "Quitter"),
    "tray_still_running": (
        "OpenDrop keeps running in the background.",
        "OpenDrop continue de fonctionner en arrière-plan.",
    ),
    "error": ("Error", "Erreur"),
    "error_receive": ("Receiving is unavailable: {0}", "Réception indisponible : {0}"),
    "error_discover": ("Discovery is unavailable: {0}", "Recherche indisponible : {0}"),
}


def _detect_language():
    forced = os.environ.get("OPENDROP_LANG")
    if forced:
        return forced[:2].lower()
    try:
        from PySide6.QtCore import QLocale  # pylint: disable=import-outside-toplevel

        return QLocale.system().name()[:2].lower()
    except ImportError:
        lang = locale.getlocale()[0] or ""
        return lang[:2].lower()


_INDEX = 1 if _detect_language() == "fr" else 0


def t(key, *args):
    text = STRINGS[key][_INDEX]
    return text.format(*args) if args else text
