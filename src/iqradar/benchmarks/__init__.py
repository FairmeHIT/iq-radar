# iqradar.benchmarks — benchmark backend registry and execution adapters.
#
# Each benchmark type (deep-swe, terminal-bench, terminal-bench-2) has a
# corresponding ``BenchmarkBackend`` subclass in this package.  The
# ``build_backends`` factory constructs them from a ``BenchmarkConfigSet``.
#
# Boundary: backends may import ``iqradar.deepswe`` (for deep-swe) but must
# *not* import ``iqradar.reporting`` or ``iqradar.publication``.