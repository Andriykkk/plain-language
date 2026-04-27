"""
VM — executes bytecode.

One flat memory array (pre-filled from the module's `initial_memory`, padded
to MEMORY_SIZE with None for future growth). A small pool of scratch
registers. A single IP that walks the code list.
"""

import sys

from bytecode import Module, Opcode


class VMError(Exception):
    pass


MEMORY_SIZE = 4096       # total memory slots
REGISTER_COUNT = 32      # scratch register pool
STACK_SIZE = 1024        # value-stack capacity (downward-growing)


# Opcode groups for comparison dispatch — all variants of each operator
# share one handler in Python (the type distinction matters for codegen
# to native, not here).
_EQ_OPS = frozenset({
    Opcode.EQ_I8, Opcode.EQ_I32, Opcode.EQ_I64, Opcode.EQ_F32, Opcode.EQ_F64,
    Opcode.EQ_BOOL, Opcode.EQ_REF,
})
_NE_OPS = frozenset({
    Opcode.NE_I8, Opcode.NE_I32, Opcode.NE_I64, Opcode.NE_F32, Opcode.NE_F64,
    Opcode.NE_BOOL, Opcode.NE_REF,
})
_LT_OPS = frozenset({
    Opcode.LT_I8, Opcode.LT_I32, Opcode.LT_I64, Opcode.LT_F32, Opcode.LT_F64,
})
_LE_OPS = frozenset({
    Opcode.LE_I8, Opcode.LE_I32, Opcode.LE_I64, Opcode.LE_F32, Opcode.LE_F64,
})
_GT_OPS = frozenset({
    Opcode.GT_I8, Opcode.GT_I32, Opcode.GT_I64, Opcode.GT_F32, Opcode.GT_F64,
})
_GE_OPS = frozenset({
    Opcode.GE_I8, Opcode.GE_I32, Opcode.GE_I64, Opcode.GE_F32, Opcode.GE_F64,
})


