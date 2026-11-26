"""First-run dialogs: API credentials, Telegram login, storage channel."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from telethon import errors

from .. import APP_NAME
from ..tg import TelegramService
from . import theme
from .common import spawn


def _info(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.RichText)
    label.setOpenExternalLinks(True)
    return label


def _error_label() -> QLabel:
    label = QLabel()
    label.setObjectName("ErrorText")
    label.setWordWrap(True)
    label.hide()
    return label


def _brand(title: str, subtitle: str) -> QWidget:
    """Logo + title strip at the top of the first-run dialogs."""
    box = QWidget()
    box.setObjectName("BrandHeader")
    box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 14)
    row.setSpacing(14)
    logo = QLabel()
    logo.setPixmap(theme.logo_pixmap(40))
    row.addWidget(logo, 0, Qt.AlignmentFlag.AlignTop)
    text = QVBoxLayout()
    text.setSpacing(2)
    heading = QLabel(title)
    heading.setObjectName("BrandTitle")
    sub = QLabel(subtitle)
    sub.setObjectName("Muted")
    sub.setWordWrap(True)
    text.addWidget(heading)
    text.addWidget(sub)
    text.addStretch(1)
    row.addLayout(text, 1)
    return box


class ApiCredentialsDialog(QDialog):
    def __init__(self, api_id: int = 0, api_hash: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — Telegram API keys")
        self.setMinimumWidth(480)

        self.api_id = QLineEdit(str(api_id) if api_id else "")
        self.api_id.setValidator(QIntValidator(1, 2_147_483_647, self))
        self.api_hash = QLineEdit(api_hash)
        self.api_hash.setPlaceholderText("32 characters")
        self.error = _error_label()

        form = QFormLayout()
        form.addRow("API ID", self.api_id)
        form.addRow("API hash", self.api_hash)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._check)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(_brand(f"Welcome to {APP_NAME}", "Step 1 of 3 · Telegram API keys"))
        layout.addWidget(
            _info(
                "This app connects to Telegram as <b>your own account</b>, so it needs an API ID and "
                "API hash of your own (they're free):"
                "<ol><li>Open <a href='https://my.telegram.org/apps'>my.telegram.org/apps</a> and log in.</li>"
                "<li>Create an application (any name, platform “Desktop”).</li>"
                "<li>Copy the <b>App api_id</b> and <b>App api_hash</b> here.</li></ol>"
                "They are stored only on this computer."
            )
        )
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addWidget(buttons)

    def _check(self) -> None:
        api_hash = self.api_hash.text().strip()
        if not self.api_id.text().strip():
            self.error.setText("Enter the API ID (a number).")
        elif len(api_hash) != 32 or any(c not in "0123456789abcdefABCDEF" for c in api_hash):
            self.error.setText("The API hash should be 32 letters and digits (0-9, a-f).")
        else:
            self.accept()
            return
        self.error.show()

    def values(self) -> tuple[int, str]:
        return int(self.api_id.text()), self.api_hash.text().strip()


class LoginDialog(QDialog):
    """Phone number → login code → (optional) two-step verification password."""

    def __init__(self, tg: TelegramService, parent: QWidget | None = None):
        super().__init__(parent)
        self.tg = tg
        self.phone = ""
        self.api_keys_rejected = False
        self.setWindowTitle(f"{APP_NAME} — Log in to Telegram")
        self.setMinimumWidth(420)

        self.stack = QStackedWidget()
        self.phone_edit = QLineEdit()
        self.phone_edit.setPlaceholderText("+91 98765 43210")
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("12345")
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)

        self.stack.addWidget(
            self._page(
                "Enter your phone number with the country code. Telegram will send you a login code.",
                "Phone number", self.phone_edit,
            )
        )
        self.stack.addWidget(
            self._page(
                "Enter the code Telegram just sent you (check the Telegram app on your phone).",
                "Login code", self.code_edit,
            )
        )
        self.stack.addWidget(
            self._page(
                "Your account has two-step verification. Enter your Telegram cloud password.",
                "Password", self.password_edit,
            )
        )

        self.error = _error_label()
        self.next_button = QPushButton("Send code")
        self.next_button.setDefault(True)
        self.next_button.clicked.connect(self._next)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)

        buttons = QDialogButtonBox()
        buttons.addButton(self.next_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(cancel, QDialogButtonBox.ButtonRole.RejectRole)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(_brand("Log in to Telegram", "Step 2 of 3 · Your Telegram account"))
        layout.addWidget(self.stack)
        layout.addWidget(self.error)
        layout.addWidget(buttons)

    @staticmethod
    def _page(text: str, label: str, field: QLineEdit) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(_info(text))
        form = QFormLayout()
        form.addRow(label, field)
        layout.addLayout(form)
        return page

    def _busy(self, busy: bool, text: str | None = None) -> None:
        self.next_button.setEnabled(not busy)
        if text:
            self.next_button.setText(text)
        if busy:
            self.error.hide()

    def _fail(self, message: str) -> None:
        self.error.setText(message)
        self.error.show()

    def _next(self) -> None:
        page = self.stack.currentIndex()
        if page == 0:
            spawn(self._send_code(), on_error=lambda e: self._fail(str(e)))
        elif page == 1:
            spawn(self._sign_in(), on_error=lambda e: self._fail(str(e)))
        else:
            spawn(self._password(), on_error=lambda e: self._fail(str(e)))

    async def _send_code(self) -> None:
        phone = "".join(c for c in self.phone_edit.text() if c.isdigit() or c == "+")
        if len(phone) < 8:
            self._fail("Enter your full phone number, including the country code (e.g. +91…).")
            return
        self._busy(True, "Sending…")
        try:
            await self.tg.send_code(phone)
        except errors.PhoneNumberInvalidError:
            self._fail("Telegram says this phone number is not valid.")
            self._busy(False, "Send code")
            return
        except (errors.ApiIdInvalidError, errors.ApiIdPublishedFloodError):
            self.api_keys_rejected = True
            self.reject()
            return
        except errors.FloodError as exc:
            self._fail(f"Too many attempts. Telegram asks you to wait {getattr(exc, 'seconds', '?')} seconds.")
            self._busy(False, "Send code")
            return
        except Exception:
            self._busy(False, "Send code")
            raise
        self.phone = phone
        self.stack.setCurrentIndex(1)
        self._busy(False, "Log in")
        self.code_edit.setFocus()

    async def _sign_in(self) -> None:
        code = "".join(c for c in self.code_edit.text() if c.isdigit())
        if not code:
            self._fail("Enter the code.")
            return
        self._busy(True, "Logging in…")
        try:
            await self.tg.sign_in_code(self.phone, code)
        except errors.SessionPasswordNeededError:
            self.stack.setCurrentIndex(2)
            self._busy(False, "Log in")
            self.password_edit.setFocus()
            return
        except (errors.PhoneCodeInvalidError, errors.PhoneCodeEmptyError):
            self._fail("That code is wrong. Try again.")
            self._busy(False, "Log in")
            return
        except errors.PhoneCodeExpiredError:
            self._fail("That code has expired. Cancel and start again to get a new one.")
            self._busy(False, "Log in")
            return
        except Exception:
            self._busy(False, "Log in")
            raise
        self.accept()

    async def _password(self) -> None:
        self._busy(True, "Logging in…")
        try:
            await self.tg.sign_in_password(self.password_edit.text())
        except errors.PasswordHashInvalidError:
            self._fail("Wrong password.")
            self._busy(False, "Log in")
            return
        except Exception:
            self._busy(False, "Log in")
            raise
        self.accept()


class ChannelDialog(QDialog):
    """Pick where files are stored: a new private channel, or one you already own."""

    def __init__(self, tg: TelegramService, parent: QWidget | None = None):
        super().__init__(parent)
        self.tg = tg
        self.channel_id = 0
        self.channel_title = ""
        self.setWindowTitle(f"{APP_NAME} — Storage channel")
        self.setMinimumWidth(460)

        self.create_radio = QRadioButton("Create a new private channel (recommended)")
        self.create_radio.setChecked(True)
        self.existing_radio = QRadioButton("Use a channel or group I already have")
        group = QButtonGroup(self)
        group.addButton(self.create_radio)
        group.addButton(self.existing_radio)

        self.title_edit = QLineEdit(f"{APP_NAME}")
        self.combo = QComboBox()
        self.combo.addItem("Loading your channels…")
        self.combo.setEnabled(False)
        self.error = _error_label()

        self.create_radio.toggled.connect(self._sync)

        self.ok_button = QPushButton("Continue")
        self.ok_button.setDefault(True)
        self.ok_button.clicked.connect(lambda: spawn(self._confirm(), on_error=self._fail))
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons = QDialogButtonBox()
        buttons.addButton(self.ok_button, QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(cancel, QDialogButtonBox.ButtonRole.RejectRole)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(_brand("Where should files go?", "Step 3 of 3 · Storage channel"))
        layout.addWidget(
            _info(
                "Your files are kept as messages in a private Telegram channel that only you can see. "
                "Don't delete messages from it in Telegram, or those files will be lost."
            )
        )
        layout.addWidget(self.create_radio)
        create_form = QFormLayout()
        create_form.setContentsMargins(24, 0, 0, 8)
        create_form.addRow("Channel name", self.title_edit)
        layout.addLayout(create_form)
        layout.addWidget(self.existing_radio)
        existing_form = QFormLayout()
        existing_form.setContentsMargins(24, 0, 0, 0)
        existing_form.addRow("Channel", self.combo)
        layout.addLayout(existing_form)
        layout.addWidget(self.error)
        layout.addWidget(buttons)

        self._channels_loaded = False
        self._sync()
        spawn(self._load(), on_error=self._fail)

    def _fail(self, exc: BaseException | str) -> None:
        self.error.setText(str(exc))
        self.error.show()
        self.ok_button.setEnabled(True)

    def _sync(self) -> None:
        creating = self.create_radio.isChecked()
        self.title_edit.setEnabled(creating)
        self.combo.setEnabled(not creating and self._channels_loaded and self.combo.count() > 0)

    async def _load(self) -> None:
        channels = await self.tg.list_channels()
        self.combo.clear()
        for channel_id, title in channels:
            self.combo.addItem(title, channel_id)
        if not channels:
            self.combo.addItem("(you don't own any channels yet)")
        self._channels_loaded = bool(channels)
        self._sync()

    async def _confirm(self) -> None:
        self.error.hide()
        self.ok_button.setEnabled(False)
        if self.create_radio.isChecked():
            title = self.title_edit.text().strip() or APP_NAME
            self.channel_id, self.channel_title = await self.tg.create_channel(title)
        else:
            channel_id = self.combo.currentData()
            if not channel_id:
                self._fail("Choose a channel, or create a new one.")
                return
            self.channel_id, self.channel_title = channel_id, self.combo.currentText()
        self.accept()
