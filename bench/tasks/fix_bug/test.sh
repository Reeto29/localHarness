#!/usr/bin/env bash
python3 -c "
from stats import average
assert average([1, 2, 3, 4]) == 2.5, average([1, 2, 3, 4])
assert average([10]) == 10, average([10])
assert average([2, 4]) == 3, average([2, 4])
print('ok')
"
