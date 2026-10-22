"""Read-only review views over a single selected build receipt."""
from copy import deepcopy
from PyQt5 import QtCore, QtWidgets
from build_diagnostics import format_build_report


def report_text(record):
    if record.get('record_type') == 'failed_attempt':
        return ('DFS Optimizer Failed Build Attempt\n'
                f"Run: {record.get('created_at', 'not recorded')}\n"
                f"Input ID: {record.get('input_id') or 'not recorded'}\n"
                f"Contest: {record.get('sport', 'unknown')} {record.get('contest_type', 'unknown')}\n"
                f"Requested: {record.get('requested_count', 'not recorded')}\n\n"
                'This attempt failed. No completed result, lineup, or SIM receipt is associated with it.\n'
                'Session-only error details; may include traceback paths.\n\n' + record['failure_message'] +
                ('\n\n' + record['recovery_guidance'] if record.get('recovery_guidance') else ''))
    return format_build_report(record) if record else 'No build receipt selected.'


def review_views(record):
    """Never borrow current UI inputs or another run's reports to fill missing evidence."""
    if not record:
        return {key:'No build receipt selected.' for key in ('overview','lineups','limits','compute','comparisons')}
    if record.get('record_type') == 'failed_attempt':
        unavailable='No completed result evidence for this failed attempt. Previous builds remain separate in the list.'
        return dict(overview=report_text(record),limits=record['failure_message'] + ('\n\n' + record['recovery_guidance'] if record.get('recovery_guidance') else ''),
                    lineups=unavailable,compute=unavailable,comparisons=unavailable)
    from ranked_diagnostics import format_ranked_groups
    from captain_pool import adjustment_text
    from exposure_cap_diagnostics import text as cap_text
    from zero_qb_rules import text as zero_text
    from portfolio_recovery import format_recovery
    from field_diagnostics import format_field
    candidates=record.get('candidates') or {}; portfolio=record.get('portfolio') or {}
    settings=record.get('settings') or {}; sim=record.get('sim') or {}
    def value(mapping,key):
        v=mapping.get(key)
        return 'not recorded' if v is None else str(v)
    warnings=list(portfolio.get('warnings') or [])
    edge_label = 'SIM edge' if settings.get('sim_enabled') else 'quality estimate (SIM off)'
    overview=[f"Recorded run: {record.get('created_at') or 'not recorded'}",
              f"Input ID: {record.get('input_id') or 'not recorded'}",
              f"{record.get('sport','unknown')} {record.get('contest_type','unknown')} · {record.get('status','unknown')}",
              f"Selected: {value(candidates,'selected')} / requested {value(record,'requested_count')}",
              f"Generated candidates: {value(candidates,'generated')}",
              f"Recorded warning count: {value(portfolio,'warning_count')}",
              f"Recorded average {edge_label}: {value(sim,'average_edge')}",
              f"Recorded average expected ROI (%): {value(sim,'average_expected_roi_pct')}",
              'SIM estimates describe this run; they do not establish actual contest performance or submission.']
    if record.get('application'):
        application=record['application']
        overview += [f"Saved-repair application: {application.get('status','unknown')} ({application.get('reason','not recorded')})"]
    overview += ['','Warnings'] + (warnings or ['No warnings recorded.'])
    overview += format_ranked_groups(record.get('ranked_groups') or [])
    limits=['Recorded rules and adjustments — no limits are changed by this view.']
    rules=record.get('portfolio_rules') or {}
    limits += [f"{label}: {value(rules,key)}" for key,label in (
        ('minimum_unique','Minimum unique players'),('team_max_pct','Maximum team exposure (%)'),
        ('game_max_pct','Maximum game exposure (%)'),('group_count','Player groups'),
        ('constrained_player_count','Players with exposure constraints'))]
    limits += ['','Warnings'] + (warnings or ['No warnings recorded.'])
    if portfolio.get('captain_pool_adjustments'): limits += ['',adjustment_text(portfolio['captain_pool_adjustments'])]
    limits += zero_text(portfolio) + cap_text(portfolio.get('exposure_cap_diagnostic')) + format_recovery(portfolio.get('portfolio_recovery'))
    if not portfolio.get('captain_pool_adjustments'): limits.append('Captain exposure adjustments: not recorded for this build.')
    lineups=['Recorded lineup snapshots only. Current/saved portfolios are not substituted.']
    details=record.get('lineup_details') or []
    lineups.append(f"Snapshots retained: {len(details)}; total lineups recorded: {value(record,'lineup_details_total')}. Showdown receipts retain at most 50 detailed rows.")
    if not details: lineups.append('Per-lineup snapshots are not recorded for this build. Aggregate/ranked metrics remain available in Overview and Full report.')
    for row in details:
        captain=row.get('captain') or {}; flex=row.get('flex') or []
        lineups += ['',f"Lineup {row.get('number','?')} · salary {row.get('salary','not recorded')} · projection {row.get('projection','not recorded')}",
                    'CPT: '+str(captain.get('name','unknown')),
                    'FLEX: '+', '.join(str(p.get('name','unknown')) for p in flex),
                    'Team split: '+str(row.get('team_split') or 'not recorded'),
                    'Review flags: '+('; '.join(row.get('flags') or []) or 'none recorded')]
    exposures=record.get('exposures') or {}
    for scope in ('total','captain'):
        lineups += ['',scope.title()+' exposure across the recorded portfolio:']
        lineups += [f"{r.get('label','unknown')}: {r.get('count','unknown')} ({r.get('pct','unknown')}%)" for r in exposures.get(scope) or []] or ['Not recorded.']
    compute=['Recorded preparation and compute evidence; no source validation or SIM is run here.',
             'Compute mode: '+value(settings,'compute_mode'),
             'SIM requested: '+value(settings,'sim_enabled')]
    compute += [f"{label}: {value(record.get('timing') or {},key)}" for key,label in (
        ('generation_seconds','Generation (seconds)'),('simulation_seconds','SIM (seconds)'),
        ('selection_seconds','Selection (seconds)'),('total_seconds','Total (seconds)'))]
    compute += ['','Preparation stages']
    compute += [f"{r.get('stage','unknown')}: {r.get('state','unknown')} — {r.get('detail','')}" for r in record.get('build_preparation') or []] or ['Not recorded.']
    library=record.get('candidate_library') or {}
    compute += ['','Candidate library']
    compute += ([f"{label}: {value(library,key)}" for key,label in (
        ('type','Library kind'),('saved','Saved candidates / rosters'),('accepted','Accepted'),('rejected','Rejected'),
        ('screening_reused','Screened candidates reused'),('preparation_complete','Preparation coverage complete'),
        ('scan_complete','Current scan complete'))]
                if library else ['No candidate-library receipt recorded.'])
    compute += ['','Screening / SIM evidence']
    compute += [f"{label}: {value(sim,key)}" for key,label in (
        ('screening_scenarios','Screening scenarios'),('validation_scenarios','Independent validation scenarios'),
        ('scenario_count','Portfolio scenarios'),('field_lineups','Opponent field lineups'))]
    compute += ['Full report retains detailed audits and scenario-cache evidence.']
    comparisons=['Historical comparisons recorded with this selected build. Current UI comparisons are not attached automatically.']
    comparisons += format_field(record.get('field_diagnostic') or {})
    if record.get('history_use_unscored'): comparisons += [str(record['history_use_unscored'].get('reason','History requested without a scored receipt.'))]
    if not (record.get('field_diagnostic') or {}).get('available'):
        comparisons.append('No sampled-field comparison receipt recorded. Missing history is not a zero or a claim that history was used.')
    comparisons += ['','Select exactly two completed build rows and choose Compare Two Builds for the existing timing/settings/SIM comparison.']
    return dict(overview='\n'.join(overview),lineups='\n'.join(lineups),limits='\n'.join(limits),
                compute='\n'.join(compute),comparisons='\n'.join(comparisons))


