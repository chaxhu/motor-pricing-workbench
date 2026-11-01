# Benchmark results

Run on 2026-11-01 10:20 with `python benchmark.py --sizes 1000 10000 100000 1000000 --repeats 5`.

- Machine: Windows 11, AMD64, Intel64 Family 6 Model 140 Stepping 2, GenuineIntel, 8 logical CPUs
- Python 3.12.4, pandas 3.1.0, NumPy 2.5.4
- Times are the best of 5 runs, except row-by-row above 100,000 policies, which is run once.
- Both engines were checked to give identical premiums before timing.
- Synthetic data, illustrative rates (not real Hastings Direct pricing).

| Policies | Row-by-row (s) | Vectorised (s) | Speed-up | Validation (s) |
| ---: | ---: | ---: | ---: | ---: |
| 1,000 | 0.0079 | 0.0010 | 8x | 0.0240 |
| 10,000 | 0.0790 | 0.0021 | 38x | 0.0283 |
| 100,000 | 0.9552 | 0.0165 | 58x | 0.0892 |
| 1,000,000 | 12.0158 | 0.3039 | 40x | 1.2859 |
