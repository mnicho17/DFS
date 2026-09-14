"""Run portfolio CBC with a parent-enforced deadline and cancellation."""
import os
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


def solve(problem, deadline, cancelled):
    import pulp
    check(deadline, cancelled)
    solver = pulp.PULP_CBC_CMD(msg=False)
    with tempfile.TemporaryDirectory(prefix='dfs-feasibility-') as folder:
        model = str(Path(folder)/'portfolio.mps')
        solution = str(Path(folder)/'portfolio.sol')
        variables, names, constraints, _ = problem.writeMPS(model, rename=1)
        check(deadline, cancelled)
        args = [solver.path, model]
        if problem.sense == pulp.LpMaximize:
            args.append('-max')
        args += ['-sec', str(max(.01, deadline-time.perf_counter())), '-threads', '1',
                 '-timeMode', 'elapsed', '-solve', '-printingOptions', 'all', '-solution', solution]
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        with subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, **options) as process:
            if wait_for_process(process, deadline, cancelled) != 0:
                raise pulp.PulpSolverError('Portfolio solver exited unsuccessfully')
        check(deadline, cancelled)
        if not Path(solution).exists():
            return False
        status, values, _, _, _, solution_status = solver.readsol_MPS(
            solution, problem, variables, names, constraints)
        check(deadline, cancelled)
        problem.assignVarsVals(values)
        problem.assignStatus(status, solution_status)
        return True
