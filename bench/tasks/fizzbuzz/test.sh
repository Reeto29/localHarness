#!/usr/bin/env bash
# exit 0 = pass
python3 -c "
from fizzbuzz import fizzbuzz
assert fizzbuzz(5) == ['1', '2', 'Fizz', '4', 'Buzz'], fizzbuzz(5)
assert fizzbuzz(15)[-1] == 'FizzBuzz', fizzbuzz(15)
assert fizzbuzz(15)[2] == 'Fizz'
assert fizzbuzz(15)[4] == 'Buzz'
print('ok')
"
