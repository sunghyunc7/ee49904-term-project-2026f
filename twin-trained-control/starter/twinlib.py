"""
EE49904 Term Project — twin-trained control, shared library.

The digital twin has two layers:

  (1) Geometry layer — ray-trace the scene with Sionna RT to get the path gain per position.
                          Following a user trajectory through it gives the SNR(t) time series.
  (2) Link layer     — build per-MCS BLER(SNR) curves with Sionna PHY.
                          This is the so-called link-to-system mapping, and a table of this
                          form is also what a real scheduler uses.

The control problem is **link adaptation**: look at the measured SNR and pick the MCS that
maximizes throughput while keeping the target BLER.

Key design intent: a policy derived from the twin always meets the target on the twin.
In the field it does not. Where that gap comes from is the whole of this assignment.
"""
import numpy as np

# ---------------------------------------------------------------------------
# MCS definition — (name, bits per modulation symbol, code rate).  spectral efficiency = bps * rate
# ---------------------------------------------------------------------------
MCS_TABLE = [
    ("QPSK-1/2",    2, 1 / 2),
    ("QPSK-3/4",    2, 3 / 4),
    ("16QAM-1/2",   4, 1 / 2),
    ("16QAM-3/4",   4, 3 / 4),
    ("64QAM-2/3",   6, 2 / 3),
    ("64QAM-5/6",   6, 5 / 6),
    ("256QAM-3/4",  8, 3 / 4),
]
# Approximate operating SNR of each MCS (dB). Used only to narrow the range the table is built over;
# it need not be exact — the simulation decides the actual thresholds.
MCS_SNR_HINT = [0.0, 3.5, 6.0, 10.5, 14.0, 18.5, 22.0]

SPECTRAL_EFF = np.array([bps * r for _, bps, r in MCS_TABLE])
MCS_NAMES = [n for n, _, _ in MCS_TABLE]

# Link budget defaults
DEFAULT_BW_HZ = 100e6
DEFAULT_NF_DB = 7.0
DEFAULT_TX_DBM = 44.0


def noise_floor_dbm(bw_hz=DEFAULT_BW_HZ, nf_db=DEFAULT_NF_DB):
    return -174.0 + 10 * np.log10(bw_hz) + nf_db


# ---------------------------------------------------------------------------
# (1) Geometry layer
# ---------------------------------------------------------------------------
def radio_map_snr(scene_name, freq_hz=3.5e9, cell_size=2.0, samples_per_tx=10 ** 6,
                  max_depth=3, tx_dbm=DEFAULT_TX_DBM, bw_hz=DEFAULT_BW_HZ,
                  nf_db=DEFAULT_NF_DB, tx_height_offset=5.0):
    """
    Takes a scene name and returns the SNR grid (dB), the cell-center coordinates and the grid metadata.
    Invalid cells (not reached by any ray) are NaN.
    """
    import sionna.rt as rt
    scene = rt.load_scene(getattr(rt.scene, scene_name))
    scene.frequency = freq_hz
    scene.tx_array = rt.PlanarArray(num_rows=1, num_cols=1,
                                    pattern="tr38901", polarization="V")
    scene.rx_array = rt.PlanarArray(num_rows=1, num_cols=1,
                                    pattern="dipole", polarization="V")
    bb = scene.mi_scene.bbox()
    tx_pos = [float((bb.min.x + bb.max.x) / 2),
              float((bb.min.y + bb.max.y) / 2),
              float(bb.max.z) + tx_height_offset]
    scene.add(rt.Transmitter("tx", position=tx_pos, power_dbm=tx_dbm))
    rm = rt.RadioMapSolver()(scene, max_depth=max_depth,
                             cell_size=(cell_size, cell_size),
                             samples_per_tx=samples_per_tx)
    rss = np.squeeze(np.array(rm.rss))                    # W
    centers = np.array(rm.cell_centers)
    with np.errstate(divide="ignore", invalid="ignore"):
        snr_db = 10 * np.log10(np.where(rss > 0, rss * 1e3, np.nan)) \
            - noise_floor_dbm(bw_hz, nf_db)
    return snr_db, centers, dict(scene=scene_name, tx_pos=tx_pos,
                                 cell_size=cell_size, freq_hz=freq_hz)


