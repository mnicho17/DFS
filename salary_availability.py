"""Presentation-only salary availability; never qualifies or rebuilds evidence."""


def counts(result):
    total=result.get('unpaired',0)
    unavailable=result.get('salary_unavailable')
    review=result.get('salary_review_needed')
    if all(type(value) is int and value>=0 for value in (total,unavailable,review)) and unavailable+review==total:
        return unavailable,review
    return None


def review_count(result):
    split=counts(result)
    # Older or inconsistent receipts remain reviewable until a fresh import.
    return split[1] if split is not None else result.get('unpaired',0)


def summary(result):
    split=counts(result)
    if split is None:
        total=result.get('unpaired',0)
        return (f"{total} result files still need a salary match; one salary revision can serve multiple contests."
                if total else '')
    unavailable,review=split
    lines=[]
    if unavailable:
        noun='result file' if unavailable==1 else 'result files'
        lines.append(f"Salary data not available for {unavailable:,} {noun}. Results retained; salary-dependent analysis remains unavailable.")
    if review:
        noun='result file has' if review==1 else 'result files have'
        lines.append(f"{review:,} {noun} salary matches or recorded associations to review. Use Review Salary Matches.")
    return '\n'.join(lines)


def label(row):
    status=row['status']
    if status=='NO_COMPATIBLE_MATCH':
        if row.get('source_problem'):return 'Source needs review'
        if row.get('unreadable') or not row.get('format'):return 'Result data needs review'
        if any(candidate.get('date_correctable') for candidate in row.get('candidates',[])):
            return 'Date correction needs review'
        return 'Salary data not available'
    if status=='NEEDS_REVIEW_ASSOCIATION':return 'Needs review (manual choice recorded)'
    return status
