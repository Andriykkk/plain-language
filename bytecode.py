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
    I8  = auto()
    I32 = auto()
    I64 = auto()
    F32 = auto()
    F64 = auto()
    # TEXT is not a runtime concept — it's a compile-time label meaning
    # "array of I8 intended to be printed as characters." At the VM level,
    # a TEXT value is an array of I8 just like any other; only PRINT_TEXT
    # treats it specially.
    TEXT = auto()
    BOOL = auto()
    NONE = auto()
    REF  = auto()    # pointer to an OS-allocated block (array, map, record, ...)


# ---------------------------------------------------------------------------
# Opcodes.
#
# LOAD / STORE     — direct access to main memory (compile-time address).
# LOAD_AT / STORE_AT  — indirect: read the pointer held in a register, then
#                       access that OS-allocated block at a runtime offset.
# ALLOC / APPEND / LEN — lifecycle and simple array ops on heap-allocated
#                       blocks that live outside the main memory array.
#
# PRINT, HALT       — I/O and program end.
# ---------------------------------------------------------------------------

class Opcode(Enum):
    LOAD  = auto()    # (r_dst, mem_addr)          — r_dst = memory[addr]
    STORE = auto()    # (r_src, mem_addr)          — memory[addr] = r_src

    ALLOC    = auto()  # (r_dst, size_imm)          — r_dst = new array of given size
    LOAD_AT  = auto()  # (r_dst, r_ptr, r_off)      — r_dst = (*r_ptr)[r_off]
    STORE_AT = auto()  # (r_src, r_ptr, r_off)      — (*r_ptr)[r_off] = r_src
    APPEND   = auto()  # (r_ptr, r_val)             — (*r_ptr).append(r_val)
    LEN      = auto()  # (r_dst, r_ptr)             — r_dst = len(*r_ptr)

    # Typed arithmetic — each opcode takes (r_dst, r_a, r_b). The compiler
    # picks the one matching the operand types; it inserts conversions
    # beforehand when a mixed-type expression needs promotion.
    # In Python all four int widths and both float widths dispatch to native
    # arithmetic; the type distinction is purely compile-time here but will
    # become real machine instructions when this is ported to C.
    ADD_I8  = auto(); ADD_I32 = auto(); ADD_I64 = auto(); ADD_F32 = auto(); ADD_F64 = auto()
    SUB_I8  = auto(); SUB_I32 = auto(); SUB_I64 = auto(); SUB_F32 = auto(); SUB_F64 = auto()
    MUL_I8  = auto(); MUL_I32 = auto(); MUL_I64 = auto(); MUL_F32 = auto(); MUL_F64 = auto()
    # Division always produces a float — there's no DIV_Ixx.
    # Integer operands get promoted to F64 first.
    DIV_F32 = auto(); DIV_F64 = auto()

    # Typed conversions — (r_dst, r_src). One per distinct numeric type pair.
    CVT_I8_I32  = auto(); CVT_I8_I64  = auto()
    CVT_I32_I8  = auto(); CVT_I64_I8  = auto()
    CVT_I32_I64 = auto(); CVT_I64_I32 = auto()
    CVT_F32_F64 = auto(); CVT_F64_F32 = auto()
    CVT_I8_F32  = auto(); CVT_I8_F64  = auto()
    CVT_I32_F32 = auto(); CVT_I32_F64 = auto()
    CVT_I64_F32 = auto(); CVT_I64_F64 = auto()
    CVT_F32_I8  = auto(); CVT_F64_I8  = auto()
    CVT_F32_I32 = auto(); CVT_F32_I64 = auto()
    CVT_F64_I32 = auto(); CVT_F64_I64 = auto()

    # Typed comparisons — result is BOOL in r_dst.
    EQ_I8  = auto(); NE_I8  = auto(); LT_I8  = auto(); LE_I8  = auto(); GT_I8  = auto(); GE_I8  = auto()
    EQ_I32 = auto(); NE_I32 = auto(); LT_I32 = auto(); LE_I32 = auto(); GT_I32 = auto(); GE_I32 = auto()
    EQ_I64 = auto(); NE_I64 = auto(); LT_I64 = auto(); LE_I64 = auto(); GT_I64 = auto(); GE_I64 = auto()
    EQ_F32 = auto(); NE_F32 = auto(); LT_F32 = auto(); LE_F32 = auto(); GT_F32 = auto(); GE_F32 = auto()
    EQ_F64 = auto(); NE_F64 = auto(); LT_F64 = auto(); LE_F64 = auto(); GT_F64 = auto(); GE_F64 = auto()
    EQ_BOOL = auto(); NE_BOOL = auto()
    EQ_REF  = auto(); NE_REF  = auto()

    # Typed bitwise — integer-only. Float operands are rejected at compile
    # time. Same (r_dst, r_a, r_b) shape as arithmetic; SHL/SHR take the
    # shift amount in r_b. BIT_NOT is unary, (r_dst, r_src).
    BIT_AND_I8  = auto(); BIT_AND_I32 = auto(); BIT_AND_I64 = auto()
    BIT_OR_I8   = auto(); BIT_OR_I32  = auto(); BIT_OR_I64  = auto()
    BIT_XOR_I8  = auto(); BIT_XOR_I32 = auto(); BIT_XOR_I64 = auto()
    BIT_NOT_I8  = auto(); BIT_NOT_I32 = auto(); BIT_NOT_I64 = auto()
    SHL_I8      = auto(); SHL_I32     = auto(); SHL_I64     = auto()
    SHR_I8      = auto(); SHR_I32     = auto(); SHR_I64     = auto()

    # Control flow. `target` is an absolute instruction index in the code list.
    # Placeholder target of None during compilation; patched when known.
    JMP  = auto()  # (target,)
    JMPF = auto()  # (r_cond, target)   — jump if r_cond is false
    JMPT = auto()  # (r_cond, target)   — jump if r_cond is true

    # Stack & function ops.
    #
    # The VM has a single global value stack (separate from `memory`). It
    # grows downward, CPU-style: `sp` decreases on PUSH, increases on POP.
    # No frames — argument layout is the caller's contract with the callee,
    # tracked entirely at compile time. Return value travels through a
    # dedicated heap slot (allocated on first use), so the result survives
    # arbitrary register churn between RET and the caller picking it up.
    PUSH        = auto()  # (r_src,)            — sp -= 1; stack[sp] = r_src
    POP         = auto()  # (r_dst,)            — r_dst = stack[sp]; sp += 1
    DROP        = auto()  # (count,)            — sp += count   (discard N slots)
    LOAD_STACK  = auto()  # (r_dst, offset)     — r_dst = stack[sp + offset]
    STORE_STACK = auto()  # (r_src, offset)     — stack[sp + offset] = r_src
    CALL        = auto()  # (target,)           — push ip+1; jump to target
    RET         = auto()  # ()                  — pop return address; jump to it

    # Print ops don't add their own newline. `compile_print` emits one
    # `PRINT_NEWLINE` after the last part of a `print A and B and ...`
    # statement, and `PRINT_SPACE` between adjacent parts.
    PRINT         = auto()  # (r_src,)        — write str(value), no newline
    PRINT_TEXT    = auto()  # (r_src,)        — decode a list of char codes and write
    PRINT_SPACE   = auto()  # ()              — write a single space
    PRINT_NEWLINE = auto()  # ()              — write a single '\n'
    HALT          = auto()


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
class FieldLayout:
    """One field inside a record. Offsets and sizes are in slots — primitives
    take 1 slot; nested record fields take their inner record's full size and
    are stored inline. `record_name` is set when the field is itself a record,
    so the compiler can keep walking the layout for chained access."""
    name: str
    type: "TypeCode"
    offset: int
    size: int
    record_name: str | None = None


