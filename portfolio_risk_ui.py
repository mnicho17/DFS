"""Portfolio Risk inside Results & Learning; parent owns worker retirement."""
from pathlib import Path
import threading
import time
from PyQt5 import QtCore, QtWidgets

from analysis_imports import ImportCancelled
from entry_review_ui import NumberItem
from portfolio_risk import calculate, summary, display_number, Cancelled
from portfolio_risk_evidence import capture, source_catalog, fingerprint


def coverage_text(metric):
    if metric['pct'] is None:
        return f"Unavailable (valid denominator {metric['denominator']})"
    return f"{metric['count']}/{metric['denominator']} ({metric['pct']:.1f}%)"


def pair_text(pair):
    if not pair or not pair['applicable'] or not pair['denominator']:
        return 'Unavailable: no applicable pair with valid occurrences'
    b=pair['buckets'];n=pair['denominator']
    return '; '.join(f'{label}: {count}/{n} ({100*count/n:.1f}%)' for label,count in (
        ('Both',b['both']),('A only',b['a_only']),('B only',b['b_only']),('Neither',b['neither']),
        ('Either (at least one)',pair['either']),('Exactly one',pair['exactly_one'])))


class RiskWorker(QtCore.QObject):
    progress = QtCore.pyqtSignal(int,int,str)
    finished = QtCore.pyqtSignal(dict)
    error = QtCore.pyqtSignal(str)

    def __init__(self, db_path, request):
        super().__init__()
        self.db_path = str(Path(db_path).absolute())
        self.history_root = str(Path(self.db_path).parent)
        # Detached job inputs; no widget/row-index references enter worker work.
        import copy
        self.request = copy.deepcopy(request)
        self.cancelled = threading.Event()

    def request_cancel(self):
        self.cancelled.set()

    @QtCore.pyqtSlot()
    def run(self):
        started = time.monotonic()
        try:
            request = self.request
            captured = capture(self.db_path, request['selection'],history_root=self.history_root,
                cancelled=self.cancelled.is_set,progress=lambda text:self.progress.emit(0,0,text))
            self.progress.emit(0,0,'Calculating fixed-cohort point assumptions')
            report = calculate(captured,request['targets'],request['alternatives'],cancelled=self.cancelled.is_set)
            self.finished.emit(dict(report=report,fingerprint=request['fingerprint'],seconds=time.monotonic()-started))
        except (Cancelled, ImportCancelled):
            self.finished.emit(dict(cancelled=True))
        except OverflowError:
            self.finished.emit(dict(error='Shared evidence read budget reached. No mixed or partial forecast report was applied.'))
        except ValueError as exc:
            self.finished.emit(dict(error=str(exc)))
        except Exception:
            self.finished.emit(dict(error='Risk capture failed; source access or evidence may have changed. Previous output retained.'))


