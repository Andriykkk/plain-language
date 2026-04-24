"""
Compiler — walks the AST and produces one flat bytecode stream + one flat
memory array.

For each `set x to <expr>`:
  1. Decide x's type by compiling <expr>.
  2. If x is new, pick a memory slot for it. Otherwise reuse its slot.
  3. Compile <expr> into register r0; emit STORE r0 to x's slot.

For each `set xs[i] to <expr>`:
  Compile xs into a register (it's a pointer), compile i into another,
  compile the value into another, then emit STORE_AT.

For each `print <expr>`:
  Compile expr into r0, emit PRINT r0.

For each `append <value> to xs`:
  Compile value into a register, load xs's pointer into another, emit APPEND.

Expressions handled:
  - literals:      LOADed from a pre-filled memory slot
  - variable:      LOAD from its slot
  - empty list:    ALLOC a new (size-0) heap block, return pointer
  - xs[i]:         LOAD pointer + LOAD index + LOAD_AT
  - length of xs:  LOAD pointer + LEN
"""

from bytecode import Instruction, Module, Opcode, TypeCode
from parser import (
    AppendStmt, BoolLit, ColumnsExpr, EmptyList, EmptyMatrix, IndexAccess,
    IndexLValue, LengthExpr, NoneLit, NumberLit, PrintStmt, RowsExpr, SetStmt,
    Stmt, StringLit, VarLValue, VarRef,
)


class CompileError(Exception):
    pass


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

        # Special case: creating a matrix.
        # The compiler does the shape bookkeeping — the VM just sees an ALLOC.
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

        # ----- set x to value -----
        if isinstance(target, VarLValue):
            value_type = self.compile_expr_into(stmt.value, reg=0)
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

    # ----- matrix-specific compile steps -----
    #
    # At the VM level a matrix is just a flat array. The compiler knows the
    # shape, so accesses compile to arithmetic (i*cols + j) + LOAD_AT/STORE_AT.

    def compile_set_matrix(self, name: str, em: EmptyMatrix) -> None:
        # Shape must be compile-time constants in v1.
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

        # ALLOC — creates a flat block on the OS heap. Size is rows*cols.
        self.emit(Opcode.ALLOC, (0, total))

        # Declare the variable slot (REF — a pointer), or reuse if it existed.
        if name in self.module.symbol_table:
            if self.module.symbol_types[name] != TypeCode.REF:
                raise CompileError(f"cannot re-type {name!r} as a matrix")
            addr = self.module.symbol_table[name]
        else:
            addr = self.allocate_variable(name, TypeCode.REF)

        # Remember the shape — purely compile-time metadata.
        self.module.symbol_shapes[name] = tuple(shape)

        self.emit(Opcode.STORE, (0, addr))

    def compile_matrix_get(self, expr: IndexAccess, reg: int) -> TypeCode:
        shape = self._matrix_shape_of(expr.obj)
        cols = shape[1]

        r_ptr  = reg + 1
        r_idx  = reg + 2   # will hold i, then i*cols, then i*cols + j
        r_cols = reg + 3
        r_j    = reg + 4

        self.compile_expr_into(expr.obj, r_ptr)             # r_ptr = matrix
        self.compile_expr_into(expr.indices[0], r_idx)       # r_idx = i

        cols_addr = self.allocate_constant(cols)
        self.emit(Opcode.LOAD, (r_cols, cols_addr))          # r_cols = cols
        self.emit(Opcode.MUL,  (r_idx, r_idx, r_cols))       # r_idx = i * cols

        self.compile_expr_into(expr.indices[1], r_j)         # r_j = j
        self.emit(Opcode.ADD,  (r_idx, r_idx, r_j))          # r_idx = i*cols + j

        self.emit(Opcode.LOAD_AT, (reg, r_ptr, r_idx))
        return TypeCode.REF  # element type not tracked yet

    def compile_matrix_set(self, target: IndexLValue, value_expr) -> None:
        shape = self._matrix_shape_of(target.obj)
        cols = shape[1]

        # Registers: r0 = value, r1 = pointer, r2 = offset, r3 = cols/j scratch
        self.compile_expr_into(target.obj, reg=1)           # r1 = matrix
        self.compile_expr_into(target.indices[0], reg=2)     # r2 = i

        cols_addr = self.allocate_constant(cols)
        self.emit(Opcode.LOAD, (3, cols_addr))               # r3 = cols
        self.emit(Opcode.MUL,  (2, 2, 3))                    # r2 = i * cols

        self.compile_expr_into(target.indices[1], reg=3)     # r3 = j
        self.emit(Opcode.ADD,  (2, 2, 3))                    # r2 = i*cols + j

        self.compile_expr_into(value_expr, reg=0)            # r0 = value
        self.emit(Opcode.STORE_AT, (0, 1, 2))

    def _matrix_shape_of(self, expr) -> tuple[int, ...]:
        if not isinstance(expr, VarRef):
            raise CompileError("matrix access requires a direct variable reference")
        if expr.name not in self.module.symbol_shapes:
            raise CompileError(f"{expr.name!r} is not a matrix")
        return self.module.symbol_shapes[expr.name]

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

        if isinstance(expr, EmptyList):
            # A fresh heap-allocated array. Initial size = 0; APPEND grows it.
            self.emit(Opcode.ALLOC, (reg, 0))
            return TypeCode.REF

        if isinstance(expr, IndexAccess):
            if len(expr.indices) == 1:
                # 1D list access.
                self.compile_expr_into(expr.obj, reg + 1)
                self.compile_expr_into(expr.indices[0], reg + 2)
                self.emit(Opcode.LOAD_AT, (reg, reg + 1, reg + 2))
                return TypeCode.REF
            if len(expr.indices) == 2:
                # 2D matrix access — offset = i*cols + j.
                return self.compile_matrix_get(expr, reg)
            raise CompileError("only 1D or 2D indexing supported")

        if isinstance(expr, LengthExpr):
            self.compile_expr_into(expr.value, reg + 1)
            self.emit(Opcode.LEN, (reg, reg + 1))
            return TypeCode.I64

        if isinstance(expr, RowsExpr):
            # `rows of m` — compile-time literal (matrix shape is known).
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

    def compile_expr_into_lvalue(self, lv, reg: int) -> TypeCode:
        """Like compile_expr_into but for an assignment target's *current*
        pointer/value (used by `append <val> to <lvalue>`)."""
        if isinstance(lv, VarLValue):
            if lv.name not in self.module.symbol_table:
                raise CompileError(f"undeclared variable {lv.name!r}")
            addr = self.module.symbol_table[lv.name]
            ty = self.module.symbol_types[lv.name]
            self.emit(Opcode.LOAD, (reg, addr))
            return ty
        raise CompileError(f"unsupported append target: {type(lv).__name__}")

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
