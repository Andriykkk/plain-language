from dataclasses import dataclass
from typing import Union

from lexer import Token, TK


# ---------- AST nodes ----------

@dataclass
class NumberLit:
    value: object  # int or float


@dataclass
class StringLit:
    value: str


@dataclass
class BoolLit:
    value: bool


@dataclass
class NoneLit:
    pass


@dataclass
class VarRef:
    name: str


@dataclass
class BinaryOp:
    op: str  # "plus" | "minus" | "times" | "divided"
    left: "Expr"
    right: "Expr"


Expr = Union[NumberLit, StringLit, BoolLit, NoneLit, VarRef, BinaryOp]


@dataclass
class SetStmt:
    target: str
    value: Expr


@dataclass
class AddStmt:
    amount: Expr
    target: str


@dataclass
class SubtractStmt:
    amount: Expr
    target: str


@dataclass
class MultiplyStmt:
    target: str
    factor: Expr


@dataclass
class DivideStmt:
    target: str
    divisor: Expr


@dataclass
class PrintStmt:
    parts: list[Expr]


Stmt = Union[SetStmt, AddStmt, SubtractStmt, MultiplyStmt, DivideStmt, PrintStmt]


# ---------- parser ----------

class ParseError(Exception):
    def __init__(self, message: str, token: Token):
        self.token = token
        super().__init__(f"{message} (at position {token.start})")


