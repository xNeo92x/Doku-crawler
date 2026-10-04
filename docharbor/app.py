from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import multiprocessing
from pathlib import Path
import sys

from PySide6.QtCore import QSettings, QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSpinBox, QSplitter, QTabWidget, QTextBrowser, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget)

from .engine import Config, CrawlEngine, cpu_count

STYLE = """
QWidget { background: #101827; color: #e5edf8; font-size: 13px; }
QMainWindow { background: #101827; }
QLabel#eyebrow { color: #67e8d0; font-size: 11px; font-weight: 700; }
QLabel#title { font-size: 32px; font-weight: 750; }
QLabel#muted { color: #93a4bd; }
QFrame#card { background: #172235; border: 1px solid #2a3950; border-radius: 12px; }
QFrame#card QLabel, QFrame#card QWidget { background: transparent; }
QLabel#stat { font-size: 28px; font-weight: 700; color: #67e8d0; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: #101a2b; border: 1px solid #34465f; border-radius: 7px; padding: 8px; min-height: 21px; selection-background-color: #176f67; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border: 1px solid #5bd7bd; }
QPushButton { background: #24354d; border: 1px solid #3a4c66; border-radius: 8px; padding: 10px 16px; font-weight: 600; }
QPushButton:hover { background: #304764; }
QPushButton#primary { background: #53d4b5; color: #082b2a; border: 0; }
QPushButton#primary:hover { background: #7ce8cc; }
QPushButton:disabled { color: #60708b; background: #1b293c; border-color: #25364b; }
QCheckBox { spacing: 8px; padding: 4px; }
QCheckBox::indicator { width: 17px; height: 17px; border: 1px solid #50617c; border-radius: 4px; background: #101a2b; }
QCheckBox::indicator:checked { background: #53d4b5; border: 1px solid #53d4b5; }
QTabWidget::pane { border: 1px solid #2a3950; border-radius: 8px; }
QTabBar::tab { background: #172235; color: #93a4bd; padding: 11px 18px; }
QTabBar::tab:selected { color: #67e8d0; background: #24354d; }
QTreeWidget, QPlainTextEdit, QTextBrowser { background: #101a2b; border: 0; padding: 8px; }
QTreeWidget::item { padding: 8px 4px; }
QTreeWidget::item:selected { background: #224c51; }
QHeaderView::section { background: #1c2b40; color: #93a4bd; padding: 9px; border: 0; }
QProgressBar { background: #223248; border: 0; border-radius: 4px; max-height: 7px; }
QProgressBar::chunk { background: #53d4b5; border-radius: 4px; }
QScrollBar:vertical { background: #152135; width: 10px; }
QScrollBar::handle:vertical { background: #3b4c65; border-radius: 4px; min-height: 24px; }
QSplitter::handle { background: #2a3950; }
QToolTip { background: #24354d; color: #e5edf8; border: 1px solid #50617c; }
"""


