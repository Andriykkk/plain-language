from lexer import tokenize
from parser import Parser
from compiler import compile_program
from vm import execute
from bytecode import dump_module


def run(source: str, show_bytecode: bool = False) -> None:
    tokens = tokenize(source)
    stmts = Parser(source, tokens).parse_program()
    module = compile_program(stmts)
    if show_bytecode:
        print(dump_module(module))
        print("=== output ===")
    execute(module)


if __name__ == "__main__":
    demo = '''set name to "Alice"
print name

set xs to empty list of integer
append 10 to xs
append 20 to xs
append 30 to xs

print xs[0]
print xs[1]
print xs[2]
print length of xs

set xs[1] to 99
print xs[1]
'''
    run(demo, show_bytecode=True)
