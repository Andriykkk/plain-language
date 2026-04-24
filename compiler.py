"""
Compiler — walks the AST and produces one flat bytecode stream + one flat
memory array.

Types are tracked at compile time. Each expression compiles into a register
and returns its TypeCode. When a binary op sees mixed types, the compiler
emits explicit conversion opcodes to promote the smaller/narrower operand
to the common result type. Division of two integers produces F64.

`set x to <expr> as <type>` converts the value to the annotated type.
"""

from bytecode import Instruction, Module, Opcode, TypeCode
from parser import (
    AppendStmt, BinaryOp, BoolLit, ColumnsExpr, EmptyList, EmptyMatrix,
    IndexAccess, IndexLValue, LengthExpr, NoneLit, NumberLit, PrintStmt,
    RowsExpr, SetStmt, Stmt, StringLit, TypeRef, VarLValue, VarRef,
)


class CompileError(Exception):
    pass


# ---------------------------------------------------------------------------
# Type-system helper tables.
# ---------------------------------------------------------------------------

# Source-level type names → TypeCode. Aliases ("integer", "number") resolve
# to the default-width versions.
PRIMITIVE_TYPES = {
    "i32":     TypeCode.I32,
    "i64":     TypeCode.I64,
    "f32":     TypeCode.F32,
    "f64":     TypeCode.F64,
    "integer": TypeCode.I64,
    "float":   TypeCode.F64,
    "number":  TypeCode.F64,
    "bool":    TypeCode.BOOL,
    "text":    TypeCode.TEXT,
}


NUMERIC_TYPES = {TypeCode.I32, TypeCode.I64, TypeCode.F32, TypeCode.F64}


# Promotion table — given two numeric types, what's the common type that
# covers both? "Covers" means the result can hold any value of either operand
# without loss in the common case.
#
#   I32 + I64       → I64
#   I32/I64 + F32   → F32 or F64 (we pick F64 when the int is I64 to avoid
#                                  precision loss on large integers)
#   F32 + F64       → F64
_PROMOTE: dict[tuple[TypeCode, TypeCode], TypeCode] = {}
def _set_promote(a, b, result):
    _PROMOTE[(a, b)] = result
    _PROMOTE[(b, a)] = result

for _t in NUMERIC_TYPES:
    _PROMOTE[(_t, _t)] = _t
_set_promote(TypeCode.I32, TypeCode.I64, TypeCode.I64)
_set_promote(TypeCode.F32, TypeCode.F64, TypeCode.F64)
_set_promote(TypeCode.I32, TypeCode.F32, TypeCode.F32)
_set_promote(TypeCode.I32, TypeCode.F64, TypeCode.F64)
_set_promote(TypeCode.I64, TypeCode.F32, TypeCode.F64)   # I64 needs F64 precision
_set_promote(TypeCode.I64, TypeCode.F64, TypeCode.F64)


# Arithmetic opcode per (word, type).
_ARITH_OPCODES: dict[tuple[str, TypeCode], Opcode] = {
    ("plus",    TypeCode.I32): Opcode.ADD_I32,
    ("plus",    TypeCode.I64): Opcode.ADD_I64,
    ("plus",    TypeCode.F32): Opcode.ADD_F32,
    ("plus",    TypeCode.F64): Opcode.ADD_F64,
    ("minus",   TypeCode.I32): Opcode.SUB_I32,
    ("minus",   TypeCode.I64): Opcode.SUB_I64,
    ("minus",   TypeCode.F32): Opcode.SUB_F32,
    ("minus",   TypeCode.F64): Opcode.SUB_F64,
    ("times",   TypeCode.I32): Opcode.MUL_I32,
    ("times",   TypeCode.I64): Opcode.MUL_I64,
    ("times",   TypeCode.F32): Opcode.MUL_F32,
    ("times",   TypeCode.F64): Opcode.MUL_F64,
    ("divided", TypeCode.F32): Opcode.DIV_F32,
    ("divided", TypeCode.F64): Opcode.DIV_F64,
}


