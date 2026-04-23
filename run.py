from lexer import tokenize
from parser import Parser
from evaluator import execute_program


def run(source: str) -> None:
    tokens = tokenize(source)
    stmts = Parser(source, tokens).parse_program()
    execute_program(stmts)


if __name__ == "__main__":
    demo = '''# demo — records, lists, maps

define record Order
    customer as text
    amount as number
end

define function total_of
    input orders as list of Order
    output as number

    set total to 0
    repeat for each order in orders
        add order.amount to total
    end
    return total
end

set orders to empty list of Order

set o1 to new Order
set o1.customer to "Alice"
set o1.amount to 100
append o1 to orders

set o2 to new Order
set o2.customer to "Bob"
set o2.amount to 250
append o2 to orders

set o3 to new Order
set o3.customer to "Carol"
set o3.amount to 75
append o3 to orders

set grand_total to call total_of with orders
print "order count:" and length of orders
print "grand total:" and grand_total

if grand_total is greater than 300
    print "big batch"
else
    print "small batch"
end

set prices_by_item to empty map of text to number
set prices_by_item["apple"] to 1.5
set prices_by_item["bread"] to 3.0
set prices_by_item["milk"] to 2.75
print "apple costs" and prices_by_item["apple"]
'''
    run(demo)
