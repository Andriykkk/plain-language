"""
VM — executes bytecode.

One flat memory array (pre-filled from the module's `initial_memory`, padded
to MEMORY_SIZE with None for future growth). A small pool of scratch
registers. A single IP that walks the code list.
"""

from bytecode import Module, Opcode


class VMError(Exception):
    pass


MEMORY_SIZE = 4096       # total memory slots
REGISTER_COUNT = 32      # scratch register pool


def execute(module: Module) -> None:
    # Memory = constants + variables pre-populated by the compiler,
    # padded with empty slots for future dynamic allocations.
    memory: list = list(module.initial_memory)
    if len(memory) < MEMORY_SIZE:
        memory.extend([None] * (MEMORY_SIZE - len(memory)))

    registers: list = [None] * REGISTER_COUNT

    code = module.code
    n = len(code)
    ip = module.entry

    while ip < n:
        instr = code[ip]
        op = instr.op
        operands = instr.operands

        if op is Opcode.LOAD:
            r_dst, addr = operands
            registers[r_dst] = memory[addr]

        elif op is Opcode.STORE:
            r_src, addr = operands
            memory[addr] = registers[r_src]

        elif op is Opcode.PRINT:
            (r_src,) = operands
            print(registers[r_src])

        elif op is Opcode.HALT:
            return

        else:
            raise VMError(f"unknown opcode: {op}")

        ip += 1
