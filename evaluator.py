from collections import ChainMap
from dataclasses import dataclass
from typing import Any

from parser import (
    AddStmt, BinaryOp, BoolLit, CallExpr, CallStmt, Compare, DivideStmt, Expr,
    FunctionDef, IfStmt, MultiplyStmt, NoneLit, NumberLit, PrintStmt,
    RepeatForEachStmt, RepeatRangeStmt, RepeatTimesStmt, RepeatWhileStmt,
    ReturnStmt, SetStmt, SkipStmt, Stmt, StopStmt, StringLit, SubtractStmt,
    TypeRef, VarRef,
)


Env = ChainMap  # maps the innermost scope to the outermost; writes go to innermost


class RunError(Exception):
    pass


@dataclass
class FunctionValue:
    name: str
    params: list[tuple[str, TypeRef]]
    return_type: TypeRef | None
    body: list[Stmt]


class _BreakSignal(Exception):
    pass


class _ContinueSignal(Exception):
    pass


class _ReturnSignal(Exception):
    def __init__(self, value: Any) -> None:
        self.value = value


def execute_program(stmts: list[Stmt]) -> None:
    env: Env = ChainMap()
    try:
        _execute_block(stmts, env)
    except _BreakSignal:
        raise RunError("'stop' used outside of a loop")
    except _ContinueSignal:
        raise RunError("'skip' used outside of a loop")
    except _ReturnSignal:
        raise RunError("'return' used outside of a function")


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

    if isinstance(stmt, FunctionDef):
        env[stmt.name] = FunctionValue(stmt.name, stmt.params, stmt.return_type, stmt.body)
        return

    if isinstance(stmt, ReturnStmt):
        value = evaluate(stmt.value, env) if stmt.value is not None else None
        raise _ReturnSignal(value)

    if isinstance(stmt, CallStmt):
        _call_function(stmt.call, env)
        return

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
    if isinstance(expr, CallExpr):
        return _call_function(expr, env)
    raise RunError(f"unknown expression: {expr!r}")


def _call_function(call: CallExpr, env: Env) -> object:
    try:
        fn = env[call.name]
    except KeyError:
        raise RunError(f"undefined function {call.name!r}")
    if not isinstance(fn, FunctionValue):
        raise RunError(f"{call.name!r} is not a function")
    if len(call.args) != len(fn.params):
        raise RunError(
            f"function {fn.name!r} expects {len(fn.params)} argument(s), got {len(call.args)}"
        )
    arg_values = [evaluate(a, env) for a in call.args]

    # Fresh local scope chained to the top-level scope (not the caller's locals),
    # so functions don't accidentally see each other's local variables.
    top = env.maps[-1]
    call_env: Env = ChainMap({}, top)
    for (pname, _ptype), v in zip(fn.params, arg_values):
        call_env[pname] = v

    try:
        _execute_block(fn.body, call_env)
    except _ReturnSignal as sig:
        return sig.value
    return None


def _get(env: Env, name: str) -> object:
    if name not in env:
        raise RunError(f"undefined variable {name!r}")
    return env[name]
