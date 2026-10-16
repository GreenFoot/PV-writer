# PV Writer

Application de bureau (Windows et macOS) pour rédiger le procès-verbal d'une réunion en français.

1. Préparez la réunion : intitulé, date, organisation, ordre du jour, glossaire, PV précédent.
2. Enregistrez la réunion et prenez vos notes en parallèle, sous chaque point de l'ordre du jour. Les notes sont horodatées automatiquement.
3. Transcrivez l'audio (Mistral Voxtral). Vous pouvez aussi importer des fichiers audio ou des transcriptions existants.
4. Générez le PV : l'application combine vos notes et la transcription, écarte les apartés et rédige chaque point dans le style des PV de référence.
5. Exportez en Word (.docx), relisez et corrigez dans Word, puis enregistrez en PDF.

> ⚠️ L'audio et les textes sont envoyés à Mistral AI (ou à Anthropic via le CLI Claude). N'utilisez pas PV Writer pour des réunions classifiées, confidentielles ou contenant des données ITAR.

## Installation

Prérequis : Python 3.11 ou plus récent et une clé API Mistral (console.mistral.ai).

```bash
python -m venv .venv
# macOS
.venv/bin/pip install -r requirements.txt
# Windows
.venv\Scripts\pip install -r requirements.txt
```

Optionnel : pour rédiger avec Claude, installez le [CLI Claude Code](https://claude.com/claude-code) et connectez-vous, puis choisissez « Claude (CLI local) » dans Paramètres > IA. La transcription utilise toujours Mistral.

## Lancement

- macOS : `scripts/run.sh` (ou `.venv/bin/python main.py`)
- Windows : `scripts\run.bat` (ou `.venv\Scripts\pythonw.exe main.py`)

Au premier lancement, un assistant demande la clé API Mistral et le microphone.

Sur macOS, le système demande l'autorisation d'accès au micro au premier enregistrement. Elle est accordée au Terminal (ou à l'application qui lance Python). Si le test du micro ne capte rien, vérifiez Réglages Système > Confidentialité et sécurité > Microphone.

## Utilisation

- **Ordre du jour** : une ligne par point, avec une indentation pour les sous-points (3 niveaux). Le premier niveau devient « Concernant … : », le deuxième « 1) … », le troisième « a) … ». Une section « Autres informations » est toujours ajoutée.
- **Notes** : « Insérer l'ordre du jour » crée les titres. Écrivez sous chaque titre ce qui doit figurer au PV. Pendant l'enregistrement, chaque nouveau paragraphe reçoit l'heure de l'audio. F2 horodate le paragraphe courant.
- **Transcription** : transcrite en direct pendant l'enregistrement, ou après coup avec « Transcrire ». Pour importer des fichiers, glissez-les sur la fenêtre ou passez par le menu « ⋯ ».
- **Procès-verbal** : « Générer le PV » rédige tous les points. « Régénérer ce point » réécrit un seul point après une correction des notes.
- **Export** : « Exporter en Word » crée un .docx dans le dossier de la réunion. Les réunions sont enregistrées automatiquement et se rouvrent avec Réunion > Ouvrir.

Raccourcis : Ctrl+R (Cmd+R) enregistrer / pause, F2 horodater, Ctrl+N nouvelle réunion, Ctrl+O ouvrir, Ctrl+F rechercher dans les notes.

## Développement

```bash
pip install -r requirements-dev.txt
pytest
black --check --line-length 120 src/ main.py tests/
isort --check --profile black src/ main.py tests/
flake8 src/ main.py
pylint src/ main.py
```

Voir `CLAUDE.md` pour l'architecture et `plan/implementation_plan.md` pour le plan.
