import io
import unittest
from contextlib import redirect_stdout

from evaluator import RunError
from parser import ParseError
from run import run


def run_capture(source: str) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        run(source)
    return buf.getvalue()


class TestLiterals(unittest.TestCase):
    def test_set_and_print_number(self):
        self.assertEqual(run_capture("set x to 5\nprint x\n"), "5\n")

    def test_print_string(self):
        self.assertEqual(run_capture('set s to "hello"\nprint s\n'), "hello\n")

    def test_string_escapes(self):
        self.assertEqual(run_capture('print "a\\nb"\n'), "a\nb\n")

    def test_booleans_and_none(self):
        self.assertEqual(
            run_capture("print true and false and none\n"),
            "True False None\n",
        )

    def test_float_literal(self):
        self.assertEqual(run_capture("set x to 3.14\nprint x\n"), "3.14\n")


class TestArithmetic(unittest.TestCase):
    def test_precedence_times_before_plus(self):
        self.assertEqual(run_capture("set x to 2 plus 3 times 4\nprint x\n"), "14\n")

    def test_parentheses_override_precedence(self):
        self.assertEqual(run_capture("set x to (2 plus 3) times 4\nprint x\n"), "20\n")

    def test_minus_and_plus_chain(self):
        self.assertEqual(run_capture("set x to 10 minus 3 plus 1\nprint x\n"), "8\n")

    def test_divided_by(self):
        self.assertEqual(run_capture("set x to 10 divided by 4\nprint x\n"), "2.5\n")

    def test_multiline_expression_inside_parens(self):
        src = """set x to (
    2 plus 3
) times 4
print x
"""
        self.assertEqual(run_capture(src), "20\n")


class TestStatementOps(unittest.TestCase):
    def test_add_to(self):
        self.assertEqual(run_capture("set x to 10\nadd 5 to x\nprint x\n"), "15\n")

    def test_subtract_from(self):
        self.assertEqual(run_capture("set x to 10\nsubtract 3 from x\nprint x\n"), "7\n")

    def test_multiply_by(self):
        self.assertEqual(run_capture("set x to 4\nmultiply x by 3\nprint x\n"), "12\n")

    def test_divide_by(self):
        self.assertEqual(run_capture("set x to 10\ndivide x by 4\nprint x\n"), "2.5\n")

    def test_all_four_in_sequence(self):
        src = """set x to 10
add 5 to x
subtract 3 from x
multiply x by 2
divide x by 4
print x
"""
        self.assertEqual(run_capture(src), "6.0\n")


class TestPrint(unittest.TestCase):
    def test_print_joins_with_and(self):
        self.assertEqual(
            run_capture('set x to 42\nprint "answer is" and x\n'),
            "answer is 42\n",
        )

    def test_print_expression(self):
        self.assertEqual(run_capture("print 2 plus 3\n"), "5\n")


class TestProgramStructure(unittest.TestCase):
    def test_comments_and_blank_lines_ignored(self):
        src = """# leading comment

set x to 1
# between
set y to 2

# before print
print x plus y
"""
        self.assertEqual(run_capture(src), "3\n")


class TestComparisons(unittest.TestCase):
    def test_equal_to(self):
        self.assertEqual(run_capture("print 5 is equal to 5\n"), "True\n")
        self.assertEqual(run_capture("print 5 is equal to 6\n"), "False\n")

    def test_not_equal_to(self):
        self.assertEqual(run_capture("print 5 is not equal to 6\n"), "True\n")
        self.assertEqual(run_capture("print 5 is not equal to 5\n"), "False\n")

    def test_greater_than(self):
        self.assertEqual(run_capture("print 5 is greater than 3\n"), "True\n")
        self.assertEqual(run_capture("print 3 is greater than 5\n"), "False\n")

    def test_less_than(self):
        self.assertEqual(run_capture("print 3 is less than 5\n"), "True\n")

    def test_at_least(self):
        self.assertEqual(run_capture("print 5 is at least 5\n"), "True\n")
        self.assertEqual(run_capture("print 4 is at least 5\n"), "False\n")

    def test_at_most(self):
        self.assertEqual(run_capture("print 5 is at most 5\n"), "True\n")
        self.assertEqual(run_capture("print 6 is at most 5\n"), "False\n")

    def test_string_equality(self):
        self.assertEqual(run_capture('print "hi" is equal to "hi"\n'), "True\n")

    def test_comparison_with_expression(self):
        self.assertEqual(run_capture("print 2 plus 3 is equal to 5\n"), "True\n")


