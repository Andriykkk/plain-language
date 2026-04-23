from lexer import tokenize
from parser import Parser
from evaluator import execute_program


def run(source: str) -> None:
    tokens = tokenize(source)
    stmts = Parser(source, tokens).parse_program()
    execute_program(stmts)


if __name__ == "__main__":
    demo = '''# demo — matrices (zero-indexed)

# 5x5 multiplication table
set table to empty matrix 5 by 5 of number
repeat for i from 0 to 4
    repeat for j from 0 to 4
        set table[i, j] to (i plus 1) times (j plus 1)
    end
end
print "multiplication table:"
print table

# 2x2 matrix multiply: [[1,2],[3,4]] * [[5,6],[7,8]] = [[19,22],[43,50]]
set a to empty matrix 2 by 2 of number
set a[0, 0] to 1
set a[0, 1] to 2
set a[1, 0] to 3
set a[1, 1] to 4

set b to empty matrix 2 by 2 of number
set b[0, 0] to 5
set b[0, 1] to 6
set b[1, 0] to 7
set b[1, 1] to 8

set c to empty matrix 2 by 2 of number
repeat for i from 0 to 1
    repeat for j from 0 to 1
        set sum to 0
        repeat for k from 0 to 1
            add a[i, k] times b[k, j] to sum
        end
        set c[i, j] to sum
    end
end

print "a * b ="
print c

set m3d to empty matrix 2 by 2 by 2 of number
set m3d[0, 0, 0] to 1
set m3d[0, 0, 1] to 2

print m3d
'''
    run(demo)