def walk_trajectory(snr_db, centers, steps=600, speed_mps=3.0, dt_s=0.01, seed=0):
    """
    Builds a user trajectory walking over the radio map and returns the SNR time series along it.
    It moves only inside valid cells and turns when blocked by a wall.
    Returns: snr_series(dB), positions(N,2 grid indices)
    """
    rng = np.random.default_rng(seed)
    ny, nx = snr_db.shape
    valid = np.isfinite(snr_db)
    ii, jj = np.where(valid)
    start = rng.integers(len(ii))
    y, x = float(ii[start]), float(jj[start])
    cell = abs(float(centers[0, 1, 0] - centers[0, 0, 0])) or 1.0
    step_cells = speed_mps * dt_s / cell
    theta = rng.uniform(0, 2 * np.pi)
    out_snr, out_pos = [], []
    for _ in range(steps):
        for _try in range(12):
            ny_, nx_ = y + step_cells * np.sin(theta), x + step_cells * np.cos(theta)
            iy, ix = int(round(ny_)), int(round(nx_))
            if 0 <= iy < ny and 0 <= ix < nx and valid[iy, ix]:
                y, x = ny_, nx_
                break
            theta = rng.uniform(0, 2 * np.pi)          # blocked: change direction
        iy, ix = int(round(y)), int(round(x))
        out_snr.append(snr_db[iy, ix]); out_pos.append((iy, ix))
    return np.array(out_snr), np.array(out_pos)


# ---------------------------------------------------------------------------
# (2) Link layer — per-MCS BLER(SNR) table
# ---------------------------------------------------------------------------
def build_bler_table(snr_grid_db, k=512, batch_size=200, max_mc_iter=20,
                     num_target_block_errors=60, verbose=True):
    """
    Simulates the BLER of each MCS on the given SNR grid.
    Returns: bler[n_mcs, n_snr]  (points not simulated are filled by extrapolation)
    """
    from sionna.phy import Block
    from sionna.phy.mapping import BinarySource, Mapper, Demapper
    from sionna.phy.fec.ldpc import LDPC5GEncoder, LDPC5GDecoder
    from sionna.phy.channel import AWGN
    from sionna.phy.utils import sim_ber, ebnodb2no

    class _Link(Block):
        def __init__(self, bps, rate, k):
            super().__init__()
            # The codeword length must be a multiple of the bits per symbol to split exactly into QAM symbols.
            # E.g. k=512, rate=3/4 → n=682, but 682 is not divisible by 4 and the mapper breaks.
            n = int(round(k / rate))
            n += (-n) % bps
            self.bps, self.k, self.n = bps, k, n
            self.rate = k / n                      # actual code rate after rounding
            self.src = BinarySource(); self.enc = LDPC5GEncoder(k, n)
            self.map = Mapper("qam", bps); self.dem = Demapper("app", "qam", bps)
            self.dec = LDPC5GDecoder(self.enc, hard_out=True); self.awgn = AWGN()

        def call(self, batch_size, ebno_db):
            no = ebnodb2no(ebno_db, num_bits_per_symbol=self.bps, coderate=self.rate)
            b = self.src([batch_size, self.k]); c = self.enc(b)
            y = self.awgn(self.map(c), no)
            return b, self.dec(self.dem(y, no))

    snr_grid_db = np.asarray(snr_grid_db, dtype=float)
    table = np.ones((len(MCS_TABLE), len(snr_grid_db)))
    for m, ((name, bps, rate), hint) in enumerate(zip(MCS_TABLE, MCS_SNR_HINT)):
        lo, hi = hint - 4.0, hint + 6.0
        sel = (snr_grid_db >= lo) & (snr_grid_db <= hi)
        if not sel.any():
            sel = np.abs(snr_grid_db - hint) <= 6.0
        snrs = snr_grid_db[sel]
        # sim_ber takes Eb/N0: SNR = Eb/N0 + 10log10(bps*rate)
        ebno = snrs - 10 * np.log10(bps * rate)
        link = _Link(bps, rate, k)
        _, bler = sim_ber(link, ebno, batch_size=batch_size, max_mc_iter=max_mc_iter,
                          num_target_block_errors=num_target_block_errors,
                          early_stop=True, verbose=False)
        vals = np.asarray(bler, dtype=float)
        row = table[m]
        row[sel] = vals
        row[snr_grid_db < snrs.min()] = 1.0                    # below the range: always fails
        if (vals <= 1e-3).any():
            row[snr_grid_db > snrs.max()] = vals[-1]           # above the range: hold the last value
        # BLER must fall as SNR rises. Enforce monotonicity with row[i] = max_{j>=i} row[j].
        table[m] = np.maximum.accumulate(row[::-1])[::-1]
        if verbose:
            thr = snr_grid_db[np.argmax(table[m] <= 0.1)] if (table[m] <= 0.1).any() else np.nan
            print(f"  {name:<12} 10% BLER threshold SNR ≈ {thr:5.1f} dB")
    return table


