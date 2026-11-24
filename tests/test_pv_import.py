"""Round trips of the PV through Word and through the editable PV tab."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import docx  # noqa: E402  pylint: disable=wrong-import-position
from PySide6.QtGui import QTextDocument  # noqa: E402  pylint: disable=wrong-import-position
from PySide6.QtWidgets import QApplication  # noqa: E402  pylint: disable=wrong-import-position

from src.agenda import parse_agenda  # noqa: E402  pylint: disable=wrong-import-position
from src.docx_exporter import export_docx  # noqa: E402  pylint: disable=wrong-import-position
from src.pv_document import render_preview_html  # noqa: E402  pylint: disable=wrong-import-position
from src.pv_import import (  # noqa: E402  pylint: disable=wrong-import-position
    import_docx,
    qt_records,
    records_to_pv,
    store_image,
)

AGENDA = """Concernant l'ADP
  Chocolats d'hiver
  Les cellules
Concernant les écoles
"""

BODIES = {
    "1": "",
    "1.1": (
        "<ul><li><strong>Vente</strong></li></ul>\n"
        "<p>La vente a rapporté <strong>651 €</strong>, soit <em>plus</em> que prévu.<br>Deuxième ligne.</p>\n"
        "<p>-&gt; Nous relancerons le fournisseur.</p>"
    ),
    "1.2": (
        "<p><strong>Questions des parents :</strong></p>\n"
        "<ul><li>Qui gère la tombola ?<ul><li>La cellule tombola.</li></ul></li><li>Et le bar ?</li></ul>"
    ),
    "2": "<p>Rien à signaler.</p>",
    "3": "",
}
SUMMARY = {
    "points": [{"sujet": "Chocolats", "texte": "La vente a rapporté 651 €."}],
    "actions": [
        {"action": "Relancer le fournisseur", "qui": "ADP", "echeance": ""},
        {"action": "Trouver des lots", "qui": "", "echeance": "Janvier"},
    ],
}


def test_docx_import_round_trip(tmp_path):
    roots = parse_agenda(AGENDA)
    path = export_docx(str(tmp_path / "pv.docx"), "PV", roots, BODIES, SUMMARY)
    bodies, summary, warnings = import_docx(path, roots)
    assert warnings == []
    assert bodies == BODIES
    assert summary == SUMMARY


def test_docx_import_follows_hand_edits(tmp_path):
    roots = parse_agenda(AGENDA)
    path = export_docx(str(tmp_path / "pv.docx"), "PV", roots, BODIES, SUMMARY)
    document = docx.Document(path)
    for paragraph in document.paragraphs:
        if paragraph.text == "Rien à signaler.":
            paragraph.runs[0].text = "Une école a écrit."
        if paragraph.text == "2) Les cellules":
            paragraph.runs[0].text = "2) Cellules parents"  # renamed heading
    document.add_paragraph("Titre ajouté", style="Heading 2")
    document.save(path)

    bodies, _, warnings = import_docx(path, roots)
    assert bodies["2"].startswith("<p>Une école a écrit.</p>")
    assert bodies["1.2"] == BODIES["1.2"]
    assert len(warnings) == 1 and "Titre ajouté" in warnings[0]
    assert "<strong>Titre ajouté</strong>" in bodies["3"]  # added after the last item


def test_editable_tab_round_trip():
    _app = QApplication.instance() or QApplication([])  # noqa: F841  keep the app alive
    roots = parse_agenda(AGENDA)
    document = QTextDocument()
    document.setHtml(render_preview_html("PV", roots, BODIES, SUMMARY))
    bodies, summary, warnings = records_to_pv(qt_records(document), roots)
    assert warnings == []
    assert bodies == BODIES
    assert summary == SUMMARY


def _png(folder, color="red", width=200, height=100) -> str:
    """Create a picture in the meeting's images folder and return its relative path."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # pylint: disable=import-outside-toplevel
    from PySide6.QtGui import QColor, QImage  # pylint: disable=import-outside-toplevel

    _app = QApplication.instance() or QApplication([])  # noqa: F841  keep the app alive
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return store_image(str(folder), bytes(data), "png")


def test_images_round_trip_through_word(tmp_path):
    roots = parse_agenda(AGENDA)
    src = _png(tmp_path)
    assert src.startswith("images/img_") and _png(tmp_path) == src  # named after the content
    bodies = dict(BODIES, **{"2": f'<p>Rien à signaler.</p>\n<p><img src="{src}" width="300"></p>'})
    path = export_docx(str(tmp_path / "pv.docx"), "PV", roots, bodies, SUMMARY, image_root=str(tmp_path))
    assert len(docx.Document(path).inline_shapes) == 1

    imported, _, _ = import_docx(path, roots, str(tmp_path))
    assert imported == bodies
    assert os.listdir(tmp_path / "images") == [src.split("/")[1]]  # no duplicate


def test_missing_image_is_flagged_in_word(tmp_path):
    roots = parse_agenda(AGENDA)
    bodies = {"2": '<p><img src="images/absente.png"></p>'}
    document = docx.Document(export_docx(str(tmp_path / "pv.docx"), "PV", roots, bodies, image_root=str(tmp_path)))
    assert "[image manquante : images/absente.png]" in [p.text for p in document.paragraphs]


def test_images_in_editable_tab(tmp_path):
    from PySide6.QtCore import QUrl  # pylint: disable=import-outside-toplevel
    from PySide6.QtGui import (  # pylint: disable=import-outside-toplevel
        QTextBlockFormat,
        QTextCharFormat,
        QTextCursor,
        QTextImageFormat,
    )

    roots = parse_agenda(AGENDA)
    src = _png(tmp_path)
    bodies = dict(BODIES, **{"2": f'<p>Rien à signaler.</p>\n<p><img src="{src}" width="300"></p>'})
    document = QTextDocument()
    document.setBaseUrl(QUrl.fromLocalFile(str(tmp_path) + os.sep))
    document.setHtml(render_preview_html("PV", roots, bodies, SUMMARY))
    edited, _, _ = records_to_pv(qt_records(document, str(tmp_path)), roots)
    assert edited == bodies

    # Insert a second picture after "Vente", as the "Insérer une image" button does
    other = _png(tmp_path, color="blue")
    cursor = document.find("Rien à signaler.")
    cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
    cursor.insertBlock(QTextBlockFormat(), QTextCharFormat())
    image = QTextImageFormat()
    image.setName(other)
    image.setWidth(200)
    cursor.insertImage(image)
    edited, _, _ = records_to_pv(qt_records(document, str(tmp_path)), roots)
    assert edited["2"] == f'<p>Rien à signaler.</p>\n<p><img src="{other}" width="200"></p>\n' + (
        f'<p><img src="{src}" width="300"></p>'
    )