# Conversion opcode per (from, to).
_CVT_OPCODES: dict[tuple[TypeCode, TypeCode], Opcode] = {
    (TypeCode.I32, TypeCode.I64): Opcode.CVT_I32_I64,
    (TypeCode.I64, TypeCode.I32): Opcode.CVT_I64_I32,
    (TypeCode.F32, TypeCode.F64): Opcode.CVT_F32_F64,
    (TypeCode.F64, TypeCode.F32): Opcode.CVT_F64_F32,
    (TypeCode.I32, TypeCode.F32): Opcode.CVT_I32_F32,
    (TypeCode.I32, TypeCode.F64): Opcode.CVT_I32_F64,
    (TypeCode.I64, TypeCode.F32): Opcode.CVT_I64_F32,
    (TypeCode.I64, TypeCode.F64): Opcode.CVT_I64_F64,
    (TypeCode.F32, TypeCode.I32): Opcode.CVT_F32_I32,
    (TypeCode.F32, TypeCode.I64): Opcode.CVT_F32_I64,
    (TypeCode.F64, TypeCode.I32): Opcode.CVT_F64_I32,
    (TypeCode.F64, TypeCode.I64): Opcode.CVT_F64_I64,
}


class Compiler:
    def __init__(self) -> None:
        self.module: Module = Module()

    # ----- entry -----

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
        if isinstance(stmt, AppendStmt):
            return self.compile_append(stmt)
        if isinstance(stmt, PrintStmt):
            return self.compile_print(stmt)
        raise CompileError(f"unsupported statement: {type(stmt).__name__}")

    def compile_set(self, stmt: SetStmt) -> None:
        target = stmt.target

        # Matrix creation — special-cased, no general expression value.
        if isinstance(target, VarLValue) and isinstance(stmt.value, EmptyMatrix):
            return self.compile_set_matrix(target.name, stmt.value)

        # ----- set xs[i] to value  (1D)  -----
        # ----- set m[i, j] to value (2D matrix) -----
        if isinstance(target, IndexLValue):
            if len(target.indices) == 1:
                self.compile_expr_into(target.obj, reg=1)         # r1 = pointer
                self.compile_expr_into(target.indices[0], reg=2)   # r2 = index
                self.compile_expr_into(stmt.value, reg=0)          # r0 = value
                self.emit(Opcode.STORE_AT, (0, 1, 2))
                return
            if len(target.indices) == 2:
                return self.compile_matrix_set(target, stmt.value)
            raise CompileError("only 1D or 2D indexing supported")

        # ----- set x to value [as <type>] -----
        if isinstance(target, VarLValue):
            value_type = self.compile_expr_into(stmt.value, reg=0)

            # If the user annotated with `as <type>`, convert to it.
            if stmt.annotated_type is not None:
                target_type = self.typeref_to_code(stmt.annotated_type)
                if value_type != target_type:
                    self.emit_convert(value_type, target_type, src=0, dst=0)
                value_type = target_type

            if target.name in self.module.symbol_table:
                existing = self.module.symbol_types[target.name]
                if existing != value_type:
                    raise CompileError(
                        f"cannot change type of {target.name!r} from "
                        f"{existing.name} to {value_type.name}"
                    )
                addr = self.module.symbol_table[target.name]
            else:
                addr = self.allocate_variable(target.name, value_type)
            self.emit(Opcode.STORE, (0, addr))
            return

        raise CompileError(f"unsupported assignment target: {type(target).__name__}")

    def compile_append(self, stmt: AppendStmt) -> None:
        # append <value> to <list>
        # r0 = value, r1 = list pointer; APPEND r1, r0.
        self.compile_expr_into(stmt.value, reg=0)
        self.compile_expr_into_lvalue(stmt.target, reg=1)
        self.emit(Opcode.APPEND, (1, 0))

    def compile_print(self, stmt: PrintStmt) -> None:
        for part in stmt.parts:
            self.compile_expr_into(part, reg=0)
            self.emit(Opcode.PRINT, (0,))

    # ----- matrix-specific -----

    def compile_set_matrix(self, name: str, em: EmptyMatrix) -> None:
        shape = []
        for d in em.dims:
            if not (isinstance(d, NumberLit) and isinstance(d.value, int)):
                raise CompileError(
                    "matrix dimensions must be integer literals in v1"
                )
            shape.append(d.value)
        if len(shape) != 2:
            raise CompileError("only 2D matrices supported in v1")

        total = shape[0] * shape[1]
        self.emit(Opcode.ALLOC, (0, total))

        if name in self.module.symbol_table:
            if self.module.symbol_types[name] != TypeCode.REF:
                raise CompileError(f"cannot re-type {name!r} as a matrix")
            addr = self.module.symbol_table[name]
        else:
            addr = self.allocate_variable(name, TypeCode.REF)

        self.module.symbol_shapes[name] = tuple(shape)
        self.emit(Opcode.STORE, (0, addr))

    def compile_matrix_get(self, expr: IndexAccess, reg: int) -> TypeCode:
        shape = self._matrix_shape_of(expr.obj)
        cols = shape[1]

        r_ptr  = reg + 1
        r_idx  = reg + 2
        r_cols = reg + 3
        r_j    = reg + 4

        self.compile_expr_into(expr.obj, r_ptr)
        i_type = self.compile_expr_into(expr.indices[0], r_idx)
        self.coerce(i_type, TypeCode.I64, r_idx)

        cols_addr = self.allocate_constant(cols)
        self.emit(Opcode.LOAD, (r_cols, cols_addr))
        self.emit(Opcode.MUL_I64, (r_idx, r_idx, r_cols))

        j_type = self.compile_expr_into(expr.indices[1], r_j)
        self.coerce(j_type, TypeCode.I64, r_j)
        self.emit(Opcode.ADD_I64, (r_idx, r_idx, r_j))

        self.emit(Opcode.LOAD_AT, (reg, r_ptr, r_idx))
        return TypeCode.REF   # element type not tracked yet

    def compile_matrix_set(self, target: IndexLValue, value_expr) -> None:
        shape = self._matrix_shape_of(target.obj)
        cols = shape[1]

        self.compile_expr_into(target.obj, reg=1)
        i_type = self.compile_expr_into(target.indices[0], reg=2)
        self.coerce(i_type, TypeCode.I64, 2)

        cols_addr = self.allocate_constant(cols)
        self.emit(Opcode.LOAD, (3, cols_addr))
        self.emit(Opcode.MUL_I64, (2, 2, 3))

        j_type = self.compile_expr_into(target.indices[1], reg=3)
        self.coerce(j_type, TypeCode.I64, 3)
        self.emit(Opcode.ADD_I64, (2, 2, 3))

        self.compile_expr_into(value_expr, reg=0)
        self.emit(Opcode.STORE_AT, (0, 1, 2))

    def _matrix_shape_of(self, expr) -> tuple[int, ...]:
        if not isinstance(expr, VarRef):
            raise CompileError("matrix access requires a direct variable reference")
        if expr.name not in self.module.symbol_shapes:
            raise CompileError(f"{expr.name!r} is not a matrix")
        return self.module.symbol_shapes[expr.name]

    # ----- expressions -----

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

        if isinstance(expr, BinaryOp):
            return self.compile_binop(expr, reg)

        if isinstance(expr, EmptyList):
            self.emit(Opcode.ALLOC, (reg, 0))
            return TypeCode.REF

        if isinstance(expr, IndexAccess):
            if len(expr.indices) == 1:
                self.compile_expr_into(expr.obj, reg + 1)
                idx_type = self.compile_expr_into(expr.indices[0], reg + 2)
                self.coerce(idx_type, TypeCode.I64, reg + 2)
                self.emit(Opcode.LOAD_AT, (reg, reg + 1, reg + 2))
                return TypeCode.REF
            if len(expr.indices) == 2:
                return self.compile_matrix_get(expr, reg)
            raise CompileError("only 1D or 2D indexing supported")

        if isinstance(expr, LengthExpr):
            self.compile_expr_into(expr.value, reg + 1)
            self.emit(Opcode.LEN, (reg, reg + 1))
            return TypeCode.I64

        if isinstance(expr, RowsExpr):
            shape = self._matrix_shape_of(expr.value)
            addr = self.allocate_constant(shape[0])
            self.emit(Opcode.LOAD, (reg, addr))
            return TypeCode.I64

        if isinstance(expr, ColumnsExpr):
            shape = self._matrix_shape_of(expr.value)
            if len(shape) < 2:
                raise CompileError("matrix has no second dimension")
            addr = self.allocate_constant(shape[1])
            self.emit(Opcode.LOAD, (reg, addr))
            return TypeCode.I64

        raise CompileError(f"unsupported expression: {type(expr).__name__}")

    def compile_binop(self, expr: BinaryOp, reg: int) -> TypeCode:
        """Typed arithmetic. Emits conversions for mixed types, then the
        typed ADD/SUB/MUL/DIV opcode."""
        left_reg  = reg + 1
        right_reg = reg + 2

        left_type  = self.compile_expr_into(expr.left,  left_reg)
        right_type = self.compile_expr_into(expr.right, right_reg)

        if left_type not in NUMERIC_TYPES or right_type not in NUMERIC_TYPES:
            raise CompileError(
                f"arithmetic requires numeric operands, got "
                f"{left_type.name} and {right_type.name}"
            )

        # Division rule: always F64. Promote both to F64.
        if expr.op == "divided":
            result_type = TypeCode.F64
        else:
            result_type = _PROMOTE[(left_type, right_type)]

        self.coerce(left_type,  result_type, left_reg)
        self.coerce(right_type, result_type, right_reg)

        opcode = _ARITH_OPCODES[(expr.op, result_type)]
        self.emit(opcode, (reg, left_reg, right_reg))
        return result_type

    def compile_expr_into_lvalue(self, lv, reg: int) -> TypeCode:
        if isinstance(lv, VarLValue):
            if lv.name not in self.module.symbol_table:
                raise CompileError(f"undeclared variable {lv.name!r}")
            addr = self.module.symbol_table[lv.name]
            ty = self.module.symbol_types[lv.name]
            self.emit(Opcode.LOAD, (reg, addr))
            return ty
        raise CompileError(f"unsupported append target: {type(lv).__name__}")

    # ----- type helpers -----

    def typeref_to_code(self, type_ref: TypeRef) -> TypeCode:
        if type_ref.name not in PRIMITIVE_TYPES:
            raise CompileError(
                f"unknown type {type_ref.name!r} "
                f"(expected one of: {', '.join(sorted(PRIMITIVE_TYPES))})"
            )
        return PRIMITIVE_TYPES[type_ref.name]

    def coerce(self, from_type: TypeCode, to_type: TypeCode, reg: int) -> None:
        """Emit a conversion in `reg` if needed. No-op when types match."""
        if from_type == to_type:
            return
        self.emit_convert(from_type, to_type, src=reg, dst=reg)

    def emit_convert(self, from_type: TypeCode, to_type: TypeCode,
                     src: int, dst: int) -> None:
        if from_type == to_type:
            if src != dst:
                raise CompileError("internal: MOV between regs not yet supported")
            return
        op = _CVT_OPCODES.get((from_type, to_type))
        if op is None:
            raise CompileError(
                f"no conversion from {from_type.name} to {to_type.name}"
            )
        self.emit(op, (dst, src))

    # ----- memory layout -----

    def allocate_constant(self, value) -> int:
        addr = len(self.module.initial_memory)
        self.module.initial_memory.append(value)
        return addr

    def allocate_variable(self, name: str, ty: TypeCode) -> int:
        addr = len(self.module.initial_memory)
        self.module.initial_memory.append(None)
        self.module.symbol_table[name] = addr
        self.module.symbol_types[name] = ty
        return addr

    # ----- emission -----

    def emit(self, op: Opcode, operands: tuple) -> None:
        self.module.code.append(Instruction(op, operands))


def compile_program(stmts: list[Stmt]) -> Module:
    return Compiler().compile_program(stmts)
