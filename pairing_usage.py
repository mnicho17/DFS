"""Shared descriptive pairing metrics; never generation inputs."""
KEYS=('receiver_own_qb_pct','kd_zero_pct','kd_one_pct','kd_two_pct','kd_three_plus_pct')
LABELS=dict(zip(KEYS,('WR/TE Captain with own QB (%)','Zero K/DST (%)','One K/DST (%)','Two K/DST (%)','Three or more K/DST (%)')))


def counts(slots):
    from receiver_specialist_review import features
    from winner_pairing_review import counts as decode
    return decode(features(slots))


def metrics(values):
    from winner_pairing_review import rates
    return dict(zip(KEYS,rates(values)))
