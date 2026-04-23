from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Union


class TypeCode(Enum):
    I32 = auto()
    I64 = auto()
    F32 = auto()
    F64 = auto()
    BOOL = auto()
    TEXT = auto()
    REF = auto()
    NONE = auto()


class Opcode(Enum):
    # integer arithmetic
    ADD_I64 = auto()
    SUB_I64 = auto()
    MUL_I64 = auto()
    DIV_I64 = auto()
    NEG_I64 = auto()
    # float arithmetic
    ADD_F64 = auto()
    SUB_F64 = auto()
    MUL_F64 = auto()
    DIV_F64 = auto()
    NEG_F64 = auto()

    # conversions
    SITOF_I64_F64 = auto()
    FTOSI_F64_I64 = auto()

    # comparisons (each returns BOOL)
    EQ_I64 = auto(); NE_I64 = auto()
    LT_I64 = auto(); LE_I64 = auto()
    GT_I64 = auto(); GE_I64 = auto()
    EQ_F64 = auto(); NE_F64 = auto()
    LT_F64 = auto(); LE_F64 = auto()
    GT_F64 = auto(); GE_F64 = auto()
    EQ_BOOL = auto(); NE_BOOL = auto()
    EQ_TEXT = auto(); NE_TEXT = auto()
    EQ_REF = auto();  NE_REF = auto()

    # control flow
    JMP = auto()        # operand: target idx
    JMPF = auto()       # operands: cond, target idx  (jump if false)
    JMPT = auto()       # operands: cond, target idx

    # locals
    LOAD_LOCAL = auto()    # operand: slot                   -> SSA
    STORE_LOCAL = auto()   # operands: slot, value

    # functions
    CALL = auto()          # operands: fn_name, (args tuple)  -> SSA
    RET = auto()           # operand: value
    RETN = auto()          # no operand

    # I/O
    PRINT = auto()         # operand: value
    PRINT_MANY = auto()    # operand: (values tuple) — joined with space

    # records
    NEW_RECORD = auto()    # operand: type_name -> SSA (REF)
    GET_FIELD = auto()     # operands: ref, field_name -> SSA
    SET_FIELD = auto()     # operands: ref, field_name, value

    # lists
    NEW_LIST = auto()      # -> SSA (REF)
    APPEND = auto()        # operands: list, value
    LIST_GET = auto()      # operands: list, idx -> SSA
    LIST_SET = auto()      # operands: list, idx, value
    LIST_LEN = auto()      # operand: list -> SSA (I64)

    # maps
    NEW_MAP = auto()       # -> SSA (REF)
    MAP_GET = auto()       # operands: map, key -> SSA
    MAP_SET = auto()       # operands: map, key, value
    MAP_LEN = auto()       # operand: map -> SSA (I64)

    # matrices
    NEW_MATRIX = auto()    # operands: (dims tuple), default_val -> SSA (REF)
    MATRIX_GET = auto()    # operands: matrix, (indices tuple) -> SSA
    MATRIX_SET = auto()    # operands: matrix, (indices tuple), value
    MATRIX_LEN = auto()    # operand: matrix -> SSA (I64)
    MATRIX_ROWS = auto()   # operand: matrix -> SSA (I64)
    MATRIX_COLS = auto()   # operand: matrix -> SSA (I64)

    # iteration (for `repeat for each`)
    ITER_INIT = auto()     # operand: iterable -> SSA (iterator state)
    ITER_NEXT = auto()     # operand: iter state -> SSA (tuple(has_next:bool, value))
    # the result is a 2-tuple; the compiler unpacks via TUPLE_GET
    TUPLE_GET = auto()     # operands: tuple, idx:int -> SSA

    # text
    STRING_LEN = auto()    # operand: text -> SSA (I64)


@dataclass
class SSA:
    index: int


@dataclass
class Const:
    type: TypeCode
    value: Any


Operand = Union[SSA, Const, int, str, tuple]


@dataclass
class Instruction:
    op: Opcode
    operands: tuple
    result_type: TypeCode | None = None
    line: int = 0


@dataclass
class RecordLayout:
    """Metadata for a record type — name to field-offset mapping, field types."""
    name: str
    fields: list[tuple[str, TypeCode]]   # in declaration order

    @property
    def field_names(self) -> list[str]:
        return [n for n, _ in self.fields]


@dataclass
class Function:
    name: str
    param_names: list[str] = field(default_factory=list)
    param_types: list[TypeCode] = field(default_factory=list)
    return_type: TypeCode | None = None
    local_types: list[TypeCode] = field(default_factory=list)
    local_names: list[str] = field(default_factory=list)
    instructions: list[Instruction] = field(default_factory=list)


@dataclass
class Module:
    functions: dict[str, Function] = field(default_factory=dict)
    records: dict[str, RecordLayout] = field(default_factory=dict)


# ---- pretty-printing for debugging ----

def format_operand(op: Operand) -> str:
    if isinstance(op, SSA):
        return f"%{op.index}"
    if isinstance(op, Const):
        return f"{op.value!r}:{op.type.name}"
    if isinstance(op, tuple):
        return "(" + ", ".join(format_operand(x) for x in op) + ")"
    if isinstance(op, str):
        return f'"{op}"'
    if isinstance(op, int):
        return f"#{op}"
    return repr(op)


def format_instruction(idx: int, instr: Instruction) -> str:
    ops = ", ".join(format_operand(o) for o in instr.operands)
    prefix = f"%{idx:<3} = " if instr.result_type is not None else "       "
    ty = f"  -> {instr.result_type.name}" if instr.result_type is not None else ""
    return f"{prefix}{instr.op.name:<16} {ops}{ty}"


def dump_function(fn: Function) -> str:
    out = [f"function {fn.name}({', '.join(f'{n}:{t.name}' for n, t in zip(fn.param_names, fn.param_types))}) -> {fn.return_type.name if fn.return_type else 'none'}:"]
    if fn.local_types:
        out.append("  locals:")
        for i, (name, ty) in enumerate(zip(fn.local_names, fn.local_types)):
            out.append(f"    slot{i}: {name} : {ty.name}")
    out.append("  code:")
    for i, instr in enumerate(fn.instructions):
        out.append("    " + format_instruction(i, instr))
    return "\n".join(out)


def dump_module(mod: Module) -> str:
    parts = []
    for rec in mod.records.values():
        parts.append(f"record {rec.name}:")
        for name, ty in rec.fields:
            parts.append(f"  {name} : {ty.name}")
        parts.append("")
    for fn in mod.functions.values():
        parts.append(dump_function(fn))
        parts.append("")
    return "\n".join(parts)