def install_review(dialog, splitter):
    dialog.setWindowTitle('Review build'); dialog.resize(1180,780)
    splitter.setHandleWidth(8)
    dialog.review_tabs=QtWidgets.QTabWidget()
    dialog.review_tabs.setObjectName('buildReviewTabs')
    dialog.review_panels={}
    for key,title in (('overview','Overview'),('lineups','Lineups and exposures'),('limits','Limits and adjustments'),
                      ('compute','Preparation and SIM'),('comparisons','Comparisons')):
        text=QtWidgets.QPlainTextEdit(); text.setReadOnly(True);text.setObjectName('buildReview_'+key)
        dialog.review_panels[key]=text;dialog.review_tabs.addTab(text,title)
    splitter.replaceWidget(1,dialog.review_tabs)
    dialog.review_tabs.show()
    splitter.setChildrenCollapsible(False)
    dialog.review_tabs.addTab(dialog.report,'Full report');dialog.report.show()


def refresh_review(dialog,record):
    # Detached views stay stable if another build completes while review is open.
    views=review_views(deepcopy(record))
    for key,text in views.items():dialog.review_panels[key].setPlainText(text)
    if hasattr(dialog,'export_button'):dialog.export_button.setEnabled(bool(record))


def export_review(dialog):
    text=dialog.report.toPlainText().strip()
    if not dialog.selected_record() or not text:return
    path,_=QtWidgets.QFileDialog.getSaveFileName(dialog,'Export displayed build report','','Text report (*.txt)')
    if not path:return
    target=QtCore.QSaveFile(path)
    if not target.open(QtCore.QIODevice.WriteOnly):
        dialog.note.setText('Report export failed: '+target.errorString());return
    data=text.encode('utf-8')
    if target.write(data)!=len(data) or not target.commit():
        target.cancelWriting();dialog.note.setText('Report export failed: '+target.errorString());return
    dialog.note.setText('Displayed build report exported. No build or portfolio settings changed.')
