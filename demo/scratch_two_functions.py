def is_sorted(arr):
    """Return True if arr is sorted in non-decreasing order."""
    return all(arr[i] <= arr[i + 1] for i in range(len(arr) - 1))


def first_index_at_least(arr, target):
    """Return the first index in sorted arr whose value is >= target, or
    len(arr) if every element is smaller than target."""
    lo, hi = 0, len(arr)
    while lo < hi:
        mid = (lo + hi) // 2
        if arr[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo
