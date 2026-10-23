"""Notification guidance only; never retries work or changes recorded outcomes."""
ATTENTION_STATES = frozenset(('Failed', 'Needs review', 'Incomplete', 'Paused / partial'))


def needs_attention(row):
    return bool(row.get('ended') is not None and row.get('state') in ATTENTION_STATES
                and not row.get('attention_reviewed'))


def result_reason(result):
    notes = []
    for key in ('error', 'pause_reason'):
        if result.get(key):notes.append(str(result[key]))
    errors = result.get('errors') or []
    if isinstance(errors, str):errors = [errors]
    notes.extend(str(error) for error in errors[:5])
    history = result.get('username_history') or {}
    for error in history.get('errors', [])[:5]:
        notes.append(str(error.get('name', 'History')) + ': ' + str(error.get('error', 'Needs review'))
                     if isinstance(error, dict) else str(error))
    if result.get('unpaired'):
        notes.append(f"{result['unpaired']} result files still need a salary match; one salary revision can serve multiple contests.")
    if result.get('changed_sources'):
        notes.append('Changed source revisions need review; existing saved associations were preserved.')
    if 'requested' in result and len(result.get('lineups') or []) < result['requested']:
        notes.append(f"Only {len(result.get('lineups') or []):,}/{result['requested']:,} requested lineups were assembled.")
    return '\n'.join(notes)


def guidance(row):
    if row.get('state') not in ATTENTION_STATES:
        return dict(reason='', next_step='', target=None)
    title = row.get('title', '').lower()
    target = ('backup' if 'backup' in title else
              'history' if any(word in title for word in ('history', 'results', 'learning')) else
              'readiness' if any(word in title for word in ('lineups', 'generation', 'ownership', 'build preparation')) else
              'preparation' if any(word in title for word in ('preparation', 'screening', 'library')) else None)
    original = str(row.get('attention_reason') or row.get('detail') or 'The recorded attempt needs review.')
    reason = '\n'.join(original.splitlines()[:3])[:240]
    if reason != original:reason += '… (full message below)'
    if target == 'history':
        action = 'Open Results & Learning. Review Salary Matches for unpaired files, or History Overview for refresh errors and Retry/Resume. A Needs review association does not qualify conflicting evidence.'
    elif target == 'backup':
        action = 'Open Backups to inspect the destination and last error. Back up now retries through the existing worker checks; earlier complete backups remain available.'
    elif target == 'readiness':
        action = 'This may describe an earlier slate. Match its Build Report to the current inputs, then open Slate Readiness to review blocking limits or source errors before generating again.'
    elif target == 'preparation':
        action = 'Open Preparation and match the recorded task to its captured slate and pause reason. Retry / Resume rechecks coverage and queues missing work under the existing limits.'
    else:
        action = 'Read the original task messages below, then retry from the tool that started this operation.'
    return dict(reason=reason, next_step=action, target=target)
