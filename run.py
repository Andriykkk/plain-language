from lexer import tokenize
from parser import Parser
from evaluator import execute_program


def run(source: str) -> None:
    tokens = tokenize(source)
    stmts = Parser(source, tokens).parse_program()
    execute_program(stmts)


if __name__ == "__main__":
    demo = '''# demo
set price to 10
set quantity to 3
set total to (price times quantity) plus 5
add 7 to total
multiply total by 2
subtract 4 from total
divide total by 2
print "total is" and total
print "half of total plus tax" and (total divided by 2) plus 1.5
'''
    run(demo)
