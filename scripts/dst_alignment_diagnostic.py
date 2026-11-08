"""Research-only fixed-alignment controls; never generates or changes model draws."""
import hashlib
import math
import statistics

VERSION = 'dst-alignment-diagnostic-v1'
MAX_DRAWS = 6000
EVENTS = ('low_qb_high_dst', 'high_qb_high_dst', 'high_qb_low_dst')
THRESHOLDS = dict(low_qb=10., high_qb=20., low_dst=4., high_dst=8.)


def _check(cancelled):
    if cancelled():raise InterruptedError('DST alignment diagnostic cancelled; no partial result.')


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Finite numeric diagnostic observations required.')
    return float(value)


def block_indices(seeds, block_size):
    """One outcome-independent nonzero cyclic shift, shared by all DSTs per seed."""
    if (not isinstance(block_size, int) or isinstance(block_size, bool) or block_size < 2
        or not 1 <= len(seeds) <= 3 or len(seeds) * block_size > MAX_DRAWS):
        raise ValueError('Use 1–3 seed blocks, at least two draws each, at most 6000 draws.')
    if any(isinstance(s, bool) or not isinstance(s, int) or not 0 <= s < 2**63 for s in seeds) or len(set(seeds)) != len(seeds):
        raise ValueError('Unique nonnegative integer seeds required.')
    indices, offsets = [], []
    for block, seed in enumerate(seeds):
        digest = hashlib.sha256(f'{VERSION}:{seed}'.encode('ascii')).digest()
        offset = 1 + int.from_bytes(digest[:8], 'big') % (block_size - 1)
        start = block * block_size
        indices.extend(start + (i + offset) % block_size for i in range(block_size))
        offsets.append(offset)
    return indices, offsets


def _events(qb, dst):
    return (qb <= THRESHOLDS['low_qb'] and dst >= THRESHOLDS['high_dst'],
            qb >= THRESHOLDS['high_qb'] and dst >= THRESHOLDS['high_dst'],
            qb >= THRESHOLDS['high_qb'] and dst <= THRESHOLDS['low_dst'])


def _marginal(values, actual):
    s = sorted(values);n = len(s);mean = statistics.mean(s)
    return dict(mean=mean, sd=statistics.pstdev(s), bias=mean-actual,
        crps=sum(abs(v-actual) for v in s)/n-sum((2*i-n-1)*v for i,v in enumerate(s,1))/(n*n),
        p10=s[int((n-1)*.1)], p90=s[int((n-1)*.9)])


def _joint(qb, dst, actual_qb, actual_dst, cancelled):
    counts = [0, 0, 0]
    for i, (q, d) in enumerate(zip(qb, dst)):
        if i % 128 == 0:_check(cancelled)
        for index, event in enumerate(_events(q, d)):counts[index] += event
    observed = _events(actual_qb, actual_dst)
    mq, md = statistics.mean(qb), statistics.mean(dst)
    events = {name:dict(probability=counts[i]/len(qb), observed=bool(observed[i]),
                       brier=(counts[i]/len(qb)-float(observed[i]))**2) for i,name in enumerate(EVENTS)}
    return dict(correlation=statistics.correlation(qb,dst) if statistics.pstdev(qb)>0 and statistics.pstdev(dst)>0 else None,
        covariance=statistics.mean((q-mq)*(d-md) for q,d in zip(qb,dst)), events=events,
        mean_event_brier=statistics.mean(event['brier'] for event in events.values()))


def compare_alignment(qb, dst, actual_qb, actual_dst, *, seeds, block_size, cancelled=lambda:False):
    """Compare original alignment to a fixed within-seed shift, with exact marginals.

    This is a finite alignment control, not a claim of independent draws or a
    new production coupling. Threshold Briers assess only the declared events.
    """
    _check(cancelled)
    indices, offsets = block_indices(seeds, block_size)
    if len(qb) != len(indices) or len(dst) != len(indices):
        raise ValueError('Complete equally sized seed blocks required; no partial diagnostic.')
    actual_qb, actual_dst = _number(actual_qb), _number(actual_dst)
    q, d = [], []
    for i, (x, y) in enumerate(zip(qb, dst)):
        if i % 128 == 0:_check(cancelled)
        q.append(_number(x));d.append(_number(y))
    shifted = [d[i] for i in indices]
    before, after = _marginal(d,actual_dst), _marginal(shifted,actual_dst)
    if before != after or sorted(d) != sorted(shifted):
        raise ValueError('DST marginal changed; diagnostic withheld.')
    per_seed = []
    for index, seed in enumerate(seeds):
        _check(cancelled)
        start=index*block_size;end=start+block_size
        if sorted(d[start:end]) != sorted(shifted[start:end]):
            raise ValueError('Seed-specific marginal changed; diagnostic withheld.')
        per_seed.append(dict(seed=seed, offset=offsets[index],
            original=_joint(q[start:end],d[start:end],actual_qb,actual_dst,cancelled),
            shifted=_joint(q[start:end],shifted[start:end],actual_qb,actual_dst,cancelled)))
    result=dict(version=VERSION,draws=len(q),block_size=block_size,seeds=list(seeds),offsets=offsets,
        marginal_exact=True,qb_marginal=_marginal(q,actual_qb),dst_marginal=before,
        original=_joint(q,d,actual_qb,actual_dst,cancelled),
        shifted=_joint(q,shifted,actual_qb,actual_dst,cancelled),per_seed=per_seed)
    _check(cancelled)
    return result
