#!/usr/bin/env bash
python3 -c "
from bank import Account
a = Account(100)
a.deposit(50)
assert a.balance == 150, a.balance
a.withdraw(30)
assert a.balance == 120, a.balance
try:
    a.withdraw(1000)
    raise AssertionError('expected ValueError on overdraft')
except ValueError:
    pass
b = Account(0)
a.transfer(b, 20)
assert a.balance == 100, a.balance
assert b.balance == 20, b.balance
print('ok')
"
