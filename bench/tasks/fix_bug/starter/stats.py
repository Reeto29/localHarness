def average(nums):
    total = 0
    for n in nums:
        total += n
    # bug: integer division truncates, and off-by-one on the count
    return total // (len(nums) + 1)
