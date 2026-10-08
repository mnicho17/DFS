"""Explicit Captain eligibility checklist; FLEX and exposure settings are preserved."""
from PyQt5 import QtCore, QtWidgets
from portfolio_rules import player_key
from showdown_simulation import active_showdown_players


class CaptainPoolDialog(QtWidgets.QDialog):
    def __init__(self, players, parent=None):
        super().__init__(parent)
        self.players = players
        self.setWindowTitle('Showdown Captain Pool')
        layout = QtWidgets.QVBoxLayout(self)
        note = QtWidgets.QLabel('Checked players may be Captain during screening and generation. '
            'Unchecked players remain available at FLEX under their existing rules. '
            'This does not force equal exposure. Existing Captain locks take priority; '
            'FLEX locks and unavailable players cannot be added here.')
        note.setWordWrap(True); layout.addWidget(note)
        self.list = QtWidgets.QListWidget(); layout.addWidget(self.list)
        self.rows = {}
        active = {player_key(p) for p in active_showdown_players(players)}
        for player in sorted(players, key=lambda p: (-float(p.get('CptSalary') or 0), player_key(p))):
            key = player_key(player)
            if key in self.rows:
                raise ValueError('Duplicate player identity in Captain pool.')
            eligible = key in active and not player.get('FadeFlex') and not player.get('LockFlex')
            locked = bool(player.get('LockCpt'))
            if locked and (not eligible or player.get('FadeCpt')):
                raise ValueError('Resolve the conflicting Captain lock for '+str(player.get('Name')))
            label = f"{player.get('Name')} ({player.get('Team')}, {player.get('Position', player.get('Pos', ''))}) — CPT ${float(player.get('CptSalary') or 0):,.0f}"
            if locked: label += ' — locked'
            elif not eligible: label += ' — unavailable for Captain'
            item = QtWidgets.QListWidgetItem(label)
            item.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Checked if eligible and not player.get('FadeCpt') else QtCore.Qt.Unchecked)
            if not eligible or locked: item.setFlags(QtCore.Qt.NoItemFlags)
            self.list.addItem(item); self.rows[key] = (player, item, eligible, locked)
        row = QtWidgets.QHBoxLayout()
        for label, checked in (('All eligible', True), ('Clear unlocked', False)):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(lambda _, value=checked: self.set_all(value)); row.addWidget(button)
        layout.addLayout(row)
        self.message = QtWidgets.QLabel(); self.message.setWordWrap(True); layout.addWidget(self.message)
        self.list.itemChanged.connect(self.describe)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self.save = buttons.button(QtWidgets.QDialogButtonBox.Save)
        self.describe(); self.resize(620, 600)

    def set_all(self, checked):
        for _, item, eligible, locked in self.rows.values():
            if eligible and not locked:
                item.setCheckState(QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked)

    def describe(self):
        count = sum(eligible and item.checkState() == QtCore.Qt.Checked for _, item, eligible, _ in self.rows.values())
        locked = sum(locked for _, _, _, locked in self.rows.values())
        self.message.setText(f'{count} Captains enabled.' +
            (f' Existing locks restrict the effective pool to {locked}.' if locked else '') +
            ' Changing the pool requires new screening; the full roster library remains reusable.')
        self.save.setEnabled(count > 0)

    def accept(self):
        if not any(eligible and item.checkState() == QtCore.Qt.Checked for _, item, eligible, _ in self.rows.values()):
            return
        for player, item, eligible, _ in self.rows.values():
            if eligible:
                player['FadeCpt'] = item.checkState() != QtCore.Qt.Checked
        super().accept()
