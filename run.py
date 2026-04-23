from lexer import tokenize
from parser import Parser
from evaluator import execute_program
from compiler import compile_program
from vm import execute


def run(source: str) -> None:
    """Reference interpreter — tree-walking, dynamic. Supports the full language."""
    tokens = tokenize(source)
    stmts = Parser(source, tokens).parse_program()
    execute_program(stmts)


def run_bytecode(source: str) -> None:
    """Bytecode compiler + VM — statically typed, currently supports a subset."""
    tokens = tokenize(source)
    stmts = Parser(source, tokens).parse_program()
    module = compile_program(stmts)
    execute(module)


if __name__ == "__main__":
    # Subset the bytecode path currently supports:
    #   - integer literals (i64) and float literals (f64)
    #   - set <name> to <expr>       (type inferred at first assignment, locked after)
    #   - arithmetic: + - * /  (and plus/minus/times/divided by)
    #   - variable references
    #   - print <expr>
    #   - implicit int→float promotion on mixed arithmetic
    demo = '''# integer math stays integer
set x to 5
set y to 3
set z to x + y * 2
print z

# float arithmetic
set pi to 3.14
set area to pi * x * x
print area

# int + float promotes to float
set mix to x + pi
print mix

# division always produces float
set half to x / y
print half
'''
    run_bytecode(demo)
