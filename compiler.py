"""
Compiler — walks the AST and produces one flat bytecode stream + one flat
memory array.

For each `set x to <literal>`:
  1. Detect the literal's type (int → I64, float → F64, string → TEXT, etc.)
  2. Put the literal value into memory at the next free address.
     (This slot acts as a "constant" — memory pre-filled by the compiler.)
  3. If the variable `x` is new, pick another memory address for it
     (uninitialized — just a slot). Otherwise reuse its existing address.
  4. Emit:  LOAD  r0, <const_addr>    ; r0 = value
            STORE r0, <var_addr>      ; variable = r0

For each `print <expr>`:
  1. Compute the expression's address (constant or variable, same thing).
  2. Emit:  LOAD  r0, <addr>
            PRINT r0
"""

from bytecode import Instruction, Module, Opcode, TypeCode
from parser import (
    BoolLit, NoneLit, NumberLit, PrintStmt, SetStmt, Stmt, StringLit,
    VarLValue, VarRef,
)


class CompileError(Exception):
    pass


class Compiler:
    def __init__(self) -> None:
        self.module: Module = Module()

    # ----- entry point -----

    def compile_program(self, stmts: list[Stmt]) -> Module:
        self.module.entry = 0
        for stmt in stmts:
            self.compile_stmt(stmt)
        self.emit(Opcode.HALT, ())
        return self.module

    # ----- statements -----

    def compile_stmt(self, stmt: Stmt) -> None:
        if isinstance(stmt, SetStmt):
            return self.compile_set(stmt)
        if isinstance(stmt, PrintStmt):
            return self.compile_print(stmt)
        raise CompileError(f"unsupported statement: {type(stmt).__name__}")

    def compile_set(self, stmt: SetStmt) -> None:
        if not isinstance(stmt.target, VarLValue):
            raise CompileError("only simple variable targets supported")
        name = stmt.target.name

        # Compile the value — put it into register 0, learn its type.
        value_type = self.compile_expr_into(stmt.value, reg=0)

        # Resolve the variable's memory address (allocate if new).
        if name in self.module.symbol_table:
            existing_ty = self.module.symbol_types[name]
            if existing_ty != value_type:
                raise CompileError(
                    f"cannot change type of {name!r} from "
                    f"{existing_ty.name} to {value_type.name}"
                )
            addr = self.module.symbol_table[name]
        else:
            addr = self.allocate_variable(name, value_type)

        self.emit(Opcode.STORE, (0, addr))

    def compile_print(self, stmt: PrintStmt) -> None:
        for part in stmt.parts:
            self.compile_expr_into(part, reg=0)
            self.emit(Opcode.PRINT, (0,))

    # ----- expressions -----
    #
    # compile_expr_into(expr, reg) emits code so that `reg` holds the
    # expression's value after execution. Returns the expression's type.

    def compile_expr_into(self, expr, reg: int) -> TypeCode:
        if isinstance(expr, NumberLit):
            ty = TypeCode.I64 if isinstance(expr.value, int) else TypeCode.F64
            addr = self.allocate_constant(expr.value)
            self.emit(Opcode.LOAD, (reg, addr))
            return ty

        if isinstance(expr, StringLit):
            addr = self.allocate_constant(expr.value)
            self.emit(Opcode.LOAD, (reg, addr))
            return TypeCode.TEXT

        if isinstance(expr, BoolLit):
            addr = self.allocate_constant(expr.value)
            self.emit(Opcode.LOAD, (reg, addr))
            return TypeCode.BOOL

        if isinstance(expr, NoneLit):
            addr = self.allocate_constant(None)
            self.emit(Opcode.LOAD, (reg, addr))
            return TypeCode.NONE

        if isinstance(expr, VarRef):
            if expr.name not in self.module.symbol_table:
                raise CompileError(f"undeclared variable {expr.name!r}")
            addr = self.module.symbol_table[expr.name]
            ty = self.module.symbol_types[expr.name]
            self.emit(Opcode.LOAD, (reg, addr))
            return ty

        raise CompileError(f"unsupported expression: {type(expr).__name__}")

    # ----- memory layout -----
    #
    # Both constants and variables are just slots in `initial_memory`.
    # The difference is whether we pre-fill the slot or leave it empty.

    def allocate_constant(self, value) -> int:
        addr = len(self.module.initial_memory)
        self.module.initial_memory.append(value)
        return addr

    def allocate_variable(self, name: str, ty: TypeCode) -> int:
        addr = len(self.module.initial_memory)
        self.module.initial_memory.append(None)   # uninitialized
        self.module.symbol_table[name] = addr
        self.module.symbol_types[name] = ty
        return addr

    # ----- emission -----

    def emit(self, op: Opcode, operands: tuple) -> None:
        self.module.code.append(Instruction(op, operands))


def compile_program(stmts: list[Stmt]) -> Module:
    return Compiler().compile_program(stmts)
