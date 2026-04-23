from dataclasses import dataclass
from typing import Any, Iterator

from bytecode import (
    Const, Function, Module, Opcode, RecordLayout, SSA, TypeCode,
)


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


@dataclass
class RecordInstance:
    type_name: str
    fields: dict

    def __repr__(self) -> str:
        parts = [f"{k}={v!r}" for k, v in self.fields.items()]
        return f"{self.type_name}({', '.join(parts)})"


@dataclass
class MatrixInstance:
    shape: tuple
    data: list

    def __len__(self) -> int:
        return len(self.data)

    def __iter__(self) -> Iterator:
        return iter(self.data)

    def __repr__(self) -> str:
        if len(self.shape) == 2:
            rows, cols = self.shape
            formatted = []
            for r in range(rows):
                row = self.data[r * cols:(r + 1) * cols]
                formatted.append("[" + ", ".join(repr(v) for v in row) + "]")
            return "[" + ", ".join(formatted) + "]"
        return f"matrix{self.shape}({self.data!r})"


def _matrix_offset(mat: MatrixInstance, indices: tuple) -> int:
    if len(indices) != len(mat.shape):
        raise VMError(f"matrix has {len(mat.shape)} dims, got {len(indices)} indices")
    offset = 0
    stride = 1
    for dim, idx in zip(reversed(mat.shape), reversed(indices)):
        i = int(idx)
        if i < 0 or i >= dim:
            raise VMError(f"matrix index {idx} out of range for dim of size {dim}")
        offset += i * stride
        stride *= dim
    return offset


@dataclass
class _IterState:
    """Holds a Python iterator for `repeat for each`."""
    it: Iterator


class Frame:
    __slots__ = ("fn", "locals", "ssa", "ip", "return_value", "returned")

    def __init__(self, fn: Function, args: list | None = None) -> None:
        self.fn = fn
        self.locals = [_DEFAULTS[t] for t in fn.local_types]
        # bind args to the first len(param_types) slots
        if args is not None:
            for i, v in enumerate(args):
                self.locals[i] = v
        self.ssa: list = [None] * len(fn.instructions)
        self.ip = 0
        self.return_value: Any = None
        self.returned = False


def execute(module: Module) -> None:
    if "main" not in module.functions:
        raise VMError("module has no 'main' function")
    run_function(module.functions["main"], [], module)


