from bytecode import (
    Const, Function, Instruction, Module, Opcode, Operand, SSA, TypeCode,
)
from parser import (
    BinaryOp, Expr, NumberLit, PrintStmt, SetStmt, Stmt, VarLValue, VarRef,
)


class CompileError(Exception):
    pass


class Compiler:
    def __init__(self) -> None:
        self.fn: Function | None = None
        self.local_slots: dict[str, int] = {}

    # ---- entry ----

    def compile_program(self, stmts: list[Stmt]) -> Module:
        main = Function(name="main")
        self.fn = main
        self.local_slots = {}
        for stmt in stmts:
            self.compile_stmt(stmt)
        self.emit(Opcode.RETN, ())
        return Module({"main": main})

    # ---- statements ----

    def compile_stmt(self, stmt: Stmt) -> None:
        if isinstance(stmt, SetStmt):
            return self.compile_set(stmt)
        if isinstance(stmt, PrintStmt):
            return self.compile_print(stmt)
        raise CompileError(
            f"{type(stmt).__name__} not yet supported by the bytecode compiler"
        )

    def compile_set(self, stmt: SetStmt) -> None:
        if not isinstance(stmt.target, VarLValue):
            raise CompileError(
                "only simple variable targets are supported in the v1 bytecode compiler"
            )
        name = stmt.target.name
        value_op, value_ty = self.compile_expr(stmt.value)

        if name in self.local_slots:
            slot = self.local_slots[name]
            expected = self.fn.local_types[slot]
            # locals have fixed types — convert RHS to match, if lossless
            if value_ty != expected:
                if not self.can_widen(value_ty, expected):
                    raise CompileError(
                        f"cannot assign {value_ty.name} to {name!r} of type {expected.name} "
                        f"without explicit conversion"
                    )
                value_op = self.convert(value_op, value_ty, expected)
        else:
            slot = self.declare_local(name, value_ty)

        self.emit(Opcode.STORE_LOCAL, (slot, value_op))

    def compile_print(self, stmt: PrintStmt) -> None:
        for part in stmt.parts:
            op, _ty = self.compile_expr(part)
            self.emit(Opcode.PRINT, (op,))

    # ---- expressions ----

    def compile_expr(self, expr: Expr) -> tuple[Operand, TypeCode]:
        if isinstance(expr, NumberLit):
            if isinstance(expr.value, int):
                return Const(TypeCode.I64, expr.value), TypeCode.I64
            return Const(TypeCode.F64, float(expr.value)), TypeCode.F64

        if isinstance(expr, VarRef):
            if expr.name not in self.local_slots:
                raise CompileError(f"undeclared variable {expr.name!r}")
            slot = self.local_slots[expr.name]
            ty = self.fn.local_types[slot]
            idx = self.emit_ssa(Opcode.LOAD_LOCAL, (slot,), ty)
            return SSA(idx), ty

        if isinstance(expr, BinaryOp):
            return self.compile_binop(expr)

        raise CompileError(
            f"{type(expr).__name__} not yet supported by the bytecode compiler"
        )

    def compile_binop(self, expr: BinaryOp) -> tuple[Operand, TypeCode]:
        left_op, left_ty = self.compile_expr(expr.left)
        right_op, right_ty = self.compile_expr(expr.right)

        # division always produces f64, regardless of operand types
        if expr.op == "divided":
            result_ty = TypeCode.F64
        else:
            result_ty = self.promote(left_ty, right_ty)

        left_op = self.convert(left_op, left_ty, result_ty)
        right_op = self.convert(right_op, right_ty, result_ty)

        opcode = self.binop_opcode(expr.op, result_ty)
        idx = self.emit_ssa(opcode, (left_op, right_op), result_ty)
        return SSA(idx), result_ty

    # ---- helpers ----

    def declare_local(self, name: str, ty: TypeCode) -> int:
        slot = len(self.fn.local_types)
        self.fn.local_types.append(ty)
        self.fn.local_names.append(name)
        self.local_slots[name] = slot
        return slot

    def promote(self, a: TypeCode, b: TypeCode) -> TypeCode:
        if a == b:
            return a
        # "wider wins" — F64 > F32 > I64 > I32
        order = [TypeCode.F64, TypeCode.F32, TypeCode.I64, TypeCode.I32]
        for t in order:
            if a == t or b == t:
                return t
        raise CompileError(f"cannot mix types {a.name} and {b.name}")

    def can_widen(self, a: TypeCode, b: TypeCode) -> bool:
        """True if a → b is lossless."""
        if a == b:
            return True
        if a == TypeCode.I32 and b == TypeCode.I64:
            return True
        if a == TypeCode.F32 and b == TypeCode.F64:
            return True
        if a in (TypeCode.I32, TypeCode.I64) and b in (TypeCode.F32, TypeCode.F64):
            return True
        return False

    def convert(self, op: Operand, from_ty: TypeCode, to_ty: TypeCode) -> Operand:
        if from_ty == to_ty:
            return op
        # constant folding — avoid emitting a conversion op for literals
        if isinstance(op, Const):
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
        raise CompileError(
            f"no conversion available from {from_ty.name} to {to_ty.name}"
        )

    def binop_opcode(self, op_str: str, ty: TypeCode) -> Opcode:
        table = {
            ("plus", TypeCode.I64): Opcode.ADD_I64,
            ("minus", TypeCode.I64): Opcode.SUB_I64,
            ("times", TypeCode.I64): Opcode.MUL_I64,
            ("divided", TypeCode.F64): Opcode.DIV_F64,
            ("plus", TypeCode.F64): Opcode.ADD_F64,
            ("minus", TypeCode.F64): Opcode.SUB_F64,
            ("times", TypeCode.F64): Opcode.MUL_F64,
        }
        if (op_str, ty) not in table:
            raise CompileError(
                f"binary op {op_str!r} not supported on {ty.name} in v1"
            )
        return table[(op_str, ty)]

    def emit(self, op: Opcode, operands: tuple) -> None:
        self.fn.instructions.append(Instruction(op, operands))

    def emit_ssa(self, op: Opcode, operands: tuple, result_type: TypeCode) -> int:
        idx = len(self.fn.instructions)
        self.fn.instructions.append(Instruction(op, operands, result_type))
        return idx


def compile_program(stmts: list[Stmt]) -> Module:
    return Compiler().compile_program(stmts)