class Parser:
    def __init__(self, source: str, tokens: list[Token]):
        self.source = source
        self.tokens = tokens
        self.pos = 0
        self.bracket_depth = 0

    # ---- token helpers ----

    def _skip_ignored_newlines(self) -> None:
        if self.bracket_depth > 0:
            while self.tokens[self.pos].kind == TK.NEWLINE:
                self.pos += 1

    def peek(self) -> Token:
        self._skip_ignored_newlines()
        return self.tokens[self.pos]

    def advance(self) -> Token:
        self._skip_ignored_newlines()
        tok = self.tokens[self.pos]
        if tok.kind != TK.EOF:
            self.pos += 1
        if tok.kind in (TK.LPAREN, TK.LBRACKET):
            self.bracket_depth += 1
        elif tok.kind in (TK.RPAREN, TK.RBRACKET):
            self.bracket_depth -= 1
        return tok

    def text(self, tok: Token) -> str:
        return self.source[tok.start:tok.end]

    def match(self, kind: TK, text: str | None = None) -> bool:
        tok = self.peek()
        if tok.kind != kind:
            return False
        if text is not None and self.text(tok) != text:
            return False
        return True

    def consume(self, kind: TK, text: str | None = None) -> Token:
        if not self.match(kind, text):
            tok = self.peek()
            want = kind.name + (f" '{text}'" if text else "")
            got = f"{tok.kind.name} {self.text(tok)!r}"
            raise ParseError(f"expected {want}, got {got}", tok)
        return self.advance()

    def _skip_blank_lines(self) -> None:
        while self.tokens[self.pos].kind == TK.NEWLINE:
            self.pos += 1

    def _end_of_statement(self) -> None:
        tok = self.peek()
        if tok.kind == TK.EOF:
            return
        if tok.kind == TK.NEWLINE:
            self._skip_blank_lines()
            return
        raise ParseError(
            f"expected end of line after statement, got {self.text(tok)!r}", tok
        )

    # ---- entry point ----

    def parse_program(self) -> list[Stmt]:
        self._skip_blank_lines()
        stmts: list[Stmt] = []
        while self.peek().kind != TK.EOF:
            stmts.append(self.parse_statement())
            self._end_of_statement()
        return stmts

    # ---- statements ----

    def parse_statement(self) -> Stmt:
        tok = self.peek()
        if tok.kind == TK.KEYWORD:
            word = self.text(tok)
            if word == "set":      return self.parse_set()
            if word == "add":      return self.parse_add()
            if word == "subtract": return self.parse_subtract()
            if word == "multiply": return self.parse_multiply()
            if word == "divide":   return self.parse_divide()
            if word == "print":    return self.parse_print()
        raise ParseError(f"unexpected token {self.text(tok)!r}", tok)

    def parse_set(self) -> SetStmt:
        self.consume(TK.KEYWORD, "set")
        name = self.text(self.consume(TK.IDENT))
        self.consume(TK.KEYWORD, "to")
        value = self.parse_expression()
        return SetStmt(name, value)

    def parse_add(self) -> AddStmt:
        self.consume(TK.KEYWORD, "add")
        amount = self.parse_expression()
        self.consume(TK.KEYWORD, "to")
        target = self.text(self.consume(TK.IDENT))
        return AddStmt(amount, target)

    def parse_subtract(self) -> SubtractStmt:
        self.consume(TK.KEYWORD, "subtract")
        amount = self.parse_expression()
        self.consume(TK.KEYWORD, "from")
        target = self.text(self.consume(TK.IDENT))
        return SubtractStmt(amount, target)

    def parse_multiply(self) -> MultiplyStmt:
        self.consume(TK.KEYWORD, "multiply")
        target = self.text(self.consume(TK.IDENT))
        self.consume(TK.KEYWORD, "by")
        factor = self.parse_expression()
        return MultiplyStmt(target, factor)

    def parse_divide(self) -> DivideStmt:
        self.consume(TK.KEYWORD, "divide")
        target = self.text(self.consume(TK.IDENT))
        self.consume(TK.KEYWORD, "by")
        divisor = self.parse_expression()
        return DivideStmt(target, divisor)

    def parse_print(self) -> PrintStmt:
        self.consume(TK.KEYWORD, "print")
        parts = [self.parse_expression()]
        while self.match(TK.KEYWORD, "and"):
            self.advance()
            parts.append(self.parse_expression())
        return PrintStmt(parts)

    # ---- expressions (two precedence levels) ----

    def parse_expression(self) -> Expr:
        return self.parse_addition()

    def parse_addition(self) -> Expr:
        left = self.parse_multiplication()
        while self.match(TK.KEYWORD, "plus") or self.match(TK.KEYWORD, "minus"):
            op = self.text(self.advance())
            right = self.parse_multiplication()
            left = BinaryOp(op, left, right)
        return left

    def parse_multiplication(self) -> Expr:
        left = self.parse_primary()
        while True:
            if self.match(TK.KEYWORD, "times"):
                self.advance()
                right = self.parse_primary()
                left = BinaryOp("times", left, right)
            elif self.match(TK.KEYWORD, "divided"):
                self.advance()
                self.consume(TK.KEYWORD, "by")
                right = self.parse_primary()
                left = BinaryOp("divided", left, right)
            else:
                break
        return left

    def parse_primary(self) -> Expr:
        tok = self.peek()

        if tok.kind == TK.NUMBER:
            self.advance()
            raw = self.text(tok)
            return NumberLit(float(raw) if "." in raw else int(raw))

        if tok.kind == TK.STRING:
            self.advance()
            return StringLit(decode_string(self.text(tok)))

        if tok.kind == TK.KEYWORD:
            word = self.text(tok)
            if word == "true":
                self.advance()
                return BoolLit(True)
            if word == "false":
                self.advance()
                return BoolLit(False)
            if word == "none":
                self.advance()
                return NoneLit()

        if tok.kind == TK.IDENT:
            self.advance()
            return VarRef(self.text(tok))

        if tok.kind == TK.LPAREN:
            self.advance()
            expr = self.parse_expression()
            self.consume(TK.RPAREN)
            return expr

        raise ParseError(f"expected expression, got {self.text(tok)!r}", tok)


def decode_string(raw: str) -> str:
    inner = raw[1:-1]
    out: list[str] = []
    i = 0
    while i < len(inner):
        c = inner[i]
        if c == "\\" and i + 1 < len(inner):
            nxt = inner[i + 1]
            if nxt == "n":
                out.append("\n")
            elif nxt == "t":
                out.append("\t")
            elif nxt == '"':
                out.append('"')
            elif nxt == "\\":
                out.append("\\")
            else:
                out.append(nxt)
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)
