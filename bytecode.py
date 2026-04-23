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
    # local access
    LOAD_LOCAL = auto()    # operand: slot idx             → produces SSA
    STORE_LOCAL = auto()   # operands: slot idx, value op  → no SSA
    # I/O & control
    PRINT = auto()         # operand: value op             → no SSA
    RETN = auto()          # no operands                   → halts main


@dataclass
class SSA:
    """Reference to the output of instruction `index` in the current function."""
    index: int


@dataclass
class Const:
    """Inline constant operand. Type is known at compile time."""
    type: TypeCode
    value: Any


# An operand is one of: SSA(i), Const(ty, v), or an int (slot index)
Operand = Union[SSA, Const, int]


@dataclass
class Instruction:
    op: Opcode
    operands: tuple
    result_type: TypeCode | None = None   # None for ops that don't produce an SSA value
    line: int = 0


@dataclass
class Function:
    name: str
    param_types: list[TypeCode] = field(default_factory=list)
    return_type: TypeCode | None = None
    local_types: list[TypeCode] = field(default_factory=list)   # slot idx -> type
    local_names: list[str] = field(default_factory=list)        # slot idx -> name
    instructions: list[Instruction] = field(default_factory=list)


@dataclass
class Module:
    functions: dict[str, Function] = field(default_factory=dict)


# pretty-print helpers for debugging

def format_operand(op: Operand) -> str:
    if isinstance(op, SSA):
        return f"%{op.index}"
    if isinstance(op, Const):
        return f"{op.value!r}:{op.type.name}"
    if isinstance(op, int):
        return f"#slot{op}"
    return repr(op)


def format_instruction(idx: int, instr: Instruction) -> str:
    ops = ", ".join(format_operand(o) for o in instr.operands)
    prefix = f"%{idx} = " if instr.result_type is not None else "       "
    ty_suffix = f"  -> {instr.result_type.name}" if instr.result_type is not None else ""
    return f"{prefix}{instr.op.name:<20} {ops}{ty_suffix}"


def dump_function(fn: Function) -> str:
    out = [f"function {fn.name}:"]
    if fn.local_types:
        out.append("  locals:")
        for i, (name, ty) in enumerate(zip(fn.local_names, fn.local_types)):
            out.append(f"    slot{i}: {name} : {ty.name}")
    out.append("  code:")
    for i, instr in enumerate(fn.instructions):
        out.append("    " + format_instruction(i, instr))
    return "\n".join(out)