def execute(module: Module) -> None:
    # Memory = constants + variables pre-populated by the compiler,
    # padded with empty slots for future dynamic allocations.
    memory: list = list(module.initial_memory)
    if len(memory) < MEMORY_SIZE:
        memory.extend([None] * (MEMORY_SIZE - len(memory)))

    registers: list = [None] * REGISTER_COUNT

    # Value stack. CPU-style: `sp` is the index of the topmost valid element
    # (or STACK_SIZE when the stack is empty — one past the end of the array).
    # PUSH decrements then writes; POP reads then increments.
    stack: list = [None] * STACK_SIZE
    sp = STACK_SIZE

    code = module.code
    n = len(code)
    ip = module.entry

    while ip < n:
        instr = code[ip]
        op = instr.op
        operands = instr.operands

        # --- control flow (set ip and skip the default increment) ---

        if op is Opcode.JMP:
            ip = operands[0]
            continue

        if op is Opcode.JMPF:
            r_cond, target = operands
            if not registers[r_cond]:
                ip = target
                continue
            # Fall through to the default `ip += 1` below.
            ip += 1
            continue

        if op is Opcode.JMPT:
            r_cond, target = operands
            if registers[r_cond]:
                ip = target
                continue
            ip += 1
            continue

        if op is Opcode.CALL:
            (target,) = operands
            sp -= 1
            stack[sp] = ip + 1     # return address
            ip = target
            continue

        if op is Opcode.RET:
            ret_addr = stack[sp]
            sp += 1
            ip = ret_addr
            continue

        if op is Opcode.LOAD:
            r_dst, addr = operands
            registers[r_dst] = memory[addr]

        elif op is Opcode.STORE:
            r_src, addr = operands
            memory[addr] = registers[r_src]

        elif op is Opcode.ALLOC:
            r_dst, size = operands
            # OS-managed allocation. In Python this is just a list; in the
            # eventual C port this would be `calloc(size, sizeof(slot))`.
            registers[r_dst] = [None] * size

        elif op is Opcode.LOAD_AT:
            r_dst, r_ptr, r_off = operands
            block = registers[r_ptr]
            off = registers[r_off]
            # Bounds check at the VM boundary — Python's IndexError isn't a
            # VMError, so the test harness wouldn't recognize it as a clean
            # runtime failure.
            if off < 0 or off >= len(block):
                raise VMError(
                    f"index {off} out of range for container of length {len(block)}"
                )
            registers[r_dst] = block[off]

        elif op is Opcode.STORE_AT:
            r_src, r_ptr, r_off = operands
            block = registers[r_ptr]
            off = registers[r_off]
            if off < 0 or off >= len(block):
                raise VMError(
                    f"index {off} out of range for container of length {len(block)}"
                )
            block[off] = registers[r_src]

        elif op is Opcode.APPEND:
            r_ptr, r_val = operands
            registers[r_ptr].append(registers[r_val])

        elif op is Opcode.LEN:
            r_dst, r_ptr = operands
            registers[r_dst] = len(registers[r_ptr])

        # --- value stack ------------------------------------------------------

        elif op is Opcode.PUSH:
            (r_src,) = operands
            sp -= 1
            stack[sp] = registers[r_src]

        elif op is Opcode.POP:
            (r_dst,) = operands
            registers[r_dst] = stack[sp]
            sp += 1

        elif op is Opcode.DROP:
            (count,) = operands
            sp += count

        elif op is Opcode.LOAD_STACK:
            r_dst, offset = operands
            registers[r_dst] = stack[sp + offset]

        elif op is Opcode.STORE_STACK:
            r_src, offset = operands
            stack[sp + offset] = registers[r_src]

        # --- typed arithmetic -------------------------------------------------
        # In Python, all four integer widths and both float widths dispatch
        # to native arithmetic. The type distinction is a compile-time
        # contract that becomes real in a C port where these become different
        # native instructions.

        elif op is Opcode.ADD_I8 or op is Opcode.ADD_I32 or op is Opcode.ADD_I64 \
             or op is Opcode.ADD_F32 or op is Opcode.ADD_F64:
            r_dst, r_a, r_b = operands
            registers[r_dst] = registers[r_a] + registers[r_b]

        elif op is Opcode.SUB_I8 or op is Opcode.SUB_I32 or op is Opcode.SUB_I64 \
             or op is Opcode.SUB_F32 or op is Opcode.SUB_F64:
            r_dst, r_a, r_b = operands
            registers[r_dst] = registers[r_a] - registers[r_b]

        elif op is Opcode.MUL_I8 or op is Opcode.MUL_I32 or op is Opcode.MUL_I64 \
             or op is Opcode.MUL_F32 or op is Opcode.MUL_F64:
            r_dst, r_a, r_b = operands
            registers[r_dst] = registers[r_a] * registers[r_b]

        elif op is Opcode.DIV_F32 or op is Opcode.DIV_F64:
            r_dst, r_a, r_b = operands
            b = registers[r_b]
            if b == 0 or b == 0.0:
                raise VMError("division by zero")
            registers[r_dst] = registers[r_a] / b

        # --- typed conversions ------------------------------------------------
        # Integer-to-integer and float-to-float are no-ops in Python (values
        # are unbounded ints / native floats). int→float uses float(), and
        # float→int uses int() (truncation toward zero).

        elif op is Opcode.CVT_I8_I32  or op is Opcode.CVT_I8_I64  \
             or op is Opcode.CVT_I32_I8 or op is Opcode.CVT_I64_I8 \
             or op is Opcode.CVT_I32_I64 or op is Opcode.CVT_I64_I32:
            r_dst, r_src = operands
            registers[r_dst] = registers[r_src]

        elif op is Opcode.CVT_F32_F64 or op is Opcode.CVT_F64_F32:
            r_dst, r_src = operands
            registers[r_dst] = registers[r_src]

        elif op is Opcode.CVT_I8_F32  or op is Opcode.CVT_I8_F64 \
             or op is Opcode.CVT_I32_F32 or op is Opcode.CVT_I32_F64 \
             or op is Opcode.CVT_I64_F32 or op is Opcode.CVT_I64_F64:
            r_dst, r_src = operands
            registers[r_dst] = float(registers[r_src])

        elif op is Opcode.CVT_F32_I8  or op is Opcode.CVT_F64_I8 \
             or op is Opcode.CVT_F32_I32 or op is Opcode.CVT_F32_I64 \
             or op is Opcode.CVT_F64_I32 or op is Opcode.CVT_F64_I64:
            r_dst, r_src = operands
            registers[r_dst] = int(registers[r_src])

        # --- typed comparisons (all produce BOOL; collapse to Python ops) ---

        elif op in _EQ_OPS:
            r_dst, r_a, r_b = operands
            registers[r_dst] = registers[r_a] == registers[r_b]
        elif op in _NE_OPS:
            r_dst, r_a, r_b = operands
            registers[r_dst] = registers[r_a] != registers[r_b]
        elif op in _LT_OPS:
            r_dst, r_a, r_b = operands
            registers[r_dst] = registers[r_a] < registers[r_b]
        elif op in _LE_OPS:
            r_dst, r_a, r_b = operands
            registers[r_dst] = registers[r_a] <= registers[r_b]
        elif op in _GT_OPS:
            r_dst, r_a, r_b = operands
            registers[r_dst] = registers[r_a] > registers[r_b]
        elif op in _GE_OPS:
            r_dst, r_a, r_b = operands
            registers[r_dst] = registers[r_a] >= registers[r_b]

        elif op is Opcode.PRINT:
            (r_src,) = operands
            sys.stdout.write(str(registers[r_src]))

        elif op is Opcode.PRINT_TEXT:
            (r_src,) = operands
            # Register holds an array of character codes — decode and write.
            chars = registers[r_src]
            sys.stdout.write("".join(chr(c) for c in chars))

        elif op is Opcode.PRINT_SPACE:
            sys.stdout.write(" ")

        elif op is Opcode.PRINT_NEWLINE:
            sys.stdout.write("\n")

        elif op is Opcode.HALT:
            return

        else:
            raise VMError(f"unknown opcode: {op}")

        ip += 1