@dataclass
class RecordLayout:
    """Compile-time layout of a record type. Fields hold their own pre-
    computed offset and size; the record's total size is the sum, supporting
    inlined nested records. The VM never sees this — it only sees flat slot
    reads/writes — but the compiler uses it to compute every offset that
    eventually shows up in LOAD_AT / STORE_AT instructions."""
    name: str
    fields: list[FieldLayout]

    @property
    def size(self) -> int:
        return sum(f.size for f in self.fields)

    def find(self, name: str) -> FieldLayout | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None


@dataclass
class Module:
    code: list[Instruction] = field(default_factory=list)
    entry: int = 0
    initial_memory: list[Any] = field(default_factory=list)
    # Debug / compile-time metadata (not consulted at runtime):
    symbol_table: dict[str, int] = field(default_factory=dict)   # name → address
    symbol_types: dict[str, TypeCode] = field(default_factory=dict)  # name → type
    # For matrix variables: the compile-time-known shape (rows, cols, ...).
    # The VM doesn't know this — matrices are just flat arrays at runtime.
    symbol_shapes: dict[str, tuple[int, ...]] = field(default_factory=dict)
    # For list / matrix / text variables: the type of the elements inside.
    # Used so that e.g. `xs[i]` can return its real element type instead of
    # the generic REF the VM sees.
    symbol_elem_types: dict[str, TypeCode] = field(default_factory=dict)
    # When a list/matrix has elements of a record type, this maps the
    # variable to the record's name so the compiler can compute the
    # per-element stride and resolve `xs[i].field` chains.
    symbol_elem_record_types: dict[str, str] = field(default_factory=dict)
    # For record variables: the name of the record type. Used to look up
    # field offsets at field-access time.
    symbol_record_types: dict[str, str] = field(default_factory=dict)
    # All record types defined in the program. Populated when the compiler
    # encounters a `define record <Name>`.
    records: dict[str, RecordLayout] = field(default_factory=dict)


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
    # Best-effort guess — the compiler's type metadata (symbol_types) is the
    # authoritative source; this is only for dump output on unlabeled cells.
    if isinstance(value, bool):  return "BOOL"
    if isinstance(value, int):   return "I64"
    if isinstance(value, float): return "F64"
    if isinstance(value, str):   return "TEXT"
    if isinstance(value, list):  return "REF"
    if value is None:            return "NONE"
    return "?"
