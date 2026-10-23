"""Background combined import and explicit salary-match review."""
import threading
import sqlite3
import traceback
from PyQt5 import QtCore, QtWidgets

from analysis_imports import import_folders, pairing_state, save_pair, ImportCancelled


class CombinedImportWorker(QtCore.QObject):
    progress = QtCore.pyqtSignal(int, int, str)
    finished = QtCore.pyqtSignal(dict)
    error = QtCore.pyqtSignal(str)

    def __init__(self, results_folder='', salary_folder='', username='', pair=None, db_path=None, refresh_history=False, automatic=False, salary_review=None):
        super().__init__()
        self.results_folder, self.salary_folder, self.username = results_folder, salary_folder, username
        self.pair = pair
        self.salary_review = salary_review
        self.automatic = automatic
        self.refresh_history = refresh_history
        from learning_db import history_db_path
        self.db_path = str(db_path or history_db_path())
        from compute_ledger import folder
        self.ledger_root = folder()
        self.cancelled = threading.Event()

    def request_cancel(self):
        self.cancelled.set()

    @QtCore.pyqtSlot()
    def run(self):
        try:
            source_checkpoint = checkpoint = source_reuse = None
            if self.automatic and self.refresh_history and not self.pair and not self.salary_review:
                import history_refresh_cache as cache
                self.progress.emit(0, 0, 'Checking history for changes…')
                try:
                    checkpoint, _ = cache.inputs(self)
                    cached = cache.read(self, checkpoint)
                    if cached is not None and not self.cancelled.is_set():
                        self.finished.emit(cached)
                        return
                    history_inputs, _ = cache.inputs(self, include_calibration=False)
                    source_reuse = cache.read(self, history_inputs, sources_only=True)
                    source_checkpoint, _ = cache.inputs(self, sources_only=True)
                except (OSError, ValueError, sqlite3.Error):
                    checkpoint = None
            if source_reuse is not None:
                result = source_reuse
            elif self.salary_review:
                from analysis_salary_reviews import save as save_review
                saved = save_review(*self.salary_review, db_path=self.db_path, cancelled=self.cancelled.is_set)
                result = dict(review_association_saved=int(saved), review_only=True, errors=[])
            elif self.pair:
                corrected_import=None
                if len(self.pair)>3:
                    from analysis_date_corrections import save
                    corrected_import=save(*self.pair[:2],self.pair[3],db_path=self.db_path,cancelled=self.cancelled.is_set)
                    saved=True
                else:
                    saved = save_pair(*self.pair[:2], db_path=self.db_path, confirm_date=self.pair[2], cancelled=self.cancelled.is_set)
                result = dict(pairs_added=int(saved), pair_only=True, errors=[])
                if corrected_import and not self.cancelled.is_set():
                    from performance_review import analyze_saved_results
                    review=analyze_saved_results(username=self.username,import_ids={corrected_import},db_path=self.db_path,
                        cancelled=self.cancelled.is_set,progress=lambda text:self.progress.emit(0,0,text),ledger_root=self.ledger_root)
                    result['analysis_message']=review['message']
                    result['coverage_reconciled']=review.get('identity_reconciled',False)
            else:
                watch_paths = []
                result = import_folders(self.results_folder, self.salary_folder, username=self.username,
                                        db_path=self.db_path, cancelled=self.cancelled.is_set, progress=self.progress.emit, watch_paths=watch_paths)
                result['watch_paths'] = watch_paths
                ids = result.get('analysis_import_ids', [])
                if ids and not self.cancelled.is_set():
                    from performance_review import analyze_saved_results
                    try:
                        review = analyze_saved_results(username=self.username, import_ids=set(ids), db_path=self.db_path,
                            cancelled=self.cancelled.is_set, progress=lambda text: self.progress.emit(0, 0, text), ledger_root=self.ledger_root)
                        result['analysis_message'] = review['message']
                        result['coverage_reconciled'] = review.get('identity_reconciled',False)
                    except Exception as exc:
                        result['errors'].append('Files saved; Analyze Saved Results can retry analysis: ' + str(exc))
            result['cancelled'] = bool(result.get('cancelled') or self.cancelled.is_set())
            if not result['cancelled'] and any(result.get(key) for key in ('results_imported','salaries_imported','pairs_added')):
                # The same authoritative reconciliation also handles salary-only
                # imports and explicit pair saves that resolve older contests.
                from historical_identity import reconcile
                try:
                    if not result.get('coverage_reconciled'):
                        reconcile(self.db_path, cancelled=self.cancelled.is_set,
                                  progress=lambda text: self.progress.emit(0,0,text), ledger_root=self.ledger_root)
                    result['coverage_reconciled'] = True
                except ImportCancelled:
                    result['cancelled'] = True
                except Exception:
                    result['errors'].append('Sources saved; Historical Coverage > Reconcile can retry evidence qualification.')
            if not self.salary_review and not result['cancelled']:
                from calibration_capture_discovery import refresh as discover_calibration
                try:
                    result['calibration_discovery'] = discover_calibration(self.db_path,
                        cancelled=self.cancelled.is_set,
                        progress=lambda text: self.progress.emit(0, 0, text))
                except ImportCancelled:
                    result['cancelled'] = True
                except Exception as exc:
                    result['errors'].append('Calibration captures need retry: ' + str(exc))
            if not result['cancelled'] and (any(result.get(key) for key in ('results_imported','salaries_imported','pairs_added'))
                    or result.get('calibration_discovery', {}).get('changed')):
                from learning_db import generate_learning_report
                try:
                    self.progress.emit(0, 0, 'Refreshing the saved-results report…')
                    result['report'] = generate_learning_report(username=self.username, db_path=self.db_path)
                except Exception as exc:
                    result['errors'].append('Files saved; Refresh Report can retry: ' + str(exc))
            result['cancelled'] = bool(result.get('cancelled') or self.cancelled.is_set())
            if result['cancelled']:
                result.pop('report', None)
            if self.refresh_history and not self.salary_review and not result['cancelled'] and not result.get('calibration_only'):
                from opponent_history import sync_saved
                try:
                    result['username_history'] = sync_saved(self.db_path, self.cancelled.is_set,
                        lambda text: self.progress.emit(0, 0, text))
                except ImportCancelled:
                    result['cancelled'] = True
                except Exception as exc:
                    result['errors'].append('Username history needs retry: ' + str(exc))
            if result['cancelled']:
                result.pop('report', None)
            # Duplicate sources do not rerun personal forecast analysis.
            if checkpoint is not None:
                try:
                    cache.save(self, checkpoint, result, source_checkpoint)
                except (OSError, ValueError, sqlite3.Error):
                    pass  # A cache failure must not invalidate completed imports.
            self.finished.emit(result)
        except ImportCancelled:
            self.finished.emit(dict(cancelled=True, errors=[]))
        except Exception:
            self.error.emit(traceback.format_exc())