# ---------------------------------------------------------------------------
# Policy — derived from the BLER table of the twin
# ---------------------------------------------------------------------------
def derive_policy(snr_grid_db, bler_table, target_bler=0.1, margin_db=0.0):
    """
    For each SNR grid point, picks "the MCS with the highest spectral efficiency that keeps the target BLER".
    If no MCS satisfies the condition, -1 (hold transmission).
    If margin_db > 0, that much is subtracted from the observed SNR before picking (conservative backoff).
    Returns: policy[n_snr] — MCS index
    """
    snr_grid_db = np.asarray(snr_grid_db, dtype=float)
    pol = np.full(len(snr_grid_db), -1, dtype=int)
    for i, s in enumerate(snr_grid_db):
        j = int(np.clip(np.searchsorted(snr_grid_db, s - margin_db), 0, len(snr_grid_db) - 1))
        ok = np.where(bler_table[:, j] <= target_bler)[0]
        if ok.size:
            pol[i] = ok[np.argmax(SPECTRAL_EFF[ok])]
    return pol


def _idx(snr_grid_db, snr):
    return int(np.clip(np.searchsorted(snr_grid_db, snr), 0, len(snr_grid_db) - 1))


def evaluate(snr_true, snr_observed, snr_grid_db, bler_table, policy,
             target_bler=0.1, seed=0, truth_grid_db=None, truth_table=None):
    """
    Runs the policy. **The policy picks the MCS from snr_observed, but the
    transmission happens at snr_true.** In the twin evaluation the two are equal;
    in the field evaluation they differ because of delay and estimation error.

    snr_grid_db / bler_table are what the *policy* was derived from. truth_grid_db /
    truth_table are the link layer the *field* obeys — the table that decides whether a
    block is actually lost. Leave them as None and the field obeys the policy's own table
    (the twin's belief and the truth coincide). Pass the link layer of another twin to
    evaluate a policy whose table differs from the truth.

    Returns: dict(throughput, bler, violation, silent_frac, mcs_hist)
    """
    if truth_table is None:
        truth_grid_db, truth_table = snr_grid_db, bler_table
    rng = np.random.default_rng(seed)
    n = len(snr_true)
    thr = np.zeros(n); err = np.zeros(n, dtype=bool); silent = np.zeros(n, dtype=bool)
    mcs_used = np.full(n, -1, dtype=int)
    for t in range(n):
        m = policy[_idx(snr_grid_db, snr_observed[t])]
        mcs_used[t] = m
        if m < 0:
            silent[t] = True
            continue
        p = truth_table[m, _idx(truth_grid_db, snr_true[t])]
        if rng.random() < p:
            err[t] = True                      # block error → transmission failed
        else:
            thr[t] = SPECTRAL_EFF[m]           # on success, gain the spectral efficiency
    sent = ~silent
    bler_meas = err[sent].mean() if sent.any() else 0.0
    return dict(
        throughput=float(thr.mean()),                 # bit/s/Hz, mean (failure=0)
        bler=float(bler_meas),                        # fraction of attempted transmissions that failed
        violation=bool(bler_meas > target_bler),
        bler_ratio=float(bler_meas / target_bler) if target_bler else float("nan"),
        silent_frac=float(silent.mean()),
        mcs_hist=np.bincount(mcs_used[sent] if sent.any() else np.array([], dtype=int),
                             minlength=len(MCS_TABLE)).tolist(),
    )


def stale_and_noisy(snr_true, delay_steps=0, est_std_db=0.0, seed=0):
    """
    The SNR the policy actually sees in the field: it is delay_steps old and
    carries an estimation error of est_std_db.
    """
    rng = np.random.default_rng(seed)
    obs = np.roll(snr_true, delay_steps)
    if delay_steps > 0:
        obs[:delay_steps] = snr_true[0]
    if est_std_db > 0:
        obs = obs + rng.normal(0.0, est_std_db, size=obs.shape)
    return obs
