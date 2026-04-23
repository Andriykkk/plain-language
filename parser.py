from dataclasses import dataclass
from typing import Union

from lexer import Token, TK


# ---------- AST: types ----------

@dataclass
class TypeRef:
    name: str
    inner: list["TypeRef"]


# ---------- AST: expressions ----------

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


@dataclass
class Compare:
    op: str  # "equal" | "not_equal" | "greater" | "less" | "at_least" | "at_most"
    left: "Expr"
    right: "Expr"


@dataclass
class CallExpr:
    name: str
    args: list["Expr"]


@dataclass
class FieldAccess:
    obj: "Expr"
    field: str


@dataclass
class IndexAccess:
    obj: "Expr"
    indices: list["Expr"]


@dataclass
class NewExpr:
    type_name: str


@dataclass
class EmptyList:
    elem_type: TypeRef


@dataclass
class EmptyMap:
    key_type: TypeRef
    value_type: TypeRef


@dataclass
class EmptyMatrix:
    dims: list["Expr"]
    elem_type: TypeRef


@dataclass
class LengthExpr:
    value: "Expr"


@dataclass
class RowsExpr:
    value: "Expr"


@dataclass
class ColumnsExpr:
    value: "Expr"


Expr = Union[
    NumberLit, StringLit, BoolLit, NoneLit, VarRef, BinaryOp, Compare, CallExpr,
    FieldAccess, IndexAccess, NewExpr, EmptyList, EmptyMap, EmptyMatrix,
    LengthExpr, RowsExpr, ColumnsExpr,
]


# ---------- AST: lvalues (assignment targets) ----------

@dataclass
class VarLValue:
    name: str


@dataclass
class FieldLValue:
    obj: Expr
    field: str


@dataclass
class IndexLValue:
    obj: Expr
    indices: list[Expr]


LValue = Union[VarLValue, FieldLValue, IndexLValue]


# ---------- AST: statements ----------

@dataclass
class SetStmt:
    target: LValue
    value: Expr


@dataclass
class AddStmt:
    amount: Expr
    target: LValue


@dataclass
class SubtractStmt:
    amount: Expr
    target: LValue


@dataclass
class MultiplyStmt:
    target: LValue
    factor: Expr


@dataclass
class DivideStmt:
    target: LValue
    divisor: Expr


@dataclass
class AppendStmt:
    value: Expr
    target: LValue


@dataclass
class PrintStmt:
    parts: list[Expr]


@dataclass
class IfStmt:
    condition: Expr
    then_block: list["Stmt"]
    else_block: list["Stmt"] | None


@dataclass
class RepeatTimesStmt:
    count: Expr
    body: list["Stmt"]


@dataclass
class RepeatForEachStmt:
    var: str
    iterable: Expr
    body: list["Stmt"]


@dataclass
class RepeatRangeStmt:
    var: str
    start: Expr
    end: Expr
    body: list["Stmt"]


@dataclass
class RepeatWhileStmt:
    condition: Expr
    body: list["Stmt"]


@dataclass
class StopStmt:
    pass


@dataclass
class SkipStmt:
    pass


@dataclass
class FunctionDef:
    name: str
    params: list[tuple[str, TypeRef]]
    return_type: TypeRef | None
    body: list["Stmt"]


@dataclass
class RecordDef:
    name: str
    fields: list[tuple[str, TypeRef]]


@dataclass
class ReturnStmt:
    value: Expr | None


@dataclass
class CallStmt:
    call: CallExpr


Stmt = Union[
    SetStmt, AddStmt, SubtractStmt, MultiplyStmt, DivideStmt, AppendStmt,
    PrintStmt, IfStmt, RepeatTimesStmt, RepeatForEachStmt, RepeatRangeStmt,
    RepeatWhileStmt, StopStmt, SkipStmt,
    FunctionDef, RecordDef, ReturnStmt, CallStmt,
]


