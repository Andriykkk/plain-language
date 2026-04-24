"""
Bytecode data types.

One flat instruction stream (`code`), an `entry` index where execution begins,
and a single flat `memory` array. The compiler pre-fills memory with constant
values at addresses it picks; variables are just uninitialized slots at other
addresses. The VM doesn't distinguish between "constant" and "variable" slots —
both are just memory.
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


# ---------------------------------------------------------------------------
# Type codes — compiler-side metadata (not used by the VM at runtime).
# ---------------------------------------------------------------------------

class TypeCode(Enum):
    I64 = auto()
    F64 = auto()
    TEXT = auto()
    BOOL = auto()
    NONE = auto()


# ---------------------------------------------------------------------------
# Opcodes. Three kinds of memory traffic (LOAD, STORE, MOVE between regs)
# plus PRINT and HALT. That's it for this first version.
# ---------------------------------------------------------------------------

class Opcode(Enum):
    LOAD  = auto()    # (r_dst, mem_addr)    — r_dst = memory[addr]
    STORE = auto()    # (r_src, mem_addr)    — memory[addr] = r_src
    PRINT = auto()    # (r_src,)
    HALT  = auto()    # stop execution


@dataclass
class Instruction:
    op: Opcode
    operands: tuple
    line: int = 0


# ---------------------------------------------------------------------------
# Module — a compiled program.
#
# At runtime the VM copies `initial_memory` into its memory array, sets IP to
# `entry`, and runs. Symbol tables are for debugging only — they let us print
# a readable layout.
# ---------------------------------------------------------------------------

@dataclass
class Module:
    code: list[Instruction] = field(default_factory=list)
    entry: int = 0
    initial_memory: list[Any] = field(default_factory=list)
    # Debug metadata (not consulted at runtime):
    symbol_table: dict[str, int] = field(default_factory=dict)   # name → address
    symbol_types: dict[str, TypeCode] = field(default_factory=dict)  # name → type


# ---------------------------------------------------------------------------
# Pretty-printing.
# ---------------------------------------------------------------------------

def dump_module(mod: Module) -> str:
    out = ["=== module ===", f"entry: {mod.entry}", ""]

    out.append("memory layout:")
    # Invert symbol_table so we can annotate addresses with names.
    addr_to_name = {addr: name for name, addr in mod.symbol_table.items()}
    for addr, value in enumerate(mod.initial_memory):
        name = addr_to_name.get(addr)
        if name is None:
            label = "(constant)"
            ty = _guess_type(value)
        else:
            label = name
            ty = mod.symbol_types.get(name)
            ty_name = ty.name if ty else "?"
            ty = ty_name
        ty_name = ty.name if hasattr(ty, "name") else ty
        out.append(f"  [{addr:3}] {value!r:15}  {label:<10}  : {ty_name}")
    out.append("")

    out.append("code:")
    for i, instr in enumerate(mod.code):
        args = ", ".join(str(o) for o in instr.operands)
        marker = "->" if i == mod.entry else "  "
        out.append(f"  {marker} {i:3}: {instr.op.name:<6} {args}")
    return "\n".join(out)


def _guess_type(value: Any) -> str:
    if isinstance(value, bool):  return "BOOL"
    if isinstance(value, int):   return "I64"
    if isinstance(value, float): return "F64"
    if isinstance(value, str):   return "TEXT"
    if value is None:            return "NONE"
    return "?"
