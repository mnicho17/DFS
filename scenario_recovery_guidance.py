"""Pure guidance from recorded outcomes; never inspect, retire or qualify a cache."""
ACTION='Review cache storage'
GUIDANCE=('Open Review cache storage, then use Archive and free legacy cache space if the audit identifies legacy files. '
          'That explicit action verifies a recoverable copy in your configured backup folder before removing its working copy. '
          'Current caches, unknown files, research data and candidate libraries stay protected. '
          'After resolving storage, use Retry / Resume or Generate; exact-input and integrity checks still apply.')


def storage_blocked(cache):
    if not isinstance(cache,dict) or cache.get('status')!='not saved':return False
    reason=cache.get('reason')
    return isinstance(reason,str) and 'cache storage limit reached' in reason[:4096].casefold()


def warming_blocked(record):
    if not isinstance(record,dict) or record.get('cache_status')!='not saved':return False
    if record.get('cache_storage_blocked') is True:return True
    # Older receipts already retained this controlled outcome text.
    return storage_blocked(dict(status=record['cache_status'],reason=record.get('message')))


def cache_row(cache,label):
    if not isinstance(cache,dict) or not cache:return None
    blocked=storage_blocked(cache)
    status=cache.get('status') if isinstance(cache.get('status'),str) else 'not recorded'
    detail=f'{label}: cache {status}. This is a previous recorded outcome, not current-slate validation or a fresh storage check.'
    if blocked:detail+='\nRecorded storage limit prevented saving reusable scenarios. '+GUIDANCE
    else:detail+='\nGenerate rechecks exact inputs, complete frames and integrity before replay. No storage scan or cleanup is run by this summary.'
    return dict(state='Storage blocked (recorded)' if blocked else status.replace('_',' ').capitalize(),detail=detail,action=ACTION)


def warming_row(record,label='Last Smart preparation'):
    if not isinstance(record,dict) or not record:return None
    cache=dict(status=record.get('cache_status','not recorded'),reason=record.get('message',''))
    if warming_blocked(record):cache['reason']='Cache storage limit reached'
    return cache_row(cache,label)