BLOCK_KINDS = {"if", "repeat", "function", "record"}


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

    # ---- block helpers ----

    def parse_block_until(self, terminators: set[str]) -> list[Stmt]:
        stmts: list[Stmt] = []
        while True:
            tok = self.peek()
            if tok.kind == TK.EOF:
                raise ParseError("unexpected end of file inside block", tok)
            if tok.kind == TK.KEYWORD and self.text(tok) in terminators:
                return stmts
            stmts.append(self.parse_statement())
            self._end_of_statement()

    def consume_block_end(self, kind: str, opener: Token) -> None:
        self.consume(TK.KEYWORD, "end")
        tok = self.peek()
        if tok.kind == TK.KEYWORD:
            word = self.text(tok)
            if word in BLOCK_KINDS:
                if word != kind:
                    raise ParseError(
                        f"'{kind}' block opened at position {opener.start} "
                        f"cannot be closed with 'end {word}'; "
                        f"use 'end' or 'end {kind}'",
                        tok,
                    )
                self.advance()

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
            if word == "append":   return self.parse_append()
            if word == "print":    return self.parse_print()
            if word == "if":       return self.parse_if()
            if word == "repeat":   return self.parse_repeat()
            if word == "define":   return self.parse_define()
            if word == "return":   return self.parse_return()
            if word == "call":     return CallStmt(self.parse_call())
            if word == "stop":
                self.advance()
                return StopStmt()
            if word == "skip":
                self.advance()
                return SkipStmt()
        raise ParseError(f"unexpected token {self.text(tok)!r}", tok)

    def parse_set(self) -> SetStmt:
        self.consume(TK.KEYWORD, "set")
        target = self.parse_lvalue()
        self.consume(TK.KEYWORD, "to")
        value = self.parse_expression()
        return SetStmt(target, value)

    def parse_add(self) -> AddStmt:
        self.consume(TK.KEYWORD, "add")
        amount = self.parse_expression()
        self.consume(TK.KEYWORD, "to")
        target = self.parse_lvalue()
        return AddStmt(amount, target)

    def parse_subtract(self) -> SubtractStmt:
        self.consume(TK.KEYWORD, "subtract")
        amount = self.parse_expression()
        self.consume(TK.KEYWORD, "from")
        target = self.parse_lvalue()
        return SubtractStmt(amount, target)

    def parse_multiply(self) -> MultiplyStmt:
        self.consume(TK.KEYWORD, "multiply")
        target = self.parse_lvalue()
        self.consume(TK.KEYWORD, "by")
        factor = self.parse_expression()
        return MultiplyStmt(target, factor)

    def parse_divide(self) -> DivideStmt:
        self.consume(TK.KEYWORD, "divide")
        target = self.parse_lvalue()
        self.consume(TK.KEYWORD, "by")
        divisor = self.parse_expression()
        return DivideStmt(target, divisor)

    def parse_append(self) -> AppendStmt:
        self.consume(TK.KEYWORD, "append")
        value = self.parse_expression()
        self.consume(TK.KEYWORD, "to")
        target = self.parse_lvalue()
        return AppendStmt(value, target)

    def parse_print(self) -> PrintStmt:
        self.consume(TK.KEYWORD, "print")
        parts = [self.parse_expression()]
        while self.match(TK.KEYWORD, "and"):
            self.advance()
            parts.append(self.parse_expression())
        return PrintStmt(parts)

    def parse_if(self) -> IfStmt:
        opener = self.consume(TK.KEYWORD, "if")
        condition = self.parse_expression()
        self._end_of_statement()
        then_block = self.parse_block_until({"else", "end"})
        else_block: list[Stmt] | None = None

        if self.match(TK.KEYWORD, "else"):
            self.advance()
            if self.match(TK.KEYWORD, "if"):
                else_block = [self.parse_if()]
                return IfStmt(condition, then_block, else_block)
            self._end_of_statement()
            else_block = self.parse_block_until({"end"})

        self.consume_block_end("if", opener)
        return IfStmt(condition, then_block, else_block)

    def parse_repeat(self) -> Stmt:
        opener = self.consume(TK.KEYWORD, "repeat")

        if self.match(TK.KEYWORD, "while"):
            self.advance()
            condition = self.parse_expression()
            self._end_of_statement()
            body = self.parse_block_until({"end"})
            self.consume_block_end("repeat", opener)
            return RepeatWhileStmt(condition, body)

        if self.match(TK.KEYWORD, "for"):
            self.advance()
            if self.match(TK.KEYWORD, "each"):
                self.advance()
                var = self.text(self.consume(TK.IDENT))
                self.consume(TK.KEYWORD, "in")
                iterable = self.parse_expression()
                self._end_of_statement()
                body = self.parse_block_until({"end"})
                self.consume_block_end("repeat", opener)
                return RepeatForEachStmt(var, iterable, body)
            var = self.text(self.consume(TK.IDENT))
            self.consume(TK.KEYWORD, "from")
            start = self.parse_expression()
            self.consume(TK.KEYWORD, "to")
            end_expr = self.parse_expression()
            self._end_of_statement()
            body = self.parse_block_until({"end"})
            self.consume_block_end("repeat", opener)
            return RepeatRangeStmt(var, start, end_expr, body)

        # "repeat N times": 'times' here is the loop marker, not multiplication.
        count = self.parse_addition(allow_times=False)
        self.consume(TK.KEYWORD, "times")
        self._end_of_statement()
        body = self.parse_block_until({"end"})
        self.consume_block_end("repeat", opener)
        return RepeatTimesStmt(count, body)

    def parse_define(self) -> Stmt:
        opener = self.consume(TK.KEYWORD, "define")
        if self.match(TK.KEYWORD, "function"):
            return self.parse_function_def(opener)
        if self.match(TK.KEYWORD, "record"):
            return self.parse_record_def(opener)
        tok = self.peek()
        raise ParseError(
            f"expected 'function' or 'record' after 'define', got {self.text(tok)!r}", tok
        )

    def parse_function_def(self, opener: Token) -> FunctionDef:
        self.consume(TK.KEYWORD, "function")
        name = self.text(self.consume(TK.IDENT))
        self._end_of_statement()

        params: list[tuple[str, TypeRef]] = []
        return_type: TypeRef | None = None

        while True:
            if self.match(TK.KEYWORD, "input"):
                self.advance()
                pname = self.text(self.consume(TK.IDENT))
                self.consume(TK.KEYWORD, "as")
                ptype = self.parse_type()
                params.append((pname, ptype))
                self._end_of_statement()
            elif self.match(TK.KEYWORD, "output"):
                self.advance()
                self.consume(TK.KEYWORD, "as")
                return_type = self.parse_type()
                self._end_of_statement()
            else:
                break

        body = self.parse_block_until({"end"})
        self.consume_block_end("function", opener)
        return FunctionDef(name, params, return_type, body)

    def parse_record_def(self, opener: Token) -> RecordDef:
        self.consume(TK.KEYWORD, "record")
        name = self.text(self.consume(TK.IDENT))
        self._end_of_statement()

        fields: list[tuple[str, TypeRef]] = []
        while True:
            tok = self.peek()
            if tok.kind == TK.KEYWORD and self.text(tok) == "end":
                break
            if tok.kind != TK.IDENT:
                raise ParseError(
                    f"expected field name or 'end', got {self.text(tok)!r}", tok
                )
            fname = self.text(self.advance())
            self.consume(TK.KEYWORD, "as")
            ftype = self.parse_type()
            fields.append((fname, ftype))
            self._end_of_statement()

        self.consume_block_end("record", opener)
        return RecordDef(name, fields)

    def parse_type(self) -> TypeRef:
        if self.match(TK.KEYWORD, "list"):
            self.advance()
            self.consume(TK.KEYWORD, "of")
            inner = self.parse_type()
            return TypeRef("list", [inner])
        if self.match(TK.KEYWORD, "map"):
            self.advance()
            self.consume(TK.KEYWORD, "of")
            key = self.parse_type()
            self.consume(TK.KEYWORD, "to")
            value = self.parse_type()
            return TypeRef("map", [key, value])
        tok = self.peek()
        if tok.kind == TK.IDENT:
            self.advance()
            return TypeRef(self.text(tok), [])
        raise ParseError(f"expected type name, got {self.text(tok)!r}", tok)

    def parse_call(self) -> CallExpr:
        self.consume(TK.KEYWORD, "call")
        name = self.text(self.consume(TK.IDENT))
        args: list[Expr] = []
        if self.match(TK.KEYWORD, "with"):
            self.advance()
            args.append(self.parse_expression())
            while self.match(TK.KEYWORD, "and"):
                self.advance()
                args.append(self.parse_expression())
        return CallExpr(name, args)

    def parse_return(self) -> ReturnStmt:
        self.consume(TK.KEYWORD, "return")
        tok = self.peek()
        if tok.kind in (TK.NEWLINE, TK.EOF):
            return ReturnStmt(None)
        value = self.parse_expression()
        return ReturnStmt(value)

    # ---- lvalues ----

    def parse_lvalue(self) -> LValue:
        name_tok = self.consume(TK.IDENT)
        current: Expr = VarRef(self.text(name_tok))
        while True:
            if self.match(TK.DOT):
                self.advance()
                field = self.text(self.consume(TK.IDENT))
                current = FieldAccess(current, field)
            elif self.match(TK.LBRACKET):
                self.advance()
                indices = [self.parse_expression()]
                while self.match(TK.COMMA):
                    self.advance()
                    indices.append(self.parse_expression())
                self.consume(TK.RBRACKET)
                current = IndexAccess(current, indices)
            else:
                break
        if isinstance(current, VarRef):
            return VarLValue(current.name)
        if isinstance(current, FieldAccess):
            return FieldLValue(current.obj, current.field)
        if isinstance(current, IndexAccess):
            return IndexLValue(current.obj, current.indices)
        raise ParseError("invalid assignment target", name_tok)

    # ---- expressions ----

    def parse_expression(self) -> Expr:
        return self.parse_comparison()

    def parse_comparison(self) -> Expr:
        left = self.parse_addition()
        if not self.match(TK.KEYWORD, "is"):
            return left
        self.advance()

        if self.match(TK.KEYWORD, "not"):
            self.advance()
            self.consume(TK.KEYWORD, "equal")
            self.consume(TK.KEYWORD, "to")
            op = "not_equal"
        elif self.match(TK.KEYWORD, "equal"):
            self.advance()
            self.consume(TK.KEYWORD, "to")
            op = "equal"
        elif self.match(TK.KEYWORD, "greater"):
            self.advance()
            self.consume(TK.KEYWORD, "than")
            op = "greater"
        elif self.match(TK.KEYWORD, "less"):
            self.advance()
            self.consume(TK.KEYWORD, "than")
            op = "less"
        elif self.match(TK.KEYWORD, "at"):
            self.advance()
            if self.match(TK.KEYWORD, "least"):
                self.advance()
                op = "at_least"
            elif self.match(TK.KEYWORD, "most"):
                self.advance()
                op = "at_most"
            else:
                tok = self.peek()
                raise ParseError(
                    f"expected 'least' or 'most' after 'at', got {self.text(tok)!r}", tok
                )
        else:
            tok = self.peek()
            raise ParseError(
                f"expected comparison operator after 'is', got {self.text(tok)!r}", tok
            )

        right = self.parse_addition()
        return Compare(op, left, right)

    def parse_addition(self, allow_times: bool = True) -> Expr:
        left = self.parse_multiplication(allow_times)
        while self.match(TK.KEYWORD, "plus") or self.match(TK.KEYWORD, "minus"):
            op = self.text(self.advance())
            right = self.parse_multiplication(allow_times)
            left = BinaryOp(op, left, right)
        return left

    def parse_multiplication(self, allow_times: bool = True) -> Expr:
        left = self.parse_primary()
        while True:
            if allow_times and self.match(TK.KEYWORD, "times"):
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
        expr = self._parse_atom()
        while True:
            if self.match(TK.DOT):
                self.advance()
                field = self.text(self.consume(TK.IDENT))
                expr = FieldAccess(expr, field)
            elif self.match(TK.LBRACKET):
                self.advance()
                indices = [self.parse_expression()]
                while self.match(TK.COMMA):
                    self.advance()
                    indices.append(self.parse_expression())
                self.consume(TK.RBRACKET)
                expr = IndexAccess(expr, indices)
            else:
                break
        return expr

    def _parse_atom(self) -> Expr:
        tok = self.peek()

        if tok.kind == TK.KEYWORD:
            word = self.text(tok)
            if word == "call":
                return self.parse_call()
            if word == "new":
                return self.parse_new()
            if word == "empty":
                return self.parse_empty()
            if word == "length":
                return self.parse_length()
            if word == "rows":
                return self.parse_rows()
            if word == "columns":
                return self.parse_columns()
            if word == "true":
                self.advance()
                return BoolLit(True)
            if word == "false":
                self.advance()
                return BoolLit(False)
            if word == "none":
                self.advance()
                return NoneLit()

        if tok.kind == TK.NUMBER:
            self.advance()
            raw = self.text(tok)
            return NumberLit(float(raw) if "." in raw else int(raw))

        if tok.kind == TK.STRING:
            self.advance()
            return StringLit(decode_string(self.text(tok)))

        if tok.kind == TK.IDENT:
            self.advance()
            return VarRef(self.text(tok))

        if tok.kind == TK.LPAREN:
            self.advance()
            expr = self.parse_expression()
            self.consume(TK.RPAREN)
            return expr

        raise ParseError(f"expected expression, got {self.text(tok)!r}", tok)

    def parse_new(self) -> NewExpr:
        self.consume(TK.KEYWORD, "new")
        name = self.text(self.consume(TK.IDENT))
        return NewExpr(name)

    def parse_empty(self) -> Expr:
        self.consume(TK.KEYWORD, "empty")
        if self.match(TK.KEYWORD, "list"):
            self.advance()
            self.consume(TK.KEYWORD, "of")
            elem = self.parse_type()
            return EmptyList(elem)
        if self.match(TK.KEYWORD, "map"):
            self.advance()
            self.consume(TK.KEYWORD, "of")
            key = self.parse_type()
            self.consume(TK.KEYWORD, "to")
            value = self.parse_type()
            return EmptyMap(key, value)
        if self.match(TK.KEYWORD, "matrix"):
            self.advance()
            dims = [self.parse_addition()]
            while self.match(TK.KEYWORD, "by"):
                self.advance()
                dims.append(self.parse_addition())
            self.consume(TK.KEYWORD, "of")
            elem = self.parse_type()
            return EmptyMatrix(dims, elem)
        tok = self.peek()
        raise ParseError(
            f"expected 'list', 'map' or 'matrix' after 'empty', got {self.text(tok)!r}", tok
        )

    def parse_length(self) -> LengthExpr:
        self.consume(TK.KEYWORD, "length")
        self.consume(TK.KEYWORD, "of")
        value = self.parse_primary()
        return LengthExpr(value)

    def parse_rows(self) -> RowsExpr:
        self.consume(TK.KEYWORD, "rows")
        self.consume(TK.KEYWORD, "of")
        value = self.parse_primary()
        return RowsExpr(value)

    def parse_columns(self) -> ColumnsExpr:
        self.consume(TK.KEYWORD, "columns")
        self.consume(TK.KEYWORD, "of")
        value = self.parse_primary()
        return ColumnsExpr(value)


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
