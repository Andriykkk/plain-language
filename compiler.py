"""
Compiler — walks the AST and produces one flat bytecode stream + one flat
memory array.

Types are tracked at compile time. Each expression compiles into a register
and returns its TypeCode. When a binary op sees mixed types, the compiler
emits explicit conversion opcodes to promote the smaller/narrower operand
to the common result type. Division of two integers produces F64.

`set x to <expr> as <type>` converts the value to the annotated type.
"""

from dataclasses import dataclass, field

from bytecode import Instruction, Module, Opcode, RecordLayout, TypeCode
from parser import (
    AppendStmt, BinaryOp, BoolLit, CallExpr, CallStmt, ColumnsExpr, Compare,
    EmptyList, EmptyMatrix, FieldAccess, FieldLValue, FunctionDef, IfStmt,
    IndexAccess, IndexLValue, LengthExpr, NewExpr, NoneLit, NumberLit,
    PrintStmt, RecordDef, RepeatForEachStmt, RepeatRangeStmt, RepeatTimesStmt,
    RepeatWhileStmt, ReturnStmt, RowsExpr, SetStmt, SkipStmt, Stmt, StopStmt,
    StringLit, TypeRef, VarLValue, VarRef,
)


@dataclass
class LoopContext:
    """Tracks jumps that exit or continue the nearest enclosing loop.
    Each `stop` / `skip` statement emits a placeholder JMP and records its
    instruction index here; `compile_loop` patches them at the right target
    once the loop's structure is fully emitted."""
    break_patches: list[int] = field(default_factory=list)
    continue_patches: list[int] = field(default_factory=list)


@dataclass
class FunctionInfo:
    """Compile-time record of a function: where its body lives, what
    arguments it takes (in declaration order), and what it returns.
    Parameters live on the stack — the caller pushes them in order, so at
    function entry the i-th parameter sits at offset (N - i) above the
    return address."""
    name: str
    entry: int
    params: list[tuple[str, TypeCode]]
    return_type: TypeCode | None


class CompileError(Exception):
    pass


# ---------------------------------------------------------------------------
# Type-system helper tables.
# ---------------------------------------------------------------------------

