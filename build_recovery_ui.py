"""Captured search guidance and navigation; never changes or retries a build."""
from compute_settings import DEEP_CONTROLS


def guidance(context):
    if not isinstance(context, dict):
        return ''
    settings = context.get('settings') or {}
    if not isinstance(settings, dict):
        return ''
    sport = context.get('sport')
    mode = settings.get('compute_mode')
    if not sport or not mode:
        return 'Search settings were not recorded for this attempt. Review current search settings; they may differ from the failed build.'
    lines = ['Recovery options for the failed build (review only):']
    if sport != 'NFL':
        lines.append('Review the current Build settings and available player pool; NFL Deep budgets do not apply to this sport.')
    elif mode != 'Deep':
        lines.append('This used Fast search. Review search settings and choose an existing timed Deep profile if a broader search is wanted.')
    else:
        options = settings.get('deep_compute')
        if not isinstance(options, dict):
            lines.append('Deep search budgets were not recorded. Review current settings before choosing a broader search.')
        else:
            minutes = options.get('minutes')
            if type(minutes) is int:
                lines.append(f'Recorded Deep search cap: {minutes} minutes. Any increase requires your deliberate choice.')
            wider = []
            for key, label in (('candidates', 'Candidate ceiling'), ('shortlist', 'Validation shortlist')):
                value = options.get(key)
                maximum = DEEP_CONTROLS[key][3]
                if type(value) is not int or not 0 <= value <= maximum:
                    lines.append(label + ': not recorded.')
                elif value == maximum:
                    lines.append(f'{label}: {value:,}, already at the supported maximum.')
                else:
                    proposed = min(maximum, value * 2) if value else maximum
                    wider.append(f'{label}: {value:,} -> {proposed:,}' if value else f'{label}: automatic -> explicit ceiling {proposed:,}')
            lines.extend(wider)
            if wider:
                lines.append('These are bounded search options to review within the existing time cap, not applied changes or proof that enough candidates will be found.')
            elif all(type(options.get(k)) is int and options[k] == DEEP_CONTROLS[k][3] for k in ('candidates','shortlist')):
                lines.append('Both breadth controls are at their supported maximum. Review preparation and candidate coverage, or deliberately choose more search time; widening these controls again is unavailable.')
    if context.get('repair_source'):
        lines.append('This was a repair attempt. Keep the saved portfolio and use its Repair action again after any deliberate settings changes.')
    lines += ['Review search opens the current Build controls; Review limits opens current Portfolio Rules. Neither action changes settings or runs a build.',
              'Keep Captain locks, exposures and uniqueness unless you deliberately edit them. More SIM scenarios alone do not add lineup choices. A smaller entry request also recalculates percentage caps.',
              'No complete portfolio was found by this search; that is not proof of infeasibility.']
    return '\n'.join(lines)


def open_controls(window, action):
    if action not in ('search', 'limits') or not window._accept_build_results:
        return
    window.action_show_build_controls.setChecked(True)
    window.tabs_workspace_controls.setCurrentIndex(0 if action == 'search' else 1)
    target = window.combo_compute_profile if action == 'search' else window.spin_portfolio_unique
    target.setFocus()
    window.status.showMessage('Review current settings. No limits or budgets were changed; use Generate or saved Repair after deliberate edits.', 10000)