def run_function(fn: Function, args: list, module: Module) -> Any:
    frame = Frame(fn, args)
    instructions = fn.instructions
    n = len(instructions)

    while frame.ip < n and not frame.returned:
        instr = instructions[frame.ip]
        op = instr.op
        operands = instr.operands
        step = 1  # default: advance by one

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
            if b == 0:
                raise VMError("division by zero")
            frame.ssa[frame.ip] = _resolve(operands[0], frame) / b
        elif op is Opcode.NEG_F64:
            frame.ssa[frame.ip] = -_resolve(operands[0], frame)

        elif op is Opcode.SITOF_I64_F64:
            frame.ssa[frame.ip] = float(_resolve(operands[0], frame))
        elif op is Opcode.FTOSI_F64_I64:
            frame.ssa[frame.ip] = int(_resolve(operands[0], frame))

        elif op is Opcode.EQ_I64 or op is Opcode.EQ_F64 or op is Opcode.EQ_BOOL or op is Opcode.EQ_TEXT:
            frame.ssa[frame.ip] = _resolve(operands[0], frame) == _resolve(operands[1], frame)
        elif op is Opcode.NE_I64 or op is Opcode.NE_F64 or op is Opcode.NE_BOOL or op is Opcode.NE_TEXT:
            frame.ssa[frame.ip] = _resolve(operands[0], frame) != _resolve(operands[1], frame)
        elif op is Opcode.LT_I64 or op is Opcode.LT_F64:
            frame.ssa[frame.ip] = _resolve(operands[0], frame) < _resolve(operands[1], frame)
        elif op is Opcode.LE_I64 or op is Opcode.LE_F64:
            frame.ssa[frame.ip] = _resolve(operands[0], frame) <= _resolve(operands[1], frame)
        elif op is Opcode.GT_I64 or op is Opcode.GT_F64:
            frame.ssa[frame.ip] = _resolve(operands[0], frame) > _resolve(operands[1], frame)
        elif op is Opcode.GE_I64 or op is Opcode.GE_F64:
            frame.ssa[frame.ip] = _resolve(operands[0], frame) >= _resolve(operands[1], frame)
        elif op is Opcode.EQ_REF:
            frame.ssa[frame.ip] = _resolve(operands[0], frame) is _resolve(operands[1], frame)
        elif op is Opcode.NE_REF:
            frame.ssa[frame.ip] = _resolve(operands[0], frame) is not _resolve(operands[1], frame)

        elif op is Opcode.JMP:
            frame.ip = operands[0]
            step = 0
        elif op is Opcode.JMPF:
            if not _resolve(operands[0], frame):
                frame.ip = operands[1]
                step = 0
        elif op is Opcode.JMPT:
            if _resolve(operands[0], frame):
                frame.ip = operands[1]
                step = 0

        elif op is Opcode.LOAD_LOCAL:
            slot = operands[0]
            frame.ssa[frame.ip] = frame.locals[slot]
        elif op is Opcode.STORE_LOCAL:
            slot, src = operands
            frame.locals[slot] = _resolve(src, frame)

        elif op is Opcode.CALL:
            name, args_tuple = operands
            arg_values = [_resolve(a, frame) for a in args_tuple]
            callee = module.functions.get(name)
            if callee is None:
                raise VMError(f"undefined function {name!r}")
            frame.ssa[frame.ip] = run_function(callee, arg_values, module)
        elif op is Opcode.RET:
            frame.return_value = _resolve(operands[0], frame)
            frame.returned = True
        elif op is Opcode.RETN:
            frame.return_value = None
            frame.returned = True

        elif op is Opcode.PRINT:
            print(_resolve(operands[0], frame))
        elif op is Opcode.PRINT_MANY:
            values = [_resolve(v, frame) for v in operands[0]]
            print(*values)

        elif op is Opcode.NEW_RECORD:
            type_name = operands[0]
            layout = module.records[type_name]
            fields = {name: _default_for_record_field(ty) for name, ty in layout.fields}
            frame.ssa[frame.ip] = RecordInstance(type_name, fields)
        elif op is Opcode.GET_FIELD:
            base = _resolve(operands[0], frame)
            if not isinstance(base, RecordInstance):
                raise VMError(f"GET_FIELD on non-record value")
            field = operands[1]
            if field not in base.fields:
                raise VMError(f"record {base.type_name!r} has no field {field!r}")
            frame.ssa[frame.ip] = base.fields[field]
        elif op is Opcode.SET_FIELD:
            base = _resolve(operands[0], frame)
            if not isinstance(base, RecordInstance):
                raise VMError(f"SET_FIELD on non-record value")
            field = operands[1]
            if field not in base.fields:
                raise VMError(f"record {base.type_name!r} has no field {field!r}")
            base.fields[field] = _resolve(operands[2], frame)

        elif op is Opcode.NEW_LIST:
            frame.ssa[frame.ip] = []
        elif op is Opcode.APPEND:
            target = _resolve(operands[0], frame)
            value = _resolve(operands[1], frame)
            if not isinstance(target, list):
                raise VMError(f"append target is not a list")
            target.append(value)
        elif op is Opcode.LIST_GET:
            base = _resolve(operands[0], frame)
            index = _resolve(operands[1], frame)
            frame.ssa[frame.ip] = _index_read(base, index)
        elif op is Opcode.LIST_SET:
            base = _resolve(operands[0], frame)
            index = _resolve(operands[1], frame)
            value = _resolve(operands[2], frame)
            _index_write(base, index, value)
        elif op is Opcode.LIST_LEN:
            val = _resolve(operands[0], frame)
            if isinstance(val, (list, dict, str, MatrixInstance)):
                frame.ssa[frame.ip] = len(val)
            else:
                raise VMError(f"length: unsupported type {type(val).__name__}")

        elif op is Opcode.NEW_MAP:
            frame.ssa[frame.ip] = {}
        elif op is Opcode.MAP_GET:
            m = _resolve(operands[0], frame)
            k = _resolve(operands[1], frame)
            if k not in m:
                raise VMError(f"map has no key {k!r}")
            frame.ssa[frame.ip] = m[k]
        elif op is Opcode.MAP_SET:
            m = _resolve(operands[0], frame)
            k = _resolve(operands[1], frame)
            v = _resolve(operands[2], frame)
            m[k] = v
        elif op is Opcode.MAP_LEN:
            frame.ssa[frame.ip] = len(_resolve(operands[0], frame))

        elif op is Opcode.NEW_MATRIX:
            dims_ops, default_op = operands
            dims = tuple(int(_resolve(d, frame)) for d in dims_ops)
            default = _resolve(default_op, frame)
            total = 1
            for d in dims:
                total *= d
            frame.ssa[frame.ip] = MatrixInstance(dims, [default] * total)
        elif op is Opcode.MATRIX_GET:
            mat = _resolve(operands[0], frame)
            indices = tuple(_resolve(i, frame) for i in operands[1])
            if not isinstance(mat, MatrixInstance):
                raise VMError("MATRIX_GET on non-matrix value")
            frame.ssa[frame.ip] = mat.data[_matrix_offset(mat, indices)]
        elif op is Opcode.MATRIX_SET:
            mat = _resolve(operands[0], frame)
            indices = tuple(_resolve(i, frame) for i in operands[1])
            value = _resolve(operands[2], frame)
            if not isinstance(mat, MatrixInstance):
                raise VMError("MATRIX_SET on non-matrix value")
            mat.data[_matrix_offset(mat, indices)] = value
        elif op is Opcode.MATRIX_LEN:
            frame.ssa[frame.ip] = len(_resolve(operands[0], frame))
        elif op is Opcode.MATRIX_ROWS:
            mat = _resolve(operands[0], frame)
            if not isinstance(mat, MatrixInstance) or not mat.shape:
                raise VMError("MATRIX_ROWS on non-matrix or empty-shape value")
            frame.ssa[frame.ip] = mat.shape[0]
        elif op is Opcode.MATRIX_COLS:
            mat = _resolve(operands[0], frame)
            if not isinstance(mat, MatrixInstance) or len(mat.shape) < 2:
                raise VMError("MATRIX_COLS on matrix with fewer than 2 dims")
            frame.ssa[frame.ip] = mat.shape[1]

        elif op is Opcode.ITER_INIT:
            v = _resolve(operands[0], frame)
            try:
                frame.ssa[frame.ip] = _IterState(iter(v))
            except TypeError:
                raise VMError(f"cannot iterate over {type(v).__name__}")
        elif op is Opcode.ITER_NEXT:
            state = _resolve(operands[0], frame)
            if not isinstance(state, _IterState):
                raise VMError("ITER_NEXT on non-iterator")
            try:
                value = next(state.it)
                frame.ssa[frame.ip] = (True, value)
            except StopIteration:
                frame.ssa[frame.ip] = (False, None)
        elif op is Opcode.TUPLE_GET:
            t = _resolve(operands[0], frame)
            i = operands[1]
            frame.ssa[frame.ip] = t[i]
        elif op is Opcode.STRING_LEN:
            frame.ssa[frame.ip] = len(_resolve(operands[0], frame))

        else:
            raise VMError(f"unknown opcode: {op}")

        frame.ip += step

    return frame.return_value