# Source-level type names → TypeCode. Aliases ("integer", "number") resolve
# to the default-width versions.
PRIMITIVE_TYPES = {
    "i8":      TypeCode.I8,
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


NUMERIC_TYPES = {TypeCode.I8, TypeCode.I32, TypeCode.I64,
                 TypeCode.F32, TypeCode.F64}


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
# int ↔ int widening — always widen to the larger size.
_set_promote(TypeCode.I8,  TypeCode.I32, TypeCode.I32)
_set_promote(TypeCode.I8,  TypeCode.I64, TypeCode.I64)
_set_promote(TypeCode.I32, TypeCode.I64, TypeCode.I64)
# float widening.
_set_promote(TypeCode.F32, TypeCode.F64, TypeCode.F64)
# int ↔ float — promote the int to the float type (wider float if needed).
_set_promote(TypeCode.I8,  TypeCode.F32, TypeCode.F32)
_set_promote(TypeCode.I8,  TypeCode.F64, TypeCode.F64)
_set_promote(TypeCode.I32, TypeCode.F32, TypeCode.F32)
_set_promote(TypeCode.I32, TypeCode.F64, TypeCode.F64)
_set_promote(TypeCode.I64, TypeCode.F32, TypeCode.F64)   # I64 needs F64 precision
_set_promote(TypeCode.I64, TypeCode.F64, TypeCode.F64)


# Arithmetic opcode per (word, type).
_ARITH_OPCODES: dict[tuple[str, TypeCode], Opcode] = {
    ("plus",    TypeCode.I8):  Opcode.ADD_I8,
    ("plus",    TypeCode.I32): Opcode.ADD_I32,
    ("plus",    TypeCode.I64): Opcode.ADD_I64,
    ("plus",    TypeCode.F32): Opcode.ADD_F32,
    ("plus",    TypeCode.F64): Opcode.ADD_F64,
    ("minus",   TypeCode.I8):  Opcode.SUB_I8,
    ("minus",   TypeCode.I32): Opcode.SUB_I32,
    ("minus",   TypeCode.I64): Opcode.SUB_I64,
    ("minus",   TypeCode.F32): Opcode.SUB_F32,
    ("minus",   TypeCode.F64): Opcode.SUB_F64,
    ("times",   TypeCode.I8):  Opcode.MUL_I8,
    ("times",   TypeCode.I32): Opcode.MUL_I32,
    ("times",   TypeCode.I64): Opcode.MUL_I64,
    ("times",   TypeCode.F32): Opcode.MUL_F32,
    ("times",   TypeCode.F64): Opcode.MUL_F64,
    ("divided", TypeCode.F32): Opcode.DIV_F32,
    ("divided", TypeCode.F64): Opcode.DIV_F64,
}


# Conversion opcode per (from, to).
_CVT_OPCODES: dict[tuple[TypeCode, TypeCode], Opcode] = {
    # int → int
    (TypeCode.I8,  TypeCode.I32): Opcode.CVT_I8_I32,
    (TypeCode.I8,  TypeCode.I64): Opcode.CVT_I8_I64,
    (TypeCode.I32, TypeCode.I8):  Opcode.CVT_I32_I8,
    (TypeCode.I64, TypeCode.I8):  Opcode.CVT_I64_I8,
    (TypeCode.I32, TypeCode.I64): Opcode.CVT_I32_I64,
    (TypeCode.I64, TypeCode.I32): Opcode.CVT_I64_I32,
    # float → float
    (TypeCode.F32, TypeCode.F64): Opcode.CVT_F32_F64,
    (TypeCode.F64, TypeCode.F32): Opcode.CVT_F64_F32,
    # int → float
    (TypeCode.I8,  TypeCode.F32): Opcode.CVT_I8_F32,
    (TypeCode.I8,  TypeCode.F64): Opcode.CVT_I8_F64,
    (TypeCode.I32, TypeCode.F32): Opcode.CVT_I32_F32,
    (TypeCode.I32, TypeCode.F64): Opcode.CVT_I32_F64,
    (TypeCode.I64, TypeCode.F32): Opcode.CVT_I64_F32,
    (TypeCode.I64, TypeCode.F64): Opcode.CVT_I64_F64,
    # float → int (truncation toward zero)
    (TypeCode.F32, TypeCode.I8):  Opcode.CVT_F32_I8,
    (TypeCode.F64, TypeCode.I8):  Opcode.CVT_F64_I8,
    (TypeCode.F32, TypeCode.I32): Opcode.CVT_F32_I32,
    (TypeCode.F32, TypeCode.I64): Opcode.CVT_F32_I64,
    (TypeCode.F64, TypeCode.I32): Opcode.CVT_F64_I32,
    (TypeCode.F64, TypeCode.I64): Opcode.CVT_F64_I64,
}


# Comparison opcode per (op, type). The op string comes from the AST's
# Compare node: "equal" | "not_equal" | "less" | "at_most" | "greater" | "at_least".
_CMP_OPCODES: dict[tuple[str, TypeCode], Opcode] = {}
for _ty, _suffix in [
    (TypeCode.I8,  "I8"),  (TypeCode.I32, "I32"), (TypeCode.I64, "I64"),
    (TypeCode.F32, "F32"), (TypeCode.F64, "F64"),
]:
    _CMP_OPCODES[("equal",     _ty)] = getattr(Opcode, f"EQ_{_suffix}")
    _CMP_OPCODES[("not_equal", _ty)] = getattr(Opcode, f"NE_{_suffix}")
    _CMP_OPCODES[("less",      _ty)] = getattr(Opcode, f"LT_{_suffix}")
    _CMP_OPCODES[("at_most",   _ty)] = getattr(Opcode, f"LE_{_suffix}")
    _CMP_OPCODES[("greater",   _ty)] = getattr(Opcode, f"GT_{_suffix}")
    _CMP_OPCODES[("at_least",  _ty)] = getattr(Opcode, f"GE_{_suffix}")

_CMP_OPCODES[("equal",     TypeCode.BOOL)] = Opcode.EQ_BOOL
_CMP_OPCODES[("not_equal", TypeCode.BOOL)] = Opcode.NE_BOOL
_CMP_OPCODES[("equal",     TypeCode.REF)]  = Opcode.EQ_REF
_CMP_OPCODES[("not_equal", TypeCode.REF)]  = Opcode.NE_REF


class Compiler:
    def __init__(self) -> None:
        self.module: Module = Module()
        self.loop_stack: list[LoopContext] = []
        self._hidden_counter: int = 0
        # Function table — populated as `define function` statements are
        # encountered. Compiled bodies live at the start of the bytecode,
        # before main entry.
        self.functions: dict[str, FunctionInfo] = {}
        # Set while compiling a function body. None at top level.
        self.current_func: FunctionInfo | None = None
        # name → index in current_func.params (only populated inside a body).
        self.param_indices: dict[str, int] = {}
        # How many extra slots the current body has pushed onto the stack
        # since its entry, but not yet popped. Parameter offsets shift by
        # this much each time we read them.
        self.stack_delta: int = 0
        # Memory address of the single shared "return value" slot. Allocated
        # lazily on first function-related code; functions store their result
        # here on RET, callers LOAD it after CALL.
        self.return_slot_addr: int | None = None

    # ----- entry -----

    def compile_program(self, stmts: list[Stmt]) -> Module:
        # Source order is preserved. FunctionDefs emit their bodies inline,
        # wrapped in a JMP that jumps past the body so straight-line
        # execution doesn't fall into it. Functions self-recurse fine
        # (registered before their body is compiled), but mutual recursion
        # and forward calls require the callee to be defined first.
        self.module.entry = 0
        for stmt in stmts:
            self.compile_stmt(stmt)
        self.emit(Opcode.HALT, ())
        return self.module

    # ----- stack-tracking emission helpers -----

    def emit_push(self, reg: int) -> None:
        self.emit(Opcode.PUSH, (reg,))
        self.stack_delta += 1

    def emit_drop(self, count: int) -> None:
        if count <= 0:
            return
        self.emit(Opcode.DROP, (count,))
        self.stack_delta -= count

    def _ensure_return_slot(self) -> int:
        if self.return_slot_addr is None:
            self.return_slot_addr = self.allocate_constant(None)
        return self.return_slot_addr

    # ----- statements -----

    def compile_stmt(self, stmt: Stmt) -> None:
        if isinstance(stmt, RecordDef):
            return self.compile_record_def(stmt)
        if isinstance(stmt, SetStmt):
            return self.compile_set(stmt)
        if isinstance(stmt, AppendStmt):
            return self.compile_append(stmt)
        if isinstance(stmt, PrintStmt):
            return self.compile_print(stmt)
        if isinstance(stmt, IfStmt):
            return self.compile_if(stmt)
        if isinstance(stmt, RepeatWhileStmt):
            return self.compile_repeat_while(stmt)
        if isinstance(stmt, RepeatTimesStmt):
            return self.compile_repeat_times(stmt)
        if isinstance(stmt, RepeatRangeStmt):
            return self.compile_repeat_range(stmt)
        if isinstance(stmt, RepeatForEachStmt):
            raise CompileError(
                "'repeat for each' isn't implemented yet — needs iterator state"
            )
        if isinstance(stmt, StopStmt):
            return self.compile_stop()
        if isinstance(stmt, SkipStmt):
            return self.compile_skip()
        if isinstance(stmt, FunctionDef):
            return self.compile_function_def(stmt)
        if isinstance(stmt, ReturnStmt):
            return self.compile_return(stmt)
        if isinstance(stmt, CallStmt):
            return self.compile_call_stmt(stmt)
        raise CompileError(f"unsupported statement: {type(stmt).__name__}")

    def compile_set(self, stmt: SetStmt) -> None:
        target = stmt.target

        # Matrix creation — special-cased, no general expression value.
        if isinstance(target, VarLValue) and isinstance(stmt.value, EmptyMatrix):
            return self.compile_set_matrix(target.name, stmt.value)

        # Record creation — the compiler remembers which record type the
        # variable holds so later `p.field` reads know the layout.
        if isinstance(target, VarLValue) and isinstance(stmt.value, NewExpr):
            return self.compile_set_new_record(target.name, stmt.value)

        # Field assignment: `set p.field to value`.
        if isinstance(target, FieldLValue):
            return self.compile_field_assign(target, stmt.value)

        # ----- set xs[i] to value  (1D)  -----
        # ----- set m[i, j] to value (2D matrix) -----
        if isinstance(target, IndexLValue):
            self._check_index_arity(target.obj, len(target.indices))
            if len(target.indices) == 1:
                # Compile value FIRST into r0. If the value expression is
                # itself indexed (e.g. s[1] = s[4]), it uses r1..r_N as scratch;
                # doing it before we set up r1/r2 avoids clobbering them.
                self.compile_char_or_value(target, stmt.value, reg=0)
                self.compile_expr_into(target.obj, reg=1)         # r1 = pointer
                self.compile_expr_into(target.indices[0], reg=2)   # r2 = index
                self.emit(Opcode.STORE_AT, (0, 1, 2))
                return
            if len(target.indices) == 2:
                return self.compile_matrix_set(target, stmt.value)
            raise CompileError("only 1D or 2D indexing supported")

        # ----- set x to value [as <type>] -----
        if isinstance(target, VarLValue):
            # Writing to a parameter would need STORE_STACK with the same
            # offset arithmetic as the read side; intentionally not supported
            # in v1 to keep the calling convention strictly value-in.
            if self.current_func is not None and target.name in self.param_indices:
                raise CompileError(
                    f"cannot assign to parameter {target.name!r}; "
                    f"copy it into a local first"
                )
            value_type = self.compile_expr_into(stmt.value, reg=0)

            # If the user annotated with `as <type>`, convert to it.
            if stmt.annotated_type is not None:
                target_type = self.typeref_to_code(stmt.annotated_type)
                if value_type != target_type:
                    self.emit_convert(value_type, target_type, src=0, dst=0)
                value_type = target_type

            if target.name in self.module.symbol_table:
                existing = self.module.symbol_types[target.name]
                # Variable keeps its declared type on reassignment.
                # Silent coercion when the RHS is numeric — enables the
                # desugared compound form `divide x by 4` (becomes
                # `set x to x / 4`) to work for integer x: the RHS is F64
                # from the division rule, then narrowed back to I64 here.
                if existing != value_type:
                    if existing in NUMERIC_TYPES and value_type in NUMERIC_TYPES:
                        self.emit_convert(value_type, existing, src=0, dst=0)
                        value_type = existing
                    else:
                        raise CompileError(
                            f"cannot change type of {target.name!r} from "
                            f"{existing.name} to {value_type.name}"
                        )
                addr = self.module.symbol_table[target.name]
            else:
                addr = self.allocate_variable(target.name, value_type)
                # If the RHS creates/carries a typed container, remember its
                # element type so later `xs[i]` reads return the real type.
                elem_type = self.infer_elem_type(stmt.value)
                if elem_type is not None:
                    self.module.symbol_elem_types[target.name] = elem_type
            self.emit(Opcode.STORE, (0, addr))
            return

        raise CompileError(f"unsupported assignment target: {type(target).__name__}")

    def compile_char_or_value(self, target: IndexLValue, value_expr,
                              reg: int) -> None:
        """Compile the RHS of `set s[i] to <value>`.

        Special case: if s is a TEXT variable and the value is a single-char
        string literal ("H"), fold the char to its i8 code at compile time.
        Otherwise compile the value normally — numbers stay numbers,
        computed values (including s[j] which reads an i8) stay numbers.
        """
        if (isinstance(target.obj, VarRef)
                and self.module.symbol_types.get(target.obj.name) == TypeCode.TEXT
                and isinstance(value_expr, StringLit)):
            chars = value_expr.value
            if len(chars) != 1:
                raise CompileError(
                    f"cannot store multi-character string {chars!r} into "
                    f"a single text slot; use one character"
                )
            code = ord(chars[0])
            addr = self.allocate_constant(code)
            self.emit(Opcode.LOAD, (reg, addr))
            return
        self.compile_expr_into(value_expr, reg=reg)

    def compile_append(self, stmt: AppendStmt) -> None:
        # append <value> to <list>
        # r0 = value, r1 = list pointer; APPEND r1, r0.
        self.compile_expr_into(stmt.value, reg=0)
        self.compile_expr_into_lvalue(stmt.target, reg=1)
        self.emit(Opcode.APPEND, (1, 0))

    def compile_print(self, stmt: PrintStmt) -> None:
        # `print A and B and ...` is one statement → one trailing newline.
        # Adjacent parts get a single-space separator.
        for i, part in enumerate(stmt.parts):
            if i > 0:
                self.emit(Opcode.PRINT_SPACE, ())
            ty = self.compile_expr_into(part, reg=0)
            # TEXT values are arrays of char codes — use PRINT_TEXT to
            # decode them back. Everything else uses plain PRINT.
            if ty == TypeCode.TEXT:
                self.emit(Opcode.PRINT_TEXT, (0,))
            else:
                self.emit(Opcode.PRINT, (0,))
        self.emit(Opcode.PRINT_NEWLINE, ())

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

        # Shape and element type — purely compile-time metadata.
        self.module.symbol_shapes[name] = tuple(shape)
        self.module.symbol_elem_types[name] = self.typeref_to_elem_code(em.elem_type)
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
        return self._elem_type_of(expr.obj)

    def compile_matrix_set(self, target: IndexLValue, value_expr) -> None:
        shape = self._matrix_shape_of(target.obj)
        cols = shape[1]

        # Compile the value FIRST into r0 (same reason as the 1D case:
        # if value_expr reads from the matrix itself, its scratch use of
        # r1..r_N would clobber our setup below).
        self.compile_expr_into(value_expr, reg=0)

        self.compile_expr_into(target.obj, reg=1)
        i_type = self.compile_expr_into(target.indices[0], reg=2)
        self.coerce(i_type, TypeCode.I64, 2)

        cols_addr = self.allocate_constant(cols)
        self.emit(Opcode.LOAD, (3, cols_addr))
        self.emit(Opcode.MUL_I64, (2, 2, 3))

        j_type = self.compile_expr_into(target.indices[1], reg=3)
        self.coerce(j_type, TypeCode.I64, 3)
        self.emit(Opcode.ADD_I64, (2, 2, 3))

        self.emit(Opcode.STORE_AT, (0, 1, 2))

    # ----- records -----
    #
    # A record is a heap-allocated block of N slots (one slot per field in
    # declaration order). The variable holds the pointer. Field access is
    # just LOAD_AT with the field's offset. No vtable, no per-instance type
    # tag — the compiler tracks each record variable's type statically.

    def compile_record_def(self, stmt: RecordDef) -> None:
        """Compile-time only: store the record's layout in the module so
        later field-accesses can look up offsets and types."""
        fields: list[tuple[str, TypeCode]] = []
        for field_name, type_ref in stmt.fields:
            fields.append((field_name, self.typeref_to_elem_code(type_ref)))
        self.module.records[stmt.name] = RecordLayout(stmt.name, fields)

    def compile_set_new_record(self, name: str, new_expr: NewExpr) -> None:
        """Allocate a fresh record block, initialize fields to their type
        defaults, and bind the pointer to `name`."""
        record_name = new_expr.type_name
        if record_name not in self.module.records:
            raise CompileError(f"unknown record type {record_name!r}")
        layout = self.module.records[record_name]
        size = len(layout.fields)

        # r0 = new record block (size slots, all initially None from ALLOC)
        self.emit(Opcode.ALLOC, (0, size))

        # Initialize each field to its type's default value. The VM's ALLOC
        # zero-fills with None; for non-reference fields we overwrite with
        # the type's natural zero (0, 0.0, False). For TEXT/REF fields we
        # allocate a fresh empty array so `length of record.field` returns
        # 0 instead of erroring on None.
        for i, (_field_name, field_type) in enumerate(layout.fields):
            offset_addr = self.allocate_constant(i)
            self.emit(Opcode.LOAD, (2, offset_addr))

            if field_type == TypeCode.TEXT or field_type == TypeCode.REF:
                # Fresh empty array for each field — avoids shared state.
                self.emit(Opcode.ALLOC, (1, 0))
            else:
                default = self._default_for_field(field_type)
                default_addr = self.allocate_constant(default)
                self.emit(Opcode.LOAD, (1, default_addr))

            self.emit(Opcode.STORE_AT, (1, 0, 2))

        # Bind the record pointer to the variable and remember its type.
        if name in self.module.symbol_table:
            if self.module.symbol_types[name] != TypeCode.REF:
                raise CompileError(f"cannot re-type {name!r} as a record")
            addr = self.module.symbol_table[name]
        else:
            addr = self.allocate_variable(name, TypeCode.REF)
        self.module.symbol_record_types[name] = record_name
        self.emit(Opcode.STORE, (0, addr))

    def compile_new_record_inline(self, new_expr: NewExpr, reg: int) -> TypeCode:
        """`new Person` used inside an expression (not on the right side of
        `set`). Same ALLOC + default-init, but we can't attach a record
        type to any variable since there's no target here."""
        record_name = new_expr.type_name
        if record_name not in self.module.records:
            raise CompileError(f"unknown record type {record_name!r}")
        layout = self.module.records[record_name]
        size = len(layout.fields)

        self.emit(Opcode.ALLOC, (reg, size))
        for i, (_field_name, field_type) in enumerate(layout.fields):
            offset_addr = self.allocate_constant(i)
            self.emit(Opcode.LOAD, (reg + 2, offset_addr))
            if field_type == TypeCode.TEXT or field_type == TypeCode.REF:
                self.emit(Opcode.ALLOC, (reg + 1, 0))
            else:
                default = self._default_for_field(field_type)
                default_addr = self.allocate_constant(default)
                self.emit(Opcode.LOAD, (reg + 1, default_addr))
            self.emit(Opcode.STORE_AT, (reg + 1, reg, reg + 2))
        return TypeCode.REF

    def compile_field_access(self, expr: FieldAccess, reg: int) -> TypeCode:
        """Read a field: LOAD record pointer, LOAD offset, LOAD_AT."""
        layout, offset, field_type = self._resolve_field(expr.obj, expr.field)
        # Load pointer into reg+1
        self.compile_expr_into(expr.obj, reg + 1)
        # Load offset constant into reg+2
        offset_addr = self.allocate_constant(offset)
        self.emit(Opcode.LOAD, (reg + 2, offset_addr))
        # Deref: reg = (*ptr)[offset]
        self.emit(Opcode.LOAD_AT, (reg, reg + 1, reg + 2))
        return field_type

    def compile_field_assign(self, target: FieldLValue, value_expr) -> None:
        """Write a field: compile value, LOAD record pointer, LOAD offset, STORE_AT."""
        layout, offset, field_type = self._resolve_field(target.obj, target.field)

        # Compile value FIRST into r0 (before setting up r1/r2 — the value
        # expression may itself use r1+ as scratch).
        value_type = self.compile_expr_into(value_expr, reg=0)

        # If the value is a single-char string literal and the field is
        # (conceptually) a character, fold to the code. But we don't track
        # that distinction for record fields yet; for now allow mismatched
        # numeric types via coercion and leave non-numeric mismatches as
        # runtime responsibility.
        if value_type != field_type \
           and value_type in NUMERIC_TYPES \
           and field_type in NUMERIC_TYPES:
            self.emit_convert(value_type, field_type, src=0, dst=0)

        # Set up pointer and offset, then STORE_AT.
        self.compile_expr_into(target.obj, reg=1)
        offset_addr = self.allocate_constant(offset)
        self.emit(Opcode.LOAD, (2, offset_addr))
        self.emit(Opcode.STORE_AT, (0, 1, 2))

    def _resolve_field(self, obj_expr, field_name: str) -> tuple[RecordLayout, int, TypeCode]:
        """Given a record-typed expression and a field name, return the
        record's layout, the field's offset, and the field's type."""
        if not isinstance(obj_expr, VarRef):
            raise CompileError("field access requires a direct variable reference")
        if obj_expr.name not in self.module.symbol_record_types:
            raise CompileError(f"{obj_expr.name!r} is not a record")
        record_name = self.module.symbol_record_types[obj_expr.name]
        layout = self.module.records[record_name]
        for i, (fname, ftype) in enumerate(layout.fields):
            if fname == field_name:
                return layout, i, ftype
        raise CompileError(f"record {record_name!r} has no field {field_name!r}")

    def _default_for_field(self, ty: TypeCode) -> object:
        """Natural zero for a primitive field type (used to default-init
        a record's fields on creation)."""
        if ty in (TypeCode.I8, TypeCode.I32, TypeCode.I64):
            return 0
        if ty in (TypeCode.F32, TypeCode.F64):
            return 0.0
        if ty == TypeCode.BOOL:
            return False
        return None   # TEXT/REF handled separately via ALLOC

    def _matrix_shape_of(self, expr) -> tuple[int, ...]:
        if not isinstance(expr, VarRef):
            raise CompileError("matrix access requires a direct variable reference")
        if expr.name not in self.module.symbol_shapes:
            raise CompileError(f"{expr.name!r} is not a matrix")
        return self.module.symbol_shapes[expr.name]

    def _check_index_arity(self, obj_expr, n_indices: int) -> None:
        """If `obj_expr` is a known matrix variable, enforce that the
        number of indices matches its declared dimensionality. Plain lists
        and unknown expressions are unconstrained — only matrices have a
        compile-time-known shape."""
        if not isinstance(obj_expr, VarRef):
            return
        shape = self.module.symbol_shapes.get(obj_expr.name)
        if shape is None:
            return
        if len(shape) != n_indices:
            raise CompileError(
                f"{obj_expr.name!r} is a {len(shape)}-dimensional matrix; "
                f"indexing with {n_indices} index(es) is invalid"
            )

    def _contains_call(self, expr) -> bool:
        """True if evaluating `expr` could emit a CALL — meaning all
        registers might be clobbered. Used to decide when a parent
        expression needs to spill an in-register value to the stack across
        the compute of `expr`."""
        if isinstance(expr, CallExpr):
            return True
        # Walk dataclass children — anything with sub-expressions could
        # transitively contain a call.
        if isinstance(expr, BinaryOp):
            return self._contains_call(expr.left) or self._contains_call(expr.right)
        if isinstance(expr, Compare):
            return self._contains_call(expr.left) or self._contains_call(expr.right)
        if isinstance(expr, IndexAccess):
            if self._contains_call(expr.obj):
                return True
            return any(self._contains_call(i) for i in expr.indices)
        if isinstance(expr, FieldAccess):
            return self._contains_call(expr.obj)
        if isinstance(expr, (LengthExpr, RowsExpr, ColumnsExpr)):
            return self._contains_call(expr.value)
        return False

    def _elem_type_of(self, expr) -> TypeCode:
        """Return the element type of the container expression `expr`.
        Falls back to REF if the compiler doesn't know statically
        (e.g., the container came from a function call or an indirect
        expression). Arithmetic on a REF result will error cleanly —
        that's a signal the caller needs to annotate more types."""
        if isinstance(expr, VarRef):
            return self.module.symbol_elem_types.get(expr.name, TypeCode.REF)
        return TypeCode.REF

    # ----- expressions -----

    def compile_expr_into(self, expr, reg: int) -> TypeCode:
        if isinstance(expr, NumberLit):
            ty = TypeCode.I64 if isinstance(expr.value, int) else TypeCode.F64
            addr = self.allocate_constant(expr.value)
            self.emit(Opcode.LOAD, (reg, addr))
            return ty

        if isinstance(expr, StringLit):
            # Text is an array of character codes. The "string" literal
            # is pre-populated into memory as a list of ord values; LOAD
            # gives a pointer to that array.
            chars = [ord(c) for c in expr.value]
            addr = self.allocate_constant(chars)
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
            # Inside a function body, parameter names resolve to stack
            # slots, not main memory. The arg the caller pushed for
            # params[i] sits at offset (N - i) above the return address;
            # add the body's running stack_delta since extra pushes inside
            # the body shift everything up.
            if self.current_func is not None and expr.name in self.param_indices:
                idx = self.param_indices[expr.name]
                n_params = len(self.current_func.params)
                offset = (n_params - idx) + self.stack_delta
                self.emit(Opcode.LOAD_STACK, (reg, offset))
                return self.current_func.params[idx][1]
            if expr.name not in self.module.symbol_table:
                raise CompileError(f"undeclared variable {expr.name!r}")
            addr = self.module.symbol_table[expr.name]
            ty = self.module.symbol_types[expr.name]
            self.emit(Opcode.LOAD, (reg, addr))
            return ty

        if isinstance(expr, CallExpr):
            return self.compile_call(expr, reg)

        if isinstance(expr, BinaryOp):
            return self.compile_binop(expr, reg)

        if isinstance(expr, Compare):
            return self.compile_compare(expr, reg)

        if isinstance(expr, EmptyList):
            self.emit(Opcode.ALLOC, (reg, 0))
            return TypeCode.REF

        if isinstance(expr, IndexAccess):
            self._check_index_arity(expr.obj, len(expr.indices))
            if len(expr.indices) == 1:
                self.compile_expr_into(expr.obj, reg + 1)
                idx_type = self.compile_expr_into(expr.indices[0], reg + 2)
                self.coerce(idx_type, TypeCode.I64, reg + 2)
                self.emit(Opcode.LOAD_AT, (reg, reg + 1, reg + 2))
                return self._elem_type_of(expr.obj)
            if len(expr.indices) == 2:
                return self.compile_matrix_get(expr, reg)
            raise CompileError("only 1D or 2D indexing supported")

        if isinstance(expr, FieldAccess):
            return self.compile_field_access(expr, reg)

        if isinstance(expr, NewExpr):
            # Bare `new Person` in an expression context (e.g. inside a
            # function call argument). Allocates but doesn't record the
            # record type on any variable — the caller needs to assign it
            # to a variable for field access to work afterwards.
            return self.compile_new_record_inline(expr, reg)

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

        left_type = self.compile_expr_into(expr.left, left_reg)
        # A function CALL clobbers every register, so if computing the right
        # operand might invoke one, spill the left value to the stack across
        # the right-side compute and restore it afterwards.
        spill = self._contains_call(expr.right)
        if spill:
            self.emit_push(left_reg)
        right_type = self.compile_expr_into(expr.right, right_reg)
        if spill:
            self.emit(Opcode.POP, (left_reg,))
            self.stack_delta -= 1

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

    def compile_compare(self, expr: Compare, reg: int) -> TypeCode:
        """Typed comparison. Same promotion rules as arithmetic for numeric
        operands. Result is always BOOL."""
        left_reg  = reg + 1
        right_reg = reg + 2

        left_type = self.compile_expr_into(expr.left, left_reg)
        spill = self._contains_call(expr.right)
        if spill:
            self.emit_push(left_reg)
        right_type = self.compile_expr_into(expr.right, right_reg)
        if spill:
            self.emit(Opcode.POP, (left_reg,))
            self.stack_delta -= 1

        if left_type in NUMERIC_TYPES and right_type in NUMERIC_TYPES:
            cmp_type = _PROMOTE[(left_type, right_type)]
            self.coerce(left_type,  cmp_type, left_reg)
            self.coerce(right_type, cmp_type, right_reg)
        elif left_type == right_type:
            cmp_type = left_type
            # TEXT is an array at runtime — use REF opcodes for it.
            if cmp_type == TypeCode.TEXT:
                cmp_type = TypeCode.REF
        else:
            raise CompileError(
                f"cannot compare {left_type.name} with {right_type.name}"
            )

        key = (expr.op, cmp_type)
        if key not in _CMP_OPCODES:
            raise CompileError(
                f"comparison {expr.op!r} not supported on {cmp_type.name}"
            )
        self.emit(_CMP_OPCODES[key], (reg, left_reg, right_reg))
        return TypeCode.BOOL

    # ----- control flow -----

    def compile_if(self, stmt: IfStmt) -> None:
        """Compile `if cond then-body [else else-body] end` with forward
        jump-patching. `else if` chains parse as nested IfStmts in the
        else_block, and this method handles them naturally via recursion —
        each nested IfStmt emits its own jumps that get patched at its own
        end, propagating outward.
        """
        # Compile the condition into r0.
        cond_type = self.compile_expr_into(stmt.condition, reg=0)
        if cond_type != TypeCode.BOOL:
            raise CompileError(
                f"if condition must be BOOL, got {cond_type.name}"
            )

        # Emit JMPF to a placeholder — patched either to the else branch
        # (if there is one) or to the position after the then-body.
        jmpf_idx = self.emit_placeholder_jump(Opcode.JMPF, r_cond=0)

        for s in stmt.then_block:
            self.compile_stmt(s)

        if stmt.else_block is None:
            # No else — JMPF just skips the then-body.
            self.patch_jmp_target(jmpf_idx, self.current_pos())
            return

        # With else — emit an unconditional JMP past the else-body, then
        # patch the JMPF to land at the start of the else-body.
        jmp_end_idx = self.emit_placeholder_jump(Opcode.JMP)
        self.patch_jmp_target(jmpf_idx, self.current_pos())

        for s in stmt.else_block:
            self.compile_stmt(s)

        # Patch the "skip else" jump to land past the else-body.
        self.patch_jmp_target(jmp_end_idx, self.current_pos())

    # ----- loops -----
    #
    # All forms share one shape:
    #
    #   [init-code emitted before the loop]
    #   top:
    #     [optional condition check → JMPF end]
    #     <body>                    ← `stop` jumps to end; `skip` jumps to cont
    #   cont:
    #     [optional post/increment]
    #     JMP top
    #   end:
    #
    # compile_loop is the helper. Each `repeat <form>` fills in cond_emit /
    # post_emit with the form-specific pieces.

    def compile_loop(self, cond_emit, post_emit, body: list[Stmt]) -> None:
        """Generic while-like loop with an optional post step (for counter
        increments, etc.). `cond_emit` is called once to emit the condition
        check; it should put a BOOL into a register and return that register
        index. Pass None for an infinite loop. `post_emit` is called once to
        emit code that runs between the body and the condition re-check.
        """
        loop_top = self.current_pos()

        jmpf_end_idx: int | None = None
        if cond_emit is not None:
            cond_reg = cond_emit()
            jmpf_end_idx = self.emit_placeholder_jump(Opcode.JMPF, r_cond=cond_reg)

        ctx = LoopContext()
        self.loop_stack.append(ctx)
        for s in body:
            self.compile_stmt(s)
        self.loop_stack.pop()

        # Continue target — skip lands here; post runs before the re-check.
        continue_target = self.current_pos()
        if post_emit is not None:
            post_emit()

        self.emit(Opcode.JMP, (loop_top,))
        end_target = self.current_pos()

        if jmpf_end_idx is not None:
            self.patch_jmp_target(jmpf_end_idx, end_target)
        for idx in ctx.break_patches:
            self.patch_jmp_target(idx, end_target)
        for idx in ctx.continue_patches:
            self.patch_jmp_target(idx, continue_target)

    def compile_repeat_while(self, stmt: RepeatWhileStmt) -> None:
        def cond() -> int:
            cond_type = self.compile_expr_into(stmt.condition, reg=0)
            if cond_type != TypeCode.BOOL:
                raise CompileError(
                    f"while condition must be BOOL, got {cond_type.name}"
                )
            return 0
        self.compile_loop(cond_emit=cond, post_emit=None, body=stmt.body)

    def compile_repeat_times(self, stmt: RepeatTimesStmt) -> None:
        # Hidden counter + cached count (so a side-effectful count expression
        # only runs once).
        counter_addr = self.allocate_variable(self._hidden("c"), TypeCode.I64)
        count_addr   = self.allocate_variable(self._hidden("n"), TypeCode.I64)
        zero_addr    = self.allocate_constant(0)
        one_addr     = self.allocate_constant(1)

        # Pre-loop: counter = 0; count = <N>
        self.emit(Opcode.LOAD,  (0, zero_addr))
        self.emit(Opcode.STORE, (0, counter_addr))
        n_type = self.compile_expr_into(stmt.count, reg=0)
        self.coerce(n_type, TypeCode.I64, 0)
        self.emit(Opcode.STORE, (0, count_addr))

        def cond() -> int:
            self.emit(Opcode.LOAD,   (1, counter_addr))
            self.emit(Opcode.LOAD,   (2, count_addr))
            self.emit(Opcode.LT_I64, (0, 1, 2))
            return 0

        def post() -> None:
            self.emit(Opcode.LOAD,    (1, counter_addr))
            self.emit(Opcode.LOAD,    (2, one_addr))
            self.emit(Opcode.ADD_I64, (0, 1, 2))
            self.emit(Opcode.STORE,   (0, counter_addr))

        self.compile_loop(cond_emit=cond, post_emit=post, body=stmt.body)

    def compile_repeat_range(self, stmt: RepeatRangeStmt) -> None:
        # Loop variable is user-visible (`stmt.var`). Inclusive on both ends.
        end_addr = self.allocate_variable(self._hidden("end"), TypeCode.I64)
        one_addr = self.allocate_constant(1)

        # var = start
        start_type = self.compile_expr_into(stmt.start, reg=0)
        self.coerce(start_type, TypeCode.I64, 0)
        if stmt.var in self.module.symbol_table:
            var_addr = self.module.symbol_table[stmt.var]
        else:
            var_addr = self.allocate_variable(stmt.var, TypeCode.I64)
        self.emit(Opcode.STORE, (0, var_addr))

        # cached_end = end
        end_type = self.compile_expr_into(stmt.end, reg=0)
        self.coerce(end_type, TypeCode.I64, 0)
        self.emit(Opcode.STORE, (0, end_addr))

        def cond() -> int:
            self.emit(Opcode.LOAD,   (1, var_addr))
            self.emit(Opcode.LOAD,   (2, end_addr))
            self.emit(Opcode.LE_I64, (0, 1, 2))    # var <= end (inclusive)
            return 0

        def post() -> None:
            self.emit(Opcode.LOAD,    (1, var_addr))
            self.emit(Opcode.LOAD,    (2, one_addr))
            self.emit(Opcode.ADD_I64, (0, 1, 2))
            self.emit(Opcode.STORE,   (0, var_addr))

        self.compile_loop(cond_emit=cond, post_emit=post, body=stmt.body)

    def compile_stop(self) -> None:
        if not self.loop_stack:
            raise CompileError("'stop' used outside of a loop")
        idx = self.emit_placeholder_jump(Opcode.JMP)
        self.loop_stack[-1].break_patches.append(idx)

    def compile_skip(self) -> None:
        if not self.loop_stack:
            raise CompileError("'skip' used outside of a loop")
        idx = self.emit_placeholder_jump(Opcode.JMP)
        self.loop_stack[-1].continue_patches.append(idx)

    # ----- functions -----
    #
    # Calling convention:
    #   1. Caller compiles each argument and PUSHes it (in declaration order).
    #   2. Caller emits CALL — the VM pushes the return address and jumps.
    #   3. Callee body runs. Parameter reads use LOAD_STACK relative to SP;
    #      offsets are tracked at compile time via stack_delta so that any
    #      pushes the body itself does (e.g., for a nested call) shift the
    #      param offsets correctly.
    #   4. `return X` stores X into the shared return slot, then RET.
    #   5. Caller LOADs the result from the return slot, then DROPs the
    #      arguments. Result lives in the caller's destination register.
    #
    # The return slot is a single heap-memory address. It's clobbered on each
    # call, so callers must consume the result before another call happens —
    # which is the natural compilation order anyway (every CallExpr's result
    # is loaded into a register or pushed before any further code runs).

    def compile_function_def(self, fd: FunctionDef) -> None:
        if fd.name in self.functions:
            raise CompileError(f"function {fd.name!r} defined twice")
        if self.current_func is not None:
            raise CompileError(
                "nested function definitions aren't supported"
            )

        params: list[tuple[str, TypeCode]] = []
        for pname, ptype in fd.params:
            params.append((pname, self.typeref_to_elem_code(ptype)))
        return_type = (
            self.typeref_to_code(fd.return_type) if fd.return_type is not None else None
        )

        # Functions are emitted inline in source order, so we need to jump
        # over the body at runtime — otherwise straight-line execution from
        # the surrounding code would fall right into it.
        skip_idx = self.emit_placeholder_jump(Opcode.JMP)

        info = FunctionInfo(
            name=fd.name,
            entry=self.current_pos(),
            params=params,
            return_type=return_type,
        )
        # Register before the body so the function can call itself.
        self.functions[fd.name] = info

        prev_func, prev_indices, prev_delta = (
            self.current_func, self.param_indices, self.stack_delta
        )
        self.current_func = info
        self.param_indices = {name: i for i, (name, _) in enumerate(params)}
        self.stack_delta = 0

        for s in fd.body:
            self.compile_stmt(s)
        # Implicit RET — covers bodies that don't end with an explicit return.
        # If the body did end with `return`, this is unreachable but harmless.
        self.emit(Opcode.RET, ())

        self.current_func = prev_func
        self.param_indices = prev_indices
        self.stack_delta = prev_delta

        self.patch_jmp_target(skip_idx, self.current_pos())

    def compile_call(self, ce: CallExpr, reg: int) -> TypeCode:
        if ce.name not in self.functions:
            raise CompileError(f"unknown function {ce.name!r}")
        info = self.functions[ce.name]
        if len(ce.args) != len(info.params):
            raise CompileError(
                f"function {ce.name!r} expects {len(info.params)} argument(s), "
                f"got {len(ce.args)}"
            )

        # Evaluate and push each argument. Compiling into r0 is safe because
        # every arg's value is consumed by PUSH before the next arg starts;
        # nested calls inside an arg follow the same pattern internally.
        for i, arg in enumerate(ce.args):
            arg_type = self.compile_expr_into(arg, reg=0)
            param_type = info.params[i][1]
            if arg_type != param_type:
                if arg_type in NUMERIC_TYPES and param_type in NUMERIC_TYPES:
                    self.emit_convert(arg_type, param_type, src=0, dst=0)
                else:
                    raise CompileError(
                        f"argument {i + 1} of {ce.name!r}: cannot pass "
                        f"{arg_type.name} where {param_type.name} expected"
                    )
            self.emit_push(0)

        self.emit(Opcode.CALL, (info.entry,))

        # Pick up the result from the shared return slot, then drop the args.
        ret_slot = self._ensure_return_slot()
        self.emit(Opcode.LOAD, (reg, ret_slot))
        self.emit_drop(len(ce.args))

        return info.return_type if info.return_type is not None else TypeCode.NONE

    def compile_call_stmt(self, cs: CallStmt) -> None:
        # `call f with ...` as a statement: evaluate, discard result.
        self.compile_call(cs.call, reg=0)

    def compile_return(self, rs: ReturnStmt) -> None:
        if self.current_func is None:
            raise CompileError("'return' used outside of a function")

        if rs.value is not None:
            ret_type = self.compile_expr_into(rs.value, reg=0)
            declared = self.current_func.return_type
            if declared is not None and ret_type != declared:
                if ret_type in NUMERIC_TYPES and declared in NUMERIC_TYPES:
                    self.emit_convert(ret_type, declared, src=0, dst=0)
                else:
                    raise CompileError(
                        f"function {self.current_func.name!r} declared to return "
                        f"{declared.name}, got {ret_type.name}"
                    )
            ret_slot = self._ensure_return_slot()
            self.emit(Opcode.STORE, (0, ret_slot))

        self.emit(Opcode.RET, ())

    def _hidden(self, prefix: str) -> str:
        """Unique compiler-generated name so auto-allocated loop variables
        don't collide with user variables or each other across nested loops."""
        self._hidden_counter += 1
        return f"__{prefix}_{self._hidden_counter}"

    def emit_placeholder_jump(self, op: Opcode, r_cond: int | None = None) -> int:
        """Emit JMP/JMPF/JMPT with a placeholder target (-1). Returns the
        instruction index so the caller can patch it later."""
        idx = len(self.module.code)
        if op is Opcode.JMP:
            self.module.code.append(Instruction(op, (-1,)))
        elif op in (Opcode.JMPF, Opcode.JMPT):
            assert r_cond is not None, "JMPF/JMPT need a condition register"
            self.module.code.append(Instruction(op, (r_cond, -1)))
        else:
            raise CompileError(f"not a jump opcode: {op}")
        return idx

    def patch_jmp_target(self, idx: int, target: int) -> None:
        """Rewrite the placeholder target at `idx` with the real target."""
        instr = self.module.code[idx]
        if instr.op is Opcode.JMP:
            new_operands = (target,)
        elif instr.op in (Opcode.JMPF, Opcode.JMPT):
            new_operands = (instr.operands[0], target)
        else:
            raise CompileError(f"patch_jmp_target on non-jump opcode: {instr.op}")
        self.module.code[idx] = Instruction(instr.op, new_operands, instr.line)

    def current_pos(self) -> int:
        return len(self.module.code)

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

    def typeref_to_elem_code(self, type_ref: TypeRef) -> TypeCode:
        """Map a TypeRef used as an *element* type. Primitives map normally;
        nested collections / record names become REF (opaque pointer)."""
        if type_ref.name in PRIMITIVE_TYPES:
            return PRIMITIVE_TYPES[type_ref.name]
        return TypeCode.REF

    def infer_elem_type(self, expr) -> TypeCode | None:
        """For an expression that produces a container, return the type of
        its elements if the compiler can see it statically. Returns None
        when the element type isn't known (e.g., a function call returning
        a list)."""
        if isinstance(expr, EmptyList):
            return self.typeref_to_elem_code(expr.elem_type)
        if isinstance(expr, EmptyMatrix):
            return self.typeref_to_elem_code(expr.elem_type)
        if isinstance(expr, StringLit):
            # Text is an array of i8 character codes.
            return TypeCode.I8
        if isinstance(expr, VarRef):
            # Inherit the source variable's element type.
            return self.module.symbol_elem_types.get(expr.name)
        return None

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
