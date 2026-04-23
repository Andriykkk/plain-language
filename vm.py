from bytecode import Const, Function, Module, Opcode, SSA, TypeCode


class VMError(Exception):
    pass


_DEFAULTS = {
    TypeCode.I32: 0,
    TypeCode.I64: 0,
    TypeCode.F32: 0.0,
    TypeCode.F64: 0.0,
    TypeCode.BOOL: False,
    TypeCode.TEXT: "",
    TypeCode.REF: None,
    TypeCode.NONE: None,
}


class Frame:
    __slots__ = ("fn", "locals", "ssa", "ip")

    def __init__(self, fn: Function) -> None:
        self.fn = fn
        self.locals = [_DEFAULTS[t] for t in fn.local_types]
        self.ssa: list = [None] * len(fn.instructions)
        self.ip = 0


def execute(module: Module) -> None:
    if "main" not in module.functions:
        raise VMError("module has no 'main' function")
    run_function(module.functions["main"])


def run_function(fn: Function) -> object:
    frame = Frame(fn)
    instructions = fn.instructions
    n = len(instructions)

    while frame.ip < n:
        instr = instructions[frame.ip]
        op = instr.op
        operands = instr.operands

        if op is Opcode.ADD_I64:
            frame.ssa[frame.ip] = _resolve(operands[0], frame) + _resolve(operands[1], frame)
        elif op is Opcode.SUB_I64:
            frame.ssa[frame.ip] = _resolve(operands[0], frame) - _resolve(operands[1], frame)
        elif op is Opcode.MUL_I64:
            frame.ssa[frame.ip] = _resolve(operands[0], frame) * _resolve(operands[1], frame)
        elif op is Opcode.DIV_I64:
            b = _resolve(operands[1], frame)
            if b == 0:
                raise VMError("integer division by zero")
            frame.ssa[frame.ip] = _resolve(operands[0], frame) // b
        elif op is Opcode.NEG_I64:
            frame.ssa[frame.ip] = -_resolve(operands[0], frame)

        elif op is Opcode.ADD_F64:
            frame.ssa[frame.ip] = _resolve(operands[0], frame) + _resolve(operands[1], frame)
        elif op is Opcode.SUB_F64:
            frame.ssa[frame.ip] = _resolve(operands[0], frame) - _resolve(operands[1], frame)
        elif op is Opcode.MUL_F64:
            frame.ssa[frame.ip] = _resolve(operands[0], frame) * _resolve(operands[1], frame)
        elif op is Opcode.DIV_F64:
            b = _resolve(operands[1], frame)
            if b == 0.0:
                raise VMError("float division by zero")
            frame.ssa[frame.ip] = _resolve(operands[0], frame) / b
        elif op is Opcode.NEG_F64:
            frame.ssa[frame.ip] = -_resolve(operands[0], frame)

        elif op is Opcode.SITOF_I64_F64:
            frame.ssa[frame.ip] = float(_resolve(operands[0], frame))
        elif op is Opcode.FTOSI_F64_I64:
            frame.ssa[frame.ip] = int(_resolve(operands[0], frame))

        elif op is Opcode.LOAD_LOCAL:
            slot = operands[0]
            frame.ssa[frame.ip] = frame.locals[slot]
        elif op is Opcode.STORE_LOCAL:
            slot, src = operands
            frame.locals[slot] = _resolve(src, frame)

        elif op is Opcode.PRINT:
            print(_resolve(operands[0], frame))

        elif op is Opcode.RETN:
            return None

        else:
            raise VMError(f"unknown opcode: {op}")

        frame.ip += 1

    return None


def _resolve(operand, frame: Frame):
    if isinstance(operand, SSA):
        return frame.ssa[operand.index]
    if isinstance(operand, Const):
        return operand.value
    raise VMError(f"cannot resolve operand: {operand!r}")
