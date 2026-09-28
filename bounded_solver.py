"""Run portfolio CBC with a parent-enforced deadline and cancellation."""
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path


def check(deadline, cancelled):
    if cancelled() or time.perf_counter() >= deadline:
        raise TimeoutError('Portfolio feasibility time budget ended or was cancelled')


def wait_for_process(process, deadline, cancelled):
    try:
        while process.poll() is None:
            check(deadline, cancelled)
            try:
                process.wait(timeout=min(.1, max(.001, deadline-time.perf_counter())))
            except subprocess.TimeoutExpired:
                pass
        check(deadline, cancelled)
        return process.returncode
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def solve(problem, deadline, cancelled, *, strict=False):
    """Default feasibility API unchanged; strict metadata is opt-in for diagnostics."""
    import pulp
    check(deadline, cancelled)
    solver = pulp.PULP_CBC_CMD(msg=False)
    with tempfile.TemporaryDirectory(prefix='dfs-feasibility-') as folder:
        model = str(Path(folder)/'portfolio.mps')
        solution = str(Path(folder)/'portfolio.sol')
        log_path = Path(folder)/'solver.log'
        variables, names, constraints, _ = problem.writeMPS(model, rename=1)
        check(deadline, cancelled)
        args = [solver.path, model]
        if problem.sense == pulp.LpMaximize:
            args.append('-max')
        if strict:
            args += ['-ratioGap','0','-allowableGap','0','-randomSeed','1','-randomCbcSeed','1']
        args += ['-sec', str(max(.01, deadline-time.perf_counter())), '-threads', '1',
                 '-timeMode', 'elapsed', '-solve', '-printingOptions', 'all', '-solution', solution]
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        from contextlib import ExitStack
        with ExitStack() as stack:
            output = stack.enter_context(log_path.open('wb')) if strict else subprocess.DEVNULL
            with subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=output,
                                  stderr=output, **options) as process:
                if wait_for_process(process, deadline, cancelled) != 0:
                    raise pulp.PulpSolverError('Portfolio solver exited unsuccessfully')
        check(deadline, cancelled)
        if not Path(solution).exists():
            return dict(processed=False,termination='missing_solution') if strict else False
        if strict and (Path(solution).stat().st_size>4_000_000 or log_path.stat().st_size>4_000_000):
            raise pulp.PulpSolverError('Diagnostic solver output limit exceeded')
        status, values, _, _, _, solution_status = solver.readsol_MPS(
            solution, problem, variables, names, constraints)
        check(deadline, cancelled)
        problem.assignVarsVals(values)
        problem.assignStatus(status, solution_status)
        if not strict:
            return True
        if Path(solution).stat().st_size>4_000_000 or log_path.stat().st_size>4_000_000:
            raise pulp.PulpSolverError('Diagnostic solver output limit exceeded')
        text = Path(solution).read_text(encoding='utf-8')
        log = log_path.read_text(encoding='utf-8',errors='replace')
        check(deadline,cancelled)
        header = text.splitlines()[0]
        optimal = bool(re.fullmatch(r'Optimal - objective value [-+\d.eE]+',header.strip()) and
                       'Result - Optimal solution found' in log and 'within gap tolerance' not in log.lower())
        infeasible = header.startswith(('Infeasible - ','Integer infeasible - ')) and (
            'Problem is infeasible' in log or 'Problem proven infeasible' in log or
            'Result - Linear relaxation infeasible' in log)
        termination = ('optimal' if optimal else 'infeasible' if infeasible else
                       'time_limit' if 'Stopped' in header or 'Stopped on time' in log else 'unverified')
        present = set(); expected_names=set(names.values())
        for line in text.splitlines()[1:]:
            parts=line.split()
            if parts and parts[0]=='**':parts=parts[1:]
            if len(parts)>=4 and parts[1] in expected_names:
                if parts[1] in present:
                    raise pulp.PulpSolverError('Duplicate diagnostic variable receipt')
                present.add(parts[1])
        version=re.search(r'Version:\s*([^\r\n]+)',log)
        objective=re.search(r'objective value ([-+\d.eE]+)$',header)
        return dict(processed=True,model_status=status,solution_status=solution_status,
            termination=termination,header=header,objective=objective[1] if objective else None,
            complete_variables=present==set(names.values()),zero_gap_options=True,exit_code=0,
            solver_version=version[1].strip() if version else None,pulp_version=pulp.__version__)
