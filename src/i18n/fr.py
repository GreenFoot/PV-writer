"""French string table — the only UI language of PV Writer."""

STRINGS = {
    # ── Units ─────────────────────────────────────────────
    "units.bytes": "o",
    "units.kilobytes": "Ko",
    "units.megabytes": "Mo",
    "units.gigabytes": "Go",
    "units.terabytes": "To",
    # ── Rich editor (vendored) ────────────────────────────
    "editor.tooltip.bold": "Gras (Ctrl+B)",
    "editor.tooltip.italic": "Italique (Ctrl+I)",
    "editor.tooltip.underline": "Souligner (Ctrl+U)",
    "editor.tooltip.fold_all": "Replier tout (Ctrl+Shift+-)",
    "editor.tooltip.unfold_all": "Déplier tout (Ctrl+Shift+=)",
    "editor.btn.save": " Enregistrer",
    "editor.heading.normal": "Normal",
    "editor.heading.h1": "Titre 1",
    "editor.heading.h2": "Titre 2",
    "editor.heading.h3": "Titre 3",
    "editor.search.placeholder": "Rechercher…",
    "editor.search.no_results": "0 résultat",
    "editor.search.prev_tooltip": "Précédent (Shift+Entrée)",
    "editor.search.next_tooltip": "Suivant (Entrée)",
    "editor.search.close_tooltip": "Fermer (Échap)",
    "fold.lines_folded": "{count} ligne{s} repliée{s}",
    # ── Recorder / transcriber (vendored) ─────────────────
    "recorder.error.recording": "Erreur d'enregistrement : {error}",
    "recorder.error.pause": "Erreur de pause : {error}",
    "recorder.error.resume": "Erreur de reprise : {error}",
    "transcriber.error.no_api_key": "Clé API Mistral non configurée. Allez dans Paramètres.",
    "transcriber.error.no_api_key_short": "Clé API Mistral non configurée.",
    "transcriber.error.invalid_key": "Clé API invalide. Vérifiez votre clé dans les Paramètres.",
    "transcriber.error.transcription": "Erreur de transcription : {error}",
    "transcriber.error.live_transcription": "Erreur de transcription en direct : {error}",
    "summarizer.error.no_api_key": "Clé API Mistral non configurée. Allez dans Paramètres.",
    # ── File ordering dialog (vendored) ───────────────────
    "session.dialog.import_title": "Importer des fichiers audio ou des transcriptions",
    "session.dialog.import_filter": (
        "Audio et transcriptions (*.wav *.mp3 *.flac *.ogg *.m4a *.wma *.txt *.md *.text);;"
        "Fichiers audio (*.wav *.mp3 *.flac *.ogg *.m4a *.wma);;Transcriptions (*.txt *.md *.text);;Tous (*)"
    ),
    "session.order.title": "Ordre des fichiers de la réunion",
    "session.order.hint": (
        "Les fichiers sont traités les uns après les autres dans cet ordre : les fichiers audio sont "
        "transcrits, les transcriptions sont insérées telles quelles. Glissez-déposez pour réordonner, "
        "ou utilisez les boutons ci-dessous."
    ),
    "session.order.btn_up": "Monter",
    "session.order.btn_down": "Descendre",
    "session.order.btn_remove": "Retirer",
    "session.order.btn_add": "Ajouter des fichiers…",
    "session.order.type_audio": "audio",
    "session.order.type_transcript": "transcription",
    # ── Meeting info panel ────────────────────────────────
    "info.heading": "Réunion",
    "info.title": "Intitulé :",
    "info.title_placeholder": "Réunion des délégués",
    "info.date": "Date :",
    "info.organization": "Organisation :",
    "info.organization_placeholder": "Ex. : Association des parents",
    "info.organization_tooltip": "L'organisation qui rédige le PV : c'est elle que « nous » désigne dans le texte.",
    "info.agenda": "Ordre du jour (une ligne par point, indentez les sous-points) :",
    "info.agenda_placeholder": (
        "Concernant l'école\n    Rentrée scolaire\n        Horaires\nConcernant l'association\n    Budget"
    ),
    "info.glossary": "Glossaire (noms propres, acronymes — un par ligne) :",
    "info.glossary_placeholder": "Noms, sigles, termes techniques…",
    "info.previous_pv": "PV précédent (pour la continuité) :",
    "info.previous_none": "Aucun",
    "info.previous_choose": "Choisir…",
    "info.previous_clear": "Retirer le PV précédent",
    # ── Meeting view ──────────────────────────────────────
    "meeting.drop_hint": "Déposez des fichiers audio ou des transcriptions ici",
    "tab.notes": "Notes",
    "tab.transcript": "Transcription",
    "tab.pv": "Procès-verbal",
    "rec.btn.record": "● Enregistrer",
    "rec.btn.record_tooltip": "Démarrer l'enregistrement (Ctrl+R)",
    "rec.btn.pause": "❚❚ Pause",
    "rec.btn.resume": "● Reprendre",
    "rec.btn.stop": "■ Arrêter",
    "rec.btn.stamp": "⏱ Horodater",
    "rec.btn.stamp_tooltip": "Insérer l'heure de l'enregistrement au début du paragraphe (F2)",
    "notes.hint": (
        "Notez ce qui doit figurer au PV, sous le bon point de l'ordre du jour. "
        "Pendant l'enregistrement, chaque paragraphe est horodaté automatiquement."
    ),
    "notes.btn_insert_agenda": "Insérer l'ordre du jour",
    "transcript.placeholder": (
        "La transcription apparaîtra ici, au fil de l'enregistrement ou après l'import de fichiers audio."
    ),
    "pv.regen_label": "Point :",
    "pv.btn_regen_node": "Régénérer ce point",
    "pv.btn_open_docx": "Ouvrir le document Word",
    "pv.placeholder": (
        "Le procès-verbal apparaîtra ici après la génération. "
        "Il est exporté en document Word, modifiable et réexportable en PDF."
    ),
    "pv.versions": "Documents : {files}",
    "action.transcribe": "Transcrire",
    "action.generate": "Générer le PV",
    "action.export_docx": "Exporter en Word",
    "action.import": "Importer audio / transcriptions…",
    "action.save_audio": "Enregistrer l'audio sous…",
    "action.open_folder": "Ouvrir le dossier de la réunion",
    "action.more_tooltip": "Plus d'actions",
    "status.ready": "Prêt",
    "status.recording": "Enregistrement…",
    "status.paused": "En pause",
    "status.stopped": "Enregistrement arrêté",
    "status.finalizing": "Finalisation de la transcription…",
    "status.saved": "Enregistré",
    "status.transcribing": "Transcription en cours…",
    "status.transcribing_segment": "Transcription du segment {count}…",
    "status.transcribing_file": "Fichier {index}/{count} : {name}",
    "status.transcribing_chunk": "Partie {current}/{total}",
    "status.segments_transcribed": "{count} segment(s) transcrit(s)",
    "status.transcription_done": "Transcription terminée",
    "status.transcription_error": "Erreur de transcription : {msg}",
    "status.generating": "Génération du PV…",
    "status.generation_done": "PV généré",
    "status.exported": "Exporté : {name}",
    "status.imported": "{count} fichier(s) importé(s)",
    "status.audio_saved": "Audio enregistré : {name}",
    "post.title": "Enregistrement terminé",
    "post.duration": "Durée :",
    "post.size": "Taille :",
    "post.file": "Fichier :",
    "post.btn_transcribe": "Transcrire maintenant",
    "post.btn_later": "Plus tard",
    "confirm.title": "Confirmation",
    "confirm.overwrite_transcript": "Remplacer la transcription existante par une nouvelle transcription des sources ?",
    "error.no_sources": "Aucun fichier audio ou transcription à traiter.",
    "error.copy_failed": "Impossible de copier le fichier : {error}",
    "error.drop_while_busy": "Une opération est en cours : réessayez lorsqu'elle sera terminée.",
    "error.no_audio_save": "Aucun fichier audio dans cette réunion.",
    "error.save_failed": "Échec de l'enregistrement : {error}",
    "error.export_failed": "Échec de l'export Word : {error}",
    "dialog.save_audio_title": "Enregistrer l'audio",
    "dialog.save_audio_pick": "Fichier audio :",
    "dialog.save_audio_filter": "Audio FLAC (*.flac)",
    "dialog.previous_title": "Choisir le PV précédent",
    "dialog.previous_filter": "Documents (*.docx *.pdf *.txt *.md);;Tous (*)",
    # ── Main window ───────────────────────────────────────
    "menu.meeting": "Réunion",
    "menu.new": "Nouvelle réunion…",
    "menu.open": "Ouvrir une réunion…",
    "menu.open_folder": "Ouvrir le dossier de la réunion",
    "menu.record": "Enregistrer / Pause",
    "menu.quit": "Quitter",
    "menu.tools": "Outils",
    "menu.settings": "Paramètres…",
    "menu.help": "Aide",
    "menu.shortcuts": "Raccourcis clavier",
    "menu.about": "À propos de PV Writer",
    "help.shortcuts": (
        "Ctrl+R : démarrer / mettre en pause l'enregistrement\n"
        "F2 : horodater le paragraphe courant\n"
        "Ctrl+N : nouvelle réunion\n"
        "Ctrl+O : ouvrir une réunion\n"
        "Ctrl+F : rechercher dans les notes\n"
        "Ctrl+B / Ctrl+I / Ctrl+U : gras / italique / souligné\n"
        "Ctrl+Maj+- / Ctrl+Maj+= : replier / déplier tous les titres"
    ),
    "help.about": (
        "PV Writer {version}\n\n"
        "Rédaction de procès-verbaux de réunion à partir de vos notes et de l'enregistrement audio.\n\n"
        "L'audio et les textes sont envoyés à Mistral AI (ou à Claude via le CLI) pour la transcription "
        "et la rédaction. Ne l'utilisez pas pour des réunions classifiées ou soumises à ITAR."
    ),
    "new.title": "Nouvelle réunion",
    "new.default_title": "Réunion",
    "open.title": "Ouvrir une réunion",
    "open.none": "Aucune réunion enregistrée.",
    "open.error": "Impossible d'ouvrir la réunion : {error}",
    "busy.title": "Opération en cours",
    "busy.message": "Arrêtez l'enregistrement ou attendez la fin de l'opération en cours.",
    "quit.title": "Quitter",
    "quit.recording": "Un enregistrement est en cours. L'arrêter et quitter ?",
    "quit.busy": "Une transcription ou une génération est en cours et sera interrompue. Quitter quand même ?",
    # ── Settings ──────────────────────────────────────────
    "settings.title": "Paramètres",
    "settings.tab.ai": "IA",
    "settings.tab.audio": "Audio",
    "settings.tab.advanced": "Avancé",
    "settings.tab.prompts": "Prompts",
    "settings.ai.mistral_group": "Mistral AI",
    "settings.ai.mistral_model_label": "Modèle de rédaction :",
    "settings.ai.transcription_model_label": "Modèle de transcription :",
    "settings.ai.transcription_note": "La transcription utilise toujours Mistral Voxtral, en français.",
    "settings.ai.generation_group": "Rédaction du PV",
    "settings.ai.provider_label": "Moteur :",
    "settings.ai.provider_mistral": "Mistral AI",
    "settings.ai.provider_claude": "Claude (CLI local)",
    "settings.ai.claude_model_label": "Modèle Claude :",
    "settings.ai.btn_refresh_models": "Actualiser",
    "settings.ai.claude_detected": "Claude CLI détecté (version {version}).",
    "settings.ai.claude_not_found": (
        "Claude CLI introuvable. Installez-le et connectez-vous, puis cliquez sur Actualiser."
    ),
    "settings.api.key_label": "Clé API :",
    "settings.api.key_placeholder": "Collez votre clé API Mistral",
    "settings.api.btn_test": "Tester la connexion",
    "settings.api.enter_key_first": "Saisissez d'abord une clé API.",
    "settings.api.test_success": "Connexion réussie.",
    "settings.api.test_fail": "Échec de la connexion : {error}",
    "settings.audio.device_label": "Microphone :",
    "settings.audio.device_default": "Périphérique par défaut",
    "settings.audio.btn_test_mic": "Tester le microphone (1 s)",
    "settings.audio.sample_rate_label": "Fréquence d'échantillonnage (Hz) :",
    "settings.audio.test_title": "Test du microphone",
    "settings.audio.test_ok": "Le microphone fonctionne (niveau {level}).",
    "settings.audio.test_weak": (
        "Signal très faible. Vérifiez le microphone choisi et, sur macOS, l'autorisation d'accès au micro."
    ),
    "settings.audio.test_error": "Erreur du microphone : {error}",
    "settings.advanced.default_org": "Organisation par défaut :",
    "settings.advanced.chunk_label": "Découpage de l'audio pour la transcription :",
    "settings.advanced.diarize": "Identifier les intervenants (diarisation)",
    "settings.advanced.auto_timestamps": "Horodater automatiquement les notes pendant l'enregistrement",
    "settings.advanced.notification_sounds": "Sons de notification",
    "settings.advanced.glossary_label": "Glossaire global :",
    "settings.advanced.glossary_placeholder": "Termes ajoutés à toutes les réunions, un par ligne",
    "settings.limits.group": "Limites de l'API Mistral",
    "settings.limits.hint": (
        "Recopiez les limites de votre clé (<a href='https://admin.mistral.ai/plateforme/limits'>"
        "admin.mistral.ai › Limits</a>). L'application espace ses requêtes pour les respecter. "
        "Valeurs par défaut : offre gratuite."
    ),
    "settings.limits.chat_tpm": "Rédaction — tokens par minute :",
    "settings.limits.chat_rps": "Rédaction — requêtes par seconde :",
    "settings.limits.transcription_tpm": "Transcription — tokens par minute :",
    "settings.limits.transcription_rps": "Transcription — requêtes par seconde :",
    "settings.limits.unlimited": "Illimité",
    "settings.prompts.prompt_label": "Prompt :",
    "settings.prompts.extract": "1. Extraction des faits",
    "settings.prompts.redact": "2. Rédaction du PV",
    "settings.prompts.btn_reset": "Rétablir le défaut",
    "settings.prompts.hint": (
        "Instructions système envoyées au modèle. Modifiez-les avec prudence : "
        "le format de sortie attendu doit être conservé."
    ),
    # ── First-run wizard ──────────────────────────────────
    "wizard.title": "Bienvenue",
    "wizard.welcome": "Bienvenue dans PV Writer",
    "wizard.subtitle": (
        "Enregistrez la réunion, prenez vos notes en parallèle, puis générez le procès-verbal en Word."
    ),
    "wizard.api_group": "Clé API Mistral",
    "wizard.api_hint": "Nécessaire pour la transcription. Créez une clé sur console.mistral.ai.",
    "wizard.mic_group": "Microphone",
    "wizard.data_notice": (
        "L'audio et les textes de la réunion sont envoyés à Mistral AI (ou à Claude). "
        "N'utilisez pas PV Writer pour des réunions classifiées ou contenant des données ITAR."
    ),
    "wizard.btn_skip": "Plus tard",
    "wizard.btn_done": "Terminer",
    # ── PV pipeline ───────────────────────────────────────
    "pipeline.progress.extract": "Analyse de la transcription : partie {current}/{total}…",
    "pipeline.progress.redact": "Rédaction du point {current}/{total} : {title}",
    "pipeline.progress.rate_wait": "Limite de débit de l'API Mistral : reprise dans {seconds} s…",
    "transcriber.rate_wait": "Limite de débit de l'API Mistral : reprise dans {seconds} s…",
    "pipeline.no_organization": "(non précisée : « nous » désigne les organisateurs de la réunion)",
    "pipeline.warning.extract_failed": "La partie {part} de la transcription n'a pas pu être analysée.",
    "pipeline.error.nothing": "Rien à rédiger : ajoutez des notes ou une transcription.",
    "pipeline.error.generic": "Erreur pendant la génération du PV : {error}",
}
