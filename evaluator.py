from parser import (
    AddStmt, BinaryOp, BoolLit, Compare, DivideStmt, Expr, IfStmt, MultiplyStmt,
    NoneLit, NumberLit, PrintStmt, RepeatForEachStmt, RepeatRangeStmt,
    RepeatTimesStmt, RepeatWhileStmt, SetStmt, SkipStmt, Stmt, StopStmt,
    StringLit, SubtractStmt, VarRef,
)


Env = dict[str, object]


class RunError(Exception):
    pass


class _BreakSignal(Exception):
    pass


class _ContinueSignal(Exception):
    pass


def execute_program(stmts: list[Stmt]) -> None:
    env: Env = {}
    try:
        _execute_block(stmts, env)
    except _BreakSignal:
        raise RunError("'stop' used outside of a loop")
    except _ContinueSignal:
        raise RunError("'skip' used outside of a loop")


def _execute_block(stmts: list[Stmt], env: Env) -> None:
    for stmt in stmts:
        execute(stmt, env)


def execute(stmt: Stmt, env: Env) -> None:
    if isinstance(stmt, SetStmt):
        env[stmt.target] = evaluate(stmt.value, env)
        return

    if isinstance(stmt, AddStmt):
        env[stmt.target] = _get(env, stmt.target) + evaluate(stmt.amount, env)
        return

    if isinstance(stmt, SubtractStmt):
        env[stmt.target] = _get(env, stmt.target) - evaluate(stmt.amount, env)
        return

    if isinstance(stmt, MultiplyStmt):
        env[stmt.target] = _get(env, stmt.target) * evaluate(stmt.factor, env)
        return

    if isinstance(stmt, DivideStmt):
        env[stmt.target] = _get(env, stmt.target) / evaluate(stmt.divisor, env)
        return

    if isinstance(stmt, PrintStmt):
        values = [evaluate(e, env) for e in stmt.parts]
        print(*values)
        return

    if isinstance(stmt, IfStmt):
        if evaluate(stmt.condition, env):
            _execute_block(stmt.then_block, env)
        elif stmt.else_block is not None:
            _execute_block(stmt.else_block, env)
        return

    if isinstance(stmt, RepeatTimesStmt):
        count = int(evaluate(stmt.count, env))
        for _ in range(count):
            try:
                _execute_block(stmt.body, env)
            except _ContinueSignal:
                continue
            except _BreakSignal:
                break
        return

    if isinstance(stmt, RepeatForEachStmt):
        iterable = evaluate(stmt.iterable, env)
        try:
            items = iter(iterable)  # type: ignore[arg-type]
        except TypeError:
            raise RunError(f"cannot iterate over value of type {type(iterable).__name__}")
        for item in items:
            env[stmt.var] = item
            try:
                _execute_block(stmt.body, env)
            except _ContinueSignal:
                continue
            except _BreakSignal:
                break
        return

    if isinstance(stmt, RepeatRangeStmt):
        start = int(evaluate(stmt.start, env))
        end = int(evaluate(stmt.end, env))
        for i in range(start, end + 1):
            env[stmt.var] = i
            try:
                _execute_block(stmt.body, env)
            except _ContinueSignal:
                continue
            except _BreakSignal:
                break
        return

    if isinstance(stmt, RepeatWhileStmt):
        while evaluate(stmt.condition, env):
            try:
                _execute_block(stmt.body, env)
            except _ContinueSignal:
                continue
            except _BreakSignal:
                break
        return

    if isinstance(stmt, StopStmt):
        raise _BreakSignal()

    if isinstance(stmt, SkipStmt):
        raise _ContinueSignal()

    raise RunError(f"unknown statement: {stmt!r}")


def evaluate(expr: Expr, env: Env) -> object:
    if isinstance(expr, NumberLit):
        return expr.value
    if isinstance(expr, StringLit):
        return expr.value
    if isinstance(expr, BoolLit):
        return expr.value
    if isinstance(expr, NoneLit):
        return None
    if isinstance(expr, VarRef):
        return _get(env, expr.name)
    if isinstance(expr, BinaryOp):
        left = evaluate(expr.left, env)
        right = evaluate(expr.right, env)
        if expr.op == "plus":    return left + right
        if expr.op == "minus":   return left - right
        if expr.op == "times":   return left * right
        if expr.op == "divided": return left / right
        raise RunError(f"unknown operator {expr.op!r}")
    if isinstance(expr, Compare):
        left = evaluate(expr.left, env)
        right = evaluate(expr.right, env)
        if expr.op == "equal":     return left == right
        if expr.op == "not_equal": return left != right
        if expr.op == "greater":   return left > right
        if expr.op == "less":      return left < right
        if expr.op == "at_least":  return left >= right
        if expr.op == "at_most":   return left <= right
        raise RunError(f"unknown comparison {expr.op!r}")
    raise RunError(f"unknown expression: {expr!r}")


def _get(env: Env, name: str) -> object:
    if name not in env:
        raise RunError(f"undefined variable {name!r}")
    return env[name]
