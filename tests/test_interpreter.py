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


if __name__ == "__main__":
    unittest.main()
