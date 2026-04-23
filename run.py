from lexer import tokenize
from parser import Parser
from evaluator import execute_program


def run(source: str) -> None:
    tokens = tokenize(source)
    stmts = Parser(source, tokens).parse_program()
    execute_program(stmts)


if __name__ == "__main__":
    demo = '''# demo
set total to 0
repeat for i from 1 to 5
    add i to total
end
print "sum 1..5 is" and total

if total is greater than 10
    print "big"
else if total is equal to 10
    print "exactly ten"
else
    print "small"
end if

set n to 1
repeat while n is less than 20
    multiply n by 2
end repeat
print "n is" and n

set seen to 0
repeat 10 times
    add 1 to seen
    if seen is equal to 3
        skip
    end
    if seen is at least 7
        stop
    end
    print "seen is" and seen
end
print "final seen is" and seen
'''
    run(demo)
