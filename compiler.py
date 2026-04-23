from bytecode import (
    Const, Function, Instruction, Module, Opcode, Operand, RecordLayout,
    SSA, TypeCode,
)
from parser import (
    AddStmt, AppendStmt, BinaryOp, BoolLit, CallExpr, CallStmt, ColumnsExpr,
    Compare, DivideStmt, EmptyList, EmptyMap, EmptyMatrix, Expr, FieldAccess,
    FieldLValue, FunctionDef, IfStmt, IndexAccess, IndexLValue, LengthExpr,
    LValue, MultiplyStmt, NewExpr, NoneLit, NumberLit, PrintStmt, RecordDef,
    RepeatForEachStmt, RepeatRangeStmt, RepeatTimesStmt, RepeatWhileStmt,
    ReturnStmt, RowsExpr, SetStmt, SkipStmt, Stmt, StopStmt, StringLit,
    SubtractStmt, TypeRef, VarLValue, VarRef,
)


class CompileError(Exception):
    pass


# Map of primitive-type names (from AST TypeRef) to TypeCode.
PRIMITIVE_TYPES = {
    "i32": TypeCode.I32,
    "i64": TypeCode.I64,
    "integer": TypeCode.I64,
    "f32": TypeCode.F32,
    "f64": TypeCode.F64,
    "float": TypeCode.F64,
    "number": TypeCode.F64,
    "bool": TypeCode.BOOL,
    "text": TypeCode.TEXT,
}


class LoopContext:
    """Tracks jump targets for `stop` (break) and `skip` (continue)."""
    def __init__(self) -> None:
        self.break_patches: list[int] = []     # indices of JMP ops to patch to loop end
        self.continue_patches: list[int] = []  # indices of JMP ops to patch to loop "continue" point


