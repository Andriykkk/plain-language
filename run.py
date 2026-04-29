from lexer import tokenize
from parser import Parser
from compiler import compile_program
from vm import execute
from bytecode import dump_module


def run(source: str, show_bytecode: bool = False) -> None:
    tokens = tokenize(source)
    program = Parser(source, tokens).parse_program()
    # The import block is parsed but not yet processed — the loader will
    # consume `program.imports` once it lands. For now compile only the
    # statement body, which is the same code path as before.
    module = compile_program(program.stmts)
    if show_bytecode:
        print(dump_module(module))
        print("=== output ===")
    execute(module)


if __name__ == "__main__":
    demo = '''# simple if / else
define function calculate_total
    set total to 5
    return total
end
set x to call calculate_total
print x
'''
    run(demo)
