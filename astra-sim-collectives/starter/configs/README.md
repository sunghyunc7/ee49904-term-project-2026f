# Reference configurations

Hand-editable examples of the two config kinds ASTRA-sim needs.
`run_sweep.py` generates these on the fly; these files are here so you can see the shape
and run the simulator directly when you want to inspect one configuration closely.

    $ASTRA_SIM_ROOT/build/astra_analytical/build/bin/AstraSim_Analytical_Congestion_Aware \
      --workload-configuration=$ASTRA_SIM_ROOT/examples/workload/microbenchmarks/all_reduce/8npus_1MB/all_reduce \
      --system-configuration=sys/ring_algorithm.json \
      --network-configuration=net/ring_8npus_50GBps.yml \
      --remote-memory-configuration=$ASTRA_SIM_ROOT/examples/remote_memory/analytical/no_memory_expansion.json

Available collective algorithms for the four `*-implementation` fields:
`ring`, `oneRing`, `direct`, `oneDirect`, `doubleBinaryTree`, `halvingDoubling`, `oneHalvingDoubling`.

Two of them are restricted. `doubleBinaryTree`, `halvingDoubling` and `oneHalvingDoubling` need a
power-of-two NPU count, and `halvingDoubling` and `oneHalvingDoubling` have no all-to-all
implementation. Outside those limits the simulator does not refuse the configuration — it stops part
way through and prints `!!!Hardware Resource … has unreleased nodes!!!` with no wall time.
`run_sweep.py` checks for both before it runs anything; a hand-edited configuration does not.

Topologies: `Ring`, `Switch`, `FullyConnected`.

## Two-dimensional topologies (congestion-unaware backend only)

The congestion-unaware backend accepts a multi-dimensional network. Give every field one entry per
dimension, and list the fast (intra-node) tier first — the order of the dimensions changes the result.

    topology: [ Switch, Switch ]
    npus_count: [ 8, 4 ]          # 8 NPUs per node x 4 nodes = 32 NPUs; the workload must have 32
    bandwidth: [ 400.0, 50.0 ]    # GB/s
    latency: [ 500.0, 5000.0 ]    # ns

The system file then needs **one algorithm per dimension** in each `*-implementation` list, for
example `["ring", "ring"]`. A list that is too short is not reported as an error: in our check, a
one-entry list on the network above ran to completion and returned the result of the first dimension
alone.

The congestion-aware backend stops with `only support 1-dim topology`. `run_sweep.py` writes
one-dimensional networks only, so a two-dimensional run is a hand-edited one. Generate the workload
first, for example:

    cd $ASTRA_SIM_ROOT/examples/workload/microbenchmarks
    PYTHONPATH=$ASTRA_SIM_ROOT $ASTRA_SIM_ROOT/.venv/bin/python generator_scripts/all_reduce.py \
        --npus-count 32 --coll-size 64