class SalaryMatchReviewWorker(QtCore.QObject):
    progress = QtCore.pyqtSignal(int, int, str)
    finished = QtCore.pyqtSignal(dict)
    error = QtCore.pyqtSignal(str)

    def __init__(self, db_path):
        super().__init__()
        self.db_path = db_path
        self.cancelled = threading.Event()

    def request_cancel(self):
        self.cancelled.set()

    def run(self):
        try:
            self.progress.emit(0,0,'Verifying saved salary matches…')
            state = pairing_state(self.db_path, cancelled=self.cancelled.is_set)
            self.finished.emit(dict(state=state, cancelled=self.cancelled.is_set()))
        except ImportCancelled:
            self.finished.emit(dict(cancelled=True))
        except Exception:
            self.error.emit('Salary matches could not be read. Check saved source access and retry.')


class SalaryMatchesDialog(QtWidgets.QDialog):
    """Select an explicit compatible revision; verification runs in the parent worker."""
    def __init__(self, parent=None, db_path=None, *, state=None, result_hash=None):
        super().__init__(parent)
        self.setWindowTitle('Review Salary Matches')
        self.resize(780, 560)
        self.selection = None
        self.review_selection = None
        self.state = state if state is not None else pairing_state(db_path)
        self.rows = self.state['results']
        layout = QtWidgets.QVBoxLayout(self)
        label = QtWidgets.QLabel('Choose the exact contest and salary snapshot. Existing pairings stay fixed. '
            'One salary file can serve multiple contests. '
            'Matching covers readable rosters; it does not establish a complete eligible pool or payouts.')
        label.setWordWrap(True)
        layout.addWidget(label)
        self.summary = QtWidgets.QLabel(
            f"{self.state['saved_pairings']} saved pairings; {self.state['unpaired_results']} unpaired results; "
            f"{self.state['invalid_saved_pairings']} invalid saved pairings; "
            f"{self.state.get('review_associations',0)} manual choices awaiting qualification. "
            f"Database: {self.state['database_identity']}" +
            ('\n' + '\n'.join(self.state['warnings']) if self.state['warnings'] else ''))
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.results = QtWidgets.QComboBox()
        for row in self.rows:
            best = max(row['candidates'], key=lambda c:c.get('matched',0), default={})
            coverage = (f" — best observed coverage {best['matched']}/{best['observed']}" if best.get('observed') and best.get('identity_checked') else '')
            self.results.addItem(row['name'] + ' [' + row['hash'][:12] + '] — ' + ('Needs review (manual choice recorded)' if row['status']=='NEEDS_REVIEW_ASSOCIATION' else row['status']) + coverage, row)
        layout.addWidget(self.results)
        self.correct_date=QtWidgets.QCheckBox('Review a contest-date correction (preserves the original file)')
        layout.addWidget(self.correct_date)
        self.date_reason=QtWidgets.QLineEdit()
        self.date_reason.setPlaceholderText('Reason for correcting the recorded date (at least 8 characters)')
        layout.addWidget(self.date_reason)
        self.inspect_rejected = QtWidgets.QCheckBox('Choose a salary revision manually / inspect conflicts')
        self.inspect_rejected.setToolTip('Shows imported salary revisions, including rejected ones. Full qualification still requires compatible dates, games and player identities.')
        layout.addWidget(self.inspect_rejected)
        self.salaries = QtWidgets.QComboBox()
        layout.addWidget(self.salaries)
        self.candidate_details = QtWidgets.QLabel()
        self.candidate_details.setWordWrap(True)
        layout.addWidget(self.candidate_details)
        self.details = QtWidgets.QPlainTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details)
        self.show_diagnostics = QtWidgets.QCheckBox('Show rejected salary candidates (diagnostics)')
        layout.addWidget(self.show_diagnostics)
        self.diagnostics = QtWidgets.QPlainTextEdit()
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setMaximumHeight(100)
        self.diagnostics.hide()
        self.show_diagnostics.toggled.connect(self.diagnostics.setVisible)
        layout.addWidget(self.diagnostics)
        self.confirm_date = QtWidgets.QCheckBox('I confirm this salary slate belongs to the selected contest (result date missing).')
        layout.addWidget(self.confirm_date)
        self.buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Close)
        self.confirm_review = QtWidgets.QCheckBox('I understand this records my choice only; conflicts still prevent full qualification.')
        layout.addWidget(self.confirm_review)
        self.record_review = self.buttons.addButton('Record as Needs review', QtWidgets.QDialogButtonBox.ActionRole)
        self.record_review.clicked.connect(self._record_review)
        self.confirm_review.toggled.connect(self._review_enabled)
        self.date_reason.textChanged.connect(self._review_enabled)
        self.buttons.accepted.connect(self._select)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.results.currentIndexChanged.connect(self._update)
        self.salaries.currentIndexChanged.connect(self._candidate_changed)
        self.correct_date.toggled.connect(self._update)
        self.inspect_rejected.toggled.connect(self._update)
        self._update()
        if result_hash:
            for index, row in enumerate(self.rows):
                if row['hash'] == result_hash:
                    self.results.setCurrentIndex(index)
                    break

    def _update(self):
        self.salaries.clear()
        self.show_diagnostics.setChecked(False)
        self.diagnostics.clear()
        self.confirm_date.setChecked(False)
        row = self.results.currentData()
        if not row:
            self.details.setPlainText('No results cataloged yet. Import Results & Salaries first.')
            self.buttons.button(QtWidgets.QDialogButtonBox.Save).setEnabled(False)
            self._candidate_changed()
            return
        notes = [f"Status: {row['status']}"]
        notes.append('Recorded dates: '+(', '.join(row.get('dates',[])) or 'unknown'))
        review = row.get('review_association')
        if review and not row['saved_pair']:
            notes.append('Manual salary choice recorded; full qualification still pending. Reason: ' + review['reason'])
        self.inspect_rejected.setVisible(not row['saved_pair'])
        self.correct_date.setVisible(not row['saved_pair'] and any(c.get('date_correctable') for c in row['candidates']))
        if row['entries'] is not None:
            pct = f"{row['readable_pct']:.1f}%" if row['readable_pct'] is not None else 'n/a'
            notes.append(f"Readable result rosters: {row['readable']:,} / {row['entries']:,} ({pct})")
        notes.append(f"Unreadable result rosters: {row['unreadable']:,} (excluded from matching).")
        saved = row['saved_pair']
        rejected = []
        for candidate in sorted(row['candidates'], key=lambda c:(bool(review and c['hash']==review['salary_hash']),c['compatible'],c.get('date_correctable',False),c.get('matched',0)), reverse=True):
            label = candidate['name'] + ' [' + candidate['hash'][:12] + ']'
            if not candidate['compatible']:
                rejected.append(label + ': ' + candidate['reason'])
            if (bool(review and candidate['hash']==review['salary_hash']) or self.inspect_rejected.isChecked() or candidate['compatible'] or (self.correct_date.isChecked() and candidate.get('date_correctable'))) and not saved:
                self.salaries.addItem(label + (' — conflict: review required' if not candidate['compatible'] else ''), candidate)
                notes.append(label + ': ' + candidate['reason'])
        if saved:
            notes[1:1] = [f"Salary file: {saved['name']}", f"Revision: {saved['hash']}",
                          f"Qualification: {saved['reason']}", 'Existing pairing preserved.']
            self.salaries.addItem(saved['name'] + ' [' + saved['hash'][:12] + ']', saved)
            if row['status'] == 'INVALID_SAVED_PAIR':
                notes.append('This saved revision no longer qualifies. No automatic repair or replacement was made.')
        elif not row['candidates']:
            notes.append('No salary files imported. Choose a salary folder and run the combined import.')
        elif row['status'] == 'NO_COMPATIBLE_MATCH':
            notes.extend(rejected)
        self.diagnostics.setPlainText('\n\n'.join(rejected))
        self.show_diagnostics.setVisible(bool(rejected) and row['status'] != 'NO_COMPATIBLE_MATCH')
        self.details.setPlainText('\n\n'.join(notes))
        enabled=not saved and self.salaries.count()>0
        self.salaries.setEnabled(enabled)
        self.buttons.button(QtWidgets.QDialogButtonBox.Save).setEnabled(enabled)
        self._candidate_changed()

    def _candidate_changed(self):
        candidate = self.salaries.currentData()
        row = self.results.currentData()
        self.candidate_details.setText('' if not candidate else
            f"{candidate.get('format') or 'Unknown format'} | dates: {', '.join(candidate.get('dates',[])) or 'unknown'} | "
            f"{candidate.get('player_roles','unknown')} salary player/role identities\n" +
            ('Observed identities matched: '+str(candidate['matched'])+'/'+str(candidate['observed'])+'\n' if candidate.get('identity_checked') else 'Observed identities: not checked; see the compatibility blocker.\n') +
            f"Games: {', '.join(candidate.get('games',[])) or 'unknown'}\n"
            f"Imported: {candidate.get('imported_at','not recorded')} | revision {candidate['hash'][:12]}\n"
            f"Already linked to {candidate.get('linked_contests',0)} contests; available for additional contests.\n"
            f"{candidate.get('reason','')}")
        correction=bool(candidate and candidate.get('date_correctable') and self.correct_date.isChecked())
        can_save = bool(row and not row['saved_pair'] and candidate and (candidate.get('compatible') or correction))
        self.buttons.button(QtWidgets.QDialogButtonBox.Save).setEnabled(can_save)
        if candidate and not can_save and not row['saved_pair']:
            self.candidate_details.setText(self.candidate_details.text() + '\nNot qualified: resolve the reported conflicts before saving a full pairing.')
        draft = bool(row and not row['saved_pair'] and candidate and not candidate.get('compatible') and not correction)
        self.date_reason.setVisible(correction or draft)
        self.date_reason.setPlaceholderText('Reason this is the intended salary revision (8–1000 characters)' if draft else 'Reason for correcting the recorded date (at least 8 characters)')
        self.confirm_review.setVisible(draft)
        self.confirm_review.setChecked(False)
        self.record_review.setVisible(draft)
        self._review_enabled()
        self.confirm_date.setText('I confirm changing this contest date to '+', '.join(candidate.get('dates',[]))+'.' if correction else
            'I confirm this salary slate belongs to the selected contest (result date missing).')
        self.confirm_date.setVisible(bool(row and not row['saved_pair'] and candidate and (correction or (candidate['compatible'] and not candidate['automatic']))))
        self.confirm_date.setChecked(False)

    def _review_enabled(self):
        row, candidate = self.results.currentData(), self.salaries.currentData()
        draft = bool(row and not row['saved_pair'] and candidate and not candidate.get('compatible')
                     and not (self.correct_date.isChecked() and candidate.get('date_correctable')))
        self.record_review.setEnabled(draft and self.confirm_review.isChecked() and 8 <= len(self.date_reason.text().strip()) <= 1000)

    def _record_review(self):
        self._review_enabled()
        if not self.record_review.isEnabled():return
        row, candidate = self.results.currentData(), self.salaries.currentData()
        self.review_selection = (row['hash'], candidate['hash'], self.date_reason.text().strip())
        self.accept()

    def _select(self):
        row, candidate = self.results.currentData(), self.salaries.currentData()
        if not row or not candidate or row['saved_pair']:
            return
        correction=bool(self.correct_date.isChecked() and candidate.get('date_correctable'))
        if not candidate['compatible'] and not correction:return
        needs_date = correction or not candidate['automatic']
        if needs_date and not self.confirm_date.isChecked():
            QtWidgets.QMessageBox.information(self, 'Confirm the contest date', 'Confirm that this exact salary slate belongs to the selected contest.')
            return
        self.selection = (row['hash'], candidate['hash'], self.confirm_date.isChecked())
        if correction:
            if len(self.date_reason.text().strip())<8:
                QtWidgets.QMessageBox.information(self,'Correction reason required','Record why this contest date should be corrected.')
                return
            self.selection+= (self.date_reason.text().strip(),)
        self.accept()