class TestBranches(unittest.TestCase):
    def test_if_true(self):
        src = """if 1 is less than 2
    print "yes"
end
"""
        self.assertEqual(run_capture(src), "yes\n")

    def test_if_false_no_else(self):
        src = """if 1 is greater than 2
    print "no"
end
"""
        self.assertEqual(run_capture(src), "")

    def test_if_else(self):
        src = """if 1 is greater than 2
    print "no"
else
    print "yes"
end
"""
        self.assertEqual(run_capture(src), "yes\n")

    def test_else_if_chain(self):
        src = """set x to 15
if x is greater than 100
    print "huge"
else if x is greater than 10
    print "medium"
else
    print "small"
end
"""
        self.assertEqual(run_capture(src), "medium\n")

    def test_nested_if(self):
        src = """set x to 5
if x is greater than 0
    if x is less than 10
        print "in range"
    end
end
"""
        self.assertEqual(run_capture(src), "in range\n")


class TestLoops(unittest.TestCase):
    def test_repeat_times(self):
        src = """set n to 0
repeat 5 times
    add 1 to n
end
print n
"""
        self.assertEqual(run_capture(src), "5\n")

    def test_repeat_times_with_plus_in_count(self):
        src = """set n to 0
repeat 2 plus 3 times
    add 1 to n
end
print n
"""
        self.assertEqual(run_capture(src), "5\n")

    def test_repeat_times_with_parens(self):
        src = """set n to 0
repeat (2 times 3) times
    add 1 to n
end
print n
"""
        self.assertEqual(run_capture(src), "6\n")

    def test_repeat_range_inclusive(self):
        src = """set total to 0
repeat for i from 1 to 5
    add i to total
end
print total
"""
        self.assertEqual(run_capture(src), "15\n")

    def test_repeat_while(self):
        src = """set n to 1
repeat while n is less than 10
    multiply n by 2
end
print n
"""
        self.assertEqual(run_capture(src), "16\n")

    def test_repeat_for_each_over_string(self):
        src = """set count to 0
repeat for each c in "abc"
    add 1 to count
end
print count
"""
        self.assertEqual(run_capture(src), "3\n")

    def test_stop(self):
        src = """set n to 0
repeat 10 times
    add 1 to n
    if n is equal to 3
        stop
    end
end
print n
"""
        self.assertEqual(run_capture(src), "3\n")

    def test_skip(self):
        src = """set count to 0
repeat for i from 1 to 5
    if i is equal to 3
        skip
    end
    add 1 to count
end
print count
"""
        self.assertEqual(run_capture(src), "4\n")


class TestBlockEnd(unittest.TestCase):
    def test_bare_end_closes_if(self):
        self.assertEqual(
            run_capture("if true\n    print 1\nend\n"),
            "1\n",
        )

    def test_end_if_closes_if(self):
        self.assertEqual(
            run_capture("if true\n    print 1\nend if\n"),
            "1\n",
        )

    def test_bare_end_closes_repeat(self):
        self.assertEqual(
            run_capture("repeat 1 times\n    print 1\nend\n"),
            "1\n",
        )

    def test_end_repeat_closes_repeat(self):
        self.assertEqual(
            run_capture("repeat 1 times\n    print 1\nend repeat\n"),
            "1\n",
        )

    def test_mismatched_end_repeat_on_if_errors(self):
        with self.assertRaises(ParseError):
            run_capture("if true\n    print 1\nend repeat\n")

    def test_mismatched_end_if_on_repeat_errors(self):
        with self.assertRaises(ParseError):
            run_capture("repeat 1 times\n    print 1\nend if\n")


class TestErrors(unittest.TestCase):
    def test_undefined_variable(self):
        with self.assertRaises(RunError):
            run_capture("print x\n")

    def test_two_statements_on_one_line(self):
        with self.assertRaises(ParseError):
            run_capture("set x to 1 set y to 2\n")

    def test_missing_to_after_set(self):
        with self.assertRaises(ParseError):
            run_capture("set x 5\n")

    def test_missing_expression_after_to(self):
        with self.assertRaises(ParseError):
            run_capture("set x to\n")

    def test_stop_outside_loop(self):
        with self.assertRaises(RunError):
            run_capture("stop\n")

    def test_skip_outside_loop(self):
        with self.assertRaises(RunError):
            run_capture("skip\n")

    def test_unterminated_if(self):
        with self.assertRaises(ParseError):
            run_capture("if true\n    print 1\n")


if __name__ == "__main__":
    unittest.main()
