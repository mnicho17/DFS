"""Visible entry-count defaults; never infer real field size or payouts."""
def entry_target(count):
    count=max(1,int(count))
    preset='Single Entry' if count==1 else '3-Max' if count<=3 else '20-Max' if count<=20 else '150-Max'
    selection='Individual ranking' if count==1 or count>150 else 'Portfolio selection'
    label=f'{preset}; '+('ranked alternatives' if count>150 else 'individual ranking' if count==1 else 'entries selected together')
    return dict(preset=preset,selection_mode=selection,label=label)


def setup_entry_target(w):
    from PyQt5 import QtWidgets
    w.chk_auto_entry_target=QtWidgets.QCheckBox('Auto from lineup count')
    w.chk_auto_entry_target.setObjectName('autoEntryTarget')
    w.chk_auto_entry_target.setToolTip('1: Single Entry; 2–3: 3-Max; 4–20: 20-Max; 21–150: 150-Max. More than 150 keeps individual-ranked alternatives. These are defaults, not verified contest size or payouts. Turn off to choose manually.')
    saved=w.app_settings.value('build/auto_entry_target',True,type=bool)
    w.chk_auto_entry_target.setChecked(saved)
    grid=w.lbl_compute_summary.parentWidget().layout()
    # The compute summary belongs to the existing build grid.
    grid.addWidget(w.chk_auto_entry_target,4,0,1,2)
    w.chk_auto_entry_target.toggled.connect(lambda *_:sync_entry_target(w, persist=True))
    w.spin_cl.valueChanged.connect(lambda *_:sync_entry_target(w))
    w.spin_sd.valueChanged.connect(lambda *_:sync_entry_target(w))
    sync_entry_target(w)


def sync_entry_target(w, persist=False, count=None, kind=None):
    if not hasattr(w,'chk_auto_entry_target') or getattr(w,'_syncing_entry_target',False):return
    from PyQt5 import QtCore
    w._syncing_entry_target=True
    try:
        nfl=w.combo_sport.currentText().upper()=='NFL'
        kind=kind or w._contest_mode()
        auto=w.chk_auto_entry_target.isChecked()
        if persist:w.app_settings.setValue('build/auto_entry_target',auto)
        w.chk_auto_entry_target.setVisible(nfl)
        w.lbl_field_preset.setVisible(nfl);w.combo_field_preset.setVisible(nfl)
        w.lbl_field_preset.setText('Entry target' if kind=='showdown' else 'Contest preset')
        w.combo_field_preset.setEnabled(not auto)
        if count is None:count=w.spin_sd.value() if kind=='showdown' else w.spin_cl.value()
        target=entry_target(count)
        if auto and nfl:
            block=QtCore.QSignalBlocker(w.combo_field_preset)
            w.combo_field_preset.setCurrentText(target['preset']);del block
            w.deep_compute_settings['selection_mode']=target['selection_mode']
        if nfl:
            summary=('Auto: '+target['label']) if auto else 'Manual contest and selection settings'
            summary+=' · Showdown opponent sample remains generic.' if kind=='showdown' else ' · Actual field size/payouts require a contest profile.'
            text=w.lbl_compute_summary.text().split('\nEntry target:')[0]
            if auto:
                for mode in ('Individual ranking','Portfolio selection'):text=text.replace(mode,target['selection_mode'])
            w.lbl_compute_summary.setText(text+'\nEntry target: '+summary)
    finally:w._syncing_entry_target=False