class Compiler:
    def __init__(self) -> None:
        self.module: Module = Module()
        self.fn: Function | None = None
        self.local_slots: dict[str, int] = {}
        self.loop_stack: list[LoopContext] = []
        # function signatures, collected before any body is compiled
        self.function_sigs: dict[str, tuple[list[TypeCode], TypeCode | None]] = {}

    # ---- entry ----

    def compile_program(self, stmts: list[Stmt]) -> Module:
        # Pass 1: collect all record definitions and function signatures
        main_body: list[Stmt] = []
        func_defs: list[FunctionDef] = []
        for stmt in stmts:
            if isinstance(stmt, RecordDef):
                self.declare_record(stmt)
            elif isinstance(stmt, FunctionDef):
                self.declare_function(stmt)
                func_defs.append(stmt)
            else:
                main_body.append(stmt)

        # Pass 2: compile function bodies
        for fd in func_defs:
            self.compile_function_def(fd)

        # Pass 3: compile main
        main = Function(name="main")
        self.fn = main
        self.local_slots = {}
        self.loop_stack = []
        for stmt in main_body:
            self.compile_stmt(stmt)
        self.emit(Opcode.RETN, ())
        self.module.functions["main"] = main

        return self.module

    # ---- declarations ----

    def declare_record(self, stmt: RecordDef) -> None:
        fields = [(name, self.typeref_to_code(ty)) for name, ty in stmt.fields]
        self.module.records[stmt.name] = RecordLayout(stmt.name, fields)

    def declare_function(self, stmt: FunctionDef) -> None:
        param_types = [self.typeref_to_code(t) for _, t in stmt.params]
        return_type = self.typeref_to_code(stmt.return_type) if stmt.return_type else None
        self.function_sigs[stmt.name] = (param_types, return_type)

    def compile_function_def(self, stmt: FunctionDef) -> None:
        param_types, return_type = self.function_sigs[stmt.name]
        fn = Function(
            name=stmt.name,
            param_names=[name for name, _ in stmt.params],
            param_types=param_types,
            return_type=return_type,
        )
        # Parameters live in the first local slots
        self.fn = fn
        self.local_slots = {}
        self.loop_stack = []
        for name, ty in zip(fn.param_names, fn.param_types):
            self.declare_local(name, ty)
        for body_stmt in stmt.body:
            self.compile_stmt(body_stmt)
        # Implicit `return none` at end if no explicit return
        last = fn.instructions[-1] if fn.instructions else None
        if last is None or last.op not in (Opcode.RET, Opcode.RETN):
            self.emit(Opcode.RETN, ())
        self.module.functions[stmt.name] = fn

    # ---- statements ----

    def compile_stmt(self, stmt: Stmt) -> None:
        if isinstance(stmt, SetStmt):       return self.compile_set(stmt)
        if isinstance(stmt, AddStmt):       return self.compile_compound(stmt.target, "plus", stmt.amount)
        if isinstance(stmt, SubtractStmt):  return self.compile_compound(stmt.target, "minus", stmt.amount)
        if isinstance(stmt, MultiplyStmt):  return self.compile_compound(stmt.target, "times", stmt.factor)
        if isinstance(stmt, DivideStmt):    return self.compile_compound(stmt.target, "divided", stmt.divisor)
        if isinstance(stmt, AppendStmt):    return self.compile_append(stmt)
        if isinstance(stmt, PrintStmt):     return self.compile_print(stmt)
        if isinstance(stmt, IfStmt):        return self.compile_if(stmt)
        if isinstance(stmt, RepeatTimesStmt):   return self.compile_repeat_times(stmt)
        if isinstance(stmt, RepeatRangeStmt):   return self.compile_repeat_range(stmt)
        if isinstance(stmt, RepeatWhileStmt):   return self.compile_repeat_while(stmt)
        if isinstance(stmt, RepeatForEachStmt): return self.compile_repeat_foreach(stmt)
        if isinstance(stmt, StopStmt):      return self.compile_stop()
        if isinstance(stmt, SkipStmt):      return self.compile_skip()
        if isinstance(stmt, ReturnStmt):    return self.compile_return(stmt)
        if isinstance(stmt, CallStmt):
            op, _ty = self.compile_call(stmt.call)
            return
        if isinstance(stmt, (FunctionDef, RecordDef)):
            return  # already handled in passes 1/2
        raise CompileError(f"unsupported statement: {type(stmt).__name__}")

    # --- assignment / compound ---

    def compile_set(self, stmt: SetStmt) -> None:
        target = stmt.target
        value_op, value_ty = self.compile_expr(stmt.value)

        if isinstance(target, VarLValue):
            name = target.name
            if name in self.local_slots:
                slot = self.local_slots[name]
                expected = self.fn.local_types[slot]
                value_op = self.coerce(value_op, value_ty, expected)
            else:
                slot = self.declare_local(name, value_ty)
            self.emit(Opcode.STORE_LOCAL, (slot, value_op))
            return

        if isinstance(target, FieldLValue):
            base_op, base_ty = self.compile_expr(target.obj)
            # we don't type-check field types deeply in v1; they're stored as REF
            self.emit(Opcode.SET_FIELD, (base_op, target.field, value_op))
            return

        if isinstance(target, IndexLValue):
            base_op, base_ty = self.compile_expr(target.obj)
            idx_ops = [self.compile_expr(i)[0] for i in target.indices]
            if len(idx_ops) == 1:
                # list or map — infer which from base type; at runtime, the VM handles both
                self.emit(Opcode.LIST_SET, (base_op, idx_ops[0], value_op))
                # note: VM's LIST_SET handles both lists and maps (via dict instance check)
            else:
                self.emit(Opcode.MATRIX_SET, (base_op, tuple(idx_ops), value_op))
            return

        raise CompileError(f"unsupported assignment target: {type(target).__name__}")

    def compile_compound(self, target: LValue, op_str: str, amount_expr: Expr) -> None:
        # compound assignment: target <op>= amount
        # Reads current value, applies op, stores back.
        current_op, current_ty = self.load_lvalue(target)
        amount_op, amount_ty = self.compile_expr(amount_expr)

        if op_str == "divided":
            result_ty = TypeCode.F64
        else:
            result_ty = self.promote(current_ty, amount_ty)

        current_op = self.convert(current_op, current_ty, result_ty)
        amount_op = self.convert(amount_op, amount_ty, result_ty)
        opcode = self.binop_opcode(op_str, result_ty)
        new_idx = self.emit_ssa(opcode, (current_op, amount_op), result_ty)
        new_op = SSA(new_idx)

        # Storing back — if target is a typed local, convert to its type
        if isinstance(target, VarLValue):
            slot = self.local_slots[target.name]
            slot_ty = self.fn.local_types[slot]
            new_op = self.coerce(new_op, result_ty, slot_ty)
            self.emit(Opcode.STORE_LOCAL, (slot, new_op))
        elif isinstance(target, FieldLValue):
            base_op, _ = self.compile_expr(target.obj)
            self.emit(Opcode.SET_FIELD, (base_op, target.field, new_op))
        elif isinstance(target, IndexLValue):
            base_op, _ = self.compile_expr(target.obj)
            idx_ops = [self.compile_expr(i)[0] for i in target.indices]
            if len(idx_ops) == 1:
                self.emit(Opcode.LIST_SET, (base_op, idx_ops[0], new_op))
            else:
                self.emit(Opcode.MATRIX_SET, (base_op, tuple(idx_ops), new_op))

    def compile_append(self, stmt: AppendStmt) -> None:
        value_op, _ty = self.compile_expr(stmt.value)
        target_op, _tgt_ty = self.load_lvalue(stmt.target)
        self.emit(Opcode.APPEND, (target_op, value_op))

    # --- I/O ---

    def compile_print(self, stmt: PrintStmt) -> None:
        parts = tuple(self.compile_expr(p)[0] for p in stmt.parts)
        if len(parts) == 1:
            self.emit(Opcode.PRINT, (parts[0],))
        else:
            self.emit(Opcode.PRINT_MANY, (parts,))

    # --- control flow ---

    def compile_if(self, stmt: IfStmt) -> None:
        cond_op, cond_ty = self.compile_expr(stmt.condition)
        # JMPF: jump-if-false. Placeholder target (-1), patched after then-block.
        jmpf_idx = self.emit(Opcode.JMPF, (cond_op, -1))

        for s in stmt.then_block:
            self.compile_stmt(s)

        if stmt.else_block is None:
            # patch JMPF to land after then-block
            self.patch_jmp(jmpf_idx, self.current_pos())
            return

        # Then-block end: jump to past the else-block (unconditional)
        jmp_end_idx = self.emit(Opcode.JMP, (-1,))
        # Patch JMPF to land at start of else-block (now)
        self.patch_jmp(jmpf_idx, self.current_pos())

        for s in stmt.else_block:
            self.compile_stmt(s)

        # Patch the unconditional JMP to land after else-block
        self.patch_jmp(jmp_end_idx, self.current_pos())

    def compile_repeat_times(self, stmt: RepeatTimesStmt) -> None:
        # Compile count into an I64
        count_op, count_ty = self.compile_expr(stmt.count)
        count_op = self.coerce(count_op, count_ty, TypeCode.I64)
        count_slot = self.declare_temp(TypeCode.I64, "__count")
        self.emit(Opcode.STORE_LOCAL, (count_slot, count_op))

        # i = 0
        i_slot = self.declare_temp(TypeCode.I64, "__i")
        self.emit(Opcode.STORE_LOCAL, (i_slot, Const(TypeCode.I64, 0)))

        loop_top = self.current_pos()
        # if i >= count: jump to end
        i_ssa = self.emit_ssa(Opcode.LOAD_LOCAL, (i_slot,), TypeCode.I64)
        count_ssa = self.emit_ssa(Opcode.LOAD_LOCAL, (count_slot,), TypeCode.I64)
        cmp_ssa = self.emit_ssa(Opcode.GE_I64, (SSA(i_ssa), SSA(count_ssa)), TypeCode.BOOL)
        jmpt_idx = self.emit(Opcode.JMPT, (SSA(cmp_ssa), -1))

        # enter loop context
        ctx = LoopContext()
        self.loop_stack.append(ctx)
        continue_target = None  # filled after body

        for s in stmt.body:
            self.compile_stmt(s)

        # continue target (where `skip` jumps to) — increment then back to top
        continue_pos = self.current_pos()
        i_ssa2 = self.emit_ssa(Opcode.LOAD_LOCAL, (i_slot,), TypeCode.I64)
        inc_ssa = self.emit_ssa(Opcode.ADD_I64, (SSA(i_ssa2), Const(TypeCode.I64, 1)), TypeCode.I64)
        self.emit(Opcode.STORE_LOCAL, (i_slot, SSA(inc_ssa)))
        self.emit(Opcode.JMP, (loop_top,))

        end_pos = self.current_pos()
        self.patch_jmp(jmpt_idx, end_pos)

        # Patch stop/skip jumps
        for idx in ctx.break_patches:
            self.patch_jmp(idx, end_pos)
        for idx in ctx.continue_patches:
            self.patch_jmp(idx, continue_pos)
        self.loop_stack.pop()

    def compile_repeat_range(self, stmt: RepeatRangeStmt) -> None:
        start_op, start_ty = self.compile_expr(stmt.start)
        start_op = self.coerce(start_op, start_ty, TypeCode.I64)
        end_op, end_ty = self.compile_expr(stmt.end)
        end_op = self.coerce(end_op, end_ty, TypeCode.I64)

        end_slot = self.declare_temp(TypeCode.I64, "__end")
        self.emit(Opcode.STORE_LOCAL, (end_slot, end_op))

        # i = start
        if stmt.var in self.local_slots:
            i_slot = self.local_slots[stmt.var]
        else:
            i_slot = self.declare_local(stmt.var, TypeCode.I64)
        self.emit(Opcode.STORE_LOCAL, (i_slot, start_op))

        loop_top = self.current_pos()
        # if i > end: done
        i_ssa = self.emit_ssa(Opcode.LOAD_LOCAL, (i_slot,), TypeCode.I64)
        end_ssa = self.emit_ssa(Opcode.LOAD_LOCAL, (end_slot,), TypeCode.I64)
        cmp_ssa = self.emit_ssa(Opcode.GT_I64, (SSA(i_ssa), SSA(end_ssa)), TypeCode.BOOL)
        jmpt_idx = self.emit(Opcode.JMPT, (SSA(cmp_ssa), -1))

        ctx = LoopContext()
        self.loop_stack.append(ctx)

        for s in stmt.body:
            self.compile_stmt(s)

        continue_pos = self.current_pos()
        i_ssa2 = self.emit_ssa(Opcode.LOAD_LOCAL, (i_slot,), TypeCode.I64)
        inc_ssa = self.emit_ssa(Opcode.ADD_I64, (SSA(i_ssa2), Const(TypeCode.I64, 1)), TypeCode.I64)
        self.emit(Opcode.STORE_LOCAL, (i_slot, SSA(inc_ssa)))
        self.emit(Opcode.JMP, (loop_top,))

        end_pos = self.current_pos()
        self.patch_jmp(jmpt_idx, end_pos)
        for idx in ctx.break_patches:
            self.patch_jmp(idx, end_pos)
        for idx in ctx.continue_patches:
            self.patch_jmp(idx, continue_pos)
        self.loop_stack.pop()

    def compile_repeat_while(self, stmt: RepeatWhileStmt) -> None:
        loop_top = self.current_pos()
        cond_op, _cty = self.compile_expr(stmt.condition)
        jmpf_idx = self.emit(Opcode.JMPF, (cond_op, -1))

        ctx = LoopContext()
        self.loop_stack.append(ctx)
        for s in stmt.body:
            self.compile_stmt(s)
        # continue target is back at the top (re-check condition)
        self.emit(Opcode.JMP, (loop_top,))

        end_pos = self.current_pos()
        self.patch_jmp(jmpf_idx, end_pos)
        for idx in ctx.break_patches:
            self.patch_jmp(idx, end_pos)
        for idx in ctx.continue_patches:
            self.patch_jmp(idx, loop_top)
        self.loop_stack.pop()

    def compile_repeat_foreach(self, stmt: RepeatForEachStmt) -> None:
        iterable_op, _ty = self.compile_expr(stmt.iterable)
        iter_ssa = self.emit_ssa(Opcode.ITER_INIT, (iterable_op,), TypeCode.REF)

        # loop var
        if stmt.var in self.local_slots:
            var_slot = self.local_slots[stmt.var]
        else:
            var_slot = self.declare_local(stmt.var, TypeCode.REF)

        iter_slot = self.declare_temp(TypeCode.REF, "__iter")
        self.emit(Opcode.STORE_LOCAL, (iter_slot, SSA(iter_ssa)))

        loop_top = self.current_pos()
        iter_load = self.emit_ssa(Opcode.LOAD_LOCAL, (iter_slot,), TypeCode.REF)
        next_ssa = self.emit_ssa(Opcode.ITER_NEXT, (SSA(iter_load),), TypeCode.REF)
        has_next = self.emit_ssa(Opcode.TUPLE_GET, (SSA(next_ssa), 0), TypeCode.BOOL)
        value = self.emit_ssa(Opcode.TUPLE_GET, (SSA(next_ssa), 1), TypeCode.REF)

        jmpf_idx = self.emit(Opcode.JMPF, (SSA(has_next), -1))
        self.emit(Opcode.STORE_LOCAL, (var_slot, SSA(value)))

        ctx = LoopContext()
        self.loop_stack.append(ctx)
        for s in stmt.body:
            self.compile_stmt(s)

        continue_pos = self.current_pos()
        self.emit(Opcode.JMP, (loop_top,))

        end_pos = self.current_pos()
        self.patch_jmp(jmpf_idx, end_pos)
        for idx in ctx.break_patches:
            self.patch_jmp(idx, end_pos)
        for idx in ctx.continue_patches:
            self.patch_jmp(idx, continue_pos)
        self.loop_stack.pop()

    def compile_stop(self) -> None:
        if not self.loop_stack:
            raise CompileError("'stop' used outside of a loop")
        idx = self.emit(Opcode.JMP, (-1,))
        self.loop_stack[-1].break_patches.append(idx)

    def compile_skip(self) -> None:
        if not self.loop_stack:
            raise CompileError("'skip' used outside of a loop")
        idx = self.emit(Opcode.JMP, (-1,))
        self.loop_stack[-1].continue_patches.append(idx)

    def compile_return(self, stmt: ReturnStmt) -> None:
        if stmt.value is None:
            self.emit(Opcode.RETN, ())
            return
        op, ty = self.compile_expr(stmt.value)
        if self.fn.return_type is not None:
            op = self.coerce(op, ty, self.fn.return_type)
        self.emit(Opcode.RET, (op,))

    # ---- expressions ----

    def compile_expr(self, expr: Expr) -> tuple[Operand, TypeCode]:
        if isinstance(expr, NumberLit):
            if isinstance(expr.value, int):
                return Const(TypeCode.I64, expr.value), TypeCode.I64
            return Const(TypeCode.F64, float(expr.value)), TypeCode.F64
        if isinstance(expr, StringLit):
            return Const(TypeCode.TEXT, expr.value), TypeCode.TEXT
        if isinstance(expr, BoolLit):
            return Const(TypeCode.BOOL, expr.value), TypeCode.BOOL
        if isinstance(expr, NoneLit):
            return Const(TypeCode.NONE, None), TypeCode.NONE

        if isinstance(expr, VarRef):
            if expr.name not in self.local_slots:
                raise CompileError(f"undeclared variable {expr.name!r}")
            slot = self.local_slots[expr.name]
            ty = self.fn.local_types[slot]
            idx = self.emit_ssa(Opcode.LOAD_LOCAL, (slot,), ty)
            return SSA(idx), ty

        if isinstance(expr, BinaryOp):       return self.compile_binop(expr)
        if isinstance(expr, Compare):        return self.compile_compare(expr)
        if isinstance(expr, CallExpr):       return self.compile_call(expr)
        if isinstance(expr, FieldAccess):    return self.compile_field_access(expr)
        if isinstance(expr, IndexAccess):    return self.compile_index_access(expr)
        if isinstance(expr, NewExpr):        return self.compile_new(expr)
        if isinstance(expr, EmptyList):
            idx = self.emit_ssa(Opcode.NEW_LIST, (), TypeCode.REF)
            return SSA(idx), TypeCode.REF
        if isinstance(expr, EmptyMap):
            idx = self.emit_ssa(Opcode.NEW_MAP, (), TypeCode.REF)
            return SSA(idx), TypeCode.REF
        if isinstance(expr, EmptyMatrix):
            dims = tuple(self.coerce(*self.compile_expr(d), TypeCode.I64) for d in expr.dims)
            default = self.default_for(self.typeref_to_code(expr.elem_type))
            idx = self.emit_ssa(Opcode.NEW_MATRIX, (dims, default), TypeCode.REF)
            return SSA(idx), TypeCode.REF
        if isinstance(expr, LengthExpr):
            op, ty = self.compile_expr(expr.value)
            # polymorphic: list, map, matrix, string
            idx = self.emit_ssa(Opcode.LIST_LEN, (op,), TypeCode.I64)  # VM handles dispatch
            return SSA(idx), TypeCode.I64
        if isinstance(expr, RowsExpr):
            op, _ = self.compile_expr(expr.value)
            idx = self.emit_ssa(Opcode.MATRIX_ROWS, (op,), TypeCode.I64)
            return SSA(idx), TypeCode.I64
        if isinstance(expr, ColumnsExpr):
            op, _ = self.compile_expr(expr.value)
            idx = self.emit_ssa(Opcode.MATRIX_COLS, (op,), TypeCode.I64)
            return SSA(idx), TypeCode.I64

        raise CompileError(f"unsupported expression: {type(expr).__name__}")

    def compile_binop(self, expr: BinaryOp) -> tuple[Operand, TypeCode]:
        left_op, left_ty = self.compile_expr(expr.left)
        right_op, right_ty = self.compile_expr(expr.right)
        if expr.op == "divided":
            result_ty = TypeCode.F64
        else:
            result_ty = self.promote(left_ty, right_ty)
        left_op = self.convert(left_op, left_ty, result_ty)
        right_op = self.convert(right_op, right_ty, result_ty)
        opcode = self.binop_opcode(expr.op, result_ty)
        idx = self.emit_ssa(opcode, (left_op, right_op), result_ty)
        return SSA(idx), result_ty

    def compile_compare(self, expr: Compare) -> tuple[Operand, TypeCode]:
        left_op, left_ty = self.compile_expr(expr.left)
        right_op, right_ty = self.compile_expr(expr.right)

        # Promote numeric comparisons to common type
        if left_ty in (TypeCode.I32, TypeCode.I64, TypeCode.F32, TypeCode.F64) and \
           right_ty in (TypeCode.I32, TypeCode.I64, TypeCode.F32, TypeCode.F64):
            cmp_ty = self.promote(left_ty, right_ty)
            left_op = self.convert(left_op, left_ty, cmp_ty)
            right_op = self.convert(right_op, right_ty, cmp_ty)
        else:
            cmp_ty = left_ty

        opcode = self.cmp_opcode(expr.op, cmp_ty)
        idx = self.emit_ssa(opcode, (left_op, right_op), TypeCode.BOOL)
        return SSA(idx), TypeCode.BOOL

    def compile_call(self, expr: CallExpr) -> tuple[Operand, TypeCode]:
        if expr.name not in self.function_sigs:
            raise CompileError(f"undefined function {expr.name!r}")
        param_types, return_type = self.function_sigs[expr.name]
        if len(expr.args) != len(param_types):
            raise CompileError(
                f"function {expr.name!r} expects {len(param_types)} arg(s), got {len(expr.args)}"
            )
        arg_ops: list[Operand] = []
        for arg, pt in zip(expr.args, param_types):
            op, ty = self.compile_expr(arg)
            op = self.coerce(op, ty, pt)
            arg_ops.append(op)
        ret_ty = return_type if return_type is not None else TypeCode.NONE
        idx = self.emit_ssa(Opcode.CALL, (expr.name, tuple(arg_ops)), ret_ty)
        return SSA(idx), ret_ty

    def compile_field_access(self, expr: FieldAccess) -> tuple[Operand, TypeCode]:
        base_op, _ = self.compile_expr(expr.obj)
        # v1 — field type unknown; treat as REF
        idx = self.emit_ssa(Opcode.GET_FIELD, (base_op, expr.field), TypeCode.REF)
        return SSA(idx), TypeCode.REF

    def compile_index_access(self, expr: IndexAccess) -> tuple[Operand, TypeCode]:
        base_op, _ = self.compile_expr(expr.obj)
        idx_ops = [self.compile_expr(i)[0] for i in expr.indices]
        if len(idx_ops) == 1:
            idx = self.emit_ssa(Opcode.LIST_GET, (base_op, idx_ops[0]), TypeCode.REF)
        else:
            idx = self.emit_ssa(Opcode.MATRIX_GET, (base_op, tuple(idx_ops)), TypeCode.REF)
        return SSA(idx), TypeCode.REF

    def compile_new(self, expr: NewExpr) -> tuple[Operand, TypeCode]:
        if expr.type_name not in self.module.records:
            raise CompileError(f"unknown record type {expr.type_name!r}")
        idx = self.emit_ssa(Opcode.NEW_RECORD, (expr.type_name,), TypeCode.REF)
        return SSA(idx), TypeCode.REF

    # ---- helpers ----

    def load_lvalue(self, lv: LValue) -> tuple[Operand, TypeCode]:
        if isinstance(lv, VarLValue):
            if lv.name not in self.local_slots:
                raise CompileError(f"undeclared variable {lv.name!r}")
            slot = self.local_slots[lv.name]
            ty = self.fn.local_types[slot]
            idx = self.emit_ssa(Opcode.LOAD_LOCAL, (slot,), ty)
            return SSA(idx), ty
        if isinstance(lv, FieldLValue):
            base_op, _ = self.compile_expr(lv.obj)
            idx = self.emit_ssa(Opcode.GET_FIELD, (base_op, lv.field), TypeCode.REF)
            return SSA(idx), TypeCode.REF
        if isinstance(lv, IndexLValue):
            base_op, _ = self.compile_expr(lv.obj)
            idx_ops = [self.compile_expr(i)[0] for i in lv.indices]
            if len(idx_ops) == 1:
                idx = self.emit_ssa(Opcode.LIST_GET, (base_op, idx_ops[0]), TypeCode.REF)
            else:
                idx = self.emit_ssa(Opcode.MATRIX_GET, (base_op, tuple(idx_ops)), TypeCode.REF)
            return SSA(idx), TypeCode.REF
        raise CompileError(f"unsupported lvalue: {lv!r}")

    def typeref_to_code(self, type_ref: TypeRef) -> TypeCode:
        if type_ref.name in PRIMITIVE_TYPES:
            return PRIMITIVE_TYPES[type_ref.name]
        # list, map, matrix, record name → REF
        return TypeCode.REF

    def default_for(self, ty: TypeCode) -> Const:
        if ty == TypeCode.I32 or ty == TypeCode.I64:
            return Const(ty, 0)
        if ty == TypeCode.F32 or ty == TypeCode.F64:
            return Const(ty, 0.0)
        if ty == TypeCode.BOOL:
            return Const(ty, False)
        if ty == TypeCode.TEXT:
            return Const(ty, "")
        return Const(ty, None)

    def declare_local(self, name: str, ty: TypeCode) -> int:
        slot = len(self.fn.local_types)
        self.fn.local_types.append(ty)
        self.fn.local_names.append(name)
        self.local_slots[name] = slot
        return slot

    def declare_temp(self, ty: TypeCode, base_name: str = "__tmp") -> int:
        # Compiler-generated temporaries get unique synthetic names
        n = len(self.fn.local_types)
        name = f"{base_name}_{n}"
        slot = self.declare_local(name, ty)
        return slot

    def promote(self, a: TypeCode, b: TypeCode) -> TypeCode:
        if a == b:
            return a
        order = [TypeCode.F64, TypeCode.F32, TypeCode.I64, TypeCode.I32]
        for t in order:
            if a == t or b == t:
                return t
        # non-numeric — fall back to whatever's there
        if a == TypeCode.NONE:
            return b
        if b == TypeCode.NONE:
            return a
        return a

    def can_widen(self, a: TypeCode, b: TypeCode) -> bool:
        if a == b:
            return True
        if a == TypeCode.I32 and b == TypeCode.I64:
            return True
        if a == TypeCode.F32 and b == TypeCode.F64:
            return True
        if a in (TypeCode.I32, TypeCode.I64) and b in (TypeCode.F32, TypeCode.F64):
            return True
        # REF/NONE are opaque
        if b == TypeCode.REF:
            return True
        if a == TypeCode.NONE:
            return True
        return False

    def convert(self, op: Operand, from_ty: TypeCode, to_ty: TypeCode) -> Operand:
        """Numeric conversion — implicit widening emits a conversion op."""
        if from_ty == to_ty:
            return op
        if isinstance(op, Const):
            # Constant-fold numeric conversions
            if from_ty == TypeCode.I64 and to_ty == TypeCode.F64:
                return Const(TypeCode.F64, float(op.value))
            if from_ty == TypeCode.F64 and to_ty == TypeCode.I64:
                return Const(TypeCode.I64, int(op.value))
        if from_ty == TypeCode.I64 and to_ty == TypeCode.F64:
            idx = self.emit_ssa(Opcode.SITOF_I64_F64, (op,), TypeCode.F64)
            return SSA(idx)
        if from_ty == TypeCode.F64 and to_ty == TypeCode.I64:
            idx = self.emit_ssa(Opcode.FTOSI_F64_I64, (op,), TypeCode.I64)
            return SSA(idx)
        # Same as convert but for REF/TEXT/BOOL we just pass through
        return op

    def coerce(self, op: Operand, from_ty: TypeCode, to_ty: TypeCode) -> Operand:
        """Used where the target type is fixed (local, field, parameter)."""
        if from_ty == to_ty:
            return op
        if self.can_widen(from_ty, to_ty):
            return self.convert(op, from_ty, to_ty)
        raise CompileError(
            f"cannot convert {from_ty.name} to {to_ty.name} without explicit conversion"
        )

    def binop_opcode(self, op_str: str, ty: TypeCode) -> Opcode:
        table = {
            ("plus",    TypeCode.I64): Opcode.ADD_I64,
            ("minus",   TypeCode.I64): Opcode.SUB_I64,
            ("times",   TypeCode.I64): Opcode.MUL_I64,
            ("divided", TypeCode.F64): Opcode.DIV_F64,
            ("plus",    TypeCode.F64): Opcode.ADD_F64,
            ("minus",   TypeCode.F64): Opcode.SUB_F64,
            ("times",   TypeCode.F64): Opcode.MUL_F64,
        }
        if (op_str, ty) not in table:
            raise CompileError(f"binary op {op_str!r} not supported on {ty.name}")
        return table[(op_str, ty)]

    def cmp_opcode(self, op_str: str, ty: TypeCode) -> Opcode:
        numeric_table = {
            ("equal",     TypeCode.I64): Opcode.EQ_I64,
            ("not_equal", TypeCode.I64): Opcode.NE_I64,
            ("less",      TypeCode.I64): Opcode.LT_I64,
            ("at_most",   TypeCode.I64): Opcode.LE_I64,
            ("greater",   TypeCode.I64): Opcode.GT_I64,
            ("at_least",  TypeCode.I64): Opcode.GE_I64,
            ("equal",     TypeCode.F64): Opcode.EQ_F64,
            ("not_equal", TypeCode.F64): Opcode.NE_F64,
            ("less",      TypeCode.F64): Opcode.LT_F64,
            ("at_most",   TypeCode.F64): Opcode.LE_F64,
            ("greater",   TypeCode.F64): Opcode.GT_F64,
            ("at_least",  TypeCode.F64): Opcode.GE_F64,
        }
        if (op_str, ty) in numeric_table:
            return numeric_table[(op_str, ty)]
        if ty == TypeCode.BOOL and op_str == "equal":     return Opcode.EQ_BOOL
        if ty == TypeCode.BOOL and op_str == "not_equal": return Opcode.NE_BOOL
        if ty == TypeCode.TEXT and op_str == "equal":     return Opcode.EQ_TEXT
        if ty == TypeCode.TEXT and op_str == "not_equal": return Opcode.NE_TEXT
        if ty == TypeCode.REF and op_str == "equal":      return Opcode.EQ_REF
        if ty == TypeCode.REF and op_str == "not_equal":  return Opcode.NE_REF
        raise CompileError(f"comparison {op_str!r} not supported on {ty.name}")

    # ---- bytecode emission ----

    def emit(self, op: Opcode, operands: tuple) -> int:
        idx = len(self.fn.instructions)
        self.fn.instructions.append(Instruction(op, operands))
        return idx

    def emit_ssa(self, op: Opcode, operands: tuple, result_type: TypeCode) -> int:
        idx = len(self.fn.instructions)
        self.fn.instructions.append(Instruction(op, operands, result_type))
        return idx

    def current_pos(self) -> int:
        return len(self.fn.instructions)

    def patch_jmp(self, instr_idx: int, target: int) -> None:
        """Rewrite the target field of a JMP/JMPF/JMPT instruction at `instr_idx`."""
        instr = self.fn.instructions[instr_idx]
        if instr.op == Opcode.JMP:
            self.fn.instructions[instr_idx] = Instruction(
                instr.op, (target,), instr.result_type, instr.line,
            )
        elif instr.op in (Opcode.JMPF, Opcode.JMPT):
            cond, _ = instr.operands
            self.fn.instructions[instr_idx] = Instruction(
                instr.op, (cond, target), instr.result_type, instr.line,
            )
        else:
            raise CompileError(f"cannot patch non-jump instruction {instr.op}")


def compile_program(stmts: list[Stmt]) -> Module:
    return Compiler().compile_program(stmts)
