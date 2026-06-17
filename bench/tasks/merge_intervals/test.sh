#!/usr/bin/env bash
python3 -c "
from merge import merge
assert merge([[1,3],[2,6],[8,10],[15,18]]) == [[1,6],[8,10],[15,18]]
# touching intervals merge
assert merge([[1,3],[3,5]]) == [[1,5]], merge([[1,3],[3,5]])
# unsorted input
assert merge([[8,10],[1,3],[2,6]]) == [[1,6],[8,10]], merge([[8,10],[1,3],[2,6]])
# empty
assert merge([]) == []
# fully nested
assert merge([[1,10],[2,3],[4,5]]) == [[1,10]], merge([[1,10],[2,3],[4,5]])
print('ok')
"