def _index_read(obj: Any, index: Any) -> Any:
    if isinstance(obj, list):
        i = int(index)
        if i < 0 or i >= len(obj):
            raise VMError(f"list index {index} out of range (length {len(obj)})")
        return obj[i]
    if isinstance(obj, str):
        i = int(index)
        if i < 0 or i >= len(obj):
            raise VMError(f"string index {index} out of range")
        return obj[i]
    if isinstance(obj, dict):
        if index not in obj:
            raise VMError(f"map has no key {index!r}")
        return obj[index]
    raise VMError(f"cannot index value of type {type(obj).__name__}")


def _index_write(obj: Any, index: Any, value: Any) -> None:
    if isinstance(obj, list):
        i = int(index)
        if i < 0 or i >= len(obj):
            raise VMError(f"list index {index} out of range (length {len(obj)})")
        obj[i] = value
        return
    if isinstance(obj, dict):
        obj[index] = value
        return
    raise VMError(f"cannot index-assign value of type {type(obj).__name__}")


def _default_for_record_field(ty: TypeCode) -> Any:
    return _DEFAULTS.get(ty, None)


def _resolve(operand, frame: Frame):
    if isinstance(operand, SSA):
        return frame.ssa[operand.index]
    if isinstance(operand, Const):
        return operand.value
    raise VMError(f"cannot resolve operand: {operand!r}")
