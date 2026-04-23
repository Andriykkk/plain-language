from parser import (
    AddStmt, BinaryOp, BoolLit, DivideStmt, Expr, MultiplyStmt, NoneLit,
    NumberLit, PrintStmt, SetStmt, Stmt, StringLit, SubtractStmt, VarRef,
)


Env = dict[str, object]


class RunError(Exception):
    pass


def execute_program(stmts: list[Stmt]) -> None:
    env: Env = {}
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
    raise RunError(f"unknown expression: {expr!r}")


def _get(env: Env, name: str) -> object:
    if name not in env:
        raise RunError(f"undefined variable {name!r}")
    return env[name]
