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

from bytecode import FieldLayout, Instruction, Module, Opcode, RecordLayout, TypeCode
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
class _ChainAccess:
    """Result of walking a chain of FieldAccess / IndexAccess steps from
    a root variable. The compiler can emit a single LOAD_AT/STORE_AT
    against a base pointer plus a computed offset.

    leaf_kind tells the emitter what kind of slot we'd be reading:
      - "primitive": a scalar slot (i64, text pointer, ref, ...)
      - "record":    the start of an inline record block (terminal for
                      read/write — the user must chain another `.field`)
      - "list":      the variable's value is a list pointer (terminal:
                      must `[i]` to step in)
      - "matrix":    same but multi-dim
    """
    root: str
    static_offset: int
    dynamic_terms: list  # list of (Expr, stride_int)
    leaf_kind: str
    leaf_type: "TypeCode | None" = None
    leaf_record_name: "str | None" = None
    matrix_shape: "tuple | None" = None


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
        # Forward calls — compile_call records (instr_idx, function_name) here
        # whenever it emits a CALL whose target hasn't been compiled yet.
        # Patched at the end of compile_program once every body's entry is
        # known.
        self.pending_call_patches: list[tuple[int, str]] = []
        # `repeat for each v in xs` over a list of records binds `v` as a
        # synthetic alias for `xs[idx]` rather than a real variable. The
        # chain walker dispatches through this map: when it sees `v` as the
        # root of a chain, it substitutes IndexAccess(VarRef(xs), [idx_var]).
        # Maps loop_var_name → (container_var_name, idx_var_name, record_name).
        self._view_aliases: dict[str, tuple[str, str, str]] = {}

    # ----- entry -----

    def compile_program(self, stmts: list[Stmt]) -> Module:
        # Pre-pass: register every top-level function name with a placeholder
        # entry of -1. Bodies are compiled in source order; when a body emits
        # a CALL to a function whose body hasn't been compiled yet, the CALL
        # uses the -1 placeholder and is recorded in pending_call_patches.
        # Patches are applied at the end once every entry is known.
        # This is what enables forward references and mutual recursion.
        for stmt in stmts:
            if isinstance(stmt, FunctionDef):
                self._predeclare_function(stmt)

        self.module.entry = 0
        for stmt in stmts:
            self.compile_stmt(stmt)
        self.emit(Opcode.HALT, ())

        for instr_idx, fname in self.pending_call_patches:
            entry = self.functions[fname].entry
            if entry == -1:
                raise CompileError(
                    f"function {fname!r} was declared but never defined"
                )
            old = self.module.code[instr_idx]
            self.module.code[instr_idx] = Instruction(old.op, (entry,), old.line)

        return self.module

    def _predeclare_function(self, fd: FunctionDef) -> None:
        if fd.name in self.functions:
            raise CompileError(f"function {fd.name!r} defined twice")
        params: list[tuple[str, TypeCode]] = []
        for pname, ptype in fd.params:
            params.append((pname, self.typeref_to_elem_code(ptype)))
        return_type = (
            self.typeref_to_code(fd.return_type) if fd.return_type is not None else None
        )
        self.functions[fd.name] = FunctionInfo(
            name=fd.name, entry=-1, params=params, return_type=return_type,
        )

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
            return self.compile_repeat_foreach(stmt)
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

        # Field assignment: `set p.field to value` (chains through nested
        # records and indexed access work via the chain walker too).
        if isinstance(target, FieldLValue):
            target_expr = FieldAccess(target.obj, target.field)
            return self.compile_chain_store(target_expr, stmt.value)

        # ----- set xs[i] to value  (1D)  -----
        # ----- set m[i, j] to value (2D matrix) -----
        # ----- set xs[i].field to value, set xs[i].a.b to value, etc. -----
        if isinstance(target, IndexLValue):
            target_expr = IndexAccess(target.obj, target.indices)
            # Special-case the single-character text-store: `set s[i] to "H"`
            # for a TEXT variable folds the char to its i8 code at compile
            # time. The chain walker would otherwise reject the type
            # mismatch.
            if (len(target.indices) == 1
                    and isinstance(target.obj, VarRef)
                    and self.module.symbol_types.get(target.obj.name) == TypeCode.TEXT
                    and isinstance(stmt.value, StringLit)):
                # Fold via the existing helper, which loads the i8 code
                # into r0 and emits STORE_AT against the text array.
                self.compile_char_or_value(target, stmt.value, reg=0)
                self.compile_expr_into(target.obj, reg=1)
                self.compile_expr_into(target.indices[0], reg=2)
                self.emit(Opcode.STORE_AT, (0, 1, 2))
                return
            return self.compile_chain_store(target_expr, stmt.value)

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
                # If the container's elements are records, track that too so
                # `xs[i].field` chains can compute the right strides and
                # field offsets. The element record name comes from the
                # original TypeRef at the EmptyList / EmptyMatrix node.
                elem_rec = self._infer_elem_record_name(stmt.value)
                if elem_rec is not None:
                    self.module.symbol_elem_record_types[target.name] = elem_rec
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
        # For lists of records, "appending a record" means appending K
        # consecutive slots from the source record block. That's K
        # LOAD_AT/APPEND pairs unrolled at compile time — no new opcode
        # needed.
        target_lv = stmt.target
        elem_rec_name = None
        if isinstance(target_lv, VarLValue):
            elem_rec_name = self.module.symbol_elem_record_types.get(target_lv.name)

        if elem_rec_name is not None:
            return self._compile_append_record(stmt, elem_rec_name)

        # Plain (primitive) list append.
        self.compile_expr_into(stmt.value, reg=0)
        target_type = self.compile_expr_into_lvalue(stmt.target, reg=1)
        if target_type != TypeCode.REF:
            raise CompileError(
                f"can only append to a list, not to a value of type "
                f"{target_type.name}"
            )
        self.emit(Opcode.APPEND, (1, 0))

    def _compile_append_record(self, stmt: AppendStmt, elem_rec_name: str) -> None:
        """`append <record-expr> to xs` where xs is a list-of-records.
        Copies the K slots of the source record into the end of the list."""
        layout = self.module.records[elem_rec_name]
        K = layout.size

        # Source: either a fresh `new Record` or a chain ending in a record.
        # In both cases we want a (ptr, base_offset) pair to read K slots
        # from. r2 = src ptr, r3 = src offset, r4 = scratch.
        src_expr = stmt.value
        if isinstance(src_expr, NewExpr):
            self.compile_new_record_inline(src_expr, reg=2)
            zero_addr = self.allocate_constant(0)
            self.emit(Opcode.LOAD, (3, zero_addr))
        else:
            src_ca = self._walk_chain(src_expr)
            if src_ca.leaf_kind != "record" or src_ca.leaf_record_name != elem_rec_name:
                raise CompileError(
                    f"cannot append value of record type "
                    f"{src_ca.leaf_record_name!r} to a list of "
                    f"{elem_rec_name!r}"
                )
            self._emit_chain_addr(src_ca, ptr_reg=2, off_reg=3, scratch=4)

        # Destination: the list pointer.
        self.compile_expr_into_lvalue(stmt.target, reg=1)

        one_addr = self.allocate_constant(1)
        for i in range(K):
            self.emit(Opcode.LOAD_AT, (0, 2, 3))     # r0 = src[off]
            self.emit(Opcode.APPEND, (1, 0))          # xs.append(r0)
            if i < K - 1:
                self.emit(Opcode.LOAD, (4, one_addr))
                self.emit(Opcode.ADD_I64, (3, 3, 4))

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

        # If the elements are records, allocate enough room for `cells * K`
        # slots so that record bodies sit packed inline.
        elem_rec_name = (
            em.elem_type.name
            if em.elem_type.name in self.module.records else None
        )
        elem_size = (
            self.module.records[elem_rec_name].size if elem_rec_name else 1
        )
        cells = shape[0] * shape[1]
        total = cells * elem_size
        self.emit(Opcode.ALLOC, (0, total))

        # Initialize each cell. ALLOC zero-fills with None, which would
        # surface as the literal "None" on `print g[i,j]`. Numeric cells
        # get the type's zero, text/ref cells get a fresh empty array,
        # record cells are zeroed recursively per-field.
        elem_type_code = self.typeref_to_elem_code(em.elem_type)
        if elem_rec_name is not None:
            layout = self.module.records[elem_rec_name]
            for cell in range(cells):
                self._init_record_at(
                    ptr_reg=0, layout=layout,
                    base_offset=cell * elem_size,
                    scratch_val=1, scratch_off=2,
                )
        else:
            if elem_type_code == TypeCode.TEXT or elem_type_code == TypeCode.REF:
                # Each cell gets its own fresh empty array.
                for slot in range(total):
                    self.emit(Opcode.ALLOC, (1, 0))
                    slot_addr = self.allocate_constant(slot)
                    self.emit(Opcode.LOAD, (2, slot_addr))
                    self.emit(Opcode.STORE_AT, (1, 0, 2))
            else:
                default = self._default_for_field(elem_type_code)
                default_addr = self.allocate_constant(default)
                self.emit(Opcode.LOAD, (1, default_addr))
                for slot in range(total):
                    slot_addr = self.allocate_constant(slot)
                    self.emit(Opcode.LOAD, (2, slot_addr))
                    self.emit(Opcode.STORE_AT, (1, 0, 2))

        if name in self.module.symbol_table:
            if self.module.symbol_types[name] != TypeCode.REF:
                raise CompileError(f"cannot re-type {name!r} as a matrix")
            addr = self.module.symbol_table[name]
        else:
            addr = self.allocate_variable(name, TypeCode.REF)

        # Shape and element type — purely compile-time metadata.
        self.module.symbol_shapes[name] = tuple(shape)
        self.module.symbol_elem_types[name] = elem_type_code
        if elem_rec_name is not None:
            self.module.symbol_elem_record_types[name] = elem_rec_name
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
        """Compile-time only. Builds a layout where each field carries its
        own offset and size in slots: primitives + text + ref take 1, and
        a nested record field takes the full size of that record (stored
        inline). Total record size is the sum. Records may only contain
        records that were defined earlier — recursion would mean infinite
        size and is rejected. The VM doesn't see any of this; it just gets
        LOAD_AT instructions with the offsets the compiler computed."""
        fields: list[FieldLayout] = []
        offset = 0
        for field_name, type_ref in stmt.fields:
            if type_ref.name in PRIMITIVE_TYPES:
                ftype = PRIMITIVE_TYPES[type_ref.name]
                fsize = 1
                rec_name: str | None = None
            elif type_ref.name in self.module.records:
                inner = self.module.records[type_ref.name]
                # Marker REF in `type` plus record_name carries the meaning
                # ("there's an inline record here"). The compiler's chain
                # walker uses record_name; the bare TypeCode is just a tag.
                ftype = TypeCode.REF
                fsize = inner.size
                rec_name = inner.name
            elif type_ref.name == stmt.name:
                raise CompileError(
                    f"record {stmt.name!r} cannot directly contain itself"
                )
            else:
                # Unknown primitive and unknown record — treat as opaque REF
                # (matches the previous typeref_to_elem_code fallback so
                # custom field types like `as MyEnum` don't error here).
                ftype = TypeCode.REF
                fsize = 1
                rec_name = None
            fields.append(FieldLayout(
                name=field_name, type=ftype, offset=offset,
                size=fsize, record_name=rec_name,
            ))
            offset += fsize
        self.module.records[stmt.name] = RecordLayout(stmt.name, fields)

    def compile_set_new_record(self, name: str, new_expr: NewExpr) -> None:
        """Allocate a fresh record block, recursively initialize all leaf
        slots (including nested-record fields stored inline), and bind the
        pointer to `name`."""
        record_name = new_expr.type_name
        if record_name not in self.module.records:
            raise CompileError(f"unknown record type {record_name!r}")
        layout = self.module.records[record_name]

        # r0 = new record block (record.size slots, ALLOC zero-fills)
        self.emit(Opcode.ALLOC, (0, layout.size))
        self._init_record_at(ptr_reg=0, layout=layout, base_offset=0,
                             scratch_val=1, scratch_off=2)

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
        `set`). Same ALLOC + recursive init, but the record-name binding has
        no variable to attach to — the caller is expected to either chain
        a field access (won't currently work without a binding) or copy the
        block into a list slot via `append`."""
        record_name = new_expr.type_name
        if record_name not in self.module.records:
            raise CompileError(f"unknown record type {record_name!r}")
        layout = self.module.records[record_name]

        self.emit(Opcode.ALLOC, (reg, layout.size))
        self._init_record_at(ptr_reg=reg, layout=layout, base_offset=0,
                             scratch_val=reg + 1, scratch_off=reg + 2)
        return TypeCode.REF

    def _init_record_at(self, ptr_reg: int, layout: RecordLayout,
                        base_offset: int, scratch_val: int,
                        scratch_off: int) -> None:
        """Fill in default values for every leaf slot of a record block at
        (ptr_reg, base_offset). Walks nested records recursively so each
        primitive slot gets its zero, each TEXT/REF slot gets a fresh empty
        array, and the inline sub-records' offsets are computed relative to
        the outer block."""
        for f in layout.fields:
            if f.record_name is not None:
                inner = self.module.records[f.record_name]
                self._init_record_at(
                    ptr_reg=ptr_reg, layout=inner,
                    base_offset=base_offset + f.offset,
                    scratch_val=scratch_val, scratch_off=scratch_off,
                )
                continue
            slot_offset = base_offset + f.offset
            offset_addr = self.allocate_constant(slot_offset)
            self.emit(Opcode.LOAD, (scratch_off, offset_addr))
            if f.type == TypeCode.TEXT or f.type == TypeCode.REF:
                # Fresh empty array per field so each instance has its own
                # backing store.
                self.emit(Opcode.ALLOC, (scratch_val, 0))
            else:
                default = self._default_for_field(f.type)
                default_addr = self.allocate_constant(default)
                self.emit(Opcode.LOAD, (scratch_val, default_addr))
            self.emit(Opcode.STORE_AT, (scratch_val, ptr_reg, scratch_off))

    # --- chain access (a.b.c, xs[i].field, g[i,j].field, etc.) ---

    def _walk_chain(self, expr) -> _ChainAccess:
        """Recursively walk an access chain (VarRef root + sequence of
        FieldAccess / IndexAccess steps), accumulating static field offsets
        and dynamic index*stride terms. Multi-pointer chains (e.g. a list
        nested inside a record) aren't handled — they'd require breaking
        into multiple LOAD_AT phases. Errors out clearly when it hits one.
        """
        if isinstance(expr, VarRef):
            name = expr.name
            # View-alias: `v` was bound by `for each v in xs` for a list of
            # records. Treat it as `xs[idx]` and walk that synthetic chain.
            if name in self._view_aliases:
                container, idx_name, _record = self._view_aliases[name]
                # Substitute: `v.field...` becomes `container[idx].field...`
                synthetic = IndexAccess(VarRef(container), [VarRef(idx_name)])
                return self._walk_chain(synthetic)
            # Parameters live on the value stack, not in main memory; chain
            # access through a parameter would need an entirely different
            # base-pointer story. Reject for now.
            if self.current_func is not None and name in self.param_indices:
                raise CompileError(
                    f"chain access through parameter {name!r} isn't supported yet"
                )
            if name not in self.module.symbol_table:
                raise CompileError(f"undeclared variable {name!r}")

            rec_name = self.module.symbol_record_types.get(name)
            if rec_name is not None:
                return _ChainAccess(
                    root=name, static_offset=0, dynamic_terms=[],
                    leaf_kind="record", leaf_record_name=rec_name,
                )
            shape = self.module.symbol_shapes.get(name)
            if shape is not None:
                return _ChainAccess(
                    root=name, static_offset=0, dynamic_terms=[],
                    leaf_kind="matrix",
                    leaf_type=self.module.symbol_elem_types.get(name, TypeCode.REF),
                    leaf_record_name=self.module.symbol_elem_record_types.get(name),
                    matrix_shape=shape,
                )
            if name in self.module.symbol_elem_types or \
               name in self.module.symbol_elem_record_types:
                return _ChainAccess(
                    root=name, static_offset=0, dynamic_terms=[],
                    leaf_kind="list",
                    leaf_type=self.module.symbol_elem_types.get(name, TypeCode.REF),
                    leaf_record_name=self.module.symbol_elem_record_types.get(name),
                )
            # Plain primitive variable.
            return _ChainAccess(
                root=name, static_offset=0, dynamic_terms=[],
                leaf_kind="primitive",
                leaf_type=self.module.symbol_types[name],
            )

        if isinstance(expr, FieldAccess):
            ca = self._walk_chain(expr.obj)
            if ca.leaf_kind != "record" or ca.leaf_record_name is None:
                raise CompileError(
                    f"field access on a non-record (chain leaf is {ca.leaf_kind})"
                )
            layout = self.module.records[ca.leaf_record_name]
            f = layout.find(expr.field)
            if f is None:
                raise CompileError(
                    f"record {ca.leaf_record_name!r} has no field {expr.field!r}"
                )
            ca.static_offset += f.offset
            if f.record_name is not None:
                ca.leaf_kind = "record"
                ca.leaf_record_name = f.record_name
                ca.leaf_type = None
            else:
                ca.leaf_kind = "primitive"
                ca.leaf_type = f.type
                ca.leaf_record_name = None
            return ca

        if isinstance(expr, IndexAccess):
            ca = self._walk_chain(expr.obj)
            if ca.leaf_kind == "list":
                if len(expr.indices) != 1:
                    raise CompileError("expected one index for a list")
                stride = (
                    self.module.records[ca.leaf_record_name].size
                    if ca.leaf_record_name else 1
                )
                ca.dynamic_terms.append((expr.indices[0], stride))
            elif ca.leaf_kind == "matrix":
                if len(expr.indices) != len(ca.matrix_shape):
                    raise CompileError(
                        f"matrix has {len(ca.matrix_shape)} dimensions; "
                        f"got {len(expr.indices)} index(es)"
                    )
                stride = (
                    self.module.records[ca.leaf_record_name].size
                    if ca.leaf_record_name else 1
                )
                # row-major: outermost dim has the largest stride
                trailing = stride
                strides = []
                for dim in reversed(ca.matrix_shape):
                    strides.append(trailing)
                    trailing *= dim
                strides.reverse()  # now strides[i] is the stride for dim i
                for idx_expr, s in zip(expr.indices, strides):
                    ca.dynamic_terms.append((idx_expr, s))
            else:
                raise CompileError(
                    f"cannot index a {ca.leaf_kind} (expected list or matrix)"
                )
            # After indexing, the leaf becomes the element type.
            if ca.leaf_record_name is not None:
                ca.leaf_kind = "record"
                # leaf_record_name already set
            else:
                ca.leaf_kind = "primitive"
                # leaf_type already set
            ca.matrix_shape = None
            return ca

        raise CompileError(
            f"unsupported expression in access chain: {type(expr).__name__}"
        )

    def _emit_chain_addr(self, ca: "_ChainAccess",
                         ptr_reg: int, off_reg: int, scratch: int) -> None:
        """Materialize the pointer in `ptr_reg` and the slot offset in
        `off_reg`. `scratch` and `scratch+1` are used for index*stride math.
        Caller must ensure these registers don't collide with anything live.
        """
        addr = self.module.symbol_table[ca.root]
        self.emit(Opcode.LOAD, (ptr_reg, addr))

        static_addr = self.allocate_constant(ca.static_offset)
        self.emit(Opcode.LOAD, (off_reg, static_addr))

        for idx_expr, stride in ca.dynamic_terms:
            idx_type = self.compile_expr_into(idx_expr, scratch)
            self.coerce(idx_type, TypeCode.I64, scratch)
            if stride != 1:
                stride_addr = self.allocate_constant(stride)
                self.emit(Opcode.LOAD, (scratch + 1, stride_addr))
                self.emit(Opcode.MUL_I64, (scratch, scratch, scratch + 1))
            self.emit(Opcode.ADD_I64, (off_reg, off_reg, scratch))

    def compile_chain_load(self, expr, reg: int) -> TypeCode:
        """Read the leaf slot of an access chain into `reg`."""
        ca = self._walk_chain(expr)
        if ca.leaf_kind == "record":
            raise CompileError(
                f"cannot read whole record by value; access a field instead"
            )
        if ca.leaf_kind in ("list", "matrix"):
            # Falling back to `LOAD root_addr` would be a plain pointer copy;
            # only happens if someone walks a bare VarRef of a list through
            # this path, which the caller shouldn't do — IndexAccess routes
            # here, but a bare list VarRef goes through compile_expr_into's
            # VarRef branch.
            raise CompileError(
                f"cannot read {ca.leaf_kind} as a value; index or chain into it"
            )
        self._emit_chain_addr(ca, ptr_reg=reg + 1, off_reg=reg + 2, scratch=reg + 3)
        self.emit(Opcode.LOAD_AT, (reg, reg + 1, reg + 2))
        return ca.leaf_type or TypeCode.REF

    def compile_chain_store(self, target_expr, value_expr) -> None:
        """Compile a write to the leaf slot of an access chain."""
        ca = self._walk_chain(target_expr)

        # Whole-record assignment unrolls into K LOAD_AT/STORE_AT pairs —
        # supported for the source patterns we actually need (a record
        # variable, or another chain ending in a record). For now, only
        # handle the common case `set xs[i] to p` / `set p.home to other`.
        if ca.leaf_kind == "record":
            return self._compile_record_copy(ca, value_expr)
        if ca.leaf_kind in ("list", "matrix"):
            raise CompileError(
                f"cannot assign whole {ca.leaf_kind}; assign into a slot or "
                f"a field"
            )

        # Compile value FIRST into r0 — its computation may use r1+ as
        # scratch, and we don't want to clobber the pointer/offset we set
        # up next.
        value_type = self.compile_expr_into(value_expr, reg=0)
        if ca.leaf_type is not None and value_type != ca.leaf_type:
            if value_type in NUMERIC_TYPES and ca.leaf_type in NUMERIC_TYPES:
                self.emit_convert(value_type, ca.leaf_type, src=0, dst=0)

        self._emit_chain_addr(ca, ptr_reg=1, off_reg=2, scratch=3)
        self.emit(Opcode.STORE_AT, (0, 1, 2))

    def _compile_record_copy(self, dst_ca: "_ChainAccess", src_expr) -> None:
        """Whole-record assignment, unrolled at compile time. The destination
        is an inline record slot reached via `dst_ca`; the source must be a
        chain that also ends at an inline record (or a `new Record` block)
        of the same record type. We do K LOAD_AT/STORE_AT pairs."""
        dst_record = dst_ca.leaf_record_name
        layout = self.module.records[dst_record]
        K = layout.size

        # Compile the source into a (ptr_reg, off_reg). The source can be a
        # chain ending in an inline record, OR a fresh `new Record` block
        # whose pointer lives in some register — in both cases what we need
        # is "where do K consecutive slots live."
        if isinstance(src_expr, NewExpr):
            # Allocate the new record into r1, with no offset.
            self.compile_new_record_inline(src_expr, reg=1)
            zero_addr = self.allocate_constant(0)
            self.emit(Opcode.LOAD, (2, zero_addr))
        else:
            src_ca = self._walk_chain(src_expr)
            if src_ca.leaf_kind != "record" or src_ca.leaf_record_name != dst_record:
                raise CompileError(
                    f"cannot assign value of record type "
                    f"{src_ca.leaf_record_name!r} to a slot of type "
                    f"{dst_record!r}"
                )
            self._emit_chain_addr(src_ca, ptr_reg=1, off_reg=2, scratch=3)

        # Materialize destination ptr and base offset.
        self._emit_chain_addr(dst_ca, ptr_reg=4, off_reg=5, scratch=6)

        # K unrolled copies. r0 = scratch slot value. For each i in 0..K-1:
        # off_src = src_off + i; off_dst = dst_off + i.
        # We bump src_off and dst_off in-place (cheaper than recomputing
        # from a constant each iteration).
        one_addr = self.allocate_constant(1)
        for i in range(K):
            self.emit(Opcode.LOAD_AT, (0, 1, 2))     # r0 = src[off]
            self.emit(Opcode.STORE_AT, (0, 4, 5))    # dst[off] = r0
            if i < K - 1:
                self.emit(Opcode.LOAD, (3, one_addr))
                self.emit(Opcode.ADD_I64, (2, 2, 3))
                self.emit(Opcode.ADD_I64, (5, 5, 3))

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
            return self.compile_chain_load(expr, reg)

        if isinstance(expr, FieldAccess):
            return self.compile_chain_load(expr, reg)

        if isinstance(expr, NewExpr):
            # Bare `new Person` in an expression context (e.g. inside a
            # function call argument). Allocates but doesn't record the
            # record type on any variable — the caller needs to assign it
            # to a variable for field access to work afterwards.
            return self.compile_new_record_inline(expr, reg)

        if isinstance(expr, LengthExpr):
            self.compile_expr_into(expr.value, reg + 1)
            self.emit(Opcode.LEN, (reg, reg + 1))
            # If the container holds records, the raw slot count is N*K.
            # Divide by K so the user sees the record count.
            elem_rec = None
            if isinstance(expr.value, VarRef):
                elem_rec = self.module.symbol_elem_record_types.get(expr.value.name)
            if elem_rec is not None:
                K = self.module.records[elem_rec].size
                if K > 1:
                    k_addr = self.allocate_constant(K)
                    self.emit(Opcode.LOAD, (reg + 1, k_addr))
                    # No integer-divide opcode yet; route through F64.
                    self.emit_convert(TypeCode.I64, TypeCode.F64, src=reg, dst=reg)
                    self.emit_convert(TypeCode.I64, TypeCode.F64, src=reg + 1, dst=reg + 1)
                    self.emit(Opcode.DIV_F64, (reg, reg, reg + 1))
                    self.emit_convert(TypeCode.F64, TypeCode.I64, src=reg, dst=reg)
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

    def compile_repeat_foreach(self, stmt: RepeatForEachStmt) -> None:
        """`repeat for each v in xs` — iterate over a list or matrix.

        For primitive elements: the loop variable holds each successive
        element value, taken via LOAD_AT against the container's pointer.
        For record elements: the loop variable behaves as if it were
        `xs[i]` — chain accesses through it (e.g. `v.field`) translate to
        accesses on the underlying container at the right slot offset.
        """
        if not isinstance(stmt.iterable, VarRef):
            raise CompileError("'for each' iterable must be a variable")
        name = stmt.iterable.name
        if name not in self.module.symbol_table:
            raise CompileError(f"undeclared variable {name!r}")

        elem_rec_name = self.module.symbol_elem_record_types.get(name)
        # A list/matrix is iterable; a primitive variable isn't. We detect
        # "iterable" by the presence of an element type in either map.
        if (name not in self.module.symbol_elem_types
                and elem_rec_name is None):
            raise CompileError(f"{name!r} isn't iterable")

        elem_type = self.module.symbol_elem_types.get(name, TypeCode.REF)
        K = self.module.records[elem_rec_name].size if elem_rec_name else 1

        list_addr = self.module.symbol_table[name]
        idx_var = self._hidden("idx")
        len_var = self._hidden("len")
        idx_addr = self.allocate_variable(idx_var, TypeCode.I64)
        len_addr = self.allocate_variable(len_var, TypeCode.I64)
        zero_addr = self.allocate_constant(0)
        one_addr = self.allocate_constant(1)

        # idx = 0
        self.emit(Opcode.LOAD,  (0, zero_addr))
        self.emit(Opcode.STORE, (0, idx_addr))

        # cached_len = len(container) // K  (number of elements, not slots)
        self.emit(Opcode.LOAD, (1, list_addr))
        self.emit(Opcode.LEN,  (0, 1))
        if K > 1:
            k_addr = self.allocate_constant(K)
            self.emit(Opcode.LOAD, (1, k_addr))
            self.emit_convert(TypeCode.I64, TypeCode.F64, src=0, dst=0)
            self.emit_convert(TypeCode.I64, TypeCode.F64, src=1, dst=1)
            self.emit(Opcode.DIV_F64, (0, 0, 1))
            self.emit_convert(TypeCode.F64, TypeCode.I64, src=0, dst=0)
        self.emit(Opcode.STORE, (0, len_addr))

        # Bind the loop variable. For primitive elements, `v` is a real
        # variable holding a copy of the current element. For record
        # elements, register `v` as a view-alias for `xs[idx]`; chain
        # accesses through it route through that synthetic IndexAccess.
        prev_view = self._view_aliases.get(stmt.var)
        if elem_rec_name is not None:
            self._view_aliases[stmt.var] = (name, idx_var, elem_rec_name)
            var_addr = None
        else:
            if stmt.var in self.module.symbol_table:
                if self.module.symbol_types[stmt.var] != elem_type:
                    raise CompileError(
                        f"loop variable {stmt.var!r} already has a different type"
                    )
                var_addr = self.module.symbol_table[stmt.var]
            else:
                var_addr = self.allocate_variable(stmt.var, elem_type)

        # Inline the loop (compile_loop's helper doesn't have a body-pre
        # hook, and we need to refresh `v` from `xs[idx]` at the start of
        # each iteration).
        loop_top = self.current_pos()

        # Condition: idx < len → r0
        self.emit(Opcode.LOAD,   (1, idx_addr))
        self.emit(Opcode.LOAD,   (2, len_addr))
        self.emit(Opcode.LT_I64, (0, 1, 2))
        jmpf_idx = self.emit_placeholder_jump(Opcode.JMPF, r_cond=0)

        # Body-pre: for primitive elements, v = container[idx]. Records
        # don't need a refresh — the view-alias dispatches directly.
        if elem_rec_name is None:
            self.emit(Opcode.LOAD,    (1, list_addr))
            self.emit(Opcode.LOAD,    (2, idx_addr))
            self.emit(Opcode.LOAD_AT, (0, 1, 2))
            self.emit(Opcode.STORE,   (0, var_addr))

        ctx = LoopContext()
        self.loop_stack.append(ctx)
        for s in stmt.body:
            self.compile_stmt(s)
        self.loop_stack.pop()

        cont_target = self.current_pos()

        # idx += 1
        self.emit(Opcode.LOAD,    (1, idx_addr))
        self.emit(Opcode.LOAD,    (2, one_addr))
        self.emit(Opcode.ADD_I64, (0, 1, 2))
        self.emit(Opcode.STORE,   (0, idx_addr))

        self.emit(Opcode.JMP, (loop_top,))
        end_target = self.current_pos()

        self.patch_jmp_target(jmpf_idx, end_target)
        for jx in ctx.break_patches:
            self.patch_jmp_target(jx, end_target)
        for jx in ctx.continue_patches:
            self.patch_jmp_target(jx, cont_target)

        # Restore the previous view-alias (for nested for-each over
        # records, which would shadow the outer alias).
        if elem_rec_name is not None:
            if prev_view is None:
                self._view_aliases.pop(stmt.var, None)
            else:
                self._view_aliases[stmt.var] = prev_view

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
        if self.current_func is not None:
            raise CompileError(
                "nested function definitions aren't supported"
            )
        # The pre-pass in compile_program registered this function with
        # entry = -1; here we fill in the real entry once the body's
        # position is known. If we got here without a pre-registration,
        # this is a nested FunctionDef — caught above.
        if fd.name not in self.functions:
            self._predeclare_function(fd)
        info = self.functions[fd.name]
        if info.entry != -1:
            raise CompileError(f"function {fd.name!r} defined twice")

        # Functions are emitted inline in source order, so we need to jump
        # over the body at runtime — otherwise straight-line execution from
        # the surrounding code would fall right into it.
        skip_idx = self.emit_placeholder_jump(Opcode.JMP)

        info.entry = self.current_pos()

        prev_func, prev_indices, prev_delta = (
            self.current_func, self.param_indices, self.stack_delta
        )
        self.current_func = info
        self.param_indices = {name: i for i, (name, _) in enumerate(info.params)}
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

        # If the callee's body hasn't been compiled yet, we don't know its
        # entry. Emit CALL with a placeholder and remember the instruction
        # index so compile_program can patch it after every body is laid
        # out.
        call_idx = self.current_pos()
        self.emit(Opcode.CALL, (info.entry,))
        if info.entry == -1:
            self.pending_call_patches.append((call_idx, ce.name))

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

    def _infer_elem_record_name(self, expr) -> str | None:
        """If `expr` produces a list/matrix whose elements are a known
        record type, return that record's name. Otherwise None."""
        if isinstance(expr, EmptyList):
            tname = expr.elem_type.name
            if tname in self.module.records:
                return tname
        if isinstance(expr, EmptyMatrix):
            tname = expr.elem_type.name
            if tname in self.module.records:
                return tname
        if isinstance(expr, VarRef):
            return self.module.symbol_elem_record_types.get(expr.name)
        return None

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