class CrawlThread(QThread):
    event = Signal(dict)
    failure = Signal(str)

    def __init__(self, config):
        super().__init__()
        self.engine = CrawlEngine(config, self.event.emit)

    def run(self):
        try:
            asyncio.run(self.engine.run())
        except Exception as e:
            self.failure.emit(str(e))


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DocHarbor · Documentation Archive")
        self.resize(1320, 900)
        self.setMinimumSize(960, 680)
        self.settings = QSettings("DocHarbor", "DocHarbor")
        self.thread = None
        self.rows = {}
        self.latest_stats = {}
        self.last_output = ""
        self.closing = False
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(28, 22, 28, 24)
        layout.setSpacing(16)
        self.setCentralWidget(root)
        eyebrow = QLabel("DOCUMENTATION, PRESERVED.")
        eyebrow.setObjectName("eyebrow")
        layout.addWidget(eyebrow)
        heading = QHBoxLayout()
        title = QLabel("DocHarbor")
        title.setObjectName("title")
        heading.addWidget(title)
        heading.addStretch()
        badge = QLabel(f"Qt 6  •  {cpu_count()} CPU-Kerne verfügbar")
        badge.setObjectName("muted")
        heading.addWidget(badge)
        layout.addLayout(heading)
        subtitle = QLabel("Dokumentation entdecken, parallel verarbeiten und als Markdown bewahren.")
        subtitle.setObjectName("muted")
        layout.addWidget(subtitle)
        splitter = QSplitter()
        layout.addWidget(splitter, 1)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 14, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        form_widget = QWidget()
        form = QFormLayout(form_widget)
        form.setSpacing(13)
        form.setLabelAlignment(Qt.AlignLeft)
        form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://docs.example.org/handbuch/")
        form.addRow("Startadresse", self.url)
        output_widget = QWidget()
        output_row = QHBoxLayout(output_widget)
        output_row.setContentsMargins(0, 0, 0, 0)
        self.output = QLineEdit(str(Path.home() / "Documents" / "DocHarbor" / "Archiv"))
        browse = QPushButton("…")
        browse.setMaximumWidth(42)
        browse.clicked.connect(self.choose_output)
        output_row.addWidget(self.output)
        output_row.addWidget(browse)
        form.addRow("Ausgabeordner", output_widget)
        self.scope = QComboBox()
        self.scope.addItem("Dokumentationspfad", "path")
        self.scope.addItem("Ganze Domain (exakter Host)", "domain")
        self.scope.addItem("Nur diese Seite", "page")
        self.scope.setToolTip("Pfad: bei /guide/start.html wird /guide/ archiviert. Für /guide einen abschließenden / verwenden. Subdomains werden nicht verfolgt.")
        form.addRow("Bereich", self.scope)
        self.concurrency = self.spin(1, 128, 12)
        form.addRow("Gleichzeitige Downloads", self.concurrency)
        self.workers = self.spin(0, cpu_count(), 0)
        self.workers.setSpecialValueText(f"Automatisch · alle {cpu_count()} Kerne")
        form.addRow("CPU-Prozesse", self.workers)
        self.limit = self.spin(1, 1_000_000, 2000)
        form.addRow("Maximale Seiten", self.limit)
        self.depth = self.spin(0, 1000, 30)
        form.addRow("Maximale Link-Tiefe", self.depth)
        self.delay = QDoubleSpinBox()
        self.delay.setRange(0, 60)
        self.delay.setSingleStep(0.05)
        self.delay.setValue(0.15)
        self.delay.setSuffix(" s")
        form.addRow("Abstand zwischen Anfragen", self.delay)
        self.selector = QLineEdit()
        self.selector.setPlaceholderText("Automatisch; optional: article, .docs-content")
        form.addRow("CSS-Selektor für Inhalt", self.selector)
        self.exclude = QLineEdit()
        self.exclude.setPlaceholderText(r"Optional: /search|/login|/changelog")
        form.addRow("URL-Ausschlüsse (Regex)", self.exclude)
        self.robots = QCheckBox("robots.txt berücksichtigen")
        self.robots.setChecked(True)
        self.sitemap = QCheckBox("Sitemaps zur Seitensuche verwenden")
        self.sitemap.setChecked(True)
        self.query = QCheckBox("URL-Abfrageparameter behalten")
        self.resume = QCheckBox("Vorhandenes Archiv fortsetzen")
        self.resume.setChecked(True)
        self.browser = QCheckBox("JavaScript rendern (Chromium)")
        self.browser.setToolTip("Optional: pip install '.[browser]' und python -m playwright install chromium. Maximal vier parallele Browser-Seiten.")
        for check in (self.robots, self.sitemap, self.query, self.resume, self.browser):
            form.addRow(check)
        help_label = QLabel("Export: archive.md + einzelne Seiten + Index.\nBilder bleiben als Links zur Originalquelle erhalten.")
        help_label.setObjectName("muted")
        help_label.setWordWrap(True)
        form.addRow(help_label)
        scroll.setWidget(form_widget)
        left_layout.addWidget(scroll)
        profiles = QHBoxLayout()
        save_profile = QPushButton("Profil speichern")
        load_profile = QPushButton("Profil laden")
        save_profile.clicked.connect(self.save_profile)
        load_profile.clicked.connect(self.load_profile)
        profiles.addWidget(save_profile)
        profiles.addWidget(load_profile)
        left_layout.addLayout(profiles)
        splitter.addWidget(left)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(14, 0, 0, 0)
        right_layout.setSpacing(14)
        cards = QHBoxLayout()
        self.stat_labels = {}
        for key, text in (("saved", "Archiviert"), ("queued", "Warteschlange"), ("errors", "Fehler")):
            card = QFrame()
            card.setObjectName("card")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(16, 12, 16, 12)
            count = QLabel("0")
            count.setObjectName("stat")
            label = QLabel(text)
            label.setObjectName("muted")
            card_layout.addWidget(count)
            card_layout.addWidget(label)
            cards.addWidget(card)
            self.stat_labels[key] = count
        right_layout.addLayout(cards)
        self.status = QLabel("Bereit für dein erstes Dokumentationsarchiv.")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        right_layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        right_layout.addWidget(self.progress)
        self.tabs = QTabWidget()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Seite / Adresse", "Status"])
        self.tree.setColumnWidth(0, 460)
        self.tree.setUniformRowHeights(True)
        self.tree.itemSelectionChanged.connect(self.preview_selected)
        self.tabs.addTab(self.tree, "Seiten")
        self.preview = QTextBrowser()
        self.preview.setOpenLinks(False)
        self.preview.anchorClicked.connect(self.open_preview_link)
        self.tabs.addTab(self.preview, "Markdown-Vorschau")
        self.raw = QPlainTextEdit()
        self.raw.setReadOnly(True)
        self.raw.setFont(QFont("monospace", 11))
        self.tabs.addTab(self.raw, "Markdown-Quelltext")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(4000)
        self.tabs.addTab(self.log, "Protokoll")
        right_layout.addWidget(self.tabs, 1)
        actions = QHBoxLayout()
        self.start_button = QPushButton("Archivierung starten")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start_crawl)
        self.pause_button = QPushButton("Pause")
        self.pause_button.clicked.connect(self.toggle_pause)
        self.stop_button = QPushButton("Stoppen & sichern")
        self.stop_button.clicked.connect(self.stop_crawl)
        self.open_button = QPushButton("Archiv öffnen")
        self.open_button.clicked.connect(self.open_output)
        for button in (self.start_button, self.pause_button, self.stop_button, self.open_button):
            actions.addWidget(button)
        right_layout.addLayout(actions)
        splitter.addWidget(right)
        splitter.setSizes([365, 840])
        self.form_widget = form_widget
        self.profiles_widget = left
        self.set_running(False)
        self.progress.setValue(0)
        self.load_settings()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_stats)
        self.timer.start(250)

    @staticmethod
    def spin(minimum, maximum, value):
        widget = QSpinBox()
        widget.setRange(minimum, maximum)
        widget.setValue(value)
        return widget

    def config(self):
        return Config(url=self.url.text().strip(), output=self.output.text().strip(), scope=self.scope.currentData(),
            concurrency=self.concurrency.value(), workers=self.workers.value(), max_pages=self.limit.value(),
            max_depth=self.depth.value(), delay=self.delay.value(), selector=self.selector.text().strip(),
            exclude=self.exclude.text().strip(), robots=self.robots.isChecked(), sitemap=self.sitemap.isChecked(),
            keep_query=self.query.isChecked(), resume=self.resume.isChecked(), browser=self.browser.isChecked())

    def apply_config(self, values):
        self.url.setText(values.get("url", ""))
        self.output.setText(values.get("output", self.output.text()))
        self.scope.setCurrentIndex(max(0, self.scope.findData(values.get("scope", "path"))))
        for key, widget in (("concurrency", self.concurrency), ("workers", self.workers), ("max_pages", self.limit), ("max_depth", self.depth), ("delay", self.delay)):
            if key in values:
                widget.setValue(values[key])
        self.selector.setText(values.get("selector", ""))
        self.exclude.setText(values.get("exclude", ""))
        for key, widget in (("robots", self.robots), ("sitemap", self.sitemap), ("keep_query", self.query), ("resume", self.resume), ("browser", self.browser)):
            if key in values:
                widget.setChecked(values[key])

    def load_settings(self):
        try:
            self.apply_config(json.loads(self.settings.value("profile", "{}")))
        except (ValueError, TypeError):
            pass

    def choose_output(self):
        path = QFileDialog.getExistingDirectory(self, "Ausgabeordner", self.output.text())
        if path:
            self.output.setText(path)

    def save_profile(self):
        path, _ = QFileDialog.getSaveFileName(self, "Crawl-Profil speichern", "docharbor-profile.json", "JSON (*.json)")
        if path:
            try:
                Path(path).write_text(json.dumps(asdict(self.config()), ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception as e:
                QMessageBox.warning(self, "Profil", str(e))

    def load_profile(self):
        path, _ = QFileDialog.getOpenFileName(self, "Crawl-Profil laden", "", "JSON (*.json)")
        if path:
            try:
                self.apply_config(json.loads(Path(path).read_text(encoding="utf-8")))
            except Exception as e:
                QMessageBox.warning(self, "Profil", str(e))

    def set_running(self, running):
        self.start_button.setEnabled(not running)
        self.profiles_widget.setEnabled(not running)
        self.pause_button.setEnabled(running)
        self.stop_button.setEnabled(running)
        self.open_button.setEnabled(bool(self.last_output))
        self.progress.setRange(0, 0 if running else 1)
        if not running:
            self.progress.setValue(1)

    def start_crawl(self):
        try:
            config = self.config()
            config.validate()
            self.thread = CrawlThread(config)
        except Exception as e:
            QMessageBox.warning(self, "Einstellungen prüfen", str(e))
            return
        self.settings.setValue("profile", json.dumps(asdict(config)))
        self.rows.clear()
        self.tree.clear()
        self.log.clear()
        self.latest_stats = {}
        self.preview.clear()
        self.raw.clear()
        self.last_output = str(Path(config.output).expanduser().resolve())
        self.pause_button.setText("Pause")
        self.status.setText("Archivierung läuft …")
        self.thread.event.connect(self.handle_event)
        self.thread.failure.connect(self.handle_failure)
        self.thread.finished.connect(self.thread_finished)
        self.set_running(True)
        self.thread.start()

    def handle_event(self, event):
        kind = event["kind"]
        if kind == "stats":
            self.latest_stats = event
        elif kind == "log":
            self.log.appendPlainText(event["message"])
        elif kind == "page":
            url = event["url"]
            row = self.rows.get(url)
            if row is None:
                row = QTreeWidgetItem([url, ""])
                row.setData(0, Qt.UserRole, {"url": url})
                self.tree.addTopLevelItem(row)
                self.rows[url] = row
            row.setText(0, event.get("title") or url)
            row.setText(1, event["status"])
            row.setToolTip(0, url)
            row.setToolTip(1, event.get("message", event["status"]))
            if event.get("file"):
                row.setData(0, Qt.UserRole, {"url": url, "file": event["file"]})
            if event.get("message"):
                self.log.appendPlainText(f"{url}: {event['message']}")
        elif kind == "finished":
            reason = "Gestoppt und gesichert" if event["stopped"] else "Seitenlimit erreicht" if event["limited"] else "Archivierung abgeschlossen"
            self.status.setText(reason + " · archive.md ist bereit.")
            self.log.appendPlainText(self.status.text())
            self.latest_stats["final"] = True
            self.restore_rows()

    def restore_rows(self):
        # Show previously archived pages after a resumed crawl too.
        try:
            state = json.loads((Path(self.last_output) / "crawl-state.json").read_text(encoding="utf-8"))
            for url, record in state["records"].items():
                if record["status"] == "saved" and url not in self.rows:
                    self.handle_event({"kind": "page", "url": url, "title": record["title"], "status": "Bereits archiviert", "file": str(Path(self.last_output) / "pages" / record["file"])})
        except (OSError, ValueError, KeyError):
            pass

    def refresh_stats(self):
        e = self.latest_stats
        if not e:
            return
        for key, label in self.stat_labels.items():
            label.setText(f"{e.get(key, 0):,}".replace(",", "."))
        if self.thread and self.thread.isRunning() and not e.get("final"):
            paused = self.thread.engine.pause.is_set()
            self.status.setText(f"{'Pausiert' if paused else 'Läuft'} · {e.get('active', 0)} aktiv · {e.get('skipped', 0)} übersprungen · {e.get('bytes', 0) / 1048576:.1f} MB · {int(e.get('elapsed', 0))} s")

    def toggle_pause(self):
        if self.thread.engine.pause.is_set():
            self.thread.engine.pause.clear()
            self.pause_button.setText("Pause")
        else:
            self.thread.engine.pause.set()
            self.pause_button.setText("Fortsetzen")

    def stop_crawl(self):
        if self.thread:
            self.thread.engine.stop.set()
            self.thread.engine.pause.clear()
            self.stop_button.setEnabled(False)
            self.pause_button.setEnabled(False)
            self.status.setText("Lauf wird gesichert; laufende Konvertierungen werden abgeschlossen …")

    def handle_failure(self, message):
        self.latest_stats["final"] = True
        self.status.setText("Archivierung konnte nicht abgeschlossen werden.")
        self.log.appendPlainText(message)
        if not self.closing:
            QMessageBox.warning(self, "Archivierung", message)

    def thread_finished(self):
        self.set_running(False)
        if self.closing:
            QTimer.singleShot(0, self.close)

    def preview_selected(self):
        items = self.tree.selectedItems()
        if not items:
            return
        data = items[0].data(0, Qt.UserRole)
        if data.get("file"):
            try:
                text = Path(data["file"]).read_text(encoding="utf-8")
                self.preview.setMarkdown(text)
                self.raw.setPlainText(text)
            except OSError as e:
                self.log.appendPlainText(str(e))

    def open_preview_link(self, url):
        if url.scheme() in {"http", "https"}:
            QDesktopServices.openUrl(url)

    def open_output(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.last_output))

    def closeEvent(self, event):
        if self.thread and self.thread.isRunning():
            self.closing = True
            self.stop_crawl()
            event.ignore()
        else:
            self.settings.setValue("profile", json.dumps(asdict(self.config())))
            event.accept()


def main():
    multiprocessing.freeze_support()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    app.setFont(QFont("Segoe UI" if sys.platform == "win32" else "Noto Sans", 10))
    window = Window()
    window.show()
    if "--screenshot" in sys.argv:
        index = sys.argv.index("--screenshot")
        if len(sys.argv) <= index + 1:
            print("--screenshot requires an output filename")
            return 1
        target = sys.argv[index + 1]
        def capture():
            window.grab().save(target)
            app.quit()
        QTimer.singleShot(300, capture)
    return app.exec()
