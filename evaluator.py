from collections import ChainMap
from dataclasses import dataclass
from typing import Any

from parser import (
    AddStmt, AppendStmt, BinaryOp, BoolLit, CallExpr, CallStmt, Compare,
    DivideStmt, EmptyList, EmptyMap, Expr, FieldAccess, FieldLValue, FunctionDef,
    IfStmt, IndexAccess, IndexLValue, LengthExpr, LValue, MultiplyStmt, NewExpr,
    NoneLit, NumberLit, PrintStmt, RecordDef, RepeatForEachStmt, RepeatRangeStmt,
    RepeatTimesStmt, RepeatWhileStmt, ReturnStmt, SetStmt, SkipStmt, Stmt,
    StopStmt, StringLit, SubtractStmt, TypeRef, VarLValue, VarRef,
)


Env = ChainMap


class RunError(Exception):
    pass


@dataclass
class FunctionValue:
    name: str
    params: list[tuple[str, TypeRef]]
    return_type: TypeRef | None
    body: list[Stmt]


@dataclass
class RecordType:
    name: str
    fields: list[tuple[str, TypeRef]]


@dataclass(repr=False)
class RecordValue:
    type_name: str
    fields: dict[str, Any]

    def __repr__(self) -> str:
        parts = [f"{k}={v!r}" for k, v in self.fields.items()]
        return f"{self.type_name}({', '.join(parts)})"


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
        _assign(stmt.target, evaluate(stmt.value, env), env)
        return

    if isinstance(stmt, AddStmt):
        old = _lvalue_get(stmt.target, env)
        _assign(stmt.target, old + evaluate(stmt.amount, env), env)
        return

    if isinstance(stmt, SubtractStmt):
        old = _lvalue_get(stmt.target, env)
        _assign(stmt.target, old - evaluate(stmt.amount, env), env)
        return

    if isinstance(stmt, MultiplyStmt):
        old = _lvalue_get(stmt.target, env)
        _assign(stmt.target, old * evaluate(stmt.factor, env), env)
        return

    if isinstance(stmt, DivideStmt):
        old = _lvalue_get(stmt.target, env)
        _assign(stmt.target, old / evaluate(stmt.divisor, env), env)
        return

    if isinstance(stmt, AppendStmt):
        target = _lvalue_get(stmt.target, env)
        if not isinstance(target, list):
            raise RunError(
                f"cannot append to non-list value of type {type(target).__name__}"
            )
        target.append(evaluate(stmt.value, env))
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

    if isinstance(stmt, RecordDef):
        env[stmt.name] = RecordType(stmt.name, stmt.fields)
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

    if isinstance(expr, FieldAccess):
        obj = evaluate(expr.obj, env)
        if not isinstance(obj, RecordValue):
            raise RunError(
                f"cannot access field {expr.field!r} on value of type {type(obj).__name__}"
            )
        if expr.field not in obj.fields:
            raise RunError(f"record {obj.type_name!r} has no field {expr.field!r}")
        return obj.fields[expr.field]

    if isinstance(expr, IndexAccess):
        obj = evaluate(expr.obj, env)
        index = evaluate(expr.index, env)
        return _index_read(obj, index)

    if isinstance(expr, NewExpr):
        rt = env.get(expr.type_name)
        if rt is None:
            raise RunError(f"undefined record type {expr.type_name!r}")
        if not isinstance(rt, RecordType):
            raise RunError(f"{expr.type_name!r} is not a record type")
        return RecordValue(rt.name, {fname: _default_for(ftype) for fname, ftype in rt.fields})

    if isinstance(expr, EmptyList):
        return []

    if isinstance(expr, EmptyMap):
        return {}

    if isinstance(expr, LengthExpr):
        value = evaluate(expr.value, env)
        if isinstance(value, (list, dict, str)):
            return len(value)
        raise RunError(f"cannot take length of value of type {type(value).__name__}")

    raise RunError(f"unknown expression: {expr!r}")


def _index_read(obj: Any, index: Any) -> Any:
    if isinstance(obj, list):
        i = int(index) - 1
        if i < 0 or i >= len(obj):
            raise RunError(f"list index {index} out of range (length {len(obj)})")
        return obj[i]
    if isinstance(obj, str):
        i = int(index) - 1
        if i < 0 or i >= len(obj):
            raise RunError(f"string index {index} out of range (length {len(obj)})")
        return obj[i]
    if isinstance(obj, dict):
        if index not in obj:
            raise RunError(f"map has no key {index!r}")
        return obj[index]
    raise RunError(f"cannot index value of type {type(obj).__name__}")


def _assign(lv: LValue, value: Any, env: Env) -> None:
    if isinstance(lv, VarLValue):
        env[lv.name] = value
        return
    if isinstance(lv, FieldLValue):
        obj = evaluate(lv.obj, env)
        if not isinstance(obj, RecordValue):
            raise RunError(
                f"cannot set field {lv.field!r} on value of type {type(obj).__name__}"
            )
        if lv.field not in obj.fields:
            raise RunError(f"record {obj.type_name!r} has no field {lv.field!r}")
        obj.fields[lv.field] = value
        return
    if isinstance(lv, IndexLValue):
        obj = evaluate(lv.obj, env)
        index = evaluate(lv.index, env)
        if isinstance(obj, list):
            i = int(index) - 1
            if i < 0 or i >= len(obj):
                raise RunError(f"list index {index} out of range (length {len(obj)})")
            obj[i] = value
            return
        if isinstance(obj, dict):
            obj[index] = value
            return
        raise RunError(f"cannot index-assign value of type {type(obj).__name__}")
    raise RunError(f"unknown lvalue: {lv!r}")


def _lvalue_get(lv: LValue, env: Env) -> Any:
    if isinstance(lv, VarLValue):
        return _get(env, lv.name)
    if isinstance(lv, FieldLValue):
        return evaluate(FieldAccess(lv.obj, lv.field), env)
    if isinstance(lv, IndexLValue):
        return evaluate(IndexAccess(lv.obj, lv.index), env)
    raise RunError(f"unknown lvalue: {lv!r}")


def _default_for(type_ref: TypeRef) -> Any:
    name = type_ref.name
    if name == "number":
        return 0
    if name == "text":
        return ""
    if name == "list":
        return []
    if name == "map":
        return {}
    return None


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