def import_summary(result):
    if result.get('review_only'):
        return ('Salary association recorded as Needs review.' if result.get('review_association_saved') else 'The same salary review association was already recorded.') + '\nNo qualified pairing was created. Resolve the displayed conflicts before historical qualification; sources and previous pairings remain unchanged.'
    lines = ['Import cancelled; completed files remain saved.' if result.get('cancelled') else 'Import complete.',
             f"New result files: {result.get('results_imported', 0):,}",
             f"New salary files: {result.get('salaries_imported', 0):,}",
             f"Already imported files skipped: {result.get('duplicates_skipped', 0):,}",
             f"Other CSV files ignored: {result.get('ignored', 0):,}",
             f"Your result entries added: {result.get('personal_results_added', 0):,}",
             f"New contest salary matches: {result.get('pairs_added', 0):,}"]
    if result.get('unpaired'):
        lines.append(f"{result['unpaired']:,} results need review. Use Review Salary Matches.")
    if result.get('pairs_added'):
        lines.append('Analyze Saved Results applies saved salary matches to construction reports.')
    if result.get('analysis_message'):
        lines.append(result['analysis_message'])
    if result.get('errors'):
        lines.append('Files needing retry / review:\n' + '\n'.join(result['errors']))
    return '\n'.join(lines)
