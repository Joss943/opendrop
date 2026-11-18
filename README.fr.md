# OpenDrop : une implémentation libre d'AirDrop

*OpenDrop* est un outil en ligne de commande pour partager des fichiers directement en Wi-Fi.
Il parle le protocole AirDrop d'Apple : il peut donc échanger des fichiers avec des iPhone,
iPad et Mac. Il existe en **application de bureau** et en ligne de commande.

Fork maintenu de [seemoo-lab/opendrop](https://github.com/seemoo-lab/opendrop) : nombreux bugs
corrigés, plus aucune dépendance native (`libarchive`, `openssl`).
Détails dans le [CHANGELOG.md](CHANGELOG.md). Documentation complète en anglais : [README.md](README.md).

> [!WARNING]
> **Windows n'est pas compatible avec AirDrop.** AirDrop a besoin d'AWDL, le Wi-Fi direct
> d'Apple, et Windows n'a aucun pilote AWDL : un PC Windows **ne peut pas** échanger de
> fichiers en AirDrop avec un iPhone, un Mac ou un téléphone Android. Sous Windows, OpenDrop
> fonctionne uniquement avec d'autres ordinateurs OpenDrop sur le même réseau.

## Application de bureau

```bash
pip install "opendrop[gui] @ git+https://github.com/Joss943/opendrop.git"
opendrop gui
```

Appareils à proximité, glisser-déposer pour envoyer, fenêtre « Accepter / Refuser » avec
l'aperçu des photos envoyées par l'iPhone, historique des fichiers reçus, icône dans la barre
des tâches et notifications. Des versions prêtes à lancer (sans Python) sont générées par le
workflow *Desktop app* de GitHub Actions.

**Android :** les Pixel 9/10 et certains Galaxy compatibles Quick Share ↔ AirDrop, réglés sur
« Tout le monde », devraient pouvoir échanger avec OpenDrop sur macOS ou Linux + OWL (non testé).
**Hackintosh :** si AirDrop ne marche pas parce que la carte Wi-Fi n'a pas d'AWDL, OpenDrop ne
marchera pas non plus.

## Ce qui marche, et où

AirDrop passe exclusivement par **AWDL**, une couche Wi-Fi propriétaire d'Apple.

| Système | Avec les appareils Apple | Entre deux OpenDrop |
|---------|--------------------------|---------------------|
| **macOS** | ✅ en natif (`awdl0`) | ✅ |
| **Linux** | ✅ avec [OWL](https://github.com/seemoo-lab/owl) (carte Wi-Fi compatible mode moniteur) | ✅ |
| **Windows** | ❌ **non compatible** (pas d'AWDL) | ✅ sur le réseau normal (`-i Wi-Fi`) |

Lancez `opendrop doctor` pour vérifier votre configuration.

## Installation

Python **3.9 ou plus récent** :

```bash
pip install git+https://github.com/Joss943/opendrop.git
```

Sous Windows : `py -m pip install git+https://github.com/Joss943/opendrop.git`.

## Utilisation

```bash
opendrop doctor                       # diagnostic
opendrop find -t 10                   # cherche les appareils pendant 10 s
opendrop send -r 0 -f photo.jpg       # envoie au premier appareil trouvé
opendrop send -r "iPhone de Jean" -f a.pdf b.jpg Dossier/   # plusieurs fichiers
opendrop send -r 0 -f https://example.org --url             # envoie un lien
opendrop send --scan 10 -r "MacBook de Julie" -f a.pdf      # cherche puis envoie, en une commande
opendrop receive -o ~/Téléchargements # reçoit dans ce dossier
opendrop receive --confirm --max-size 2G --once             # demande confirmation, 2 Go max, s'arrête après 1 transfert
opendrop find --json -t 5                                   # résultats en JSON (scripts)
```

Les liens reçus sont enregistrés dans `received_links.txt` (jamais ouverts automatiquement).

## Sécurité

* Un envoi n'est accepté qu'après une demande (`/Ask`) acceptée venant du même appareil.
* Aucun fichier ne peut être écrit hors du dossier de réception ; rien n'est jamais écrasé ;
  pas de fichier à moitié écrit en cas d'erreur ; l'espace disque est vérifié.
* `--confirm` pour valider chaque transfert, `--max-size` pour limiter la taille.
* Connexions inactives coupées après 60 s, 16 connexions simultanées au maximum.
* Sur un réseau public, utilisez `--confirm` : sans lui, n'importe qui à portée peut vous envoyer des fichiers.

Entre deux ordinateurs sans AWDL (par exemple un PC Windows et un PC Linux sur le même réseau) :

```bash
opendrop receive -i Wi-Fi -n "PC salon"          # sur le PC Windows
opendrop find -i wlan0 -t 5                      # sur le PC Linux
opendrop send -i wlan0 -r "PC salon" -f rapport.pdf
```

Codes de sortie de `send` : `0` succès, `1` erreur, `2` refusé par le destinataire.

## Limites

* Le réveil des appareils Apple par Bluetooth LE n'est pas implémenté : ouvrez le menu
  AirDrop sur l'iPhone/le Mac pour qu'il devienne visible.
* Pas d'authentification des pairs ; sans `--confirm`, tout est accepté automatiquement.

Licence : GNU GPL v3. Projet non affilié à Apple Inc.