class PortfolioRiskWidget(QtWidgets.QWidget):
    requested = QtCore.pyqtSignal(dict)

    def __init__(self, db_path, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.report = None
        self.report_fingerprint = None
        self.cancel_token = None
        self.started = None
        self.catalog = dict(contests=[],exports=[],issues=[])
        layout = QtWidgets.QVBoxLayout(self)
        self.source_note = QtWidgets.QLabel('Choose a disk-backed source. Cached choices are suggestions; Capture revalidates evidence.')
        self.source_note.setWordWrap(True); layout.addWidget(self.source_note)
        selectors = QtWidgets.QHBoxLayout()
        self.mode = QtWidgets.QComboBox()
        self.mode.addItem('Qualified historical generated archive','archive')
        self.mode.addItem('Recorded saved export (descriptive only)','saved_export')
        self.source = QtWidgets.QComboBox(); self.archive = QtWidgets.QComboBox()
        for combo in (self.source,self.archive):
            combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(18)
        self.reload_button = QtWidgets.QPushButton('Refresh source list')
        for widget in (self.mode,self.source,self.archive,self.reload_button):
            selectors.addWidget(widget)
        layout.addLayout(selectors)
        choices = QtWidgets.QHBoxLayout()
        self.target_a = QtWidgets.QComboBox(); self.target_b = QtWidgets.QComboBox()
        choices.addWidget(QtWidgets.QLabel('Risk athlete A')); choices.addWidget(self.target_a)
        choices.addWidget(QtWidgets.QLabel('Risk athlete B')); choices.addWidget(self.target_b)
        layout.addLayout(choices)
        self.alternatives = QtWidgets.QListWidget()
        self.alternatives.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)
        self.alternatives.setMaximumHeight(100)
        self.alternatives.setToolTip('Explicit alternative assumptions. No role promotion or production transfer.')
        layout.addWidget(QtWidgets.QLabel('Selected alternatives (optional; capture a source first to populate its identity pool)'))
        layout.addWidget(self.alternatives)
        self.run_button = QtWidgets.QPushButton('Capture / Update Risk Report')
        layout.addWidget(self.run_button)
        self.status = QtWidgets.QLabel('No risk report captured. Source files and saved lineups remain unchanged.')
        self.status.setWordWrap(True); layout.addWidget(self.status)
        self.tabs = QtWidgets.QTabWidget(); layout.addWidget(self.tabs,1)
        overview = QtWidgets.QWidget(); overview_layout = QtWidgets.QVBoxLayout(overview)
        self.overview = QtWidgets.QPlainTextEdit(); self.overview.setReadOnly(True)
        self.exposures = QtWidgets.QTableWidget()
        overview_layout.addWidget(self.overview,1); overview_layout.addWidget(self.exposures,1)
        self.tabs.addTab(overview,'Overview')
        stress = QtWidgets.QWidget(); stress_layout = QtWidgets.QVBoxLayout(stress)
        self.stress_note = QtWidgets.QLabel('Choose one or two risk athletes after capture. Retentions: 100%, 75%, 50%, 25%, 0%.')
        self.stress_note.setWordWrap(True)
        self.stresses = QtWidgets.QTableWidget()
        stress_layout.addWidget(self.stress_note); stress_layout.addWidget(self.stresses)
        self.tabs.addTab(stress,'Stress Test')
        self.coverage = QtWidgets.QPlainTextEdit(); self.coverage.setReadOnly(True)
        self.tabs.addTab(self.coverage,'Coverage')
        copy_row = QtWidgets.QHBoxLayout()
        self.details = QtWidgets.QCheckBox('Include player names and lineup details (private)')
        self.copy_button = QtWidgets.QPushButton('Copy Summary')
        self.copy_button.setEnabled(False)
        copy_row.addWidget(self.details); copy_row.addWidget(self.copy_button)
        layout.addLayout(copy_row)
        self.timer = QtCore.QTimer(self); self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self.mode.currentIndexChanged.connect(self._sources)
        self.source.currentIndexChanged.connect(self._archives)
        self.archive.currentIndexChanged.connect(self._source_changed)
        for widget in (self.target_a,self.target_b):
            widget.currentIndexChanged.connect(self._invalidate)
        self.alternatives.itemSelectionChanged.connect(self._invalidate)
        self.reload_button.clicked.connect(self.reload)
        self.run_button.clicked.connect(self._request)
        self.copy_button.clicked.connect(self.copy_summary)
        self._pool({}); self.reload()

    def _pool(self, pool):
        selected = [w.currentData() for w in (self.target_a,self.target_b)]
        alts = {i.data(QtCore.Qt.UserRole) for i in self.alternatives.selectedItems()}
        for widget in (self.target_a,self.target_b,self.alternatives):
            widget.blockSignals(True); widget.clear()
        for widget in (self.target_a,self.target_b):
            widget.addItem('None',None)
        for key,p in pool.items():
            label = f"{p['name']} [{p['team'] or '?'} {p['position'] or '?'}] | {key}"
            for widget in (self.target_a,self.target_b):
                widget.addItem(label,key)
            item = QtWidgets.QListWidgetItem(label); item.setData(QtCore.Qt.UserRole,key)
            self.alternatives.addItem(item); item.setSelected(key in alts)
        for widget,key in zip((self.target_a,self.target_b),selected):
            widget.setCurrentIndex(max(0,widget.findData(key)))
        for widget in (self.target_a,self.target_b,self.alternatives):
            widget.blockSignals(False)

    def reload(self):
        try:
            self.catalog = source_catalog(self.db_path)
        except Exception:
            self.catalog = dict(contests=[],exports=[],issues=['source_catalog_unavailable'])
        self._sources()
        if self.catalog['issues']:
            self.source_note.setText('Source list limitations: '+', '.join(self.catalog['issues'])+
                '. Cached viewing does not requalify sources; inspect Historical Coverage.')

    def _sources(self):
        self.source.blockSignals(True); self.source.clear()
        self.source.addItem('Choose a source',None)
        historical = self.mode.currentData()=='archive'
        records = self.catalog['contests'] if historical else self.catalog['exports']
        for d in records:
            if historical:
                ident = d['identity']
                self.source.addItem(f"{ident.get('sport') or '?'} / {ident.get('format') or '?'} / {ident.get('slate_date') or '?'} | contest {d['identity_id']}", d)
            else:
                self.source.addItem(f"Export {d['export_id']} | {d.get('created_at') or 'time unknown'} | {d.get('contest_type') or '?'}", d)
        self.source.blockSignals(False)
        self.archive.setVisible(historical)
        self._archives()

    def _archives(self):
        self.archive.blockSignals(True); self.archive.clear()
        self.archive.addItem('Choose a qualified archive',None)
        records = (self.source.currentData() or {}).get('archives',[])
        for a in records:
            self.archive.addItem(f"{a['recorded_at']} | {a['output_count']} outputs | {a['archive_id']}",a['archive_id'])
        if len(records)==1:
            self.archive.setCurrentIndex(1)
        self.archive.blockSignals(False)
        self._source_changed()

    def suggest_contest(self, identity_id):
        if self.mode.currentData()!='archive' or self.source.currentData():
            return
        for index in range(1,self.source.count()):
            if self.source.itemData(index)['identity_id']==identity_id:
                self.source.setCurrentIndex(index); return

    def selection(self):
        source = self.source.currentData() or {}
        if self.mode.currentData()=='archive':
            return dict(kind='archive',contest_id=source.get('identity_id'),archive_id=self.archive.currentData())
        return dict(kind='saved_export',export_id=source.get('export_id'))

    def request(self):
        selection = self.selection()
        targets = [w.currentData() for w in (self.target_a,self.target_b) if w.currentData() is not None]
        alternatives = sorted(i.data(QtCore.Qt.UserRole) for i in self.alternatives.selectedItems())
        return dict(selection=selection,targets=targets,alternatives=alternatives,
                    fingerprint=fingerprint(selection,targets,alternatives))

    def _source_changed(self):
        self._pool({})
        self._invalidate()

    def _invalidate(self):
        if self.cancel_token:
            self.cancel_token.set()
        self.copy_button.setEnabled(False)
        self.status.setText('Prior completed output retained; selectors changed. Capture again before copying.' if self.report else
                            'Capture an explicit source; incomplete evidence is unavailable, not zero.')
        selected = self.selection()
        self.run_button.setEnabled(bool(selected.get('archive_id') if selected['kind']=='archive' else selected.get('export_id')))

    def _request(self):
        self.requested.emit(self.request())

    def begin(self, cancel_token):
        self.cancel_token = cancel_token
        self.started = time.monotonic(); self.phase = 'Capturing source evidence'
        self.copy_button.setEnabled(False); self.timer.start(); self._tick()

    def progress(self, text):
        if self.started is not None:
            self.phase = text; self._tick()

    def _tick(self):
        if self.started is not None:
            self.status.setText(f'{self.phase}... {time.monotonic()-self.started:.0f}s elapsed. Cancel Operation remains available.')

    def finish(self, result, cancelled=False):
        self.timer.stop(); self.started = None; self.cancel_token = None
        if cancelled or result.get('cancelled') or result.get('error') or result.get('fingerprint')!=self.request()['fingerprint']:
            self.copy_button.setEnabled(False)
            self.status.setText(('Cancelled or superseded.' if cancelled or not result.get('error') else result['error'])+
                                ' Prior completed output retained; copying is unavailable until a matching capture completes.')
            return
        self.report = result['report']; self.report_fingerprint = result['fingerprint']
        self._render()
        self.copy_button.setEnabled(self.report_fingerprint==self.request()['fingerprint'])
        self.status.setText(f"Frozen capture complete in {result['seconds']:.3f}s. Later source changes are not tracked by this report.")

    def _table(self, table, headers, rows):
        table.setSortingEnabled(False); table.clear(); table.setColumnCount(len(headers)); table.setRowCount(len(rows))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        for row,values in enumerate(rows):
            for col,value in enumerate(values):
                item = (NumberItem(value,str(value)) if isinstance(value,(int,float)) else
                        NumberItem(float('-inf'),'Unavailable') if value is None else QtWidgets.QTableWidgetItem(str(value)))
                table.setItem(row,col,item)
        table.resizeColumnsToContents(); table.horizontalHeader().setStretchLastSection(True)
        table.setSortingEnabled(True)

    def _render(self):
        d = self.report.data; cap = d['capture']; pool = cap['pool']
        self._pool(pool)
        provenance = cap['provenance']
        self.source_note.setText(cap['label']+'\n'+f"{cap['sport']} / {cap['format']} / slate {cap['slate_date'] or 'unknown or mixed'}\n"+
            '\n'.join(f'{k}: {provenance[k]}' for k in ('archive_id','export_id','input_id','snapshot_digest','snapshot_time','archive_time') if k in provenance))
        overview=[f"R={d['R']} source occurrences; N={d['N']} valid occurrences; U={d['U']} unique rosters.",
            'Repeated rows retain their full weight. Shared roster dependencies are not measured outcome correlations.',
            'Concentration is not an error; neither selected player does not mean safe.']
        for letter,key in zip(('A','B'),d['targets']):
            overview.append(f"Risk athlete {letter}: {pool.get(key,{}).get('name','Unknown identity')}")
        if d['pair']:
            overview += ['Selected risk pair: '+pair_text(d['pair'])]
        share=d['selected_projection_share']
        overview += [f"Selected projected contribution / total projection on M={d['M']}: "+
            ('Unavailable (requires selected targets and a positive total)' if share is None else f'{share*100:.1f}%'),
            '',f"Recorded team coverage: {d['context']['team']}/{d['N']} occurrences with complete team context."]
        for team in d['teams']:
            overview.append(f"{team['team']}: {coverage_text(team['appearances'])}; athletes per entry: "+
                ', '.join(f'{count} athletes in {entries} entries' for count,entries in team['players_per_entry'].items()))
        overview += ['',f"QB-WR/TE dependencies: {d['context']['position_team']}/{d['N']} occurrences with complete position/team context."]
        for row in d['qb_receivers']:
            overview.append(f"{pool[row['qb']]['name']} + {pool[row['receiver']]['name']}: "+
                coverage_text(row['appearances'])+f"; game: {row['game'] or 'unknown'}")
        if not d['qb_receivers']:
            overview.append('No qualifying QB-WR/TE pair observed in that context cohort.')
        self.overview.setPlainText('\n'.join(overview))
        regular='FLEX' if cap['format']=='showdown' else 'Roster appearances'
        self._table(self.exposures,['Athlete',f'Any / N={d["N"]}','Any %','Captain','Captain %',regular,regular+' %'],
            [[pool[e['key']]['name'],e['any']['count'],e['any']['pct'],e['captain']['count'],e['captain']['pct'],
              e['noncaptain']['count'],e['noncaptain']['pct']] for e in d['exposures']])
        self.stress_note.setText(d['stress_reason'] or
            f"Fixed cohorts M={d['M']}, M_delta={d['M_delta']}. Signed change = baseline minus stressed points. {d['quantiles']}. Retentions are not probabilities or exact minutes; no production transfer.")
        self._table(self.stresses,['A retained %','B retained %','Affected / N','Baseline mean / M','Stressed mean / M',
            'Direct mean / M_delta','Direct min','Direct median','Direct P90','Direct max','Entry min / M','Entry median','Entry P90','Entry max'],
            [[s['retention'][0]*100,s['retention'][1]*100 if len(s['retention'])>1 else None,
              f"{s['affected']['count']}/{s['affected']['denominator']}",s['baseline_mean'],s['stressed_mean'],
              *[s['direct_change'][k] for k in ('mean','min','median','p90','max')],
              *[s['row_change'][k] for k in ('min','median','p90','max')]] for s in d['scenarios']])
        text = [f"R={d['R']}, N={d['N']}, U={d['U']}, M={d['M']}, M_delta={d['M_delta']}",
                'Repeated output rows count repeatedly. Unique rosters do not replace occurrences.',
                'Rejected/omitted occurrences: '+(', '.join(f'{k.replace("_"," ")}: {v}' for k,v in cap['rejected'].items()) or 'none'),
                'Read limitations: '+(', '.join(f'{k.replace("_"," ")}: {v}' for k,v in cap['limits'].items()) or 'none reached'),
                f"Complete context: team {d['context']['team']}/{d['N']}; position/team {d['context']['position_team']}/{d['N']}; game {d['context']['game']}/{d['N']}.",
                str(provenance.get('slate_context','')),str(provenance.get('upload_slots','')),
                'Role freshness policy v1: applicable source and aware role timestamp <= capture < kickoff, age <=24 hours at earliest historical kickoff.',
                'Timely depth alternatives do not prove exclusive replacement. Neither selected player does not mean safe.']
        for p in pool.values():
            text += ['',f"{p['name']} [{p['team']} {p['position']}]: forecast {display_number(p['base_projection'])}; source {p['forecast_source']}; captured {p['forecast_recorded_at']}",
                     p['captain_basis'],f"{p['role']['group']}; {p['role']['state']}; age {display_number(p['role']['age_hours'])}h; {p['role']['reason']}",
                     '; '.join(f"{key}: {'unknown' if value is None else value}" for key,value in p['role']['raw'].items())]
            if p['legacy_metadata']:
                text.append('Unqualified legacy metadata (not stress input): '+str(p['legacy_metadata']))
        for a in d['alternatives_coverage']:
            text += ['',f"A: {pool.get(a['risk'],{}).get('name','Unknown')}; B (alternative): {pool.get(a['alternative'],{}).get('name','Unknown')}",
                     pair_text(a['coverage']),a['relationship'],
                     'Alternative any-role: '+coverage_text(a['exposure']['any'])+'; Captain: '+coverage_text(a['exposure']['captain'])+
                     '; non-Captain: '+coverage_text(a['exposure']['noncaptain'])]
            if a['all_coexist']:
                text.append('All alternative appearances coexist with the risk player; this is not a disjoint fallback portfolio.')
        text.append('Alternative union (at least one selected alternative): '+coverage_text(d['alternative_union']))
        self.coverage.setPlainText('\n'.join(text))

    def copy_summary(self):
        if self.report and self.started is None and self.copy_button.isEnabled() and self.report_fingerprint==self.request()['fingerprint']:
            QtWidgets.QApplication.clipboard().setText(summary(self.report,self.details.isChecked()))
