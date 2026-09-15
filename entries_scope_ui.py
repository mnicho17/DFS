"""Choose the exact contest whose roster cells will be replaced."""
from PyQt5 import QtWidgets


class EntriesScopeDialog(QtWidgets.QDialog):
    def __init__(self,template,saved_count,parent=None):
        super().__init__(parent);self.setWindowTitle('Choose contest to update');self.resize(760,240)
        self.template=template;self.saved_count=saved_count
        layout=QtWidgets.QVBoxLayout(self)
        label=QtWidgets.QLabel(f'{saved_count} replacement lineups saved. Select which contest receives them. Entries are assigned in their existing order within that contest.');label.setWordWrap(True);layout.addWidget(label)
        self.combo=QtWidgets.QComboBox();self.combo.addItem('Choose a contest...', '')
        for c in template.contests():
            self.combo.addItem(f"{c['name']} | {c['fee']} | {c['count']} entries | ID {c['id']}",c['id'])
        self.combo.addItem(f'All contests — {template.entry_count} entries (file order)',None)
        layout.addWidget(self.combo)
        self.note=QtWidgets.QLabel();self.note.setWordWrap(True);layout.addWidget(self.note)
        buttons=QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok|QtWidgets.QDialogButtonBox.Cancel)
        self.ok=buttons.button(QtWidgets.QDialogButtonBox.Ok);self.ok.setText('Use selected contest')
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.combo.currentIndexChanged.connect(self.refresh);self.refresh()
    def refresh(self):
        if self.combo.currentIndex()==0:
            self.ok.setEnabled(False);self.note.setText('Other contests will remain unchanged.');return
        n=len(self.template.selected_rows(self.combo.currentData()));preserved=self.template.entry_count-n
        self.ok.setEnabled(n==self.saved_count)
        self.note.setText(f'{n} entries will receive replacements; {preserved} other entries will remain unchanged. '+
            ('Counts match. You will save a new upload file next.' if n==self.saved_count else f'Save exactly {n} lineups for this selection; currently {self.saved_count} are saved.'))


def choose_scope(parent,template,saved_count):
    contests=template.contests()
    if len(contests)==1:return True,contests[0]['id']
    dialog=EntriesScopeDialog(template,saved_count,parent)
    return dialog.exec_()==QtWidgets.QDialog.Accepted,dialog.combo.currentData()
